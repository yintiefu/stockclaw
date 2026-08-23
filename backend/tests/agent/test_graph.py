"""Task 4：原生 LangChain 图组装契约——固定中立提示词、完整工具面、
MCP 前缀发现、HITL 拒绝恢复、Skills 元数据优先与只读根约束，全部离线。"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from langchain.agents import create_agent as real_create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from deepagents.middleware.filesystem import FilesystemMiddleware
from deepagents.middleware.skills import SkillsMiddleware
from pydantic import SecretStr

import chat
import tools as legacy_tools
from agent import graph as graph_module
from agent.settings import AgentSettings
from tests.agent.fakes import ScriptedChatModel


def make_settings(tmp_path: Path) -> AgentSettings:
    skills = tmp_path / "skills"
    skills.mkdir(exist_ok=True)
    return AgentSettings.model_validate({
        "model": {
            "provider": "openai",
            "name": "test-model",
            "apiKey": "test-secret-never-send",
            "baseURL": "https://example.invalid/v1",
            "temperature": 0.2,
        },
        "skills": {"path": str(skills)},
        "mcpServers": {},
    })


@pytest.fixture
def settings(tmp_path: Path) -> AgentSettings:
    return make_settings(tmp_path)


@tool("fixture_echo")
def fixture_echo(value: str) -> str:
    """Return deterministic fixture text."""
    return value


@tool("query_quote")
def duplicate_query_quote(codes: list[str]) -> str:
    """Deliberately collide with a built-in tool in one test."""
    return ",".join(codes)


@pytest.mark.asyncio
async def test_build_graph_uses_fixed_prompt_and_complete_tool_surface(monkeypatch, settings):
    captured = {}
    compiled = object()
    monkeypatch.setattr(graph_module, "create_agent", lambda **kwargs: captured.update(kwargs) or compiled)
    monkeypatch.setattr(graph_module.MultiServerMCPClient, "get_tools", AsyncMock(return_value=[fixture_echo]))
    model = ScriptedChatModel([AIMessage(content="客观回复")])
    assert await graph_module.build_graph(model=model, settings=settings) is compiled
    assert captured["system_prompt"] == chat.SYSTEM_PROMPT.format(context="Agent 工作台")
    assert {tool_.name for tool_ in captured["tools"]} == {*legacy_tools.TOOL_NAMES, "fixture_echo"}
    assert isinstance(captured["middleware"][0], SkillsMiddleware)
    assert isinstance(captured["middleware"][1], FilesystemMiddleware)
    assert [tool_.name for tool_ in captured["middleware"][1].tools] == ["ls", "read_file"]
    exposed = {tool_.name for tool_ in captured["tools"]}
    assert not exposed & {"write_file", "edit_file", "delete", "grep", "glob", "execute"}
    hitl = captured["middleware"][2]
    assert isinstance(hitl, HumanInTheLoopMiddleware)
    assert hitl.interrupt_on == {"fixture_echo": {"allowed_decisions": ["approve", "reject"]}}


@pytest.mark.asyncio
async def test_duplicate_tool_names_fail_before_agent_creation(monkeypatch, settings):
    monkeypatch.setattr(graph_module.MultiServerMCPClient, "get_tools", AsyncMock(return_value=[duplicate_query_quote]))
    with pytest.raises(RuntimeError, match="query_quote"):
        await graph_module.build_graph(model=ScriptedChatModel([]), settings=settings)


def test_model_is_streaming_serial_and_secret_safe(settings):
    with pytest.warns(UserWarning, match="transferred to model_kwargs"):
        model = graph_module._build_model(settings)
    assert model.model_kwargs["parallel_tool_calls"] is False
    assert model.streaming is True
    assert isinstance(model.openai_api_key, SecretStr)
    assert "test-secret-never-send" not in repr(model)


@pytest.mark.asyncio
async def test_builtin_tool_call_loops_back_into_model(monkeypatch, settings):
    monkeypatch.setattr(graph_module.MultiServerMCPClient, "get_tools", AsyncMock(return_value=[]))
    monkeypatch.setattr(legacy_tools, "exec_tool", lambda name, args: {"name": name, "codes": args["codes"]})
    model = ScriptedChatModel([
        AIMessage(content="", tool_calls=[{
            "id": "builtin-1", "name": "query_quote", "args": {"codes": ["600519"]},
        }]),
        AIMessage(content="已基于客观行情完成核验。"),
    ])
    graph = await graph_module.build_graph(model=model, settings=settings)
    result = await graph.ainvoke({"messages": [{"role": "user", "content": "查询行情"}]})

    assert result["messages"][-1].content == "已基于客观行情完成核验。"
    tool_messages = [message for message in model.invocations[1] if isinstance(message, ToolMessage)]
    assert json.loads(tool_messages[-1].content) == {"name": "query_quote", "codes": ["600519"]}


@pytest.mark.asyncio
async def test_hitl_reject_resumes_without_executing_tool(monkeypatch, settings):
    executed: list[str] = []

    @tool("fixture_guarded")
    def fixture_guarded(value: str) -> str:
        """Record execution for the rejection contract."""
        executed.append(value)
        return value

    monkeypatch.setattr(
        graph_module.MultiServerMCPClient,
        "get_tools",
        AsyncMock(return_value=[fixture_guarded]),
    )
    monkeypatch.setattr(
        graph_module,
        "create_agent",
        lambda **kwargs: real_create_agent(checkpointer=InMemorySaver(), **kwargs),
    )
    model = ScriptedChatModel([
        AIMessage(content="", tool_calls=[{
            "id": "reject-1", "name": "fixture_guarded", "args": {"value": "不得执行"},
        }]),
        AIMessage(content="拒绝已记录，工具未执行。"),
    ])
    graph = await graph_module.build_graph(model=model, settings=settings)
    config = {"configurable": {"thread_id": "offline-reject"}}

    paused = await graph.ainvoke(
        {"messages": [{"role": "user", "content": "拒绝这个工具"}]},
        config=config,
    )
    assert paused["__interrupt__"]
    result = await graph.ainvoke(
        Command(resume={"decisions": [{"type": "reject", "message": "用户拒绝"}]}),
        config=config,
    )

    assert executed == []
    rejected = [
        message for message in result["messages"]
        if isinstance(message, ToolMessage) and message.tool_call_id == "reject-1"
    ]
    assert len(rejected) == 1
    assert rejected[0].status == "error"
    assert rejected[0].content == "用户拒绝"
    assert len(model.invocations) == 2
    assert any(
        isinstance(message, ToolMessage) and message.status == "error"
        for message in model.invocations[1]
    )


@pytest.mark.asyncio
async def test_skills_are_metadata_first_read_only_and_root_confined(monkeypatch, settings, tmp_path):
    skill = settings.skills.path / "research"
    references = skill / "references"
    references.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: research\ndescription: 客观核验步骤。\n---\nFULL_SKILL_BODY_MARKER\n",
        encoding="utf-8",
    )
    (references / "checklist.md").write_text("REFERENCE_MARKER", encoding="utf-8")
    (tmp_path / "outside-secret.txt").write_text("OUTSIDE_SECRET", encoding="utf-8")
    monkeypatch.setattr(graph_module.MultiServerMCPClient, "get_tools", AsyncMock(return_value=[]))
    model = ScriptedChatModel([
        AIMessage(content="", tool_calls=[{
            "id": "read-skill", "name": "read_file", "args": {"file_path": "/research/SKILL.md"},
        }]),
        AIMessage(content="", tool_calls=[{
            "id": "read-reference", "name": "read_file",
            "args": {"file_path": "/research/references/checklist.md"},
        }]),
        AIMessage(content="", tool_calls=[{
            "id": "escape-root", "name": "read_file", "args": {"file_path": "/../outside-secret.txt"},
        }]),
        AIMessage(content="完成"),
    ])
    graph = await graph_module.build_graph(model=model, settings=settings)
    result = await graph.ainvoke({"messages": [{"role": "user", "content": "读取核验技能"}]})

    system_text = "\n".join(str(message.content) for message in model.invocations[0] if isinstance(message, SystemMessage))
    assert "research" in system_text and "客观核验步骤" in system_text
    assert "FULL_SKILL_BODY_MARKER" not in system_text
    assert "REFERENCE_MARKER" not in system_text
    assert any(
        "FULL_SKILL_BODY_MARKER" in str(message.content)
        for message in model.invocations[1] if isinstance(message, ToolMessage)
    )
    assert any(
        "REFERENCE_MARKER" in str(message.content)
        for message in model.invocations[2] if isinstance(message, ToolMessage)
    )
    escape = [message for message in result["messages"] if isinstance(message, ToolMessage) and message.tool_call_id == "escape-root"]
    assert len(escape) == 1 and escape[0].status == "error"
    assert "Path traversal not allowed" in str(escape[0].content)
    assert "OUTSIDE_SECRET" not in str(result["messages"])
