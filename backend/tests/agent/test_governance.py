"""1D RunControl 契约：reservation 事务、活跃段计时、token 状态聚合与终态语义。"""

from __future__ import annotations

import asyncio

import pytest

from agent.governance import (
    GovernancePersistenceFailed,
    GovernanceTerminalError,
    GovernanceView,
    ModelCallLimitExceeded,
    RunControl,
    ToolCallLimitExceeded,
)
from agent.models import PolicySnapshot

pytestmark = pytest.mark.asyncio


class FakeClock:
    def __init__(self, start: float = 1000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def snapshot(**overrides) -> PolicySnapshot:
    values = {
        "policy_revision": 1,
        "max_model_calls": 8,
        "max_tool_calls": 16,
        "tool_timeout_seconds": 30,
        "max_active_seconds": 300,
        "max_context_chars": 120_000,
    }
    values.update(overrides)
    return PolicySnapshot(**values)


async def noop_persist(view: GovernanceView) -> None:
    await asyncio.sleep(0)


async def failing_persist(view: GovernanceView) -> None:
    raise OSError("disk full")


async def test_parallel_tool_reservations_never_overwrite_a_newer_count():
    persisted: list[tuple[int, int]] = []
    control = RunControl(snapshot(max_tool_calls=2), clock=FakeClock())

    async def persist(view):
        await asyncio.sleep(0)
        persisted.append((view.usage.tool_calls, view.control_revision))

    await asyncio.gather(control.reserve_tool(persist), control.reserve_tool(persist))
    assert [count for count, _ in persisted] == [1, 2]
    assert control.view().usage.tool_calls == 2
    assert control.view().control_revision == 2


async def test_persistence_failure_rolls_back_and_stops_waiters():
    control = RunControl(snapshot(max_tool_calls=2), clock=FakeClock())
    with pytest.raises(GovernancePersistenceFailed):
        await control.reserve_tool(failing_persist)
    assert control.view().usage.tool_calls == 0
    assert control.terminal_error.code == "PERSISTENCE_FAILED"
    # 终态后：排队/新到的预留一律拒绝
    with pytest.raises(GovernanceTerminalError) as blocked:
        await control.reserve_tool(noop_persist)
    assert blocked.value.code == "PERSISTENCE_FAILED"


async def test_model_and_tool_limits_enforced_before_increment_and_persist():
    persisted: list[GovernanceView] = []

    async def persist(view):
        persisted.append(view)

    control = RunControl(snapshot(max_model_calls=1, max_tool_calls=1), clock=FakeClock())
    await control.reserve_model(persist)
    await control.reserve_tool(persist)
    with pytest.raises(ModelCallLimitExceeded) as model_error:
        await control.reserve_model(persist)
    assert model_error.value.code == "MODEL_CALL_LIMIT_EXCEEDED"
    with pytest.raises(ToolCallLimitExceeded) as tool_error:
        await control.reserve_tool(persist)
    assert tool_error.value.code == "TOOL_CALL_LIMIT_EXCEEDED"
    assert len(persisted) == 2  # 超限从不触发持久化
    view = control.view()
    assert view.usage.model_calls == 1 and view.usage.tool_calls == 1
    assert view.control_revision == 2  # 超限不消耗 revision


async def test_control_revision_is_strictly_monotonic_under_concurrency():
    seen: list[int] = []

    async def persist(view):
        seen.append(view.control_revision)

    control = RunControl(snapshot(), clock=FakeClock())
    await asyncio.gather(*[
        asyncio.gather(control.reserve_model(persist), control.reserve_tool(persist))
        for _ in range(5)
    ])
    view = control.view()
    assert view.usage.model_calls == 5 and view.usage.tool_calls == 5
    # 10 次变更分配 10 个互不相同的 revision，最终视图持有最大值
    assert sorted(seen) == list(range(1, 11))
    assert view.control_revision == 10


async def test_active_segment_start_stop_are_idempotent_and_clock_driven():
    clock = FakeClock()
    control = RunControl(snapshot(max_active_seconds=300), clock=clock)
    control.begin_active_segment()
    control.begin_active_segment()  # 幂等：不重复计时
    clock.advance(30)
    control.close_active_segment()
    control.close_active_segment()  # 幂等
    assert control.view().active_elapsed_ms == 30_000
    clock.advance(100)  # 审批等待不计入 active
    assert control.view().active_elapsed_ms == 30_000
    control.begin_active_segment()
    clock.advance(10)
    assert control.view().active_elapsed_ms == 40_000
    assert control.remaining_active_seconds() == 260


async def test_remaining_active_seconds_clamps_at_zero():
    clock = FakeClock()
    control = RunControl(snapshot(max_active_seconds=300), clock=clock)
    control.begin_active_segment()
    clock.advance(301)
    assert control.remaining_active_seconds() == 0


async def test_record_model_usage_derives_token_status_and_sums():
    control = RunControl(snapshot(), clock=FakeClock())
    assert control.view().usage.token_status == "unavailable"

    await control.reserve_model(noop_persist)
    control.record_model_usage(input_tokens=10, output_tokens=5, total_tokens=15)
    view = control.view()
    assert view.usage.token_status == "available"
    assert (view.usage.input_tokens, view.usage.output_tokens, view.usage.total_tokens) == (10, 5, 15)

    await control.reserve_model(noop_persist)
    control.record_model_usage(None)  # Provider 错误/取消：完成但无 usage
    view = control.view()
    assert view.usage.token_status == "partial"
    assert view.usage.input_tokens == 10  # 聚合保留已报告值


async def test_reserved_but_uncompleted_call_counts_as_missing_usage():
    control = RunControl(snapshot(), clock=FakeClock())
    await control.reserve_model(noop_persist)
    await control.reserve_model(noop_persist)
    control.record_model_usage(input_tokens=3, output_tokens=1, total_tokens=4)
    # 2 次预留只回报 1 次：另一个视为缺失 → partial
    assert control.view().usage.token_status == "partial"


async def test_cancel_blocks_new_reservations_and_segments():
    control = RunControl(snapshot(), clock=FakeClock())
    control.mark_terminal("CLIENT_CANCELLED")
    with pytest.raises(GovernanceTerminalError) as blocked:
        await control.reserve_model(noop_persist)
    assert blocked.value.code == "CLIENT_CANCELLED"
    with pytest.raises(GovernanceTerminalError):
        control.begin_active_segment()
    # 只读视图与关闭段在终态后仍可用（终局持久化需要它们）
    assert control.view().usage.model_calls == 0
    control.close_active_segment()


async def test_mark_terminal_keeps_first_cause():
    control = RunControl(snapshot(), clock=FakeClock())
    control.mark_terminal("RUN_ACTIVE_TIMEOUT")
    control.mark_terminal("CLIENT_CANCELLED")
    assert control.terminal_error.code == "RUN_ACTIVE_TIMEOUT"


async def test_view_is_immutable_snapshot():
    control = RunControl(snapshot(), clock=FakeClock())
    await control.reserve_tool(noop_persist)
    first = control.view()
    await control.reserve_tool(noop_persist)
    assert first.usage.tool_calls == 1  # 旧视图不被后续变更改写
    assert control.view().usage.tool_calls == 2
