"""能力准入：不可变 preview/lease、解析器与（切片 3 起的）许可注册表。

1C 不变式：lease 无密钥、无 ClientSession、无连接配置；aclose 恰好释放一次。
"""
from __future__ import annotations

import asyncio
import json

from langchain_core.messages import AIMessage
from dataclasses import dataclass, field
from typing import Callable, Sequence

from langchain.agents.middleware import AgentMiddleware, HumanInTheLoopMiddleware
from langchain_core.tools import BaseTool

from agent.mcp import McpRegistry, McpServer, McpToolBinding, StdioTrustRequired
from agent.skills import SkillRegistry, SkillRuntimeItem, SkillRuntimeSnapshot


@dataclass(frozen=True)
class CapabilityPreview:
    """线程锁内读取的准入事实；在释放锁、取得 lease 后必须原样复验。"""

    thread_id: str
    thread_revision: int
    selected_skills: tuple[str, ...]


class CapabilityLease:
    """一次 run 的能力租约：工具目录 + 系统上下文 + 请求级中间件工厂。

    密钥只在 build_request_middleware(secrets) 时被消费，绝不出现在本对象上。
    aclose() 幂等且恰好执行一次底层释放。mcp_bindings 无 session/密钥。
    """

    def __init__(
        self,
        *,
        tools: Sequence[BaseTool],
        system_context: str,
        middleware: tuple = (),
        skill_digests: tuple[tuple[str, str], ...] = (),
        mcp_bindings: tuple = (),
        thread_id: str = "",
        allowances: "AllowanceRegistry | None" = None,
        registry: "McpRegistry | None" = None,
        on_release: Callable[[], None] | None = None,
    ):
        self._tools = tuple(tools)
        self.system_context = system_context
        self._middleware = tuple(middleware)
        self.skill_digests = tuple(skill_digests)
        self.mcp_bindings = tuple(mcp_bindings)
        self._thread_id = thread_id
        self._allowances = allowances
        self._registry = registry
        self._released = False

        def _release() -> None:
            if on_release is not None:
                on_release()

        self._release = _release

    @property
    def tools(self) -> tuple[BaseTool, ...]:
        return self._tools

    @property
    def skill_names(self) -> tuple[str, ...]:
        return tuple(name for name, _ in self.skill_digests)

    def build_request_middleware(self, secrets) -> tuple:
        """请求级中间件：附加中间件 + （有 MCP bindings 时）guard 先于 HITL。"""
        middleware = list(self._middleware)
        if self.mcp_bindings:
            registry_secrets = (
                {sid: set(vals) for sid, vals in self._registry.secret_sets().items()}
                if self._registry is not None else {})
            guard = McpArgumentGuard(
                aliases={b.alias for b in self.mcp_bindings},
                secrets=secrets,
                registry_secrets=registry_secrets,
            )
            hitl = build_hitl_policy(
                thread_id=self._thread_id,
                bindings=self.mcp_bindings,
                allowances=self._allowances or AllowanceRegistry(),
            )
            middleware = [*middleware, guard, hitl]
        return tuple(middleware)

    async def aclose(self) -> None:
        self.release()

    def release(self) -> None:
        """同步释放：幂等且恰好执行一次底层回调。"""
        if not self._released:
            self._released = True
            self._release()


class StaticCapabilityLease(CapabilityLease):
    """测试用：直接包装既有 tools/middleware。"""


class CapabilityUnavailable(RuntimeError):
    """fail-closed 准入：相关 server 不可用（HTTP 503 语义）。"""

    code = "MCP_UNAVAILABLE"


class McpUnavailable(CapabilityUnavailable):
    pass


class McpArgumentsTooLarge(RuntimeError):
    code = "MCP_ARGUMENTS_TOO_LARGE"


ARGUMENTS_BYTE_LIMIT = 65_536


def _redact_value(value, secrets: set[str]):
    if isinstance(value, str):
        text = value
        for secret in secrets:
            if secret:
                text = text.replace(secret, "[redacted]")
        return text
    if isinstance(value, dict):
        return {k: _redact_value(v, secrets) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_value(v, secrets) for v in value]
    return value


class McpArgumentGuard(AgentMiddleware):
    """请求级 MCP 参数守卫：模型响应后、HITL/写状态前检查 tool call 参数。

    只对副本脱敏做大小检查；不修改原始参数、不产生 interrupt、不触发任何
    MCP server I/O。超限抛 McpArgumentsTooLarge（无敏感详情）。
    """

    def __init__(self, *, aliases: tuple[str, ...] | set[str], secrets,
                 registry_secrets: dict[str, set[str]] | None = None):
        super().__init__()
        self._aliases = frozenset(aliases)
        self._model_key = secrets.model_api_key.get_secret_value() if secrets is not None else ""
        self._registry_secrets = registry_secrets or {}

    def _encoded_size(self, arguments: dict) -> int:
        secret_pool = {self._model_key}
        for values in self._registry_secrets.values():
            secret_pool |= values
        redacted = _redact_value(arguments, {s for s in secret_pool if s})
        encoded = json.dumps(redacted, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"))
        return len(encoded.encode("utf-8"))

    async def awrap_model_call(self, request, call_llm, **kwargs):
        """langchain 中间件协议：await handler(request)；此处 request 为 ModelRequest。"""
        response = await call_llm(request)
        messages = self._result_messages(response)
        for message in messages:
            if not isinstance(message, AIMessage):
                continue
            for tool_call in message.tool_calls or []:
                if tool_call.get("name") in self._aliases:
                    if self._encoded_size(tool_call.get("args") or {}) > ARGUMENTS_BYTE_LIMIT:
                        raise McpArgumentsTooLarge(
                            f"{McpArgumentsTooLarge.code}: MCP tool call 参数超过 64 KB")
        return response

    @staticmethod
    def _result_messages(response) -> list:
        """从 ModelResponse / ExtendedModelResponse / list 归一化出消息列表。"""
        if isinstance(response, list):
            return response
        for attr in ("messages", "result", "generations"):
            inner = getattr(response, attr, None)
            if inner is None:
                continue
            if isinstance(inner, list) and inner and hasattr(inner[0], "message"):
                return [g.message for g in inner]  # ChatGeneration 列表
            if isinstance(inner, list):
                return inner
            generations = getattr(inner, "generations", None)
            if generations and hasattr(generations[0], "message"):
                return [g.message for g in generations]
        result = getattr(response, "result", None)
        if result is not None and isinstance(result, AIMessage):
            return [result]
        if isinstance(response, AIMessage):
            return [response]
        return []


class AllowanceRegistry:
    """线程级 thread_session 许可（内存，不落盘）。"""

    def __init__(self):
        self._granted: set[tuple[str, str, str]] = set()

    def has(self, thread_id: str, server_id: str, original_tool_name: str) -> bool:
        return (thread_id, server_id, original_tool_name) in self._granted

    def grant(self, thread_id: str, server_id: str, original_tool_name: str) -> None:
        self._granted.add((thread_id, server_id, original_tool_name))

    def clear_thread(self, thread_id: str) -> int:
        removed = [k for k in self._granted if k[0] == thread_id]
        self._granted -= set(removed)
        return len(removed)

    def clear_server(self, server_id: str) -> int:
        removed = [k for k in self._granted if k[1] == server_id]
        self._granted -= set(removed)
        return len(removed)

    def clear_tool(self, server_id: str, original_tool_name: str) -> int:
        removed = [k for k in self._granted
                   if k[1] == server_id and k[2] == original_tool_name]
        self._granted -= set(removed)
        return len(removed)

    def clear_all(self) -> int:
        count = len(self._granted)
        self._granted.clear()
        return count


def build_hitl_policy(*, thread_id: str, bindings, allowances: AllowanceRegistry):
    """穷举 HITL：interrupt_on 集合与已启用 alias 严格相等。"""
    interrupt_on = {}
    for binding in bindings:
        def _when(request, _b=binding):
            return not allowances.has(thread_id, _b.server_id, _b.original_name)

        description = f"MCP 工具 {_b_description(binding)} 需要审批"
        interrupt_on[binding.alias] = {
            "allowed_decisions": ["approve", "reject"],
            "when": _when,
            "description": description,
        }
    assert set(interrupt_on) == {binding.alias for binding in bindings}
    return HumanInTheLoopMiddleware(interrupt_on=interrupt_on)


def _b_description(binding) -> str:
    return f"{binding.server_id}/{binding.original_name}"


class SkillUnavailableError(RuntimeError):
    pass


class CapabilityResolver:
    """从 preview 解析当前 Skill generation，构建不可变 lease。

    切片 2 之前不向 Graph 暴露任何 MCP alias。
    """

    def __init__(self, skills: SkillRegistry, tools_provider=None,
                 registry: McpRegistry | None = None,
                 allowances: "AllowanceRegistry | None" = None):
        self._skills = skills
        # 组合点可注入（router 测试接缝）；默认走 tool_registry
        self._tools_provider = tools_provider
        self._registry = registry
        self._allowances = allowances

    async def acquire(self, preview: CapabilityPreview) -> CapabilityLease:
        registry = self._skills
        # 取 lease 前重新扫描：preview 与最终准入之间磁盘可能已变化
        generation = await asyncio.to_thread(registry.refresh)

        items: list[SkillRuntimeItem] = []
        digests: list[tuple[str, str]] = []
        for name in preview.selected_skills:
            record = await asyncio.to_thread(registry.require, name)
            items.append(SkillRuntimeItem(
                name=record.name or name,
                description=record.description or "",
                digest=record.digest or "",
                instructions=record.instructions or "",
                files=record.files,
            ))
            digests.append((record.name or name, record.digest or ""))

        snapshot = SkillRuntimeSnapshot.from_records(generation.number, tuple(items))
        skill_tools = snapshot.build_tools(registry)

        # 切片 3：fail-closed MCP 准入（相关 server 全部连接成功才给 bindings）
        bindings: tuple = ()
        if self._registry is not None:
            bindings = await self._admit_mcp(preview.thread_id)

        # 内置工具与 Skill 工具组合（tool_registry 是唯一组合点）
        if self._tools_provider is not None:
            tools = await asyncio.to_thread(self._tools_provider, skill_tools)
        else:
            from agent.tool_registry import compose_run_tools

            tools = await asyncio.to_thread(compose_run_tools, skill_tools)
        if bindings:
            tools = [*tools, *(b.as_langchain_tool(self._registry) for b in bindings)]
        return CapabilityLease(
            tools=tools,
            system_context=snapshot.render_catalog(),
            skill_digests=tuple(digests),
            mcp_bindings=bindings,
            thread_id=preview.thread_id,
            allowances=self._allowances,
            registry=self._registry,
        )

    async def _admit_mcp(self, thread_id: str) -> tuple:
        """相关 server = 全局 enabled 且 catalog 至少一个 enabled tool。

        任一相关 server 连接失败 → 释放全部部分引用并抛 McpUnavailable。
        """
        assert self._registry is not None
        doc = await asyncio.to_thread(self._registry.store.load)
        relevant: list[McpServer] = [
            server for server in doc.servers
            if server.enabled and any(tool.enabled for tool in server.tools)
        ]
        if not relevant:
            return ()
        bindings: list = []
        for server in relevant:
            try:
                # 准入只做发现（连接/复用会话 + 官方 adapter Tool），不重写目录、
                # 不 bump MCP document revision（规范 §11.1：与持久化目录一致即可）
                generation = await self._registry._ensure_session(server)
                tools_by_original = generation.tools
            except Exception as exc:
                # fail-closed：脱敏有界错误；共享会话/目录保持原状，不误杀其他线程
                detail = str(exc)[:200]
                raise McpUnavailable(
                    f"MCP server {server.display_name} 无法连接：{detail}"
                ) from exc
            if not tools_by_original:
                # 会话存在但目录未发现：做一次只读发现（不持久化）
                try:
                    tools_by_original = await self._registry._discover_tools(
                        server.id, generation)
                except Exception as exc:
                    raise McpUnavailable(
                        f"MCP server {server.display_name} 工具发现失败：{str(exc)[:200]}"
                    ) from exc
            for entry in server.tools:
                if not entry.enabled:
                    continue
                adapter_tool = tools_by_original.get(entry.original_name)
                if adapter_tool is None:
                    raise McpUnavailable(
                        f"MCP server {server.display_name} 目录与连接不一致"
                        f"（缺少 {entry.original_name}）")
                from agent.mcp import _tool_args_schema

                bindings.append(McpToolBinding(
                    server_id=server.id,
                    original_name=entry.original_name,
                    alias=entry.alias,
                    description=entry.description,
                    args_schema=_tool_args_schema(adapter_tool),
                    config_generation=doc.revision,
                    catalog_generation=generation.number,
                    server_name=server.display_name,
                ))
        return tuple(bindings)


def enrich_pending_interrupts(pending, lease, model_key: str):
    """按 alias→binding 映射为待审批中断填充 MCP 元数据（camelCase 线格式）。

    参数先用模型密钥 + Registry secret set 递归脱敏，再进入 metadata/SSE/
    thread JSON；非 MCP 工具的中断原样返回。返回新列表（不改动原对象）。
    """
    from dataclasses import replace

    bindings = getattr(lease, "mcp_bindings", ()) or ()
    if not bindings:
        return list(pending)
    by_alias = {b.alias: b for b in bindings}
    secret_pool = {model_key}
    registry = getattr(lease, "_registry", None)
    if registry is not None:
        for values in registry.secret_sets().values():
            secret_pool |= values
    secrets = {s for s in secret_pool if s}
    enriched = []
    for item in pending:
        action = (item.value.get("action_requests") or [{}])[0]
        binding = by_alias.get(action.get("name", ""))
        if binding is None:
            enriched.append(item)
            continue
        enriched.append(replace(
            item,
            server_id=binding.server_id,
            server_name=binding.server_name,
            original_tool_name=binding.original_name,
            tool_alias=binding.alias,
            arguments=_redact_value(action.get("args") or {}, secrets),
        ))
    return enriched
