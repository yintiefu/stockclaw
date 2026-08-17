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


# ---- 1D Task 6：工具准入治理 ----

import asyncio
import json as _json
import time

from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.tools import StructuredTool, tool as lc_tool

from agent.governance import ToolExecutionGovernance, classify_tool
from agent.tool_executor import (
    BoundedToolExecutor,
    ToolCapacityExhausted,
    current_tool_execution_context,
)


def tool_request(tool_obj, args=None, call_id="call-1"):
    return ToolCallRequest(
        tool_call={"name": tool_obj.name, "args": {} if args is None else args,
                   "id": call_id, "type": "tool_call"},
        tool=tool_obj, state={"messages": []}, runtime=None)


async def record_view(view):
    await asyncio.sleep(0)


def make_tool_governance(control, *, executor=None, serial_lock=None, persist=None,
                          events=None, thread_id="thread-1", run_id="run-1"):
    async def emit(payload):
        if events is not None:
            events.append(payload)

    return ToolExecutionGovernance(
        control, persist=persist or record_view, emit=emit, executor=executor,
        builtin_serial_lock=serial_lock, thread_id=thread_id, run_id=run_id)


def local_tool(recors=None):
    async def invoke(**kwargs):
        if recors is not None:
            recors.append(kwargs)
        return _json.dumps({"ok": True, **kwargs}, ensure_ascii=False)
    return StructuredTool.from_function(
        coroutine=invoke, name="local_probe", description="本地探针",
        args_schema={"type": "object", "properties": {"x": {"type": "string"}},
                     "required": ["x"]},
        metadata={"vr_origin": "local", "vr_execution_lock": True})


def capacity_tool():
    async def invoke(**kwargs):
        context = current_tool_execution_context()
        return _json.dumps({"has_lease": context is not None and context.capacity_lease is not None})
    return StructuredTool.from_function(
        coroutine=invoke, name="cap_probe", description="容量探针",
        args_schema={"type": "object", "properties": {}},
        metadata={"vr_origin": "artifact", "vr_execution_lock": True, "vr_capacity": True})


def mcp_tool():
    async def invoke(**kwargs):
        context = current_tool_execution_context()
        return _json.dumps({"mcp": True, "no_lease": context is None or context.capacity_lease is None})
    return StructuredTool.from_function(
        coroutine=invoke, name="mcp_probe", description="MCP 探针",
        args_schema={"type": "object", "properties": {}},
        metadata={"vr_origin": "mcp"})


def test_classify_tool_uses_metadata():
    assert classify_tool(local_tool()) == ("local", True, False, False)
    builtin = StructuredTool.from_function(
        coroutine=lambda **kw: "ok", name="b", description="d",
        metadata={"vr_origin": "builtin", "vr_execution_lock": True,
                  "vr_builtin_serial": True, "vr_capacity": True})
    assert classify_tool(builtin) == ("builtin", True, True, True)
    assert classify_tool(capacity_tool()) == ("artifact", True, False, True)
    assert classify_tool(mcp_tool()) == ("mcp", False, False, False)

    @lc_tool
    def plain(x: str) -> str:
        """无元数据工具按本地处理"""
        return x
    assert classify_tool(plain) == ("local", True, False, False)


async def test_schema_rejection_consumes_no_reservation_and_skips_handler():
    calls: list = []
    probe = local_tool(recors=calls)
    control = RunControl(snapshot(), clock=FakeClock())
    governance = make_tool_governance(control)

    async def call_tool(request):
        calls.append("inner")
        return await probe.ainvoke(request.tool_call["args"])

    from agent.governance import ToolArgsInvalid
    with pytest.raises(ToolArgsInvalid):
        await governance.awrap_tool_call(tool_request(probe, args={"bogus": 1}), call_tool)
    assert calls == []  # 真实 handler 未被调用
    assert control.view().usage.tool_calls == 0


async def test_valid_local_tool_reserves_before_handler_and_installs_context():
    calls: list = []
    seen_context = []

    async def handler(**kwargs):
        seen_context.append(current_tool_execution_context())
        return _json.dumps({"ok": True})

    probe = StructuredTool.from_function(
        coroutine=handler, name="local_probe", description="本地探针",
        args_schema={"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]},
        metadata={"vr_origin": "local", "vr_execution_lock": True})
    control = RunControl(snapshot(), clock=FakeClock())
    events: list = []
    order: list = []

    async def persist(view):
        order.append(("persist", view.usage.tool_calls))

    governance = make_tool_governance(control, persist=persist, events=events)

    async def call_tool(request):
        calls.append("inner")
        order.append("handler")
        return await probe.ainvoke(request.tool_call["args"])

    result = await governance.awrap_tool_call(tool_request(probe, args={"x": "1"}), call_tool)
    _json.loads(result)
    assert order[0] == ("persist", 1)  # 持久化先于 handler
    assert control.view().usage.tool_calls == 1
    assert seen_context and seen_context[0].thread_id == "thread-1"
    assert seen_context[0].product_run_id == "run-1"
    assert seen_context[0].control is control
    assert current_tool_execution_context() is None  # 调用后上下文已复位
    assert any(e["usage"]["tool_calls"] == 1 for e in events)
    # 执行锁已释放：下一个调用可立即进入
    assert control.execution_lock.locked() is False


async def test_tool_limit_rejected_before_locks_and_handler():
    calls: list = []
    control = RunControl(snapshot(max_tool_calls=1), clock=FakeClock())
    governance = make_tool_governance(control)

    async def call_tool(request):
        calls.append("inner")
        return "ok"

    await control.reserve_tool(record_view)
    with pytest.raises(ToolCallLimitExceeded):
        await governance.awrap_tool_call(tool_request(local_tool(), args={"x": "1"}), call_tool)
    assert calls == []
    assert control.view().usage.tool_calls == 1
    assert control.execution_lock.locked() is False


async def test_capacity_tool_acquires_lease_and_runs_in_executor():
    control = RunControl(snapshot(), clock=FakeClock())
    executor = BoundedToolExecutor()
    governance = make_tool_governance(control, executor=executor)
    probe = capacity_tool()

    async def call_tool(request):
        return await probe.ainvoke(request.tool_call["args"])

    result = await governance.awrap_tool_call(tool_request(probe), call_tool)
    assert _json.loads(result)["has_lease"] is True
    assert control.view().usage.tool_calls == 1
    executor.shutdown()


async def test_capacity_exhaustion_maps_to_code_without_reservation():
    import threading
    control = RunControl(snapshot(), clock=FakeClock())
    executor = BoundedToolExecutor()
    release = threading.Event()
    started = [threading.Event() for _ in range(4)]

    async def occupy(i):
        lease = await executor.acquire(capacity_wait_seconds=0.1, deadline=time.monotonic() + 30)
        def work():
            started[i].set()
            release.wait(5)
        await executor.run_with_lease(lease, work, time.monotonic() + 30)

    tasks = [asyncio.create_task(occupy(i)) for i in range(4)]
    await asyncio.to_thread(lambda: all(e.wait(5) for e in started))

    async def call_tool(request):
        raise AssertionError("不应执行 handler")

    governance = make_tool_governance(control, executor=executor)
    with pytest.raises(ToolCapacityExhausted):
        await governance.awrap_tool_call(tool_request(capacity_tool()), call_tool)
    assert control.view().usage.tool_calls == 0
    release.set()
    await asyncio.gather(*tasks)
    executor.shutdown()


async def test_serial_lock_contention_maps_earlier_deadline():
    """跨 run serial 锁竞争：另一 run 的更早 tool deadline 到期，handler 不被调用。"""
    from agent.governance import ToolTimedOut

    waiter_control = RunControl(snapshot(tool_timeout_seconds=5), clock=FakeClock())
    serial = asyncio.Lock()

    async def never(request):
        raise AssertionError("第二个 run 不应进入 handler")

    waiter_clock = FakeClock()

    async def tick():
        await asyncio.sleep(0.05)
        waiter_clock.advance(10)  # 快进越过 tool deadline

    ticker = asyncio.create_task(tick())
    # 占用进程级 serial 锁的 run A；run B 的内置工具等待 serial 时更早到期
    async with serial:
        governance = ToolExecutionGovernance(
            waiter_control, persist=record_view,
            builtin_serial_lock=serial, thread_id="thread-2", run_id="run-b",
            clock=waiter_clock)
        probe = StructuredTool.from_function(
            coroutine=lambda **kw: "ok", name="b", description="d",
            metadata={"vr_origin": "builtin", "vr_execution_lock": True,
                      "vr_builtin_serial": True})
        with pytest.raises(ToolTimedOut) as raised:
            await governance.awrap_tool_call(tool_request(probe), never)
        assert raised.value.code == "TOOL_TIMEOUT"
        assert waiter_control.view().usage.tool_calls == 0
    await ticker


async def test_execution_lock_times_out_with_tool_timeout_code():
    from agent.governance import ToolTimedOut
    clock = FakeClock()
    control = RunControl(snapshot(tool_timeout_seconds=5), clock=clock)
    governance = ToolExecutionGovernance(control, persist=record_view, clock=clock)

    async def never(request):
        raise AssertionError("不应执行")

    async def tick():
        await asyncio.sleep(0.05)
        clock.advance(10)  # 快进越过 tool deadline

    ticker = asyncio.create_task(tick())
    # 占住执行锁：等待在 min(tool, active) 内失败
    async with control.execution_lock:
        with pytest.raises(ToolTimedOut) as raised:
            await governance.awrap_tool_call(
                tool_request(local_tool(), args={"x": "1"}), never)
        assert raised.value.code == "TOOL_TIMEOUT"
    await ticker
    assert control.view().usage.tool_calls == 0


async def test_active_deadline_expiry_maps_to_run_active_timeout():
    from agent.governance import ToolTimedOut
    clock = FakeClock()
    control = RunControl(snapshot(max_active_seconds=300), clock=clock)
    control.begin_active_segment()
    clock.advance(301)  # active 已耗尽：即使 tool deadline 未到也按 active 失败
    governance = make_tool_governance(control)

    async def never(request):
        raise AssertionError("不应执行")

    with pytest.raises(ToolTimedOut) as raised:
        await governance.awrap_tool_call(tool_request(local_tool(), args={"x": "1"}), never)
    assert raised.value.code == "RUN_ACTIVE_TIMEOUT"
    assert control.view().usage.tool_calls == 0


async def test_mcp_tool_reserves_without_local_locks_or_capacity():
    control = RunControl(snapshot(), clock=FakeClock())
    governance = make_tool_governance(control)
    probe = mcp_tool()

    async def call_tool(request):
        context = current_tool_execution_context()
        assert context is None or context.capacity_lease is None
        return await probe.ainvoke(request.tool_call["args"])

    await governance.awrap_tool_call(tool_request(probe), call_tool)
    assert control.view().usage.tool_calls == 1
    assert control.execution_lock.locked() is False


async def test_reservation_persistence_failure_releases_prerequisites():
    control = RunControl(snapshot(), clock=FakeClock())
    executor = BoundedToolExecutor()

    async def failing(view):
        raise OSError("disk full")

    governance = make_tool_governance(control, executor=executor, persist=failing)
    probe = capacity_tool()

    async def call_tool(request):
        raise AssertionError("持久化失败不得调用 handler")

    with pytest.raises(GovernancePersistenceFailed):
        await governance.awrap_tool_call(tool_request(probe), call_tool)
    assert control.view().usage.tool_calls == 0
    assert control.terminal_error.code == "PERSISTENCE_FAILED"
    # 容量已归还：可立即取得全部 4 个租约
    leases = [await executor.acquire(capacity_wait_seconds=0.1, deadline=time.monotonic() + 5)
              for _ in range(4)]
    for lease in leases:
        lease.release_unsubmitted()
    executor.shutdown()
