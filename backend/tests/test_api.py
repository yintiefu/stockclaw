"""API 验证/契约测（FastAPI TestClient）。大多在校验层就返回，不联网、可靠。"""
import pytest
from fastapi.testclient import TestClient

import app as app_module

client = TestClient(app_module.app)


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_agent_status_requires_auth_when_api_key_set(monkeypatch):
    """VR_API_KEY 公网模式下 /api/agent/status 必须带 Bearer（和其他 /api/* 一致）。"""
    monkeypatch.setattr(app_module, "_API_KEY", "test-key-123")
    no_auth = client.get("/api/agent/status")
    assert no_auth.status_code == 401
    with_auth = client.get("/api/agent/status", headers={"Authorization": "Bearer test-key-123"})
    assert with_auth.status_code == 200


def test_agent_status_returns_redacted_summary(monkeypatch):
    """端点只回脱敏摘要：模型名/主机/计数/模板，绝不出现密钥。"""
    import json as _json

    from agent.settings import agent_status_summary, load_agent_settings

    monkeypatch.setattr(app_module, "_API_KEY", "")
    monkeypatch.setattr(
        app_module, "_agent_status_payload", lambda: agent_status_summary(load_agent_settings()),
    )
    r = client.get("/api/agent/status")
    assert r.status_code == 200
    payload = r.json()
    assert payload["configured"] is True
    assert payload["model_name"] == "test-model"
    assert payload["base_url_host"] == "example.invalid"
    assert payload["builtin_skill_count"] == 5
    assert payload["mcp_server_count"] == 0
    serialized = _json.dumps(payload, ensure_ascii=False)
    assert "test-secret-never-send" not in serialized
    assert "YOUR_API_KEY" in serialized


def test_agent_status_safe_when_settings_missing(monkeypatch):
    from agent.settings import agent_status_summary

    monkeypatch.setattr(app_module, "_API_KEY", "")
    monkeypatch.setattr(
        app_module, "_agent_status_payload", lambda: agent_status_summary(None),
    )
    r = client.get("/api/agent/status")
    assert r.status_code == 200
    payload = r.json()
    assert payload["configured"] is False
    assert payload["model_name"] is None
    assert payload["base_url_host"] is None
    assert payload["builtin_skill_count"] == 0


@pytest.mark.parametrize("path", [
    "/api/quote?codes=abc",
    "/api/valuation?code=12",
    "/api/margin?code=notcode",
    "/api/holders?code=1234567",
    "/api/announcements?code=",
])
def test_bad_code_400(path):
    assert client.get(path).status_code == 400


def test_industry_top_range():
    assert client.get("/api/industry?top=2").status_code == 422   # ge=5
    assert client.get("/api/industry?top=999").status_code == 422  # le=50


def test_global_stock_404(monkeypatch):
    """无法解析的美股/港股代码 → 404（不 500、不崩）。"""
    import gstock
    monkeypatch.setattr(gstock, "us_hk_stock", lambda q: {})
    assert client.get("/api/global/stock?symbol=ZZZZ").status_code == 404


def test_gstock_quote_full_null_shape():
    """行情取不到时 `_quote_from({})` 仍返回完整 null 形状（契合 GlobalQuote 类型），不是空 dict。"""
    import gstock
    q = gstock._quote_from({})
    assert set(q) == {"code", "name", "price", "open", "high", "low", "prev_close", "amount", "mcap", "change_pct"}
    assert all(v is None for v in q.values())


def test_cors_preflight_allows_patch():
    """前端自定义方法（如持仓改用的 PATCH 语义）预检需放行。"""
    r = client.options(
        "/api/holdings",
        headers={
            "Origin": "http://127.0.0.1:5899",
            "Access-Control-Request-Method": "PATCH",
        },
    )
    assert r.status_code == 200
    assert "PATCH" in r.headers.get("access-control-allow-methods", "")


def test_fastapi_has_no_legacy_ai_or_agent_control_plane():
    """迁移完成后 FastAPI 只保留数据/业务路由 + 只读 Agent 状态，AI 编排路由全部移除。"""
    assert client.get("/api/health").status_code == 200
    for path in ("/api/chat", "/api/debate", "/api/reflect", "/api/daily-review", "/api/news-digest"):
        assert client.post(path, json={}).status_code == 404, path
    assert client.get("/api/agent/threads").status_code == 404
    # 数据 API 合同不受影响（代表性抽查）
    assert client.get("/api/health").json()["ok"] is True
