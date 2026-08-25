"""进程级工具执行器与并发策略。

控制所有 Graph / run / event loop 下的工具执行：
- 7 项 parallel_safe 工具最多允许 4 个并发在途调用；
- 6 项 Eastmoney 工具与其它未声明工具在进程级别严格串行互斥；
- 统一在 worker 线程执行并持有锁/信号量，避免 asyncio 取消导致过早释放；
- 统一结果 JSON 编码与 6000 字符上限截断。
"""
from __future__ import annotations

import asyncio
from enum import StrEnum
import json
import threading
from typing import Any

import tools as legacy_tools

RESULT_LIMIT = 6000


class ToolExecutionPolicy(StrEnum):
    EASTMONEY_SERIAL = "eastmoney_serial"
    PARALLEL_SAFE = "parallel_safe"


EASTMONEY_SERIAL_TOOLS = frozenset({
    "query_valuation", "query_fund_flow", "query_margin", "query_holders",
    "query_lockup", "query_concepts",
})

PARALLEL_SAFE_TOOLS = frozenset({
    "query_quote", "query_valuation_percentile", "query_financials",
    "query_kline", "query_announcements", "query_reports", "query_news",
})

_SERIAL_LOCK = threading.Lock()
_PARALLEL_CAPACITY = threading.BoundedSemaphore(4)


def tool_policy(name: str) -> ToolExecutionPolicy:
    """获取指定工具的执行并发策略。"""
    if name in PARALLEL_SAFE_TOOLS:
        return ToolExecutionPolicy.PARALLEL_SAFE
    return ToolExecutionPolicy.EASTMONEY_SERIAL


def encode_tool_result(value: Any, limit: int = RESULT_LIMIT) -> str:
    """序列化工具调用返回值并按上限截断。"""
    encoded = json.dumps(value, ensure_ascii=False, default=str)
    suffix = "...[truncated]"
    if len(encoded) <= limit:
        return encoded
    return encoded[:limit - len(suffix)] + suffix


def _dispatch(name: str, args: dict[str, Any]) -> Any:
    guard = _PARALLEL_CAPACITY if tool_policy(name) is ToolExecutionPolicy.PARALLEL_SAFE else _SERIAL_LOCK
    with guard:
        return legacy_tools.exec_tool(name, args)


async def execute_tool(name: str, args: dict[str, Any]) -> Any:
    """在 worker 线程中异步分发并执行工具。"""
    return await asyncio.to_thread(_dispatch, name, args)
