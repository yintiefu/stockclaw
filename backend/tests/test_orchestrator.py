"""orchestrator 意图分类测试。

关键回归：classify_intent 在 graph 上下文里跑时，messages 会被 LangGraph
的 add_messages 转成 BaseMessage（HumanMessage.type == "human"，不是 "user"）。
之前只检查 role == "user"，导致 graph 真跑时所有 decision 请求被误分类成 general。

Phase 1.5 验收时通过真实 LLM 调用发现（用户输入「分析 600519 给目标价」
graph 返回 intent=general，没触发 decision_node）。
"""
import pytest

from agents.nodes.orchestrator import classify_intent
from agents.graph import agent_graph


def test_classify_intent_dict_messages_direct():
    """dict 形式 messages：classify_intent 单独跑应识别 decision 关键词。"""
    state = {"messages": [{"role": "user", "content": "分析 600519 给目标价止损"}]}
    assert classify_intent(state) == "decision"


def test_classify_intent_human_message_via_graph():
    """关键回归：通过真实 graph.ainvoke 跑，messages 会被转成 HumanMessage。
    HumanMessage.type == "human" 不是 "user"——之前这里漏了，所有 decision
    请求被误分类成 general。"""
    import asyncio

    async def run():
        state = {
            "messages": [{"role": "user", "content": "分析 600519 给目标价止损止盈仓位节奏"}],
            "context_codes": ["600519"],
            "style": "balanced",
        }
        result = await agent_graph.ainvoke(state)
        return result.get("intent")

    intent = asyncio.run(run())
    assert intent == "decision", (
        f"graph 应分类为 decision（含 5 个决策关键词），实际 {intent}。"
        f"检查 classify_intent 的 BaseMessage 分支是否认 human 类型。"
    )


def test_classify_intent_general_no_keywords():
    """无决策关键词 + 无代码 → general。"""
    state = {"messages": [{"role": "user", "content": "你好介绍下自己"}]}
    assert classify_intent(state) == "general"


def test_classify_intent_human_message_direct():
    """直接传 HumanMessage（不经 graph）也应识别。"""
    from langchain_core.messages import HumanMessage

    state = {"messages": [HumanMessage(content="分析茅台 给目标价")]}
    assert classify_intent(state) == "decision"


def test_classify_intent_empty_messages():
    """空 messages → general（防 IndexError）。"""
    assert classify_intent({"messages": []}) == "general"
    assert classify_intent({}) == "general"
