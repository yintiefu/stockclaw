"""Orchestrator 节点：分类用户意图并路由。

Phase 1 简化版：基于规则分类（不调 LLM）
- "目标价" / "止损" / "止盈" / "仓位" / "决策" / "建仓" → intent="decision"
- 含 A 股代码或美股字母代码 + 一般问句 → intent="decision"（默认走决策）
- 其他 → intent="general"（直接调 LLM，不走 Decision Node）
"""
from __future__ import annotations

import re

from agents.state import AgentState


_DECISION_KEYWORDS = {"目标价", "止损", "止盈", "仓位", "决策", "建仓", "入场", "买点", "卖点", "节奏"}
# A 股 6 位 / 美股 1-5 字母 / 港股 4-5 位数字
_CODE_PATTERNS = [
    re.compile(r"\b\d{6}\b"),
    re.compile(r"\b[A-Z]{1,5}\b"),
    re.compile(r"\b\d{4,5}\b"),
]


def classify_intent(state: AgentState) -> str:
    """根据最后一条 user 消息分类意图。返回 'decision' | 'research' | 'general'。"""
    msgs = state.get("messages") or []
    if not msgs:
        return "general"
    last_user = ""
    for m in reversed(msgs):
        if isinstance(m, dict) and m.get("role") == "user":
            last_user = m.get("content", "") or ""
            break
        # LangChain BaseMessage 兼容——HumanMessage.type == "human"（不是 "user"）
        role = getattr(m, "type", None) or getattr(m, "role", None)
        if role in ("user", "human"):
            last_user = getattr(m, "content", "") or ""
            break

    # 关键词触发
    if any(kw in last_user for kw in _DECISION_KEYWORDS):
        return "decision"
    # 代码 + 求分析类问句
    has_code = any(p.search(last_user) for p in _CODE_PATTERNS)
    has_analysis = any(k in last_user for k in ["分析", "看看", "怎么样", "如何"])
    if has_code and has_analysis:
        return "decision"
    return "general"


def orchestrator_node(state: AgentState) -> dict:
    """LangGraph 节点：分类意图，写回 state['intent']。"""
    intent = classify_intent(state)
    return {"intent": intent}
