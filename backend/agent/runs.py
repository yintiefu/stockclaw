from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Literal

from agent.models import ModelRef, RunSecrets
from agent.protocol import PendingInterrupt
from agent.runtime import AgentFactory, RuntimeHandle

if TYPE_CHECKING:
    from agent.stores import RunStore, ThreadStore

Phase = Literal["running", "awaiting_approval", "completed", "failed", "cancelled"]


@dataclass
class ActiveRunHandle:
    """线程的活动 run 状态（1A 仅内存，无磁盘 IO / 重试状态）。"""

    runtime: RuntimeHandle
    phase: Phase
    pending_interrupts: list[PendingInterrupt] = field(default_factory=list)
    thread_revision: int | None = None


class ThreadBusy(RuntimeError):
    code = "THREAD_BUSY"


class ResumeRejected(RuntimeError):
    """resume/steer-away 校验失败（不完整/未知中断 ID/形状非法），待审批列表保持原状。"""

    code = "RESUME_REJECTED"


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
    ):
        self._factory = factory or AgentFactory()
        self._locks: dict[str, asyncio.Lock] = {}
        self._handles: dict[str, ActiveRunHandle] = {}
        # 1B：可选持久化引用；1A 测试仍可 RunCoordinator() 纯内存构造
        self._threads = threads
        self._runs = runs

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
            middleware=(),
        )
        return ActiveRunHandle(runtime=placeholder, phase="running")

    def _start_locked(
        self,
        thread_id: str,
        *,
        model_ref: ModelRef,
        secrets: RunSecrets,
        model_builder: Callable,
        tools,
        middleware,
    ) -> ActiveRunHandle:
        runtime = self._factory.create(
            model_ref=model_ref,
            secrets=secrets,
            model_builder=model_builder,
            tools=tools,
            thread_id=thread_id,
            middleware=middleware,
        )
        handle = ActiveRunHandle(runtime=runtime, phase="running")
        self._handles[thread_id] = handle
        return handle

    async def acquire_start(
        self,
        *,
        model_ref: ModelRef,
        secrets: RunSecrets,
        model_builder,
        tools,
        thread_id: str,
        middleware=(),
    ) -> ActiveRunHandle:
        async with self._lock(thread_id):
            existing = self._handles.get(thread_id)
            if existing is not None and existing.phase in ("running", "awaiting_approval"):
                raise ThreadBusy(f"{ThreadBusy.code}: thread {thread_id} already has an active run")
            return self._start_locked(
                thread_id,
                model_ref=model_ref,
                secrets=secrets,
                model_builder=model_builder,
                tools=tools,
                middleware=middleware,
            )

    async def acquire_resume(
        self,
        thread_id: str,
        *,
        model_ref: ModelRef,
        secrets: RunSecrets,
        model_builder,
        validate: Callable[[list[PendingInterrupt]], dict],
    ) -> tuple[ActiveRunHandle, dict]:
        """单锁完成：校验 resume 条目 → 重建 Graph → 转 running → 清 pending。

        任何一步失败都不改变协调器状态（fail-closed）。
        """
        async with self._lock(thread_id):
            handle = self._handles.get(thread_id)
            if handle is None or handle.phase != "awaiting_approval":
                raise ResumeRejected(f"{ResumeRejected.code}: thread {thread_id} has no awaiting run")
            resume_value = validate(handle.pending_interrupts)  # ValueError → 交给调用方 400
            self._factory.resume(
                handle=handle.runtime,
                model_ref=model_ref,
                secrets=secrets,
                model_builder=model_builder,
            )  # RunConfigMismatch → 状态未变
            handle.phase = "running"
            handle.pending_interrupts = []
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
        tools,
        middleware=(),
    ) -> ActiveRunHandle:
        """单锁完成：校验 steer-away 条目 → 关闭旧句柄 → 建新句柄。"""
        async with self._lock(thread_id):
            handle = self._handles.get(thread_id)
            if handle is None or not handle.pending_interrupts:
                raise ResumeRejected(f"{ResumeRejected.code}: thread {thread_id} has no pending interrupts")
            validate(handle.pending_interrupts, entries)  # ValueError → 旧句柄保持原状
            handle.phase = "cancelled"
            handle.runtime.release_graph()
            self._handles.pop(thread_id, None)
            return self._start_locked(
                thread_id,
                model_ref=model_ref,
                secrets=secrets,
                model_builder=model_builder,
                tools=tools,
                middleware=middleware,
            )

    async def mark_awaiting_approval(self, thread_id: str) -> None:
        async with self._lock(thread_id):
            handle = self._handles.get(thread_id)
            if handle is None:
                return
            handle.phase = "awaiting_approval"
            handle.runtime.release_graph()

    async def cancel(self, thread_id: str) -> None:
        async with self._lock(thread_id):
            self.cancel_sync(thread_id)

    def cancel_sync(self, thread_id: str) -> None:
        """同步取消路径 —— 供流式生成器的 CancelledError 分支使用（不能 await）。"""
        handle = self._handles.get(thread_id)
        if handle is None:
            return
        handle.phase = "cancelled"
        handle.runtime.release_graph()
        self._handles.pop(thread_id, None)

    async def finish_if_terminal(self, thread_id: str, phase: Phase) -> None:
        async with self._lock(thread_id):
            handle = self._handles.get(thread_id)
            if handle is None:
                return
            handle.phase = phase
            if phase in ("completed", "failed", "cancelled"):
                handle.runtime.release_graph()
                self._handles.pop(thread_id, None)

    def active(self, thread_id: str) -> ActiveRunHandle | None:
        return self._handles.get(thread_id)

    # ---- 1B：线程 CRUD 走与 run 准入相同的每线程锁 ----

    def _require_stores(self) -> tuple["ThreadStore", "RunStore"]:
        if self._threads is None or self._runs is None:
            raise RuntimeError("RunCoordinator 未注入 ThreadStore/RunStore（1A 纯内存构造）")
        return self._threads, self._runs

    async def patch_thread(self, thread_id: str, revision: int, title: str):
        threads, _ = self._require_stores()
        async with self._lock(thread_id):
            updated = await asyncio.to_thread(
                threads.update, thread_id, revision,
                lambda doc: doc.model_copy(update={"title": title}),
            )
            handle = self._handles.get(thread_id)
            if handle is not None:
                # 活动 run 后续写入从 PATCH 后的 revision 继续，避免下次写冲突
                handle.thread_revision = updated.revision
            return updated

    async def delete_thread(self, thread_id: str) -> None:
        threads, runs = self._require_stores()
        async with self._lock(thread_id):
            handle = self._handles.get(thread_id)
            if handle is not None and handle.phase in ("running", "awaiting_approval"):
                raise ThreadBusy(f"{ThreadBusy.code}: thread {thread_id} has an active run")
            thread_runs = await asyncio.to_thread(runs.runs_for_thread, thread_id)
            for run in thread_runs:
                await asyncio.to_thread(runs.delete, run.id)
            await asyncio.to_thread(threads.delete, thread_id)

    async def shutdown(self) -> None:
        """进程退出：取消并释放所有活动句柄（持久化在 Task 5 加入）。"""
        for thread_id in list(self._handles):
            await self.cancel(thread_id)
