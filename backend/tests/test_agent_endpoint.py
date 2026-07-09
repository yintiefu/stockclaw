"""/api/agent/chat 端点协议测试——CLI 拒绝、鉴权 401 短路、NDJSON 流。"""
import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("VR_API_KEY", raising=False)
    import app
    return TestClient(app.app)


def _llm_api_cfg():
    return {"provider": "", "baseURL": "https://api.example.com", "apiKey": "test-key", "model": "gpt-4o"}


def test_cli_mode_rejected_with_400(client):
    """provider=cli-* → 400 + JSON 错误体（不是 SSE 流）。"""
    resp = client.post("/api/agent/chat", json={
        "messages": [{"role": "user", "content": "分析茅台"}],
        "llm": {"provider": "cli-claude", "baseURL": "", "apiKey": "", "model": "claude"},
    })
    assert resp.status_code == 400
    assert resp.headers["content-type"].startswith("application/json")
    body = resp.json()
    assert "cli" in body["detail"].lower() or "API" in body["detail"]


def test_auth_required_returns_401_not_sse(monkeypatch):
    """设了 VR_API_KEY 但请求不带 → 401 + JSON，不挂 SSE 流。"""
    monkeypatch.setenv("VR_API_KEY", "secret-key")
    import importlib, app
    importlib.reload(app)
    client = TestClient(app.app)
    try:
        resp = client.post("/api/agent/chat", json={
            "messages": [{"role": "user", "content": "分析"}],
            "llm": _llm_api_cfg(),
        })
        assert resp.status_code == 401
        assert resp.headers["content-type"].startswith("application/json")
    finally:
        # 还原无 key 状态，避免 importlib.reload 后 module 级 _API_KEY 泄漏到后续用例。
        monkeypatch.delenv("VR_API_KEY", raising=False)
        importlib.reload(app)


def test_request_body_validation_missing_messages(client):
    """缺 messages → 422。"""
    resp = client.post("/api/agent/chat", json={"llm": _llm_api_cfg()})
    assert resp.status_code == 422


def test_ndjson_stream_emits_text_delta_and_done(client):
    """正常请求 → text/x-ndjson 流；至少含 text_delta + done 事件。"""
    async def fake_run_agent(req):
        yield {"type": "text_delta", "text": "分析中"}
        yield {"type": "decision_artifact", "decision_id": "test-did",
               "data": {"code": "600519", "name": "茅台", "target_price": 1900.0,
                        "basis_type": "model", "cadence": [], "model_versions_json": {},
                        "current_price": 1685.0, "entry_low": 1685.0, "entry_high": 1720.0,
                        "stop_loss": 1550.0, "take_profit": 2080.0,
                        "assumptions": [], "citations": [], "explanation": "测试"}}
        yield {"type": "done", "summary": {"rounds": 1}}

    with patch("app.run_agent", fake_run_agent):
        resp = client.post("/api/agent/chat", json={
            "thread_id": None,
            "messages": [{"role": "user", "content": "分析茅台 给目标价"}],
            "context_codes": ["600519"],
            "llm": _llm_api_cfg(),
            "style": "balanced",
        })
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/x-ndjson")
    body = resp.text
    lines = [l for l in body.split("\n") if l.strip()]
    types = [json.loads(l)["type"] for l in lines]
    assert "text_delta" in types
    assert "decision_artifact" in types
    assert "done" in types
    # 每行严格以 \n 结尾（spec §9 Phase 1 #8）
    assert body.endswith("\n")
