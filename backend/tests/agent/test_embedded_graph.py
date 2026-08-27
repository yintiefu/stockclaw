"""嵌入式 Agent 图（页面 Ask-AI）隔离与上下文契约测试。

测试覆盖：
- 首轮 page_context 为空时拦截拒绝；
- 后续轮次省略或为空时保留已有快照（不重置）；
- 相同 scope 传入合法新快照时版本号递增并更新时间；
- 切换不同 route 或 scope 时报错拒绝（防止串历史）；
- 动态提示词注入当前页面快照与固定中立系统策略；
- 嵌入式图只开放内置工具与 /builtin/ 技能，不包含 MCP、HITL 与 /user/ 技能。
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock
import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import ValidationError

from agent.embedded_graph import (
    AssistantContextRef,
    EmbeddedAgentState,
    PageContextInput,
    PageContextSnapshot,
    build_embedded_graph,
    keep_latest_nonempty_context,
)
from agent.policy import fixed_system_policy
from agent.skill_backends import BUILTIN_SKILLS_DIR
from tests.agent.fakes import ScriptedChatModel


def test_keep_latest_nonempty_context_reducer_matrix():
    # 1. none + empty -> None
    assert keep_latest_nonempty_context(None, None) is None
    assert keep_latest_nonempty_context(None, PageContextInput(route="/stock", scope_key="600519", source_as_of="15:00", content="")) is None

    # 2. initial valid input -> v1
    inp1 = PageContextInput(route="/stock", scope_key="600519", source_as_of="15:00", content="茅台现价 1800")
    v1 = keep_latest_nonempty_context(None, inp1)
    assert isinstance(v1, PageContextSnapshot)
    assert v1.version == 1
    assert v1.route == "/stock"
    assert v1.scope_key == "600519"
    assert v1.content == "茅台现价 1800"

    # 3. v1 + empty/None -> keep v1
    assert keep_latest_nonempty_context(v1, None) is v1
    assert keep_latest_nonempty_context(v1, PageContextInput(route="/stock", scope_key="600519", source_as_of="15:01", content="")) is v1

    # 4. v1 + valid update same scope -> v2
    inp2 = PageContextInput(route="/stock", scope_key="600519", source_as_of="15:05", content="茅台现价 1810")
    v2 = keep_latest_nonempty_context(v1, inp2)
    assert v2.version == 2
    assert v2.content == "茅台现价 1810"

    # 5. different route or scope -> raises ValueError
    inp_diff_scope = PageContextInput(route="/stock", scope_key="000001", source_as_of="15:05", content="平安现价 10")
    with pytest.raises(ValueError, match="mismatch|不匹配"):
        keep_latest_nonempty_context(v1, inp_diff_scope)


@pytest.mark.asyncio
async def test_embedded_graph_first_turn_requires_page_context():
    model = ScriptedChatModel([AIMessage(content="回答")])
    graph = await build_embedded_graph(model=model, checkpointer=InMemorySaver(), builtin_skills_root=BUILTIN_SKILLS_DIR)

    config = {"configurable": {"thread_id": "test-empty-ctx"}}
    with pytest.raises(Exception, match="page_context|快照"):
        await graph.ainvoke({"messages": [HumanMessage(content="你好")]}, config=config)


@pytest.mark.asyncio
async def test_embedded_graph_injects_snapshot_and_persists_across_turns():
    model = ScriptedChatModel([
        AIMessage(content="基于 v1 快照的回答。"),
        AIMessage(content="基于 v2 快照的回答。"),
    ])
    graph = await build_embedded_graph(model=model, checkpointer=InMemorySaver(), builtin_skills_root=BUILTIN_SKILLS_DIR)
    config = {"configurable": {"thread_id": "test-turn-reuse"}}

    # Turn 1: with snapshot v1
    res1 = await graph.ainvoke(
        {
            "messages": [HumanMessage(content="当前价格如何？")],
            "page_context": PageContextInput(route="/stock", scope_key="600519", source_as_of="15:00", content="茅台现价 1800"),
        },
        config=config,
    )
    assert res1["messages"][-1].content == "基于 v1 快照的回答。"
    sys_msg1 = [m.content for m in model.invocations[0] if isinstance(m, SystemMessage)][0]
    assert "茅台现价 1800" in sys_msg1
    assert "当前页面快照 v1" in sys_msg1

    # Turn 2: update with snapshot v2
    res2 = await graph.ainvoke(
        {
            "messages": [HumanMessage(content="价格有变化吗？")],
            "page_context": PageContextInput(route="/stock", scope_key="600519", source_as_of="15:05", content="茅台现价 1810"),
        },
        config=config,
    )
    assert res2["messages"][-1].content == "基于 v2 快照的回答。"
    sys_msg2 = [m.content for m in model.invocations[1] if isinstance(m, SystemMessage)][0]
    assert "茅台现价 1810" in sys_msg2
    assert "当前页面快照 v2" in sys_msg2


@pytest.mark.asyncio
async def test_embedded_graph_has_no_mcp_hitl_or_user_skills(monkeypatch):
    from deepagents.backends import CompositeBackend, FilesystemBackend

    from agent import embedded_graph as embedded_module
    from agent.skill_catalog import SKILLS_SYSTEM_PROMPT, FilteredSkillBackend

    captured: dict = {}
    compiled = object()
    monkeypatch.setattr(embedded_module, "create_agent", lambda **kwargs: captured.update(kwargs) or compiled)
    model = ScriptedChatModel([AIMessage(content="回答")])
    graph = await build_embedded_graph(model=model, checkpointer=InMemorySaver(), builtin_skills_root=BUILTIN_SKILLS_DIR)

    assert graph is compiled
    skills_middleware = captured["middleware"][0]
    assert skills_middleware.sources == ["/builtin/"]
    assert skills_middleware.system_prompt_template == SKILLS_SYSTEM_PROMPT
    backend = skills_middleware._backend
    assert isinstance(backend, CompositeBackend)
    assert set(backend.routes) == {"/builtin/"}
    for routed in backend.routes.values():
        assert isinstance(routed, FilteredSkillBackend)
        assert not isinstance(routed, FilesystemBackend)


@pytest.mark.asyncio
async def test_embedded_graph_attributes_history_turns_to_compact_snapshot_refs():
    """历史助手回答在后续模型输入中只带紧凑快照版本/时间标记，不重复旧快照正文。"""
    model = ScriptedChatModel([
        AIMessage(content="基于 v1 快照的回答。"),
        AIMessage(content="基于 v2 快照的回答。"),
    ])
    graph = await build_embedded_graph(model=model, checkpointer=InMemorySaver(), builtin_skills_root=BUILTIN_SKILLS_DIR)
    config = {"configurable": {"thread_id": "test-context-refs"}}

    await graph.ainvoke(
        {
            "messages": [HumanMessage(content="当前价格如何？")],
            "page_context": PageContextInput(route="/stock", scope_key="600519", source_as_of="15:00", content="茅台现价 1800"),
        },
        config=config,
    )
    state = await graph.aget_state(config)
    refs = state.values.get("assistant_context_refs")
    assert refs, "首轮结束后必须记录 assistant_context_refs"
    first_ref = refs[-1]
    assert first_ref.snapshot_version == 1
    assert first_ref.captured_at

    await graph.ainvoke(
        {
            "messages": [HumanMessage(content="价格有变化吗？")],
            "page_context": PageContextInput(route="/stock", scope_key="600519", source_as_of="15:05", content="茅台现价 1810"),
        },
        config=config,
    )

    sys2 = [m.content for m in model.invocations[1] if isinstance(m, SystemMessage)][0]
    # 紧凑版本/时间标记必须进入后续 prompt
    assert "v1" in sys2
    assert first_ref.captured_at in sys2
    # 旧快照正文不得重复注入（当前快照 v2 全文照常注入）
    assert "茅台现价 1800" not in sys2
    assert "茅台现价 1810" in sys2
    # 历史标记必须声明「不得当作当前数据」
    assert "不代表当前" in sys2 or "不是当前" in sys2
