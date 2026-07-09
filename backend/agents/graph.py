"""LangGraph 主图构建。

Phase 1 简化版：
  START → orchestrator → {decision: decision_node, general: tool_calling_llm}
  decision_node → END
  tool_calling_llm → END

注：Phase 2 会把 general 路径改成 LangGraph 标准 ReAct agent（多轮工具调用）。
"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from agents.nodes.decision import decision_node
from agents.nodes.orchestrator import orchestrator_node
from agents.state import AgentState


def _route_intent(state: AgentState) -> str:
    """orchestrator 之后的条件路由：'decision' | 'general'。"""
    return state.get("intent", "general")


def _general_passthrough(state: AgentState) -> dict:
    """general 路径占位：Phase 1 不调 LLM，由 runner 自己处理（runner 调 OpenAI 流式 + tools）。

    此节点存在是为了让 graph 结构完整；实际 LLM 调用在 runner.py。
    """
    return {}


def build_agent_graph():
    """构建并编译 agent 主图。"""
    g = StateGraph(AgentState)
    g.add_node("orchestrator", orchestrator_node)
    g.add_node("decision", decision_node)
    g.add_node("general", _general_passthrough)

    g.add_edge(START, "orchestrator")
    g.add_conditional_edges(
        "orchestrator",
        _route_intent,
        {"decision": "decision", "general": "general"},
    )
    g.add_edge("decision", END)
    g.add_edge("general", END)

    return g.compile()


# 全局编译好的图（runner 复用）
agent_graph = build_agent_graph()
