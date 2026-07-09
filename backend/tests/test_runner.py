"""runner.run_agent 单测——NDJSON 事件流结构。"""
import json
from contextlib import asynccontextmanager

import pytest

import runner


class _FakeStreamResponse:
    """模拟 httpx 流式响应——吐预设的 SSE 行。"""
    def __init__(self, lines: list[str], status_code: int = 200):
        self._lines = lines
        self.status_code = status_code

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def aread(self):
        return b""


@asynccontextmanager
async def _fake_stream_factory(lines: list[str], status_code: int = 200):
    """构造一个假 httpx.AsyncClient.stream context manager。"""
    async def _stream(method, url, **kwargs):
        return _FakeStreamResponse(lines, status_code)
    yield _FakeStreamResponse(lines, status_code)


def _patch_httpx_stream(monkeypatch, sse_lines: list[str], status_code: int = 200):
    """Patch runner.httpx.AsyncClient.stream 返回预设 SSE 流。"""
    @asynccontextmanager
    async def fake_stream(self, method, url, **kwargs):
        yield _FakeStreamResponse(sse_lines, status_code)

    monkeypatch.setattr("runner.httpx.AsyncClient.stream", fake_stream)


def _sse_delta(text: str) -> str:
    """构造一个 OpenAI 兼容的 SSE data 行。"""
    payload = {"choices": [{"delta": {"content": text}}]}
    return f"data: {json.dumps(payload)}"


@pytest.mark.asyncio
async def test_run_agent_emits_decision_artifact_for_decision_intent(monkeypatch):
    """decision 路径 → text_delta + decision_artifact + done。
    真 _stream_llm_text 被跑——只 mock httpx.AsyncClient.stream。"""
    async def fake_graph_ainvoke(input_state):
        return {"intent": "decision", "decision_card": {
            "code": "600519", "name": "茅台", "current_price": 1685.0,
            "target_price": 1900.0, "entry_low": 1685.0, "entry_high": 1720.0,
            "stop_loss": 1550.0, "take_profit": 2080.0, "cadence": [],
            "basis_type": "model", "model_versions_json": {},
            "assumptions": [], "citations": [], "explanation": "测试"
        }}

    # 真 SSE 流：吐 "分析中" + [DONE]
    sse_lines = [_sse_delta("分析中"), "data: [DONE]"]
    _patch_httpx_stream(monkeypatch, sse_lines)

    monkeypatch.setattr("runner.agent_graph.ainvoke", fake_graph_ainvoke)

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
    # text_delta 的内容必须是 "分析中"——证明 _stream_llm_text 真的被跑、SSE 真被解析
    text_events = [e for e in events if e["type"] == "text_delta"]
    assert any("分析中" in e["text"] for e in text_events)


@pytest.mark.asyncio
async def test_run_agent_no_decision_card_for_general_intent(monkeypatch):
    """general 路径 → 只有 text_delta + done，无 decision_artifact。"""
    async def fake_graph_ainvoke(input_state):
        return {"intent": "general", "decision_card": None}

    sse_lines = [_sse_delta("你好"), "data: [DONE]"]
    _patch_httpx_stream(monkeypatch, sse_lines)
    monkeypatch.setattr("runner.agent_graph.ainvoke", fake_graph_ainvoke)

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
    text_events = [e for e in events if e["type"] == "text_delta"]
    assert any("你好" in e["text"] for e in text_events)
