"""Task 5 服务端集成测试的确定性夹具图：真实 stdio MCP 发现 + 服务名前缀 +
HITL 中断，模型用离线脚本化回复（16 条，覆盖 session 内全部 run 且重启即重建）。"""
from __future__ import annotations

import asyncio

from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langchain_core.messages import AIMessage
from langchain_mcp_adapters.client import MultiServerMCPClient

import chat
from agent.settings import load_agent_settings
from agent.session_trace import SessionTraceMiddleware
from tests.agent.fakes import ScriptedChatModel


async def build_fixture_graph():
    settings = load_agent_settings()
    trace = SessionTraceMiddleware(settings.trace) if settings.trace.enabled else None
    tools = await MultiServerMCPClient(
        settings.mcp_connections(), tool_name_prefix=True,
    ).get_tools()
    echo = next(tool for tool in tools if tool.name == "fixture_echo")
    echo.return_direct = True
    replies = [
        AIMessage(content="", tool_calls=[{
            "id": f"fixture-call-{index}",
            "name": "fixture_echo",
            "args": {"value": "approved fixture value"},
        }])
        for index in range(16)
    ]
    return create_agent(
        model=ScriptedChatModel(replies),
        tools=tools,
        middleware=[*( [trace] if trace else []), HumanInTheLoopMiddleware({
            tool.name: {"allowed_decisions": ["approve", "reject"]}
            for tool in tools
        })],
        system_prompt=chat.SYSTEM_PROMPT.format(context="Agent 工作台"),
    )


graph = asyncio.run(build_fixture_graph())
