from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Literal, Sequence
from uuid import uuid4

from ag_ui.core.types import RunAgentInput

from agent.models import AgentMessage, ModelRef, RunDocument, RunSecrets, RunSummary, RunUsage, ThreadDocument
from agent.protocol import PendingInterrupt, interrupt_payloads
from agent.capabilities import (
    AllowanceRegistry,
    CapabilityLease,
    CapabilityPreview,
    CapabilityResolver,
    StaticCapabilityLease,
)
from agent.governance import (
    DEFAULT_POLICY_SNAPSHOT,
    ContextAndModelGovernance,
    GovernanceView,
    RunControl,
    ToolExecutionGovernance,
    render_policy_explanation,
)
from agent.tool_executor import BoundedToolExecutor
from agent.provenance import (
    append_automatic_urls,
    extract_model_urls,
    summarize_tool_source,
)
from agent.runtime import AgentFactory, RuntimeHandle
from agent.skills import SkillError
from agent.policy import PolicyStore
from agent.provenance import SOURCE_CAPACITY
from agent.stores import DocumentNotFound, RevisionConflict, utc_now

if TYPE_CHECKING:
    from agent.stores import RunStore, ThreadStore

Phase = Literal["running", "awaiting_approval", "completed", "failed", "cancelled"]


@dataclass
class ActiveRunHandle:
    """线程的活动 run 状态；密钥绝不落在本结构上。"""

    runtime: RuntimeHandle
    phase: Phase
    pending_interrupts: list[PendingInterrupt] = field(default_factory=list)
    thread_revision: int | None = None
    product_run_id: str | None = None
    protocol_run_ids: list[str] = field(default_factory=list)
    trigger_message_id: str | None = None
    started_monotonic: float = field(default_factory=time.monotonic)
    # 进入 awaiting_approval 的单调时刻（resume 时结算 approval_wait_ms）
    approval_started_monotonic: float | None = None
    # 脱敏的历史快照引用（进入后续模型输入的服务端历史）
    history_snapshot: tuple[AgentMessage, ...] = ()
    # 语义边界持久化日志（不含密钥/闭包）
    journal: "RunJournal | None" = None
    # 1C：本次 run 的能力租约（无密钥）；resume 复用同一 lease
    capability_lease: CapabilityLease | None = None
    # 1D：本次 run 的治理控制（reservation/段计时/usage）；resume 复用同一 control。
    # 占位/纯内存句柄走默认快照；生产准入在 Task 7 起注入 PolicyStore 快照。
    control: RunControl = field(default_factory=lambda: RunControl(DEFAULT_POLICY_SNAPSHOT))


@dataclass
class StartAdmission:
    """一次成功准入的产物：句柄、产品 run、权威 adapter 输入与已提交 revision。"""

    handle: ActiveRunHandle
    run: RunDocument
    input: RunAgentInput
    revisions: list[int]


class ThreadBusy(RuntimeError):
    code = "THREAD_BUSY"


class ResumeRejected(RuntimeError):
    """resume/steer-away 校验失败（不完整/未知中断 ID/形状非法），待审批列表保持原状。"""

    code = "RESUME_REJECTED"


class DuplicateRunActive(RuntimeError):
    code = "DUPLICATE_RUN_ACTIVE"


class DuplicateRunTerminal(RuntimeError):
    code = "DUPLICATE_RUN_TERMINAL"


class MessageConflict(RuntimeError):
    code = "MESSAGE_CONFLICT"


class RetryNotAllowed(RuntimeError):
    code = "RETRY_NOT_ALLOWED"


# 持久化工具摘要的截断边界（与 1A BUILTIN_RESULT_LIMIT 对齐）
TOOL_SUMMARY_LIMIT = 6000


@dataclass(frozen=True)
class CommittedRevision:
    """一次成功落盘后的 revision 事实；事件只能在提交之后发出。"""

    thread_id: str
    revision: int
    persisted_at: str
    reason: str


class RunJournal:
    """语义边界运行日志：只消费已脱敏的标准 AG-UI 事件。

    文本增量只进内存；工具完成 / assistant 完成 / 中断 / 终态 / 取消才写 JSON。
    绝不持有密钥或密钥闭包。
    """

    def __init__(self, *, threads: "ThreadStore", runs: "RunStore", handle: ActiveRunHandle):
        self._threads = threads
        self._runs = runs
        self._handle = handle
        self._assistant_buffers: dict[str, dict] = {}
        self._tool_calls: dict[str, dict] = {}
        # 当前轮次尚未归属的 tool 请求调用（发起请求的 assistant 消息尚未持久化）
        self._pending_request_calls: list[str] = []
        # 直播事件里 tool 请求所属的 assistant 消息 ID（parent_message_id）；有值时
        # 请求消息用它作为持久化 ID，保证直播与刷新后的消息 ID 一致
        self._request_parent_message_id: str | None = None
        self.closed = False

    # ---- 观察（同步；增量内联，边界经 asyncio.to_thread 调用） ----

    def observe(self, event: Any) -> list[CommittedRevision]:
        if self.closed:
            return []
        kind = str(getattr(getattr(event, "type", None), "value", getattr(event, "type", "")))
        if kind == "TEXT_MESSAGE_START":
            # 文本开始 = 工具请求阶段结束：先把带 tool_calls 的请求 assistant 消息落盘
            # （1D：模型调用计数来自已持久化的 reservation，不再由事件推断）
            commits = self._flush_request_message()
            self._assistant_buffers.setdefault(event.message_id, {"content": "", "tool_calls": []})
            return commits
        if kind == "TEXT_MESSAGE_CONTENT":
            buffer = self._assistant_buffers.setdefault(event.message_id, {"content": "", "tool_calls": []})
            buffer["content"] += event.delta
            return []
        if kind == "TEXT_MESSAGE_END":
            return self._commit_assistant(event.message_id, partial=False)
        if kind == "TOOL_CALL_START":
            if not self._pending_request_calls:
                # 新一轮模型调用的开始：优先记住事件自带的父消息 ID
                self._request_parent_message_id = getattr(event, "parent_message_id", None)
            self._tool_calls.setdefault(event.tool_call_id, {"name": event.tool_call_name, "args": "", "message_id": None})
            if event.tool_call_id not in self._pending_request_calls:
                self._pending_request_calls.append(event.tool_call_id)
            return []
        if kind == "TOOL_CALL_ARGS":
            entry = self._tool_calls.setdefault(event.tool_call_id, {"name": "", "args": "", "message_id": None})
            entry["args"] += event.delta
            if event.tool_call_id not in self._pending_request_calls:
                self._pending_request_calls.append(event.tool_call_id)
            return []
        if kind == "TOOL_CALL_END":
            return []
        if kind == "TOOL_CALL_RESULT":
            return self._commit_tool(event)
        return []

    # ---- 边界提交 ----

    def persist_interrupt(self, pending: list[PendingInterrupt]) -> CommittedRevision:
        # 完整标准载荷：前端刷新后经 metadata.custom["ag-ui"].interrupts 恢复 resume/steer-away
        metadata = interrupt_payloads(pending)
        for message_id, buffer in self._assistant_buffers.items():
            if message_id not in self._persisted_assistant_ids():
                self._upsert_message(AgentMessage(
                    id=message_id,
                    role="assistant",
                    content=buffer["content"],
                    partial=False,
                    pending_interrupt=True,
                    interrupts=metadata,
                    tool_calls=self._request_call_records(),
                ))
        # 尚无文本流的挂起轮次：把未归属的请求调用单独落一条 pending 消息
        if self._pending_request_calls:
            self._flush_request_message(pending_interrupt=True, interrupts=metadata)
        self._handle.approval_started_monotonic = time.monotonic()
        self._update_run({"status": "awaiting_approval"})
        self._refresh_last_run("awaiting_approval")
        return self._commit_thread("interrupt")

    def persist_terminal(self, status: str, error_code: str | None = None, error_message: str | None = None) -> CommittedRevision:
        self._handle.control.close_active_segment()  # 终局：关闭最后一段（幂等）
        run_update: dict[str, Any] = {"status": status}
        if error_code is not None:
            run_update["error_code"] = error_code
        if error_message is not None:
            run_update["error_message"] = error_message
        run_update["ended_at"] = utc_now()
        run_update.update(self._usage_update(self._approval_total_ms()))
        self._update_run(run_update)
        # 先刷新 last_run（可能再提交一次 revision），再产生对外可见的终局 revision 事件，
        # 保证 SSE 的最后一个 revision 与最终 REST 文档一致
        self._refresh_last_run(status)
        commit = self._commit_thread("terminal")
        self.closed = True
        return commit

    def persist_partial_cancel(self) -> CommittedRevision | None:
        if self.closed:
            return None
        self.closed = True
        self._handle.control.close_active_segment()  # 取消：关闭最后一段（幂等）
        if self._pending_request_calls:
            self._flush_request_message(partial=True)
        for message_id, buffer in self._assistant_buffers.items():
            if not buffer["content"]:
                continue
            self._upsert_message(AgentMessage(
                id=message_id,
                role="assistant",
                content=buffer["content"],
                partial=True,
            ))
        self._update_run({
            "status": "cancelled",
            "ended_at": utc_now(),
            "error_code": "CLIENT_CANCELLED",
            "error_message": "用户停止或断开连接，运行被取消",
            **self._usage_update(self._approval_total_ms()),
        })
        commit = self._commit_thread("partial_cancel")
        self._refresh_last_run("cancelled")
        return commit

    # ---- 内部 ----

    def _elapsed_ms(self) -> int:
        return int((time.monotonic() - self._handle.started_monotonic) * 1000)

    def _approval_total_ms(self) -> int:
        """已结算的审批等待 + 当前未结算的等待段。"""
        run = self._current_run()
        settled = run.approval_wait_ms if run is not None else 0
        open_wait = 0
        if self._handle.approval_started_monotonic is not None:
            open_wait = int((time.monotonic() - self._handle.approval_started_monotonic) * 1000)
            self._handle.approval_started_monotonic = None
        return settled + open_wait

    def _usage_update(self, approval_wait_ms: int) -> dict[str, Any]:
        """终态/取消共用：usage/control_revision/active 一律取自 RunControl 权威视图。"""
        view = self._handle.control.view()
        return {
            "elapsed_ms": self._elapsed_ms(),
            "active_elapsed_ms": view.active_elapsed_ms,
            "approval_wait_ms": approval_wait_ms,
            "usage": view.usage,
            "control_revision": view.control_revision,
            "context_truncation": view.context_truncation,
        }

    def _persisted_assistant_ids(self) -> set[str]:
        thread = self._threads.get(self._handle.runtime.thread_id)
        return {m.id for m in thread.messages if m.role == "assistant"}

    def _request_call_records(self) -> list[dict]:
        """当前轮次尚未归属的 tool 请求调用（含已执行完成的——请求声明仍属于发起消息）。"""
        return [
            {"id": call_id, "name": self._tool_calls[call_id]["name"], "args": self._tool_calls[call_id]["args"]}
            for call_id in self._pending_request_calls
        ]

    def _flush_request_message(
        self,
        *,
        partial: bool = False,
        pending_interrupt: bool = False,
        interrupts: list[dict] | None = None,
    ) -> list[CommittedRevision]:
        """把当前轮次的 tool 请求调用合成一条 assistant 消息落盘（模型协议要求
        tool result 之前存在携带 tool_calls 的请求消息）。ID 由首个 call ID 决定，
        resume 重复观察时按 ID 幂等 upsert。
        """
        if not self._pending_request_calls:
            return []
        first_call = self._pending_request_calls[0]
        message_id = self._request_parent_message_id or f"asst-req-{first_call}"
        self._request_parent_message_id = None
        message = AgentMessage(
            id=message_id,
            role="assistant",
            content="",
            partial=partial,
            pending_interrupt=pending_interrupt,
            interrupts=interrupts or [],
            tool_calls=self._request_call_records(),
            created_at=utc_now(),
        )
        self._pending_request_calls = []
        self._upsert_message(message)
        return [self._commit_thread("tool_request")]

    def _commit_assistant(self, message_id: str, *, partial: bool) -> list[CommittedRevision]:
        buffer = self._assistant_buffers.get(message_id) or {"content": "", "tool_calls": []}
        # 最终回答不再重复声明已完成调用：请求消息已在 TEXT_MESSAGE_START 时落盘
        self._upsert_message(AgentMessage(
            id=message_id,
            role="assistant",
            content=buffer["content"],
            partial=partial,
            created_at=utc_now(),
        ))
        # 完整非 partial 的 assistant 文本：按文本顺序提取 URL 来源（不验证、不评分）
        if not partial and isinstance(buffer["content"], str) and buffer["content"]:
            run_update: dict[str, Any] = {}
            candidates = extract_model_urls(buffer["content"])
            if candidates:
                updated, truncated = append_automatic_urls(
                    self._current_run().sources, candidates, now=utc_now())
                if len(updated) != len(self._current_run().sources) or truncated:
                    view = self._handle.control.note_sources_added(
                        len(updated) - len(self._current_run().sources))
                    run_update = {
                        "sources": updated,
                        "sources_truncated": truncated,
                        "control_revision": view.control_revision,
                    }
            if run_update:
                self._update_run(run_update)
        return [self._commit_thread("assistant_complete")]

    def _origin_of(self, tool_name: str) -> str:
        """按 lease 内工具的不可变元数据判定来源（无元数据按 builtin 兜底）。"""
        lease = self._handle.capability_lease
        if lease is not None:
            for tool in lease.tools:
                if tool.name == tool_name:
                    origin = (getattr(tool, "metadata", None) or {}).get("vr_origin")
                    if origin in ("builtin", "skill", "mcp", "artifact"):
                        return origin
                    return "builtin"
        return "builtin"

    def _append_sources(self, added: list, run_update: dict[str, Any]) -> None:
        """Source 追加与 control_revision 递增并入同一 run 替换。"""
        run = self._current_run()
        sources = [*run.sources, *added]
        truncated = run.sources_truncated
        if len(sources) > SOURCE_CAPACITY:
            truncated = True
            sources = sources[:SOURCE_CAPACITY]
        view = self._handle.control.note_sources_added(len(added))
        run_update["sources"] = sources
        run_update["sources_truncated"] = truncated
        run_update["control_revision"] = view.control_revision

    def _commit_tool(self, event: Any) -> list[CommittedRevision]:
        # 模型协议：tool result 之前必须存在携带 tool_calls 的 assistant 请求消息
        commits = self._flush_request_message()
        content = str(getattr(event, "content", "") or "")[:TOOL_SUMMARY_LIMIT]
        self._upsert_message(AgentMessage(
            id=event.message_id,
            role="tool",
            content=content,
            tool_call_id=event.tool_call_id,
            created_at=utc_now(),
        ))
        call = self._tool_calls.get(event.tool_call_id) or {}
        run_update: dict[str, Any] = {"tool_summaries": self._append_tool_summary({
            "id": event.tool_call_id,
            "name": call.get("name", ""),
            "content": content,
        })}
        # 完成并持久化的工具调用产生一条 tool_execution 来源（含结构化业务错误）
        existing_ids = {s.tool_call_id for s in self._current_run().sources
                        if s.kind == "tool_execution"}
        if event.tool_call_id not in existing_ids:
            try:
                arguments = json.loads(call.get("args") or "{}")
            except json.JSONDecodeError:
                arguments = call.get("args")
            try:
                result_payload = json.loads(content)
            except json.JSONDecodeError:
                result_payload = content
            source = summarize_tool_source({
                "tool_call_id": event.tool_call_id,
                "tool_name": call.get("name") or "",
                "origin": self._origin_of(call.get("name") or ""),
                "completed_at": utc_now(),
                "args": arguments,
                "result": result_payload,
            })
            self._append_sources([source], run_update)
        self._update_run(run_update)
        commits.append(self._commit_thread("tool_complete"))
        return commits

    def _append_tool_summary(self, summary: dict) -> list[dict]:
        run = self._current_run()
        summaries = [item for item in run.tool_summaries if item.get("id") != summary["id"]]
        summaries.append(summary)
        return summaries[-20:]

    def _current_run(self) -> RunDocument:
        return self._runs.get(self._handle.product_run_id)

    def _update_run(self, update: dict[str, Any]) -> None:
        run = self._current_run()
        self._runs.replace(run.model_copy(update={**update, "updated_at": utc_now()}))

    def _upsert_message(self, message: AgentMessage) -> None:
        def mutate(doc: ThreadDocument) -> ThreadDocument:
            messages = [m for m in doc.messages if m.id != message.id]
            messages.append(message)
            return doc.model_copy(update={"messages": messages})

        expected = self._handle.thread_revision
        updated = self._threads.update(self._handle.runtime.thread_id, expected, mutate)
        self._handle.thread_revision = updated.revision

    def _commit_thread(self, reason: str) -> CommittedRevision:
        # _upsert_message 已完成消息写入；这里只产生事实对象（updated_at 触碰）
        def touch(doc: ThreadDocument) -> ThreadDocument:
            return doc.model_copy(update={"updated_at": utc_now()})

        expected = self._handle.thread_revision
        updated = self._threads.update(self._handle.runtime.thread_id, expected, touch)
        self._handle.thread_revision = updated.revision
        return CommittedRevision(
            thread_id=self._handle.runtime.thread_id,
            revision=updated.revision,
            persisted_at=updated.updated_at,
            reason=reason,
        )

    def _refresh_last_run(self, status: str) -> None:
        run = self._current_run()
        summary = RunSummary(id=run.id, status=status, updated_at=utc_now(), retry_of=run.retry_of)
        def mutate(doc: ThreadDocument) -> ThreadDocument:
            if doc.last_run == summary:
                return doc
            return doc.model_copy(update={"last_run": summary})
        expected = self._handle.thread_revision
        updated = self._threads.update(self._handle.runtime.thread_id, expected, mutate)
        self._handle.thread_revision = updated.revision


class RunCoordinator:
    """1A 内存版协调器：每线程一把锁 + 一个活动句柄。

    所有状态迁移（resume 重建、steer-away 换新、取消、终态）都在同一次
    持锁内完成校验与写入，避免并发请求通过校验后彼此覆盖。
    """

    def __init__(
        self,
        factory: AgentFactory | None = None,
        threads: "ThreadStore | None" = None,
        runs: "RunStore | None" = None,
        resolver: CapabilityResolver | None = None,
        middleware_provider: Callable[[], tuple] | None = None,
        allowances: "AllowanceRegistry | None" = None,
        executor: "BoundedToolExecutor | None" = None,
        builtin_serial_lock: asyncio.Lock | None = None,
        policy: "PolicyStore | None" = None,
        artifact_service: "Any | None" = None,
        paths: "Any | None" = None,
    ):
        self._factory = factory or AgentFactory()
        self._locks: dict[str, asyncio.Lock] = {}
        self._handles: dict[str, ActiveRunHandle] = {}
        # 1B：可选持久化引用；1A 测试仍可 RunCoordinator() 纯内存构造
        self._threads = threads
        self._runs = runs
        # 1C：能力解析器（未注入时 acquire_* 必须显式传 tools）
        self._resolver = resolver
        # 1C：请求级附加中间件提供方（router 的 build_middleware 接缝）
        self._middleware_provider = middleware_provider
        # 1C：thread_session 审批许可（内存；resume 持久化成功后写入）
        self._allowances = allowances
        # 1D：进程级有界同步执行器与 legacy builtin 串行锁（router 注入）
        self._executor = executor
        self._builtin_serial_lock = builtin_serial_lock
        # 1D：Policy store（新产品 run 准入时读取快照；损坏时 fail-closed）
        self._policy = policy
        # 1D：Artifact 服务（经治理上下文注入 create_artifact 工具）
        self._artifact_service = artifact_service
        # 1D：数据根（thread tombstone 删除需要受控路径）
        self._paths = paths

    def _lock(self, thread_id: str) -> asyncio.Lock:
        if thread_id not in self._locks:
            self._locks[thread_id] = asyncio.Lock()
        return self._locks[thread_id]

    def _make_running_handle(self, thread_id: str) -> ActiveRunHandle:
        # 测试钩子：占位句柄（graph/model 为 None 也满足 busy 语义）。
        from langgraph.checkpoint.memory import MemorySaver

        placeholder = RuntimeHandle(
            thread_id=thread_id,
            model_ref=ModelRef(provider="fixture", base_url="https://example.com/v1", model="fixture-model"),
            checkpointer=MemorySaver(),
            tools=(),
        )
        return ActiveRunHandle(runtime=placeholder, phase="running")

    # ---- 1D：治理控制与请求级中间件组合 ----

    def _new_run_control(self) -> RunControl:
        """新产品 run 的控制：读取最新 Policy 快照（损坏时 PolicyCorrupt fail-closed）。"""
        if self._policy is None:
            return RunControl(DEFAULT_POLICY_SNAPSHOT)
        return RunControl(self._policy.snapshot())

    def _governed_middleware_factory(self, lease: CapabilityLease, control: RunControl,
                                     thread_id: str, run_id: str):
        """每个请求重建（锁序固定）：模型治理（最外层）→ lease 的 guard/HITL → 工具治理（最内层）。"""
        async def persist(view: GovernanceView) -> None:
            await self.persist_control_view(thread_id, view)

        def factory(secrets):
            return (
                ContextAndModelGovernance(
                    control, persist=persist, thread_id=thread_id, run_id=run_id),
                *lease.build_request_middleware(secrets),
                ToolExecutionGovernance(
                    control, persist=persist, executor=self._executor,
                    builtin_serial_lock=self._builtin_serial_lock,
                    thread_id=thread_id, run_id=run_id,
                    artifact_service=self._artifact_service),
            )

        return factory


    # ---- 1C：两阶段能力准入 ----

    async def _acquire_lease(self, preview: CapabilityPreview, tools, middleware) -> CapabilityLease:
        if tools is not None:
            # 测试/静态路径：直接包装（不经 resolver，也不做 Skill I/O）
            return StaticCapabilityLease(tools=tools, system_context="", middleware=tuple(middleware))
        if self._resolver is None:
            raise RuntimeError("RunCoordinator 未注入 CapabilityResolver 且未显式提供 tools")
        lease = await self._resolver.acquire(preview)
        extra = self._middleware_provider() if self._middleware_provider is not None else ()
        if extra:
            lease = StaticCapabilityLease(
                tools=lease.tools, system_context=lease.system_context,
                middleware=tuple(extra), skill_digests=lease.skill_digests,
                on_release=lease.release,
            )
        return lease

    def _preview_facts_match(self, thread: ThreadDocument, preview: CapabilityPreview) -> bool:
        return (
            thread.revision == preview.thread_revision
            and tuple(thread.selected_skills) == tuple(preview.selected_skills)
        )

    def _release_handle(self, handle: ActiveRunHandle) -> None:
        """句柄卸载时释放 Graph 与能力租约（lease 释放恰好一次）。"""
        handle.runtime.release_graph()
        if handle.capability_lease is not None:
            handle.capability_lease.release()
            handle.capability_lease = None

    def mcp_server_in_use(self, server_id: str) -> bool:
        for handle in self._handles.values():
            if handle.phase not in ("running", "awaiting_approval"):
                continue
            lease = handle.capability_lease
            if lease is not None and any(
                    b.server_id == server_id for b in lease.mcp_bindings):
                return True
        return False

    def skill_in_use(self, name: str) -> bool:
        for handle in self._handles.values():
            if handle.phase in ("running", "awaiting_approval") and \
                    handle.capability_lease is not None and name in handle.capability_lease.skill_names:
                return True
        return False

    async def _preview_locked(
        self,
        thread_id: str,
        *,
        protocol_run_id: str,
        messages: Sequence[Any],
        client_revision: int | None,
    ) -> tuple[ThreadDocument, bool, CapabilityPreview]:
        """第一阶段：重复/busy/revision/头部校验 + 采集能力 preview 事实。"""
        await asyncio.to_thread(self._check_protocol_duplicate, protocol_run_id)
        existing = self._handles.get(thread_id)
        if existing is not None and existing.phase in ("running", "awaiting_approval"):
            raise ThreadBusy(f"{ThreadBusy.code}: thread {thread_id} already has an active run")
        thread, implicit = await asyncio.to_thread(
            lambda: self._preflight_locked(
                thread_id, protocol_run_id=protocol_run_id, messages=messages,
                client_revision=client_revision,
            )
        )
        preview = CapabilityPreview(
            thread_id=thread_id,
            thread_revision=thread.revision,
            selected_skills=tuple(thread.selected_skills),
        )
        return thread, implicit, preview

    def _verify_preview(self, thread: ThreadDocument, preview: CapabilityPreview) -> None:
        existing = self._handles.get(preview.thread_id)
        if existing is not None and existing.phase in ("running", "awaiting_approval"):
            raise ThreadBusy(f"{ThreadBusy.code}: thread {preview.thread_id} already has an active run")
        if not self._preview_facts_match(thread, preview):
            raise RevisionConflict(
                f"线程 {preview.thread_id} 在能力预览与最终准入之间发生了变化"
                f"（revision {preview.thread_revision}→{thread.revision} 或 Skill 选择变化）"
            )


    # ---- 1B：服务端权威准入 ----

    @staticmethod
    def _content_of(message: Any) -> Any:
        return getattr(message, "content", "")

    def _load_thread(self, thread_id: str) -> ThreadDocument | None:
        try:
            return self._threads.get(thread_id)
        except DocumentNotFound:
            return None

    def _check_protocol_duplicate(self, protocol_run_id: str) -> None:
        existing_run = self._runs.find_by_protocol_run_id(protocol_run_id)
        if existing_run is None:
            return
        if existing_run.status in ("running", "awaiting_approval"):
            raise DuplicateRunActive(
                f"{DuplicateRunActive.code}: protocol run {protocol_run_id} 已在运行"
            )
        raise DuplicateRunTerminal(
            f"{DuplicateRunTerminal.code}: protocol run {protocol_run_id} 已终结"
        )

    def _preflight_locked(
        self,
        thread_id: str,
        *,
        protocol_run_id: str,
        messages: Sequence[Any],
        client_revision: int | None,
    ) -> tuple[ThreadDocument, bool]:
        """无副作用的准入校验：不写任何文件、不动任何句柄。

        steer-away 必须先通过本校验，才允许取消旧的审批中运行。
        返回 (权威线程文档, 是否为隐式创建的线程)。
        """
        threads, runs = self._require_stores()
        # 1) protocol run 重复检查（先于 revision）
        self._check_protocol_duplicate(protocol_run_id)

        thread = self._load_thread(thread_id)
        implicit_thread = thread is None
        if thread is None:
            # 线程不存在则隐式创建（revision 0）；客户端 revision 必须与之对齐
            thread = ThreadDocument.new(thread_id, "新会话", now=utc_now())
            if client_revision not in (None, 0):
                raise RevisionConflict(
                    f"线程 {thread_id} 不存在，客户端 revision {client_revision} 无法对齐"
                )

        if not messages:
            raise MessageConflict("准入请求缺少新 user message")
        new_message = messages[-1]
        prefix = list(messages[:-1])
        if getattr(new_message, "role", None) != "user":
            raise MessageConflict("准入的新消息必须是 user 角色")

        # 2) 消息 ID 重复：同 ID 不同内容 → 冲突；同 ID 同内容 → 按所属 run 判重复
        #    （计划要求 message 重复检测先于 revision——重放已接受消息即使带着
        #     陈旧 revision 也必须返回 DUPLICATE_RUN_*，而不是 revision 冲突）
        for server_msg in thread.messages:
            if server_msg.id == new_message.id:
                if self._content_of(new_message) != server_msg.content:
                    raise MessageConflict(
                        f"{MessageConflict.code}: 消息 {server_msg.id} 已存在且内容不同"
                    )
                prior = runs.find_by_trigger_message_id(server_msg.id)
                if prior is not None and prior.status in ("running", "awaiting_approval"):
                    raise DuplicateRunActive(
                        f"{DuplicateRunActive.code}: 消息 {server_msg.id} 的 run 仍在运行"
                    )
                raise DuplicateRunTerminal(
                    f"{DuplicateRunTerminal.code}: 消息 {server_msg.id} 已被接受过"
                )

        # 3) revision 比较（兼容期 client_revision=None → 用锁内读到的服务端值）
        expected_revision = client_revision if client_revision is not None else thread.revision
        if thread.revision != expected_revision:
            raise RevisionConflict(
                f"线程 {thread_id} 期望 revision {expected_revision}，实际 {thread.revision}"
            )

        # 3) 客户端前缀必须与服务端权威历史一致（id/role/content）。
        #    允许两种长度：完整历史（partial 不计入），或额外带上服务端尾部的
        #    partial/pending 消息（客户端水合后会原样回传）。
        #    顺序不作要求：assistant-ui 水合后会把 assistant 消息排在 tool 结果之前。
        complete_history = thread.model_history()
        # assistant-ui 流式回合后可能把 tool 消息同时挂在 part 与独立消息上，
        # 导致客户端前缀出现重复条目：先去重，再决定比较基线
        seen_client_keys: set = set()
        deduped_prefix = []
        for client_msg in prefix:
            key = (
                getattr(client_msg, "role", None),
                getattr(client_msg, "id", None)
                or getattr(client_msg, "tool_call_id", None)
                or getattr(client_msg, "toolCallId", None),
            )
            if key in seen_client_keys:
                continue
            seen_client_keys.add(key)
            deduped_prefix.append(client_msg)
        if len(deduped_prefix) == len(thread.messages):
            baseline = thread.messages
        elif len(deduped_prefix) == len(complete_history):
            baseline = complete_history
        else:
            raise MessageConflict(
                f"客户端历史长度 {len(deduped_prefix)} 与服务端 {len(complete_history)} 不一致"
            )
        remaining = {msg.id: msg for msg in baseline}
        for client_msg in deduped_prefix:
            client_role = getattr(client_msg, "role", None)
            client_id = getattr(client_msg, "id", None)
            if client_role == "tool":
                # assistant-ui 会把 tool 消息的 ID 换成 tool_call ID：按 call 引用匹配
                call_ref = (
                    getattr(client_msg, "tool_call_id", None)
                    or getattr(client_msg, "toolCallId", None)
                    or client_id
                )
                server_msg = next(
                    (m for m in remaining.values()
                     if m.role == "tool" and (m.tool_call_id == call_ref or m.id == client_id)),
                    None,
                )
                if server_msg is not None and not self._tool_content_matches(client_msg, server_msg):
                    raise MessageConflict(
                        f"客户端历史在消息 {server_msg.id} 处与服务端不一致"
                    )
            else:
                server_msg = remaining.get(client_id)
                if server_msg is None or server_msg.role != client_role:
                    raise MessageConflict("客户端历史消息 ID 集合与服务端不一致")
                if self._content_of(client_msg) != server_msg.content:
                    raise MessageConflict(
                        f"客户端历史在消息 {server_msg.id} 处与服务端不一致"
                    )
            if server_msg is None:
                raise MessageConflict("客户端历史消息 ID 集合与服务端不一致")
            del remaining[server_msg.id]
        if remaining:
            raise MessageConflict("客户端历史消息 ID 集合与服务端不一致")

        return thread, implicit_thread

    def _admit_locked(
        self,
        thread_id: str,
        *,
        model_ref: ModelRef,
        secrets: RunSecrets,
        model_builder: Callable,
        lease: CapabilityLease,
        protocol_run_id: str,
        messages: Sequence[Any],
        client_revision: int | None,
        control: RunControl | None = None,
    ) -> StartAdmission:
        """持锁准入（第三阶段）：复验 → 持久化 → 建 Graph → 装句柄。

        调用方必须已持有该线程的锁并完成 preview 事实比对。密钥只在
        model_builder 调用时被消费；lease 绝不含密钥。
        """
        threads, runs = self._require_stores()

        # 1-3) 无副作用准入校验（重复/revision/前缀/消息重复）
        thread, implicit_thread = self._preflight_locked(
            thread_id, protocol_run_id=protocol_run_id, messages=messages,
            client_revision=client_revision,
        )
        new_message = messages[-1]


        # 4) 创建产品 run 并写入 running 状态（Policy 快照在任何 user/run 写入前固定）
        run_id = f"run-{uuid4().hex}"
        if control is None:
            control = self._new_run_control()  # PolicyCorrupt → 新 run fail-closed
        run = RunDocument.start(
            run_id=run_id,
            thread_id=thread_id,
            protocol_run_id=protocol_run_id,
            model_ref=model_ref,
            trigger_message_id=new_message.id,
            history_head_id=thread.messages[-1].id if thread.messages else None,
            now=utc_now(),
        ).model_copy(update={"budget_snapshot": control.snapshot})
        runs.replace(run)

        # 5) 追加被接受的用户消息 + 更新 last_run 摘要（同一次 revision 递增）
        accepted = AgentMessage(
            id=new_message.id,
            role="user",
            content=self._content_of(new_message),
            created_at=utc_now(),
        )

        def append_user(doc: ThreadDocument) -> ThreadDocument:
            return doc.model_copy(update={
                "messages": [*doc.messages, accepted],
                "last_run": RunSummary(
                    id=run_id, status="running", updated_at=utc_now(), retry_of=None
                ),
            })

        if implicit_thread:
            threads.create(thread)  # 隐式创建的新线程先落盘，再走 CAS 追加
        updated_thread = threads.update(thread_id, thread.revision, append_user)

        # 6) 从服务端完整历史重建 Graph 输入
        try:
            runtime = self._factory.create(
                model_ref=model_ref,
                secrets=secrets,
                model_builder=model_builder,
                tools=lease.tools,
                thread_id=thread_id,
                system_context=lease.system_context,
                middleware_factory=self._governed_middleware_factory(
                    lease, control, thread_id, run_id),
                policy_explanation=render_policy_explanation(control.snapshot),
            )
        except Exception:
            # 持久化已成功：run 终态 failed、线程摘要修复；用户消息保留
            control.mark_terminal("GRAPH_BUILD_FAILED", "Graph 构建失败")
            runs.replace(run.model_copy(update={
                "status": "failed",
                "updated_at": utc_now(),
                "ended_at": utc_now(),
                "error_code": "GRAPH_BUILD_FAILED",
                "error_message": "Graph 构建失败",
                "budget_snapshot": control.snapshot,
            }))
            threads.update(
                thread_id,
                updated_thread.revision,
                lambda doc: doc.model_copy(update={"last_run": RunSummary(
                    id=run_id, status="failed", updated_at=utc_now(), retry_of=None
                )}),
            )
            raise

        model_history = updated_thread.model_history()
        # 完成准入并占用 thread 后开启首个 active segment（与 elapsed 计时同点）
        control.begin_active_segment()
        handle = ActiveRunHandle(
            runtime=runtime,
            phase="running",
            thread_revision=updated_thread.revision,
            product_run_id=run_id,
            protocol_run_ids=[protocol_run_id],
            trigger_message_id=new_message.id,
            history_snapshot=tuple(model_history),
            capability_lease=lease,
            control=control,
        )
        # 7) 持久化成功后才安装句柄（并挂上语义边界日志）
        handle.journal = RunJournal(threads=threads, runs=runs, handle=handle)
        self._handles[thread_id] = handle
        return StartAdmission(
            handle=handle,
            run=runs.get(run_id),
            input=runtime.run_input(protocol_run_id=protocol_run_id, messages=model_history),
            revisions=[updated_thread.revision],
        )

    @staticmethod
    def _tool_content_matches(client_msg: Any, server_msg: AgentMessage) -> bool:
        client_content = RunCoordinator._content_of(client_msg)
        server_content = server_msg.content
        if client_content == server_content:
            return True
        # 客户端可能把结果解析成对象；服务端是（截断后的）JSON 字符串
        if isinstance(server_content, str):
            try:
                return json.loads(server_content) == client_content
            except (json.JSONDecodeError, TypeError):
                return str(client_content) == server_content
        return str(client_content) == str(server_content)

    async def acquire_start(
        self,
        *,
        model_ref: ModelRef,
        secrets: RunSecrets,
        model_builder,
        tools=None,
        thread_id: str,
        middleware=(),
        protocol_run_id: str = "protocol-1",
        messages: Sequence[Any] = (),
        client_revision: int | None = None,
    ) -> StartAdmission:
        self._require_stores()
        # 阶段一：锁内做重复/busy/revision/头部校验并采集 preview 事实
        async with self._lock(thread_id):
            _, _, preview = await self._preview_locked(
                thread_id, protocol_run_id=protocol_run_id, messages=messages,
                client_revision=client_revision,
            )
        # 阶段二：锁外取得能力租约（Skill I/O 不阻塞线程锁）
        lease = await self._acquire_lease(preview, tools, middleware)
        # 阶段三：重新持锁复验 preview 事实，随后持久化与构建
        try:
            async with self._lock(thread_id):
                thread, _ = await asyncio.to_thread(
                    lambda: self._preflight_locked(
                        thread_id, protocol_run_id=protocol_run_id, messages=messages,
                        client_revision=client_revision,
                    )
                )
                self._verify_preview(thread, preview)
                return await asyncio.to_thread(
                    lambda: self._admit_locked(
                        thread_id,
                        model_ref=model_ref,
                        secrets=secrets,
                        model_builder=model_builder,
                        lease=lease,
                        protocol_run_id=protocol_run_id,
                        messages=messages,
                        client_revision=client_revision,
                    )
                )
        except BaseException:
            lease.release()
            raise

    async def acquire_resume(
        self,
        thread_id: str,
        *,
        model_ref: ModelRef,
        secrets: RunSecrets,
        model_builder,
        validate: Callable[[list[PendingInterrupt]], dict],
        protocol_run_id: str = "protocol-resume",
        client_revision: int | None = None,
    ) -> tuple[ActiveRunHandle, dict]:
        """单锁完成：校验 resume 条目 → 重建 Graph → 转 running → 清 pending。

        任何一步失败都不改变协调器状态（fail-closed）。
        """
        async with self._lock(thread_id):
            handle = self._handles.get(thread_id)
            if handle is None or handle.phase != "awaiting_approval":
                raise ResumeRejected(f"{ResumeRejected.code}: thread {thread_id} has no awaiting run")
            threads, runs = self._require_stores()
            # 重复检查先于 revision（resume 的 protocol run 重放同样要拒绝）
            await asyncio.to_thread(self._check_protocol_duplicate, protocol_run_id)
            thread = await asyncio.to_thread(self._load_thread, thread_id)
            if thread is not None:
                expected = client_revision if client_revision is not None else thread.revision
                if thread.revision != expected:
                    raise RevisionConflict(
                        f"线程 {thread_id} 期望 revision {expected}，实际 {thread.revision}"
                    )
            # validate 返回 (resume 值, 拟登记许可)；许可只在重建+持久化成功后写入
            resume_value, proposed_allowances = validate(handle.pending_interrupts)

            def _rebuild_and_record() -> None:
                self._factory.resume(
                    handle=handle.runtime,
                    model_ref=model_ref,
                    secrets=secrets,
                    model_builder=model_builder,
                )  # RunConfigMismatch → 状态未变
                if handle.product_run_id is not None:
                    run = runs.get(handle.product_run_id)
                    approval_wait = 0
                    if handle.approval_started_monotonic is not None:
                        approval_wait = int((time.monotonic() - handle.approval_started_monotonic) * 1000)
                        handle.approval_started_monotonic = None
                    # 复用原控制并重开 active segment（不读取当前 Policy）
                    handle.control.begin_active_segment()
                    view = handle.control.view()
                    # 同一产品 run：追加 protocol ID、恢复 running、记录审批等待时长
                    runs.replace(run.model_copy(update={
                        "protocol_run_ids": [*run.protocol_run_ids, protocol_run_id],
                        "status": "running",
                        "ended_at": None,
                        "approval_wait_ms": run.approval_wait_ms + approval_wait,
                        "updated_at": utc_now(),
                        "usage": view.usage,
                        "control_revision": view.control_revision,
                        "active_elapsed_ms": view.active_elapsed_ms,
                        "context_truncation": view.context_truncation,
                    }))
                    handle.protocol_run_ids = list(run.protocol_run_ids) + [protocol_run_id]

            await asyncio.to_thread(_rebuild_and_record)
            handle.phase = "running"
            handle.pending_interrupts = []
            if self._allowances is not None:
                for thread_id_, server_id, tool_name in proposed_allowances:
                    self._allowances.grant(thread_id_, server_id, tool_name)
            return handle, resume_value

    async def acquire_steer_away(
        self,
        thread_id: str,
        *,
        entries: list[dict],
        validate: Callable[[list[PendingInterrupt], list[dict]], None],
        model_ref: ModelRef,
        secrets: RunSecrets,
        model_builder,
        tools=None,
        middleware=(),
        messages: Sequence[Any] = (),
        client_revision: int | None = None,
        protocol_run_id: str = "protocol-2",
    ) -> StartAdmission:
        """单锁完成：校验 steer-away 条目 → 无副作用 preflight → 旧 run 持久化为 cancelled → 新准入。

        preflight（重复/revision/前缀）失败时旧运行原封不动；只有新 run **写入**
        失败才保持旧 run 已取消。旧 lease 与新 lease 均恰好释放一次。
        """
        async with self._lock(thread_id):
            handle = self._handles.get(thread_id)
            if handle is None or not handle.pending_interrupts:
                raise ResumeRejected(f"{ResumeRejected.code}: thread {thread_id} has no pending interrupts")
            validate(handle.pending_interrupts, entries)  # ValueError → 旧句柄保持原状
            threads, runs = self._require_stores()

            def _preflight_only_old() -> ThreadDocument:
                # 只做无副作用 preflight；破坏旧运行推迟到全部前置成功之后
                thread, _ = self._preflight_locked(
                    thread_id, protocol_run_id=protocol_run_id, messages=messages,
                    client_revision=client_revision,
                )
                return thread

            thread = await asyncio.to_thread(_preflight_only_old)
            preview = CapabilityPreview(
                thread_id=thread_id, thread_revision=thread.revision,
                selected_skills=tuple(thread.selected_skills),
            )
        # 锁外取得 Policy 快照与能力租约：任一失败都保留旧 pending run 原状
        control = self._new_run_control()  # PolicyCorrupt → 旧 run 不受影响
        lease = await self._acquire_lease(preview, tools, middleware)
        try:
            async with self._lock(thread_id):
                thread, _ = await asyncio.to_thread(
                    lambda: self._preflight_locked(
                        thread_id, protocol_run_id=protocol_run_id, messages=messages,
                        client_revision=client_revision,
                    )
                )
                # 原子过渡（同一持锁段）：先取消旧 run，复验 preview 后再准入新 run
                def _cancel_old_then_admit():
                    old_handle = self._handles.get(thread_id)
                    if old_handle is not None:
                        old_handle.phase = "cancelled"
                        old_handle.control.mark_terminal(
                            "STEERED_AWAY", "用户转向新问题，原运行取消")
                        if old_handle.product_run_id is not None:
                            old_run = runs.get(old_handle.product_run_id)
                            runs.replace(old_run.model_copy(update={
                                "status": "cancelled",
                                "updated_at": utc_now(),
                                "ended_at": utc_now(),
                                "error_code": "STEERED_AWAY",
                                "error_message": "用户转向新问题，原运行取消",
                            }))
                        self._release_handle(old_handle)
                        self._handles.pop(thread_id, None)
                    # 取消旧 run 之后、准入之前完成 preview 复验
                    current_thread, _ = self._preflight_locked(
                        thread_id, protocol_run_id=protocol_run_id, messages=messages,
                        client_revision=client_revision,
                    )
                    self._verify_preview(current_thread, preview)
                    return self._admit_locked(
                        thread_id,
                        model_ref=model_ref,
                        secrets=secrets,
                        model_builder=model_builder,
                        lease=lease,
                        protocol_run_id=protocol_run_id,
                        messages=messages,
                        client_revision=client_revision,
                        control=control,
                    )

                return await asyncio.to_thread(_cancel_old_then_admit)
        except BaseException:
            lease.release()
            raise

    async def acquire_retry(
        self,
        *,
        model_ref: ModelRef,
        secrets: RunSecrets,
        model_builder,
        tools=None,
        thread_id: str,
        middleware=(),
        protocol_run_id: str,
        retry_of: str,
        client_revision: int,
    ) -> StartAdmission:
        """严格重试：校验目标/revision → 新产品 run + 新 MemorySaver → 服务端历史重建输入。

        不追加用户消息、不改写/不重开目标 run。与 start 相同的两阶段能力准入。
        """
        threads, runs = self._require_stores()
        async with self._lock(thread_id):
            existing = self._handles.get(thread_id)
            if existing is not None and existing.phase in ("running", "awaiting_approval"):
                raise ThreadBusy(f"{ThreadBusy.code}: thread {thread_id} already has an active run")
            await asyncio.to_thread(self._check_protocol_duplicate, protocol_run_id)
            try:
                target = await asyncio.to_thread(runs.get, retry_of)
            except DocumentNotFound:
                raise RetryNotAllowed(f"{RetryNotAllowed.code}: 目标 run 不存在")

            def reject(reason: str) -> None:
                raise RetryNotAllowed(f"{RetryNotAllowed.code}: {reason}")

            if target.thread_id != thread_id:
                reject("目标 run 属于另一个线程")
            if target.status not in ("failed", "cancelled", "interrupted"):
                reject("只能重试 failed/cancelled/interrupted 的 run")
            thread_runs = await asyncio.to_thread(runs.runs_for_thread, thread_id)
            latest = max(thread_runs, key=lambda r: (r.updated_at, r.id))
            if latest.id != target.id:
                reject("目标 run 不是线程最新的产品 run")

            thread = await asyncio.to_thread(self._load_thread, thread_id)
            if thread is None:
                reject("线程不存在")
            if thread.revision != client_revision:
                raise RevisionConflict(
                    f"线程 {thread_id} 期望 revision {client_revision}，实际 {thread.revision}"
                )
            preview = CapabilityPreview(
                thread_id=thread_id, thread_revision=thread.revision,
                selected_skills=tuple(thread.selected_skills),
            )

        lease = await self._acquire_lease(preview, tools, middleware)
        try:
            async with self._lock(thread_id):
                existing = self._handles.get(thread_id)
                if existing is not None and existing.phase in ("running", "awaiting_approval"):
                    raise ThreadBusy(f"{ThreadBusy.code}: thread {thread_id} already has an active run")
                thread = await asyncio.to_thread(self._load_thread, thread_id)
                if thread is None:
                    raise RetryNotAllowed(f"{RetryNotAllowed.code}: 线程不存在")
                if thread.revision != client_revision:
                    raise RevisionConflict(
                        f"线程 {thread_id} 期望 revision {client_revision}，实际 {thread.revision}"
                    )
                self._verify_preview(thread, preview)

                # 重试输入必须回到目标 run 的触发边界：失败 run 自己落盘的输出不进入自身重试
                history = thread.model_history()
                history_ids = [m.id for m in history]
                if target.trigger_message_id not in history_ids:
                    raise RetryNotAllowed(f"{RetryNotAllowed.code}: 目标 run 的触发消息不在完整历史中")
                trigger_index = history_ids.index(target.trigger_message_id)
                if any(m.role == "user" for m in history[trigger_index + 1:]):
                    raise RetryNotAllowed(f"{RetryNotAllowed.code}: 目标 run 之后完整历史已推进（出现了新的 user 消息）")
                retry_history = history[: trigger_index + 1]

                run_id = f"run-{uuid4().hex}"
                now = utc_now()
                control = self._new_run_control()  # retry 是新产品 run：读最新 Policy
                run = RunDocument.start(
                    run_id=run_id,
                    thread_id=thread_id,
                    protocol_run_id=protocol_run_id,
                    model_ref=model_ref,
                    trigger_message_id=target.trigger_message_id,
                    history_head_id=retry_history[-1].id if retry_history else None,
                    now=now,
                    retry_of=retry_of,
                ).model_copy(update={"budget_snapshot": control.snapshot})
                await asyncio.to_thread(runs.replace, run)

                def refresh_summary(doc: ThreadDocument) -> ThreadDocument:
                    return doc.model_copy(update={"last_run": RunSummary(
                        id=run_id, status="running", updated_at=utc_now(), retry_of=retry_of,
                    )})

                updated_thread = await asyncio.to_thread(
                    threads.update, thread_id, thread.revision, refresh_summary,
                )

                def _build_retry_runtime():
                    return self._factory.create(
                        model_ref=model_ref,
                        secrets=secrets,
                        model_builder=model_builder,
                        tools=lease.tools,
                        thread_id=thread_id,
                        system_context=lease.system_context,
                        middleware_factory=self._governed_middleware_factory(
                            lease, control, thread_id, run_id),
                        policy_explanation=render_policy_explanation(control.snapshot),
                    )

                try:
                    runtime = await asyncio.to_thread(_build_retry_runtime)
                except Exception:
                    def _mark_retry_build_failed() -> None:
                        control.mark_terminal("GRAPH_BUILD_FAILED", "Graph 构建失败")
                        runs.replace(run.model_copy(update={
                            "status": "failed",
                            "updated_at": utc_now(),
                            "ended_at": utc_now(),
                            "error_code": "GRAPH_BUILD_FAILED",
                            "error_message": "Graph 构建失败",
                            "budget_snapshot": control.snapshot,
                        }))
                        threads.update(
                            thread_id,
                            updated_thread.revision,
                            lambda doc: doc.model_copy(update={"last_run": RunSummary(
                                id=run_id, status="failed", updated_at=utc_now(), retry_of=retry_of,
                            )}),
                        )
                    await asyncio.to_thread(_mark_retry_build_failed)
                    raise

                control.begin_active_segment()  # 完成准入后开启首个 segment
                handle = ActiveRunHandle(
                    runtime=runtime,
                    phase="running",
                    thread_revision=updated_thread.revision,
                    product_run_id=run_id,
                    protocol_run_ids=[protocol_run_id],
                    trigger_message_id=target.trigger_message_id,
                    history_snapshot=tuple(retry_history),
                    capability_lease=lease,
                    control=control,
                )
                handle.journal = RunJournal(threads=threads, runs=runs, handle=handle)
                self._handles[thread_id] = handle
                return StartAdmission(
                    handle=handle,
                    run=runs.get(run_id),
                    input=runtime.run_input(protocol_run_id=protocol_run_id, messages=retry_history),
                    revisions=[updated_thread.revision],
                )
        except BaseException:
            lease.release()
            raise

    async def mark_awaiting_approval(self, thread_id: str) -> GovernanceView | None:
        """中断持久化后：关闭 active segment 并把最终控制视图落进 run 文档。"""
        async with self._lock(thread_id):
            handle = self._handles.get(thread_id)
            if handle is None:
                return None
            handle.phase = "awaiting_approval"
            handle.runtime.release_graph()
            view = handle.control.close_active_segment()
            product_run_id = handle.product_run_id

            def _persist_segment() -> None:
                run = self._runs.get(product_run_id)
                if view.control_revision >= run.control_revision:
                    self._runs.replace(run.model_copy(update={
                        "usage": view.usage,
                        "control_revision": view.control_revision,
                        "active_elapsed_ms": view.active_elapsed_ms,
                        "context_truncation": view.context_truncation,
                        "updated_at": utc_now(),
                    }))

            if product_run_id is not None:
                await asyncio.to_thread(_persist_segment)
            return view

    async def cancel(self, thread_id: str) -> None:
        async with self._lock(thread_id):
            self.cancel_sync(thread_id)

    def cancel_sync(self, thread_id: str) -> None:
        """同步取消路径 —— 供流式生成器的 CancelledError 分支使用（不能 await）。"""
        handle = self._handles.get(thread_id)
        if handle is None:
            return
        # 先阻断新的 reservation，再停止等待（spec §8）
        handle.control.mark_terminal("CLIENT_CANCELLED", "用户停止或断开连接，运行被取消")
        handle.phase = "cancelled"
        self._release_handle(handle)
        self._handles.pop(thread_id, None)

    async def finish_if_terminal(self, thread_id: str, phase: Phase) -> None:
        async with self._lock(thread_id):
            handle = self._handles.get(thread_id)
            if handle is None:
                return
            handle.phase = phase
            if phase in ("completed", "failed", "cancelled"):
                if handle.journal is not None and not handle.journal.closed:
                    await asyncio.to_thread(handle.journal.persist_terminal, phase)
                self._release_handle(handle)
                self._handles.pop(thread_id, None)
                if handle.journal is None:
                    await asyncio.to_thread(self._persist_run_terminal, handle, phase)

    def _persist_run_terminal(self, handle: ActiveRunHandle, phase: str) -> None:
        """终态落盘：run 状态 + 线程 last_run 摘要（同一把线程锁内被调用）。"""
        if handle.product_run_id is None or self._runs is None or self._threads is None:
            return
        try:
            run = self._runs.get(handle.product_run_id)
        except DocumentNotFound:
            return
        now = utc_now()
        self._runs.replace(run.model_copy(update={
            "status": phase,
            "updated_at": now,
            "ended_at": now,
        }))
        summary = RunSummary(id=run.id, status=phase, updated_at=now, retry_of=run.retry_of)
        try:
            thread = self._threads.get(handle.runtime.thread_id)
        except DocumentNotFound:
            return
        if thread.last_run == summary:
            return
        expected = handle.thread_revision if handle.thread_revision is not None else thread.revision
        updated = self._threads.update(
            handle.runtime.thread_id,
            expected,
            lambda doc: doc.model_copy(update={"last_run": summary}),
        )
        handle.thread_revision = updated.revision

    def clear_allowances(self, thread_id: str) -> int:
        """清空线程的 thread_session 许可；不改 thread revision。"""
        if self._allowances is None:
            return 0
        return self._allowances.clear_thread(thread_id)

    def resume_available(self, thread_id: str) -> bool:
        """计算字段：存在 awaiting_approval 的活动句柄才可恢复审批。"""
        handle = self._handles.get(thread_id)
        return handle is not None and handle.phase == "awaiting_approval"

    def active(self, thread_id: str) -> ActiveRunHandle | None:
        return self._handles.get(thread_id)

    # ---- 1D：治理视图持久化（reservation/usage/段计时回调的落盘点） ----

    async def persist_control_view(self, thread_id: str, view: GovernanceView) -> None:
        """线程锁内用治理视图替换完整 run 字段。

        预留回调在 reservation_lock 内 await 本方法（锁序：reservation → 线程锁）。
        拒绝脱离/终态句柄；迟到的低 revision 不得覆盖高 revision。
        """
        threads, runs = self._require_stores()
        async with self._lock(thread_id):
            handle = self._handles.get(thread_id)
            if handle is None or handle.phase not in ("running", "awaiting_approval"):
                raise RuntimeError(
                    f"线程 {thread_id} 没有可持久化治理视图的活动 run（句柄已脱离或已终结）")
            product_run_id = handle.product_run_id

            def _replace() -> None:
                run = runs.get(product_run_id)
                if view.control_revision < run.control_revision:
                    return
                runs.replace(run.model_copy(update={
                    "usage": view.usage,
                    "control_revision": view.control_revision,
                    "active_elapsed_ms": view.active_elapsed_ms,
                    "context_truncation": view.context_truncation,
                    "updated_at": utc_now(),
                }))

            await asyncio.to_thread(_replace)

    # ---- 1B：语义边界日志（与准入/改名的锁共用） ----

    def thread_lock(self, thread_id: str) -> asyncio.Lock:
        return self._lock(thread_id)

    def note_artifact_thread_reference(
        self, thread_id: str, run_id: str, revision: int,
    ) -> None:
        """Artifact 引用提交后立即同步当前 handle 的 CAS revision。

        调用方持有本 coordinator 的 thread lock；不能等 router 编码事件后再更新，
        否则取消/断连可在两者之间以旧 revision 提交终态。
        """
        handle = self._handles.get(thread_id)
        if handle is not None and handle.product_run_id == run_id:
            handle.thread_revision = revision

    async def _journal_write(self, thread_id: str, fn: Callable[["RunJournal"], Any]) -> list:
        # 锁序不变量：artifact_mutation_lock 先于 coordinator thread lock；
        # Source 提交在该短协调段内完成，绝不取 reservation_lock
        handle = self._handles.get(thread_id)
        if handle is None or handle.journal is None or handle.journal.closed:
            return []  # 迟到事件：句柄已取消/关闭，直接丢弃
        mutation_lock = handle.control.artifact_mutation_lock
        async with mutation_lock:
            async with self._lock(thread_id):
                handle = self._handles.get(thread_id)
                if handle is None or handle.journal is None or handle.journal.closed:
                    return []
                return await asyncio.to_thread(fn, handle.journal)

    async def journal_observe(self, thread_id: str, event: Any) -> list[CommittedRevision]:
        return await self._journal_write(thread_id, lambda journal: journal.observe(event))

    async def journal_interrupt(self, thread_id: str, pending: list[PendingInterrupt]) -> list[CommittedRevision]:
        return await self._journal_write(thread_id, lambda journal: [journal.persist_interrupt(pending)])

    async def journal_terminal(
        self, thread_id: str, status: str, error_code: str | None = None, error_message: str | None = None,
    ) -> list[CommittedRevision]:
        return await self._journal_write(
            thread_id, lambda journal: [journal.persist_terminal(status, error_code, error_message)]
        )

    async def cancel_run(self, thread_id: str, product_run_id: str | None = None) -> str | None:
        """幂等的持久化取消：断连 / REST 取消 / CancelledError 共用同一条迁移。

        传入 product_run_id 时只取消匹配的活动 run——取消一个已终结的旧 run
        绝不能误杀该线程当前在跑的新 run（此时原样返回，不产生任何写入）。
        """
        async with self._lock(thread_id):
            handle = self._handles.get(thread_id)
            if handle is None:
                return None
            if product_run_id is not None and handle.product_run_id != product_run_id:
                return None  # 目标 run 已终结，当前活动的是别的 run：不做任何事
            handle.control.mark_terminal("CLIENT_CANCELLED", "用户停止或断开连接，运行被取消")
            handle.phase = "cancelled"  # 先标记：迟到的 journal 事件被拒
            journal = handle.journal
            product_run_id = handle.product_run_id
            self._handles.pop(thread_id, None)
            self._release_handle(handle)  # 释放 Graph 与能力租约（与 cancel_sync 一致）
            if journal is not None and not journal.closed:
                await asyncio.to_thread(journal.persist_partial_cancel)
            return product_run_id

    # ---- 1B：线程 CRUD 走与 run 准入相同的每线程锁 ----

    def _require_stores(self) -> tuple["ThreadStore", "RunStore"]:
        if self._threads is None or self._runs is None:
            raise RuntimeError("RunCoordinator 未注入 ThreadStore/RunStore（1A 纯内存构造）")
        return self._threads, self._runs

    async def patch_thread(self, thread_id: str, revision: int, title: str | None,
                           selected_skills: list[str] | None = None):
        threads, _ = self._require_stores()
        async with self._lock(thread_id):
            changes: dict = {}
            if title is not None:
                changes["title"] = title
            if selected_skills is not None:
                changes["selected_skills"] = list(selected_skills)
            updated = await asyncio.to_thread(
                threads.update, thread_id, revision,
                lambda doc: doc.model_copy(update=changes),
            )
            handle = self._handles.get(thread_id)
            if handle is not None:
                # 活动 run 后续写入从 PATCH 后的 revision 继续，避免下次写冲突
                handle.thread_revision = updated.revision
            return updated

    async def delete_artifact(self, thread_id: str, artifact_id: str,
                              *, expected_revision: int) -> int:
        """叶子 Artifact 删除：先提交引用移除，再删文件；失败保留权威状态。"""
        from agent.artifacts import ArtifactHasChild, ArtifactNotFound
        threads, _ = self._require_stores()
        if self._artifact_service is None:
            raise RuntimeError("RunCoordinator 未注入 Artifact 服务")
        async with self._lock(thread_id):
            handle = self._handles.get(thread_id)
            if handle is not None and handle.phase in ("running", "awaiting_approval"):
                raise ThreadBusy(f"{ThreadBusy.code}: thread {thread_id} has an active run")

            def _delete_locked() -> int:
                thread = threads.get(thread_id)
                if artifact_id not in thread.artifact_ids:
                    raise ArtifactNotFound(f"Artifact 不存在或未引用: {artifact_id}")
                children = self._artifact_service.store.children_map(thread_id)
                if children.get(artifact_id):
                    raise ArtifactHasChild("Artifact 存在子版本，只有叶子可以删除")
                updated = threads.update(
                    thread_id, expected_revision,
                    lambda doc: doc.model_copy(update={
                        "artifact_ids": [a for a in doc.artifact_ids if a != artifact_id]}))
                # 引用已提交；文件删除失败时保留状态并返回 500（由 router 映射）
                self._artifact_service.store.delete_file(thread_id, artifact_id)
                return updated.revision

            return await asyncio.to_thread(_delete_locked)

    async def delete_thread(self, thread_id: str, expected_revision: int | None = None) -> None:
        """删除线程：tombstone 事务（rename → 删 thread 提交 → 清理）。"""
        threads, runs = self._require_stores()
        async with self._lock(thread_id):
            thread = await asyncio.to_thread(self._load_thread, thread_id)
            if thread is not None and expected_revision is not None and thread.revision != expected_revision:
                raise RevisionConflict(
                    f"线程 {thread_id} 期望 revision {expected_revision}，实际 {thread.revision}"
                )
            handle = self._handles.get(thread_id)
            if handle is not None and handle.phase in ("running", "awaiting_approval"):
                raise ThreadBusy(f"{ThreadBusy.code}: thread {thread_id} has an active run")
            if handle is not None:
                self._release_handle(handle)
            if self._allowances is not None:
                self._allowances.clear_thread(thread_id)

            def _tombstone_delete(paths) -> None:
                from agent.stores import ARTIFACT_TOMBSTONE_SUFFIX, utc_stamp
                stamp = utc_stamp()
                renames: list[tuple[Path, Path]] = []
                try:
                    artifact_dir = paths.artifacts_dir / thread_id
                    if artifact_dir.exists():
                        tomb = artifact_dir.with_name(
                            f"{thread_id}{ARTIFACT_TOMBSTONE_SUFFIX}{stamp}")
                        os.replace(artifact_dir, tomb)
                        renames.append((tomb, artifact_dir))
                    for run in runs.runs_for_thread(thread_id):
                        original = paths.runs / f"{run.id}.json"
                        if original.exists():
                            tomb = original.with_name(f"{run.id}.json{ARTIFACT_TOMBSTONE_SUFFIX}{stamp}")
                            os.replace(original, tomb)
                            renames.append((tomb, original))
                    # 提交点：删除 thread 文件
                    threads.delete(thread_id)
                except BaseException:
                    # 提交点前失败：回滚已完成的 rename，保留 thread
                    for tomb, original in reversed(renames):
                        try:
                            if tomb.exists() and not original.exists():
                                os.replace(tomb, original)
                        except OSError:
                            continue
                    raise
                # 提交点后：清理失败不回滚、不重新暴露已删除 thread（交给对账）
                for tomb, _ in renames:
                    try:
                        if tomb.is_dir():
                            import shutil
                            shutil.rmtree(tomb)
                        else:
                            tomb.unlink(missing_ok=True)
                    except OSError:
                        # 提交点后只保留 tombstone；启动对账会继续清理并上报恢复告警。
                        continue

            if self._paths is not None:
                await asyncio.to_thread(_tombstone_delete, self._paths)
            else:
                thread_runs = await asyncio.to_thread(runs.runs_for_thread, thread_id)
                for run in thread_runs:
                    await asyncio.to_thread(runs.delete, run.id)
                await asyncio.to_thread(threads.delete, thread_id)

    async def shutdown(self) -> None:
        """进程退出：每个活动 run 走统一的持久化取消迁移（partial 落盘 + run 终态）。"""
        for thread_id in list(self._handles):
            await self.cancel_run(thread_id)
        if self._allowances is not None:
            self._allowances.clear_all()
