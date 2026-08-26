"""嵌入式 Agent 图（页面 Ask-AI）：隔离的页面级问答图。

特点：
- 只消费内置工具与 /builtin/ 技能，不包含 MCP、HITL 与 /user/ 技能；
- 通过 TypedDict / AgentState 维护非空覆盖页面快照 page_context 与版本历史；
- 动态提示词注入最新页面快照，隔离不同 route / 股票的上下文。
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated, Any

from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend
from deepagents.middleware.filesystem import FilesystemMiddleware
from deepagents.middleware.skills import SkillsMiddleware
from langchain.agents import AgentState, create_agent
from langchain.agents.middleware import ModelRequest, dynamic_prompt, after_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver
from pydantic import BaseModel, ConfigDict, model_validator

from agent.model_factory import build_model
from agent.policy import fixed_system_policy
from agent.settings import load_agent_settings
from agent.skill_backends import BUILTIN_SKILLS_DIR
from agent.tool_registry import build_builtin_tools
from agent.workflow_events import utc_now


MAX_PAGE_CONTEXT_CHARS: int = 40000


class PageContextInput(BaseModel):
    """前端传入的页面快照输入。"""
    model_config = ConfigDict(extra="forbid")

    route: str
    scope_key: str
    source_as_of: str
    content: str

    @model_validator(mode="after")
    def validate_content_length(self) -> "PageContextInput":
        if len(self.content) > MAX_PAGE_CONTEXT_CHARS:
            self.content = self.content[:MAX_PAGE_CONTEXT_CHARS - 15] + "...[truncated]"
        return self


class PageContextSnapshot(BaseModel):
    """服务端版本化的页面快照。"""
    model_config = ConfigDict(extra="forbid")

    route: str
    scope_key: str
    source_as_of: str
    content: str
    captured_at: str
    version: int

    @model_validator(mode="after")
    def validate_snapshot_length(self) -> "PageContextSnapshot":
        if len(self.content) > MAX_PAGE_CONTEXT_CHARS:
            self.content = self.content[:MAX_PAGE_CONTEXT_CHARS - 15] + "...[truncated]"
        return self


class AssistantContextRef(BaseModel):
    """助手历史消息对快照版本的引用。"""
    model_config = ConfigDict(extra="forbid")

    turn_index: int
    snapshot_version: int
    captured_at: str


def _to_snapshot(ctx: Any, default_version: int = 1) -> PageContextSnapshot | None:
    """将任意快照或输入对象规整为 PageContextSnapshot。"""
    if ctx is None:
        return None
    if isinstance(ctx, PageContextSnapshot):
        return ctx
    if isinstance(ctx, PageContextInput):
        if not ctx.content or not ctx.content.strip():
            return None
        return PageContextSnapshot(
            route=ctx.route,
            scope_key=ctx.scope_key,
            source_as_of=ctx.source_as_of,
            content=ctx.content,
            captured_at=utc_now(),
            version=default_version,
        )
    if isinstance(ctx, dict):
        content = ctx.get("content", "")
        if not content or not content.strip():
            return None
        return PageContextSnapshot(
            route=ctx.get("route", ""),
            scope_key=ctx.get("scope_key", ""),
            source_as_of=ctx.get("source_as_of", ""),
            content=content,
            captured_at=ctx.get("captured_at", utc_now()),
            version=ctx.get("version", default_version),
        )
    return None


def keep_latest_nonempty_context(
    old: PageContextSnapshot | PageContextInput | dict[str, Any] | None,
    new: PageContextInput | PageContextSnapshot | dict[str, Any] | None,
) -> PageContextSnapshot | None:
    """页面快照非空覆盖 Reducer。

    规则：
    - 首轮无合法非空快照 -> 返回 None（触发后续校验拦截）；
    - 后续轮次省略或为空 -> 保留旧快照不变；
    - 同一 scope 传入合法非空快照 -> 递增版本号并更新时间；
    - 不同 route 或 scope -> 抛出异常拒绝串流。
    """
    old_snap = _to_snapshot(old, default_version=1)
    if new is None:
        return old_snap

    if isinstance(new, dict):
        new = PageContextInput.model_validate(new)

    if not new.content or not new.content.strip():
        return old_snap

    if old_snap is not None:
        if new.route != old_snap.route or new.scope_key != old_snap.scope_key:
            raise ValueError(
                f"页面上下文 route/scope 不匹配：({new.route}, {new.scope_key}) != ({old_snap.route}, {old_snap.scope_key})"
            )
        return PageContextSnapshot(
            route=new.route,
            scope_key=new.scope_key,
            source_as_of=new.source_as_of,
            content=new.content,
            captured_at=utc_now(),
            version=old_snap.version + 1,
        )

    return PageContextSnapshot(
        route=new.route,
        scope_key=new.scope_key,
        source_as_of=new.source_as_of,
        content=new.content,
        captured_at=utc_now(),
        version=1,
    )


def append_context_refs(
    old: list[AssistantContextRef] | None,
    new: list[AssistantContextRef] | AssistantContextRef | None,
) -> list[AssistantContextRef]:
    """追加助手消息快照引用。"""
    result = list(old or [])
    if new is None:
        return result
    if isinstance(new, list):
        result.extend(new)
    else:
        result.append(new)
    return result


class EmbeddedAgentState(AgentState):
    """嵌入式 Agent 图状态。"""
    page_context: Annotated[PageContextSnapshot | None, keep_latest_nonempty_context]
    assistant_context_refs: Annotated[list[AssistantContextRef], append_context_refs]


def _compact_context_refs(state: Any) -> list[str]:
    """把 assistant_context_refs 压成紧凑版本/时间标记行。

    历史回答只保留快照版本与时间归属，不重复注入旧快照正文——
    多轮上下文不会按快照大小线性膨胀。
    """
    refs = state.get("assistant_context_refs") if isinstance(state, dict) else None
    if not refs:
        return []
    lines: list[str] = []
    for ref in refs:
        if isinstance(ref, AssistantContextRef):
            lines.append(f"- 第 {ref.turn_index} 条助手回答基于页面快照 v{ref.snapshot_version} · {ref.captured_at}")
        elif isinstance(ref, dict):
            lines.append(
                f"- 第 {ref.get('turn_index', '?')} 条助手回答基于页面快照 "
                f"v{ref.get('snapshot_version', '?')} · {ref.get('captured_at', '?')}"
            )
    return lines


@dynamic_prompt
def embedded_context_prompt(request: ModelRequest) -> str:
    """动态注入最新页面数据快照、历史回答的快照归属与中立系统策略。"""
    snap = None
    state = request.state if hasattr(request, "state") and request.state else {}
    if state:
        snap = state.get("page_context")

    if snap is None:
        raise ValueError("缺少页面快照输入 (page_context)，首轮对话必须提供当前页面数据快照。")

    if isinstance(snap, dict):
        route = snap.get("route", "")
        scope_key = snap.get("scope_key", "")
        source_as_of = snap.get("source_as_of", "")
        content = snap.get("content", "")
        version = snap.get("version", 1)
        captured_at = snap.get("captured_at", utc_now())
    elif isinstance(snap, PageContextSnapshot):
        route = snap.route
        scope_key = snap.scope_key
        source_as_of = snap.source_as_of
        content = snap.content
        version = snap.version
        captured_at = snap.captured_at
    elif isinstance(snap, PageContextInput):
        route = snap.route
        scope_key = snap.scope_key
        source_as_of = snap.source_as_of
        content = snap.content
        version = 1
        captured_at = utc_now()
    else:
        raise ValueError(f"无效的页面快照类型：{type(snap)}")

    if not content or not content.strip():
        raise ValueError("页面快照内容为空 (content)")

    policy = fixed_system_policy("嵌入式问答")
    snapshot_block = (
        f"【当前页面快照 v{version} · {captured_at}】\n"
        f"- 页面路由: {route}\n"
        f"- 标的范围: {scope_key}\n"
        f"- 数据时间: {source_as_of}\n\n"
        f"{content}"
    )
    parts = [policy, snapshot_block]
    ref_lines = _compact_context_refs(state)
    if ref_lines:
        attribution = "【历史回答快照归属】\n" + "\n".join(ref_lines)
        parts.append(attribution)
    instruction = (
        "说明：历史对话中的助手回答可能基于较早版本的页面快照生成，不代表当前数据；"
        "旧快照正文不再重复提供。请始终以最新页面快照与工具查询结果为准。"
    )
    parts.append(instruction)
    return "\n\n".join(parts)


@after_agent(state_schema=EmbeddedAgentState)
def record_snapshot_ref(state: EmbeddedAgentState, runtime: Any) -> dict[str, Any]:
    """在对话轮次结束时将助手回答归属到当前页面快照版本。"""
    snap = state.get("page_context")
    if snap:
        if isinstance(snap, PageContextSnapshot):
            version = snap.version
            captured_at = snap.captured_at
        elif isinstance(snap, dict):
            version = snap.get("version", 1)
            captured_at = snap.get("captured_at", utc_now())
        else:
            version = getattr(snap, "version", 1)
            captured_at = getattr(snap, "captured_at", utc_now())
        turn_index = len(state.get("messages", []))
        ref = AssistantContextRef(
            turn_index=turn_index,
            snapshot_version=version,
            captured_at=captured_at,
        )
        return {"assistant_context_refs": [ref]}
    return {}


async def build_embedded_graph(
    model: BaseChatModel | None = None,
    *,
    checkpointer: BaseCheckpointSaver | None = None,
    builtin_skills_root: Path | None = None,
):
    """组装嵌入式 Ask-AI Agent 图。"""
    settings = load_agent_settings()
    skills_root = builtin_skills_root or BUILTIN_SKILLS_DIR

    backend = CompositeBackend(
        default=StateBackend(),
        routes={
            "/builtin/": FilesystemBackend(root_dir=str(skills_root), virtual_mode=True),
        },
    )

    middleware = [
        SkillsMiddleware(backend=backend, sources=["/builtin/"]),
        FilesystemMiddleware(backend=backend, tools=["ls", "read_file"]),
        embedded_context_prompt,
        record_snapshot_ref,
    ]

    tools = build_builtin_tools()

    return create_agent(
        model=model or build_model(settings),
        tools=tools,
        middleware=middleware,
        state_schema=EmbeddedAgentState,
        checkpointer=checkpointer,
    )


graph = asyncio.run(build_embedded_graph())
