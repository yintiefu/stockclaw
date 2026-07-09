"""runner.run_agent 单测——NDJSON 事件流结构。"""
import pytest

import runner


@pytest.mark.asyncio
async def test_run_agent_emits_decision_artifact_for_decision_intent():
    """decision 路径 → 至少含 text_delta + decision_artifact + done。"""
    async def fake_graph_ainvoke(input_state):
        return {"intent": "decision", "decision_card": {
            "code": "600519", "name": "茅台", "current_price": 1685.0,
            "target_price": 1900.0, "entry_low": 1685.0, "entry_high": 1720.0,
            "stop_loss": 1550.0, "take_profit": 2080.0, "cadence": [],
            "basis_type": "model", "model_versions_json": {},
            "assumptions": [], "citations": [], "explanation": "测试"
        }}

    async def fake_stream_text(*args, **kwargs):
        yield "分析中"

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr("runner.agent_graph.ainvoke", fake_graph_ainvoke)
        mp.setattr("runner._stream_llm_text", fake_stream_text)
        req = runner.AgentChatReq(
            thread_id=None,
            messages=[{"role": "user", "content": "分析茅台 给目标价"}],
            context_codes=["600519"],
            llm={"provider": "", "baseURL": "https://api.example.com",
                 "apiKey": "k", "model": "gpt-4o"},
            style="balanced",
        )
        events = []
        async for ev in runner.run_agent(req):
            events.append(ev)

    types = [e["type"] for e in events]
    assert "text_delta" in types
    assert "decision_artifact" in types
    assert "done" in types


@pytest.mark.asyncio
async def test_run_agent_no_decision_card_for_general_intent():
    """general 路径 → 只有 text_delta + done，无 decision_artifact。"""
    async def fake_graph_ainvoke(input_state):
        return {"intent": "general", "decision_card": None}

    async def fake_stream_text(*args, **kwargs):
        yield "你好"

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr("runner.agent_graph.ainvoke", fake_graph_ainvoke)
        mp.setattr("runner._stream_llm_text", fake_stream_text)
        req = runner.AgentChatReq(
            thread_id=None,
            messages=[{"role": "user", "content": "你好"}],
            llm={"provider": "", "baseURL": "https://api.example.com",
                 "apiKey": "k", "model": "gpt-4o"},
        )
        events = []
        async for ev in runner.run_agent(req):
            events.append(ev)

    types = [e["type"] for e in events]
    assert "decision_artifact" not in types
    assert "text_delta" in types
    assert "done" in types
