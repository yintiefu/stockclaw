from __future__ import annotations

import json
from typing import Any, Sequence

from langchain_core.tools import BaseTool, StructuredTool

import tools as legacy_tools
from agent.tool_executor import (
    ToolExecutionContext,
    current_tool_execution_context,
    install_tool_execution_context,
    reset_tool_execution_context,
)

BUILTIN_RESULT_LIMIT = 6000

BUILTIN_TOOL_METADATA = {
    # 不可变准入元数据：治理包装器据此决定 execution/serial 锁与容量要求
    "vr_origin": "builtin",
    "vr_execution_lock": True,
    "vr_builtin_serial": True,
    "vr_capacity": True,
}


def _encode_result(value: Any) -> str:
    """统一编码为 JSON 文本，超限截断，保证进入模型前体量可控。"""
    encoded = json.dumps(value, ensure_ascii=False, default=str)
    if len(encoded) <= BUILTIN_RESULT_LIMIT:
        return encoded
    return encoded[: BUILTIN_RESULT_LIMIT - len("...[truncated]")] + "...[truncated]"


def _build_one(schema: dict[str, Any]) -> StructuredTool:
    function = schema["function"]
    name = function["name"]

    async def invoke(**kwargs: Any) -> str:
        # 锁与容量由治理包装器在调用前取得（execution → serial → capacity → reservation），
        # handler 只消费上下文里的租约执行同步 dispatch，绝不重复获取任何锁或令牌。
        context = current_tool_execution_context()
        if context is None or context.capacity_lease is None:
            raise RuntimeError(
                f"内置工具 {name} 只能在治理执行上下文中调用（缺少容量租约）")
        result = await context.executor.run_with_lease(
            context.capacity_lease,
            lambda: legacy_tools.exec_tool(name, kwargs),
            context.tool_deadline,
        )
        return _encode_result(result)

    return StructuredTool.from_function(
        coroutine=invoke,
        name=name,
        description=function["description"],
        args_schema=function["parameters"],
        metadata=dict(BUILTIN_TOOL_METADATA),
    )


def build_builtin_tools() -> list[StructuredTool]:
    """把 tools.py 的 24 个既有 schema 一比一转换为 LangChain 工具。

    1D 起 execution lock 归产品 run 的 RunControl 所有；本函数不再创建任何锁。
    """
    return [_build_one(schema) for schema in legacy_tools.TOOLS]


def compose_run_tools(skill_tools: Sequence[BaseTool] = ()) -> list[BaseTool]:
    """1C 组合点：内置工具 + Skill 快照工具（切片 3 起再追加 MCP 绑定包装）。"""
    return [*build_builtin_tools(), *skill_tools]
