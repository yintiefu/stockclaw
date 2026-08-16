"""能力准入：不可变 preview/lease、解析器与（切片 3 起的）许可注册表。

1C 不变式：lease 无密钥、无 ClientSession、无连接配置；aclose 恰好释放一次。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Callable, Sequence

from langchain.agents.middleware import AgentMiddleware
from langchain_core.tools import BaseTool

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
    aclose() 幂等且恰好执行一次底层释放。
    """

    def __init__(
        self,
        *,
        tools: Sequence[BaseTool],
        system_context: str,
        middleware: tuple = (),
        skill_digests: tuple[tuple[str, str], ...] = (),
        on_release: Callable[[], None] | None = None,
    ):
        self._tools = tuple(tools)
        self.system_context = system_context
        self._middleware = tuple(middleware)
        self.skill_digests = tuple(skill_digests)
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
        """请求级中间件；切片 1 的 Skill lease 不追加额外中间件。"""
        return self._middleware

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
    code = "MCP_UNAVAILABLE"


class SkillUnavailableError(RuntimeError):
    pass


class CapabilityResolver:
    """从 preview 解析当前 Skill generation，构建不可变 lease。

    切片 2 之前不向 Graph 暴露任何 MCP alias。
    """

    def __init__(self, skills: SkillRegistry, tools_provider=None):
        self._skills = skills
        # 组合点可注入（router 测试接缝）；默认走 tool_registry
        self._tools_provider = tools_provider

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

        # 内置工具与 Skill 工具组合（tool_registry 是唯一组合点）
        if self._tools_provider is not None:
            tools = await asyncio.to_thread(self._tools_provider, skill_tools)
        else:
            from agent.tool_registry import compose_run_tools

            tools = await asyncio.to_thread(compose_run_tools, skill_tools)
        return CapabilityLease(
            tools=tools,
            system_context=snapshot.render_catalog(),
            skill_digests=tuple(digests),
        )
