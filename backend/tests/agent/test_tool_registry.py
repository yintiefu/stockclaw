import asyncio
import json

import pytest
import tools
from agent.tool_registry import BUILTIN_RESULT_LIMIT, build_builtin_tools

pytestmark = pytest.mark.asyncio


def test_all_existing_tools_are_converted_exactly_once():
    converted = build_builtin_tools()
    assert len(converted) == 24
    assert [tool.name for tool in converted] == tools.TOOL_NAMES
    assert len({tool.name for tool in converted}) == len(converted)
    for source, converted_tool in zip(tools.TOOLS, converted, strict=True):
        assert converted_tool.description == source["function"]["description"]
        assert converted_tool.args_schema == source["function"]["parameters"]


async def test_error_results_are_json_tool_results(monkeypatch):
    monkeypatch.setattr(tools, "exec_tool", lambda name, args: {"error": f"{name} failed"})
    result = await build_builtin_tools()[0].ainvoke({"codes": ["600519"]})
    assert json.loads(result) == {"error": "query_quote failed"}


async def test_large_results_are_trimmed_before_leaving_registry(monkeypatch):
    monkeypatch.setattr(tools, "exec_tool", lambda name, args: {"text": "x" * 7000})
    result = await build_builtin_tools()[0].ainvoke({"codes": ["600519"]})
    assert len(result) <= BUILTIN_RESULT_LIMIT
    assert result.endswith("...[truncated]")


async def test_builtins_share_one_per_run_execution_lock(monkeypatch):
    active = 0
    maximum = 0

    def fake(name, args):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        import time
        time.sleep(0.02)
        active -= 1
        return {"name": name}

    monkeypatch.setattr(tools, "exec_tool", fake)
    converted = build_builtin_tools()
    await asyncio.gather(
        converted[0].ainvoke({"codes": ["600519"]}),
        converted[1].ainvoke({"code": "600519"}),
    )
    assert maximum == 1
