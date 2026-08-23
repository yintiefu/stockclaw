"""内置工具的原生 LangChain 适配器：把 tools.py 的 schema 一比一转换为
StructuredTool，统一 JSON 编码与截断，并用单个进程级 asyncio.Lock 串行
派发（保护东财时间戳节流——它本身不加锁，必须靠外层串行才可靠）。"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from langchain_core.tools import StructuredTool

import tools as legacy_tools

BUILTIN_RESULT_LIMIT = 6000
BUILTIN_SERIAL_LOCK = asyncio.Lock()


def builtin_serial_lock() -> asyncio.Lock:
    return BUILTIN_SERIAL_LOCK


def _encode_result(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, default=str)
    suffix = "...[truncated]"
    return encoded if len(encoded) <= BUILTIN_RESULT_LIMIT else encoded[:BUILTIN_RESULT_LIMIT - len(suffix)] + suffix


def _build_one(schema: dict[str, Any]) -> StructuredTool:
    function = schema["function"]
    name = function["name"]

    async def invoke(**kwargs: Any) -> str:
        async with BUILTIN_SERIAL_LOCK:
            result = await asyncio.to_thread(legacy_tools.exec_tool, name, kwargs)
        return _encode_result(result)

    return StructuredTool.from_function(
        coroutine=invoke,
        name=name,
        description=function["description"],
        args_schema=function["parameters"],
        metadata={"vr_origin": "builtin", "vr_serial_lock": "process"},
    )


def build_builtin_tools() -> list[StructuredTool]:
    return [_build_one(schema) for schema in legacy_tools.TOOLS]
