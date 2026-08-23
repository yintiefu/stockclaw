"""Task 12：浏览器 E2E 专用确定性图（6 条脚本化回复，仅覆盖单个串行场景）。

先 import 生产图模块（模块级装配执行一次，验证生产构建路径），再用注入的
脚本化模型重建。浏览器夹具绝不调用真实行情源；MCP 走共享的 stdio 假服务。
"""
from __future__ import annotations

import asyncio

from agent import graph as production_graph  # module-level production builder runs once
from langchain_core.messages import AIMessage
from tests.agent.fakes import ScriptedChatModel

graph = asyncio.run(production_graph.build_graph(
    model=ScriptedChatModel([
        AIMessage(content="客观测试回复完成。"),
        AIMessage(content="", tool_calls=[{
            "id": "call-approve", "name": "fixture_echo", "args": {"value": "客观 MCP 数据"},
        }]),
        AIMessage(content="MCP 客观结果已返回。"),
        AIMessage(content="", tool_calls=[{
            "id": "call-reject", "name": "fixture_echo", "args": {"value": "不应执行"},
        }]),
        AIMessage(content="MCP 调用已拒绝，本轮未执行工具。"),
        AIMessage(content="", tool_calls=[{
            "id": "call-stop", "name": "fixture_sleep", "args": {"seconds": 5.0},
        }]),
    ]),
))
