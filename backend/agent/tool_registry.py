from __future__ import annotations

import asyncio
import json
from typing import Any

from langchain_core.tools import StructuredTool

import tools as legacy_tools

BUILTIN_RESULT_LIMIT = 6000


def _encode_result(value: Any) -> str:
    """统一编码为 JSON 文本，超限截断，保证进入模型前体量可控。"""
    encoded = json.dumps(value, ensure_ascii=False, default=str)
    if len(encoded) <= BUILTIN_RESULT_LIMIT:
        return encoded
    return encoded[: BUILTIN_RESULT_LIMIT - len("...[truncated]")] + "...[truncated]"


def _build_one(schema: dict[str, Any], execution_lock: asyncio.Lock) -> StructuredTool:
    function = schema["function"]
    name = function["name"]

    async def invoke(**kwargs: Any) -> str:
        # 同一 run 内串行执行内置工具，避免并发触发上游限流（见 AGENTS.md 限流规则）。
        async with execution_lock:
            result = await asyncio.to_thread(legacy_tools.exec_tool, name, kwargs)
        return _encode_result(result)

    return StructuredTool.from_function(
        coroutine=invoke,
        name=name,
        description=function["description"],
        args_schema=function["parameters"],
    )


def build_builtin_tools() -> list[StructuredTool]:
    """把 tools.py 的 24 个既有 schema 一比一转换为 LangChain 工具。"""
    execution_lock = asyncio.Lock()
    return [_build_one(schema, execution_lock) for schema in legacy_tools.TOOLS]
