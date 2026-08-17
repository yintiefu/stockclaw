import asyncio
import json
import threading
import time

import pytest
import tools
from agent.tool_executor import BoundedToolExecutor, ToolExecutionContext
from agent.tool_registry import (
    BUILTIN_RESULT_LIMIT,
    build_builtin_tools,
    install_tool_execution_context,
    reset_tool_execution_context,
)

pytestmark = pytest.mark.asyncio


def far_deadline() -> float:
    return time.monotonic() + 10.0


async def governed_call(tool_obj, args, *, executor, execution_lock, serial_lock,
                        deadline=None):
    """模拟 Task 6 治理包装器的锁序：execution → serial → capacity → handler。"""
    async with execution_lock:
        async with serial_lock:
            lease = await executor.acquire(capacity_wait_seconds=1.0,
                                           deadline=deadline or far_deadline())
            context = ToolExecutionContext(
                thread_id="thread-test",
                product_run_id="run-test",
                execution_lock=execution_lock,
                builtin_serial_lock=serial_lock,
                executor=executor,
                tool_deadline=deadline or far_deadline(),
                capacity_lease=lease,
            )
            token = install_tool_execution_context(context)
            try:
                return await tool_obj.ainvoke(args)
            finally:
                reset_tool_execution_context(token)


def test_all_existing_tools_are_converted_exactly_once():
    converted = build_builtin_tools()
    assert len(converted) == 24
    assert [tool.name for tool in converted] == tools.TOOL_NAMES
    assert len({tool.name for tool in converted}) == len(converted)
    for source, converted_tool in zip(tools.TOOLS, converted, strict=True):
        assert converted_tool.description == source["function"]["description"]
        assert converted_tool.args_schema == source["function"]["parameters"]
        # 1D：不可变来源/准入元数据（治理包装器据此决定锁与容量）
        assert converted_tool.metadata == {
            "vr_origin": "builtin",
            "vr_execution_lock": True,
            "vr_builtin_serial": True,
            "vr_capacity": True,
        }


async def test_error_results_are_json_tool_results(monkeypatch):
    monkeypatch.setattr(tools, "exec_tool", lambda name, args: {"error": f"{name} failed"})
    executor = BoundedToolExecutor()
    result = await governed_call(
        build_builtin_tools()[0], {"codes": ["600519"]},
        executor=executor, execution_lock=asyncio.Lock(), serial_lock=asyncio.Lock())
    assert json.loads(result) == {"error": "query_quote failed"}
    executor.shutdown()


async def test_large_results_are_trimmed_before_leaving_registry(monkeypatch):
    monkeypatch.setattr(tools, "exec_tool", lambda name, args: {"text": "x" * 7000})
    executor = BoundedToolExecutor()
    result = await governed_call(
        build_builtin_tools()[0], {"codes": ["600519"]},
        executor=executor, execution_lock=asyncio.Lock(), serial_lock=asyncio.Lock())
    assert len(result) <= BUILTIN_RESULT_LIMIT
    assert result.endswith("...[truncated]")
    executor.shutdown()


async def test_builtins_from_different_runs_never_overlap_legacy_dispatch(monkeypatch):
    """跨 run 进程序列化：两个 run 各自持有 execution lock，共享进程级 serial lock。"""
    active = 0
    maximum = 0
    enter = threading.Event()
    release = threading.Event()

    # 用线程事件证明 dispatch 区段互斥：第一个调用进入后阻塞，第二个不得进入 dispatch
    def blocking_dispatch(name, args):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        enter.set()
        release.wait(5)
        active -= 1
        return {"name": name, "serial": True}

    monkeypatch.setattr(tools, "exec_tool", blocking_dispatch)

    executor = BoundedToolExecutor()
    serial_lock = asyncio.Lock()  # 进程级共享
    run_a_lock = asyncio.Lock()
    run_b_lock = asyncio.Lock()
    converted = build_builtin_tools()

    first = asyncio.create_task(governed_call(
        converted[0], {"codes": ["600519"]},
        executor=executor, execution_lock=run_a_lock, serial_lock=serial_lock))
    await asyncio.to_thread(enter.wait, 5)
    second = asyncio.create_task(governed_call(
        converted[1], {"code": "600519"},
        executor=executor, execution_lock=run_b_lock, serial_lock=serial_lock))
    await asyncio.sleep(0.05)
    assert maximum == 1  # 第二个 run 的 dispatch 未进入
    release.set()
    await asyncio.gather(first, second)
    assert maximum == 1
    executor.shutdown()


async def test_ungoverned_invocation_fails_closed(monkeypatch):
    """没有治理上下文时内置工具拒绝执行（不得绕过锁/容量）。"""
    monkeypatch.setattr(tools, "exec_tool", lambda name, args: {"name": name})
    with pytest.raises(RuntimeError):
        await build_builtin_tools()[0].ainvoke({"codes": ["600519"]})
