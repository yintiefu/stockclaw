"""原生 LangChain 图：一次性组装模型 / 内置工具 / MCP 工具 / Skills / HITL。

LangGraph Server 导入本模块时在进程启动阶段完成全部装配；模型、MCP 与 Skills
配置只来自本地静态设置文件（agent.settings），不进入请求。生产图无请求级
模型覆盖；测试通过 build_graph(model=..., settings=...) 注入。
"""
from __future__ import annotations

import asyncio
from typing import Any

from deepagents.middleware.filesystem import FilesystemMiddleware
from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_mcp_adapters.client import MultiServerMCPClient

from agent.model_factory import build_model
from agent.policy import fixed_system_policy
from agent.session_trace import SessionTraceMiddleware
from agent.settings import AgentSettings, load_agent_settings
from agent.skill_backends import BUILTIN_SKILLS_DIR, SKILLS_SYSTEM_PROMPT, build_skill_backend
from agent.skill_reload import ReloadableSkillsMiddleware
from agent.tool_registry import build_builtin_tools


def _require_unique_tool_names(tools: list[Any]) -> None:
    counts: dict[str, int] = {}
    for tool in tools:
        counts[tool.name] = counts.get(tool.name, 0) + 1
    duplicates = sorted(name for name, count in counts.items() if count > 1)
    if duplicates:
        raise RuntimeError(f"Agent 工具名冲突：{', '.join(duplicates)}")


async def build_graph(
    model: BaseChatModel | None = None,
    *,
    settings: AgentSettings | None = None,
):
    resolved = settings or load_agent_settings()
    backend = build_skill_backend(BUILTIN_SKILLS_DIR, resolved.skills.path)
    client = MultiServerMCPClient(resolved.mcp_connections(), tool_name_prefix=True)
    builtin_tools = build_builtin_tools()
    mcp_tools = await client.get_tools()
    all_tools = [*builtin_tools, *mcp_tools]
    _require_unique_tool_names(all_tools)
    middleware = []
    if resolved.trace.enabled:
        # 追踪中间件置于列表第一位（wrap 链最外层，计时含其余中间件开销）
        middleware.append(SessionTraceMiddleware(resolved.trace))
    middleware += [
        # later-wins：/builtin/ 放最后，用户与内置同名时内置保持最终优先
        ReloadableSkillsMiddleware(
            backend=backend,
            sources=["/user/", "/builtin/"],
            system_prompt=SKILLS_SYSTEM_PROMPT,
        ),
        FilesystemMiddleware(backend=backend, tools=["ls", "read_file"]),
        HumanInTheLoopMiddleware({
            tool.name: {"allowed_decisions": ["approve", "reject"]}
            for tool in mcp_tools
        }),
    ]
    return create_agent(
        model=model or build_model(resolved),
        tools=all_tools,
        middleware=middleware,
        system_prompt=fixed_system_policy("Agent 工作台"),
    )


graph = asyncio.run(build_graph())
