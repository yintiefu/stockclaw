"""Task 5 服务端集成测试的确定性夹具图：真实 stdio MCP 发现 + 服务名前缀 +
HITL 中断，模型用离线脚本化回复（16 条，覆盖 session 内全部 run 且重启即重建）。

Task 11 起挂与生产一致的技能栈：ReloadableSkillsMiddleware（/user/ 先、
/builtin/ 后的 later-wins 顺序 + 自定义 SKILLS_SYSTEM_PROMPT）与只读
FilesystemMiddleware。脚本模型额外识别「列出当前技能」，供集成/E2E 断言
实际注入 Agent 的技能视图。
"""
from __future__ import annotations

import asyncio

from deepagents.middleware.filesystem import FilesystemMiddleware
from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langchain_core.messages import AIMessage
from langchain_mcp_adapters.client import MultiServerMCPClient

from agent.policy import fixed_system_policy
from agent.session_trace import SessionTraceMiddleware
from agent.settings import load_agent_settings
from agent.skill_backends import BUILTIN_SKILLS_DIR, SKILLS_SYSTEM_PROMPT, build_skill_backend
from agent.skill_reload import ReloadableSkillsMiddleware
from tests.agent.fakes import SkillsAwareScriptedModel


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
    backend = build_skill_backend(BUILTIN_SKILLS_DIR, settings.skills.path)
    return create_agent(
        model=SkillsAwareScriptedModel(replies),
        tools=tools,
        middleware=[*( [trace] if trace else []), ReloadableSkillsMiddleware(
            backend=backend,
            sources=["/user/", "/builtin/"],
            system_prompt=SKILLS_SYSTEM_PROMPT,
        ), FilesystemMiddleware(backend=backend, tools=["ls", "read_file"]), HumanInTheLoopMiddleware({
            tool.name: {"allowed_decisions": ["approve", "reject"]}
            for tool in tools
        })],
        system_prompt=fixed_system_policy("Agent 工作台"),
    )


graph = asyncio.run(build_fixture_graph())
