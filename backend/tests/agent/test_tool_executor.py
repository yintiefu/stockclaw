"""进程级工具执行策略与并发契约测试。

验证：
- 13 项工具分类策略（7 项 parallel_safe，6 项 eastmoney_serial）以及其它内置工具默认串行；
- parallel_safe 进程内最大并发为 4；
- 跨 OS 线程 / 跨 event loop 的串行调用互斥不重叠；
- 协程取消时不提前释放 worker 线程持有的 lock/semaphore；
- 结果编码保留 6000 字符限制及结构化错误。
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from enum import StrEnum
import json
import threading
import time

import pytest

import tools as legacy_tools
from agent.tool_executor import (
    EASTMONEY_SERIAL_TOOLS,
    PARALLEL_SAFE_TOOLS,
    ToolExecutionPolicy,
    encode_tool_result,
    execute_tool,
    tool_policy,
)

PARALLEL_SAFE = {
    "query_quote", "query_valuation_percentile", "query_financials",
    "query_kline", "query_announcements", "query_reports", "query_news",
}
EASTMONEY_SERIAL = {
    "query_valuation", "query_fund_flow", "query_margin", "query_holders",
    "query_lockup", "query_concepts",
}


def test_tool_policies_match_specification():
    assert PARALLEL_SAFE_TOOLS == PARALLEL_SAFE
    assert EASTMONEY_SERIAL_TOOLS == EASTMONEY_SERIAL

    for name in PARALLEL_SAFE:
        assert tool_policy(name) == ToolExecutionPolicy.PARALLEL_SAFE

    for name in EASTMONEY_SERIAL:
        assert tool_policy(name) == ToolExecutionPolicy.EASTMONEY_SERIAL

    # All other tools in legacy_tools.TOOL_NAMES must default to serial
    other_tools = set(legacy_tools.TOOL_NAMES) - PARALLEL_SAFE
    for name in other_tools:
        assert tool_policy(name) == ToolExecutionPolicy.EASTMONEY_SERIAL


@pytest.mark.asyncio
async def test_parallel_safe_never_exceeds_four_concurrent_handlers(monkeypatch):
    active_count = 0
    max_active = 0
    lock = threading.Lock()

    def fake_exec(name: str, args: dict):
        nonlocal active_count, max_active
        with lock:
            active_count += 1
            if active_count > max_active:
                max_active = active_count
        time.sleep(0.05)
        with lock:
            active_count -= 1
        return {"name": name, "ok": True}

    monkeypatch.setattr(legacy_tools, "exec_tool", fake_exec)

    tasks = [execute_tool("query_quote", {"codes": [f"00000{i}"]}) for i in range(8)]
    results = await asyncio.gather(*tasks)

    assert len(results) == 8
    assert max_active <= 4
    assert max_active >= 2


def test_serial_invocations_across_different_event_loops_never_overlap(monkeypatch):
    active_count = 0
    max_active = 0
    lock = threading.Lock()

    def fake_exec(name: str, args: dict):
        nonlocal active_count, max_active
        with lock:
            active_count += 1
            if active_count > max_active:
                max_active = active_count
        time.sleep(0.04)
        with lock:
            active_count -= 1
        return {"name": name, "ok": True}

    monkeypatch.setattr(legacy_tools, "exec_tool", fake_exec)

    def run_in_new_loop():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(execute_tool("query_margin", {"code": "600519"}))
        finally:
            loop.close()

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(run_in_new_loop) for _ in range(4)]
        for f in futures:
            f.result()

    assert max_active == 1


@pytest.mark.asyncio
async def test_cancellation_does_not_release_guard_early(monkeypatch):
    started = threading.Event()
    worker_finished = threading.Event()
    lock = threading.Lock()
    active_count = 0
    max_active = 0

    def slow_exec(name: str, args: dict):
        nonlocal active_count, max_active
        with lock:
            active_count += 1
            if active_count > max_active:
                max_active = active_count
        started.set()
        time.sleep(0.08)
        with lock:
            active_count -= 1
        worker_finished.set()
        return {"ok": True}

    monkeypatch.setattr(legacy_tools, "exec_tool", slow_exec)

    task = asyncio.create_task(execute_tool("query_margin", {"code": "600519"}))
    await asyncio.to_thread(started.wait)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Task is cancelled in asyncio, but worker should still hold lock until worker_finished
    # Try running another serial tool; it should wait until slow_exec finishes
    next_task = asyncio.create_task(execute_tool("query_margin", {"code": "600519"}))
    await next_task

    assert worker_finished.is_set()
    assert max_active == 1


def test_encode_tool_result_preserves_cap_and_structured_error():
    normal = {"data": "hello"}
    assert encode_tool_result(normal) == json.dumps(normal, ensure_ascii=False)

    huge = {"text": "x" * 7000}
    encoded = encode_tool_result(huge)
    assert len(encoded) <= 6000
    assert encoded.endswith("...[truncated]")

    err = {"error": "API failed", "detail": "connection timeout"}
    err_encoded = encode_tool_result(err)
    assert "API failed" in err_encoded
