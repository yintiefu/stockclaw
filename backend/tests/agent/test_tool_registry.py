"""Task 3：内置工具的原生 LangChain 适配契约——schema 一比一、结构化错误、
工作线程派发、进程级共享锁与东财请求间隔。"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from types import SimpleNamespace

import pytest

import astock
import tools as legacy_tools
from agent.tool_registry import (
    BUILTIN_RESULT_LIMIT,
    BUILTIN_SERIAL_LOCK,
    build_builtin_tools,
    builtin_serial_lock,
)

pytestmark = pytest.mark.asyncio


async def test_builtin_tools_preserve_schema_names_and_structured_errors(monkeypatch):
    monkeypatch.setattr(legacy_tools, "exec_tool", lambda name, args: {"error": f"{name} failed", "args": args})
    built = build_builtin_tools()
    assert [tool.name for tool in built] == legacy_tools.TOOL_NAMES
    result = json.loads(await built[0].ainvoke({"codes": ["600519"]}))
    assert result == {"error": "query_quote failed", "args": {"codes": ["600519"]}}


async def test_builtin_tool_dispatch_runs_off_event_loop(monkeypatch):
    main_thread = threading.get_ident()
    seen = []
    monkeypatch.setattr(legacy_tools, "exec_tool", lambda name, args: seen.append(threading.get_ident()) or {"ok": True})
    await build_builtin_tools()[0].ainvoke({"codes": ["600519"]})
    assert seen and seen[0] != main_thread


async def test_all_builtin_tools_share_one_process_lock():
    built = build_builtin_tools()
    assert BUILTIN_SERIAL_LOCK is builtin_serial_lock()
    assert all(tool.metadata == {"vr_origin": "builtin", "vr_serial_lock": "process"} for tool in built)


async def test_large_results_are_trimmed_before_leaving_registry(monkeypatch):
    monkeypatch.setattr(legacy_tools, "exec_tool", lambda name, args: {"text": "x" * 7000})
    result = await build_builtin_tools()[0].ainvoke({"codes": ["600519"]})
    assert len(result) <= BUILTIN_RESULT_LIMIT
    assert result.endswith("...[truncated]")


async def test_builtin_lock_keeps_two_eastmoney_requests_one_second_apart(monkeypatch):
    """进程级锁保证并发内置调用下东财节流（时间戳间隔）仍然成立——东财防封红线。"""
    starts: list[float] = []

    class FakeResponse:
        def json(self):
            return {"result": {"data": []}}

    class FakeSession:
        def get(self, *_args, **_kwargs):
            starts.append(time.monotonic())
            return FakeResponse()

    session = FakeSession()
    monkeypatch.setattr(astock, "_EM_MIN_INTERVAL", 1.0)
    monkeypatch.setattr(astock, "_em_last_call", [0.0])
    monkeypatch.setattr(astock, "_em_mode", ["direct"])
    monkeypatch.setattr(astock, "_em_session", lambda _direct: session)
    monkeypatch.setattr(astock, "random", SimpleNamespace(uniform=lambda *_args: 0.0))
    built = {tool.name: tool for tool in build_builtin_tools()}

    await asyncio.gather(
        built["query_margin"].ainvoke({"code": "600519"}),
        built["query_block_trade"].ainvoke({"code": "600519"}),
    )

    assert len(starts) == 2
    assert starts[1] - starts[0] >= 1.0
