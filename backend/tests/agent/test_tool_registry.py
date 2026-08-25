"""内置工具的原生 LangChain 适配契约——schema 一比一、结构化错误、
工作线程派发、元数据与执行策略。"""
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
    build_builtin_tools,
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


async def test_all_builtin_tools_have_origin_and_execution_policy_metadata():
    built = build_builtin_tools()
    for tool in built:
        assert tool.metadata["vr_origin"] == "builtin"
        assert tool.metadata["vr_execution_policy"] in ("parallel_safe", "eastmoney_serial")


async def test_large_results_are_trimmed_before_leaving_registry(monkeypatch):
    monkeypatch.setattr(legacy_tools, "exec_tool", lambda name, args: {"text": "x" * 7000})
    result = await build_builtin_tools()[0].ainvoke({"codes": ["600519"]})
    assert len(result) <= BUILTIN_RESULT_LIMIT
    assert result.endswith("...[truncated]")
