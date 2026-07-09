"""LangGraph AgentState 定义。

messages: OpenAI 消息序列（system + user + assistant + tool）
intent: orchestrator 分类结果（"decision" | "research" | "general"）
context_codes: 用户从 ContextDrawer 注入的股票代码（A 股 6 位 / 美股字母 / 港股数字）
style: 风格预设（"conservative" | "balanced" | "aggressive"），Phase 1 不生效，预留接口
artifacts: 累积的结构化产物（Decision Node 输出追加到此）
thread_id: 会话 ID（持久化用）
"""
from __future__ import annotations

from typing import Annotated, TypedDict

from langgraph.graph.message import add_messages


class AgentState(TypedDict, total=False):
    messages: Annotated[list, add_messages]
    intent: str
    context_codes: list[str]
    style: str
    artifacts: list[dict]
    thread_id: str
    # 流式输出用：runner 维护，不进 graph 传递
    decision_card: dict | None
