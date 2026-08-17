"""1D 有界同步工具执行器：4 个 worker、租约式准入、超时不释放运行中容量。

契约（spec §11）：
- 从不排队：无租约不提交；容量等待有上限（默认 1 秒），失败即 TOOL_CAPACITY_EXHAUSTED。
- 截止/取消只尝试 future.cancel()；运行中的 future 保留其容量直到真正退出（迟到结果只丢弃）。
- begin_shutdown() 原子拒绝新准入；shutdown() 不等待被阻塞的第三方代码，立即返回。
"""

from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:  # pragma: no cover - 仅类型引用
    from agent.governance import RunControl

SYNC_WORKER_COUNT = 4
CAPACITY_WAIT_SECONDS = 1.0
_ACQUIRE_POLL_SECONDS = 0.02


class ToolExecutorError(RuntimeError):
    code = "TOOL_EXECUTOR_ERROR"


class ToolCapacityExhausted(ToolExecutorError):
    code = "TOOL_CAPACITY_EXHAUSTED"


class ToolDeadlineExceeded(ToolExecutorError):
    """内部截止信号：治理包装器负责映射为 TOOL_TIMEOUT / RUN_ACTIVE_TIMEOUT。"""

    code = "TOOL_DEADLINE_EXCEEDED"


class ToolExecutorClosed(ToolExecutorError):
    code = "TOOL_EXECUTOR_CLOSED"


@dataclass
class CapacityLease:
    """容量租约：只含执行器所有权与提交/释放状态，绝不含密钥。"""

    owner: "BoundedToolExecutor"
    submitted: bool = False
    released: bool = False

    def release_unsubmitted(self) -> None:
        """提交前放弃租约（幂等）：归还信号量。"""
        self.owner._release_unsubmitted(self)


@dataclass(frozen=True)
class ToolExecutionContext:
    """治理包装器在调用真实 handler 前安装的请求级上下文（无密钥）。

    1D Task 3 只需要锁/容量/截止字段；Task 6 会补充 control 与 Artifact 服务引用。
    """

    thread_id: str
    product_run_id: str
    execution_lock: asyncio.Lock
    builtin_serial_lock: asyncio.Lock
    executor: BoundedToolExecutor
    tool_deadline: float
    capacity_lease: CapacityLease | None = None
    control: "RunControl | None" = None
    artifact_service: Any = None
    tool_timeout_seconds: float | None = None
    max_active_seconds: float | None = None


_tool_execution_context: ContextVar[ToolExecutionContext | None] = ContextVar(
    "vr_tool_execution_context", default=None)


def install_tool_execution_context(context: ToolExecutionContext) -> object:
    return _tool_execution_context.set(context)


def reset_tool_execution_context(token: object) -> None:
    _tool_execution_context.reset(token)


def current_tool_execution_context() -> ToolExecutionContext | None:
    return _tool_execution_context.get()


class BoundedToolExecutor:
    """ThreadPoolExecutor(4) + BoundedSemaphore(4)：容量即准入，队列长度恒为零。"""

    def __init__(self, max_workers: int = SYNC_WORKER_COUNT,
                 clock: Callable[[], float] = time.monotonic):
        self.max_workers = max_workers
        self._clock = clock
        self._pool = ThreadPoolExecutor(max_workers=max_workers,
                                        thread_name_prefix="vr-agent-tool")
        self._semaphore = threading.BoundedSemaphore(max_workers)
        self._state_lock = threading.Lock()
        self._shutdown = False

    # ---- 准入 ----

    async def acquire(self, *, capacity_wait_seconds: float = CAPACITY_WAIT_SECONDS,
                      deadline: float) -> CapacityLease:
        """异步可中断地等待容量；先到容量等待上限报容量耗尽，先到截止报截止。"""
        capacity_bound = self._clock() + capacity_wait_seconds
        bound = min(capacity_bound, deadline)
        while True:
            self._reject_if_closed()
            if self._semaphore.acquire(blocking=False):
                return CapacityLease(owner=self)
            now = self._clock()
            if now >= bound:
                if deadline <= capacity_bound:
                    raise ToolDeadlineExceeded("工具截止时间在容量等待前已耗尽")
                raise ToolCapacityExhausted(
                    f"同步工具容量已满（{self.max_workers} 个 worker），等待 "
                    f"{capacity_wait_seconds}s 未能准入")
            await asyncio.sleep(min(_ACQUIRE_POLL_SECONDS, bound - now))

    # ---- 提交与等待 ----

    async def run_with_lease(self, lease: CapacityLease, fn: Callable[[], Any],
                             deadline: float) -> Any:
        """提交已持有租约的同步工作；所有权随提交转移给 future 的完成回调。"""
        if lease.owner is not self or lease.released:
            raise ToolExecutorError("租约无效：已释放或不属于该执行器")
        self._reject_if_closed()
        future = self._pool.submit(fn)
        with self._state_lock:
            lease.submitted = True
        # 提交即转移所有权：运行中的 future 即使超时/取消也保留令牌直到退出
        future.add_done_callback(lambda _f: self._release_submitted(lease))
        wrapped = asyncio.wrap_future(future)
        remaining = deadline - self._clock()
        try:
            if remaining <= 0:
                wrapped.cancel()
                raise ToolDeadlineExceeded("工具截止时间已到")
            return await asyncio.wait_for(wrapped, timeout=remaining)
        except asyncio.TimeoutError as exc:
            # wait_for 已尝试取消 wrap_future → 并发 future.cancel()；
            # 已开跑的工作不会被中断，其令牌由完成回调归还，迟到结果被丢弃
            raise ToolDeadlineExceeded("工具执行超过截止时间") from exc

    # ---- 停机 ----

    def begin_shutdown(self) -> None:
        with self._state_lock:
            self._shutdown = True

    def shutdown(self) -> None:
        """立即返回；不等待仍在运行的第三方代码（cancel_futures 清空未启动项）。"""
        self.begin_shutdown()
        self._pool.shutdown(wait=False, cancel_futures=True)

    # ---- 内部 ----

    def _reject_if_closed(self) -> None:
        with self._state_lock:
            if self._shutdown:
                raise ToolExecutorClosed("同步工具执行器已进入停机，拒绝新准入")

    def _release_unsubmitted(self, lease: CapacityLease) -> None:
        with self._state_lock:
            if lease.submitted:
                raise ToolExecutorError("租约已提交，只能由 future 完成回调释放")
            if lease.released:
                return
            lease.released = True
        self._semaphore.release()

    def _release_submitted(self, lease: CapacityLease) -> None:
        with self._state_lock:
            if lease.released:
                return
            lease.released = True
        self._semaphore.release()
