from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Literal

from agent.models import ModelRef, RunSecrets
from agent.protocol import PendingInterrupt
from agent.runtime import AgentFactory, RuntimeHandle

Phase = Literal["running", "awaiting_approval", "completed", "failed", "cancelled"]


@dataclass
class ActiveRunHandle:
    """线程的活动 run 状态（1A 仅内存，无磁盘 IO / 重试状态）。"""

    runtime: RuntimeHandle
    phase: Phase
    pending_interrupts: list[PendingInterrupt] = field(default_factory=list)


class ThreadBusy(RuntimeError):
    code = "THREAD_BUSY"


class ResumeRejected(RuntimeError):
    """resume 校验失败（不完整/未知中断 ID 等），待审批列表保持原状。"""

    code = "RESUME_REJECTED"


class RunCoordinator:
    """1A 内存版协调器：每线程一把锁 + 一个活动句柄。"""

    def __init__(self, factory: AgentFactory | None = None):
        self._factory = factory or AgentFactory()
        self._locks: dict[str, asyncio.Lock] = {}
        self._handles: dict[str, ActiveRunHandle] = {}

    def _lock(self, thread_id: str) -> asyncio.Lock:
        if thread_id not in self._locks:
            self._locks[thread_id] = asyncio.Lock()
        return self._locks[thread_id]

    def _make_running_handle(self, thread_id: str) -> ActiveRunHandle:
        # 测试钩子：占位句柄（graph/model 为 None 也满足 busy 语义）。
        from agent.runtime import RuntimeHandle as RH
        from langgraph.checkpoint.memory import MemorySaver
        placeholder = RH(
            thread_id=thread_id,
            model_ref=ModelRef(provider="fixture", base_url="https://example.com/v1", model="fixture-model"),
            checkpointer=MemorySaver(),
            tools=(),
            middleware=(),
        )
        return ActiveRunHandle(runtime=placeholder, phase="running")

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

    async def acquire_resume(self, thread_id: str) -> ActiveRunHandle:
        async with self._lock(thread_id):
            handle = self._handles.get(thread_id)
            if handle is None or handle.phase != "awaiting_approval":
                raise ResumeRejected(f"{ResumeRejected.code}: thread {thread_id} has no awaiting run")
            return handle

    async def rebuild_graph(self, handle: ActiveRunHandle, *, model_ref, secrets, model_builder) -> None:
        async with self._lock(handle.runtime.thread_id):
            self._factory.resume(handle=handle.runtime, model_ref=model_ref, secrets=secrets, model_builder=model_builder)
            handle.phase = "running"

    async def mark_awaiting_approval(self, thread_id: str) -> None:
        async with self._lock(thread_id):
            handle = self._handles.get(thread_id)
            if handle is None:
                return
            handle.phase = "awaiting_approval"
            handle.runtime.release_graph()

    async def steer_away(self, thread_id: str) -> None:
        """校验并关闭旧句柄（置 cancelled、释放图、移除），由调用方另起新 run。"""
        async with self._lock(thread_id):
            handle = self._handles.get(thread_id)
            if handle is None:
                return
            handle.phase = "cancelled"
            handle.runtime.release_graph()
            self._handles.pop(thread_id, None)

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
