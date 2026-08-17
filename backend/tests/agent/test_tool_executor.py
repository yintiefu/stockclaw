"""1D 有界同步工具执行器：容量租约、截止/取消语义、迟到结果处置与有界停机。"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from agent.tool_executor import (
    SYNC_WORKER_COUNT,
    BoundedToolExecutor,
    CapacityLease,
    ToolCapacityExhausted,
    ToolDeadlineExceeded,
    ToolExecutorClosed,
    ToolExecutorError,
)

pytestmark = pytest.mark.asyncio


def far_deadline() -> float:
    return time.monotonic() + 10.0


async def wait_events(events: list[threading.Event]) -> None:
    await asyncio.to_thread(lambda: all(event.wait(5) for event in events))


async def blocking_call(
    executor: BoundedToolExecutor,
    started: threading.Event,
    release: threading.Event,
):
    lease = await executor.acquire(capacity_wait_seconds=0.2, deadline=far_deadline())

    def work():
        started.set()
        release.wait(5)
        return {"done": True}

    return await executor.run_with_lease(lease, work, far_deadline())


def test_executor_defaults_to_four_workers():
    executor = BoundedToolExecutor()
    assert executor.max_workers == SYNC_WORKER_COUNT == 4
    executor.shutdown()


async def test_four_workers_occupy_all_capacity_and_fifth_is_rejected():
    executor = BoundedToolExecutor()
    release = threading.Event()
    started = [threading.Event() for _ in range(4)]
    tasks = [asyncio.create_task(blocking_call(executor, started[i], release)) for i in range(4)]
    await wait_events(started)
    with pytest.raises(ToolCapacityExhausted) as raised:
        await executor.acquire(capacity_wait_seconds=0.01, deadline=far_deadline())
    assert raised.value.code == "TOOL_CAPACITY_EXHAUSTED"
    release.set()
    await asyncio.gather(*tasks)


async def test_timeout_does_not_release_running_future_capacity():
    executor = BoundedToolExecutor()
    release = threading.Event()
    started = [threading.Event() for _ in range(4)]
    tasks = [asyncio.create_task(blocking_call(executor, started[i], release)) for i in range(4)]
    await wait_events(started)
    with pytest.raises(ToolCapacityExhausted):
        await executor.acquire(capacity_wait_seconds=0.01, deadline=far_deadline())
    tasks[0].cancel()
    with pytest.raises(ToolCapacityExhausted):
        await executor.acquire(capacity_wait_seconds=0.01, deadline=far_deadline())
    release.set()
    await asyncio.gather(*tasks, return_exceptions=True)
    lease = await executor.acquire(capacity_wait_seconds=0.1, deadline=far_deadline())
    lease.release_unsubmitted()
    executor.shutdown()


async def test_deadline_expiry_disposes_late_result_and_holds_capacity():
    executor = BoundedToolExecutor()
    release = threading.Event()
    started = threading.Event()
    lease = await executor.acquire(capacity_wait_seconds=0.1, deadline=far_deadline())

    def blocked_work():
        started.set()
        release.wait(5)
        return {"secret_late_value": True}

    task = asyncio.create_task(
        executor.run_with_lease(lease, blocked_work, time.monotonic() + 0.05))
    await asyncio.to_thread(started.wait, 5)
    with pytest.raises(ToolDeadlineExceeded):
        await task
    # 运行中的 future 仍持有容量：占满其余 3 个后第 5 次准入失败
    fill = [threading.Event() for _ in range(3)]
    release_fill = threading.Event()
    fill_tasks = [
        asyncio.create_task(blocking_call(executor, fill[i], release_fill)) for i in range(3)
    ]
    await wait_events(fill)
    with pytest.raises(ToolCapacityExhausted):
        await executor.acquire(capacity_wait_seconds=0.01, deadline=far_deadline())
    release.set()
    release_fill.set()
    await asyncio.gather(*fill_tasks)
    # worker 退出后容量恢复
    recovered = await executor.acquire(capacity_wait_seconds=0.5, deadline=far_deadline())
    recovered.release_unsubmitted()
    executor.shutdown()


async def test_release_unsubmitted_returns_token_idempotently():
    executor = BoundedToolExecutor()
    lease = await executor.acquire(capacity_wait_seconds=0.1, deadline=far_deadline())
    lease.release_unsubmitted()
    lease.release_unsubmitted()  # 幂等
    again = await executor.acquire(capacity_wait_seconds=0.1, deadline=far_deadline())
    assert isinstance(again, CapacityLease)
    again.release_unsubmitted()
    executor.shutdown()


async def test_run_with_lease_rejects_foreign_or_released_lease():
    executor_a = BoundedToolExecutor()
    executor_b = BoundedToolExecutor()
    lease = await executor_a.acquire(capacity_wait_seconds=0.1, deadline=far_deadline())
    with pytest.raises(ToolExecutorError):
        await executor_b.run_with_lease(lease, lambda: 1, far_deadline())
    lease.release_unsubmitted()
    with pytest.raises(ToolExecutorError):
        await executor_a.run_with_lease(lease, lambda: 1, far_deadline())
    executor_a.shutdown()
    executor_b.shutdown()


async def test_begin_shutdown_rejects_new_admissions_immediately():
    executor = BoundedToolExecutor()
    release = threading.Event()
    started = threading.Event()
    task = asyncio.create_task(blocking_call(executor, started, release))
    await asyncio.to_thread(started.wait, 5)
    executor.begin_shutdown()
    with pytest.raises(ToolExecutorClosed):
        await executor.acquire(capacity_wait_seconds=0.01, deadline=far_deadline())
    release.set()
    await task
    executor.shutdown()


async def test_shutdown_returns_within_bound_before_blocked_workers_release():
    executor = BoundedToolExecutor()
    release = threading.Event()
    started = threading.Event()
    task = asyncio.create_task(blocking_call(executor, started, release))
    await asyncio.to_thread(started.wait, 5)
    began = time.monotonic()
    executor.shutdown()
    assert time.monotonic() - began < 0.5  # 不等待被阻塞的第三方代码
    with pytest.raises(ToolExecutorClosed):
        await executor.acquire(capacity_wait_seconds=0.01, deadline=far_deadline())
    release.set()
    await task


async def test_capacity_wait_advances_until_deadline_boundary():
    executor = BoundedToolExecutor()
    release = threading.Event()
    started = [threading.Event() for _ in range(4)]
    tasks = [asyncio.create_task(blocking_call(executor, started[i], release)) for i in range(4)]
    await wait_events(started)
    # 截止时间已过：直接按截止失败，而不是等满 capacity_wait
    with pytest.raises(ToolDeadlineExceeded):
        await executor.acquire(capacity_wait_seconds=5.0, deadline=time.monotonic() - 1)
    release.set()
    await asyncio.gather(*tasks)
    executor.shutdown()
