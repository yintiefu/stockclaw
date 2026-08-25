"""内置工具的原生 LangChain 适配器：把 tools.py 的 schema 一比一转换为
StructuredTool，委托给 agent.tool_executor 执行。"""
from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool

import tools as legacy_tools
from agent.tool_executor import (
    RESULT_LIMIT as BUILTIN_RESULT_LIMIT,
    encode_tool_result,
    execute_tool,
    tool_policy,
)


def _build_one(schema: dict[str, Any]) -> StructuredTool:
    function = schema["function"]
    name = function["name"]
    policy = tool_policy(name)

    async def invoke(**kwargs: Any) -> str:
        result = await execute_tool(name, kwargs)
        return encode_tool_result(result)

    return StructuredTool.from_function(
        coroutine=invoke,
        name=name,
        description=function["description"],
        args_schema=function["parameters"],
        metadata={"vr_origin": "builtin", "vr_execution_policy": policy.value},
    )


def build_builtin_tools() -> list[StructuredTool]:
    return [_build_one(schema) for schema in legacy_tools.TOOLS]
