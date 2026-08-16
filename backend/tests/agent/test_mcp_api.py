"""Task 1C-10：MCP 管理 REST（7 路由）+ 切片 2 Graph 零 MCP alias。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import agent.router as router_module
from agent.router import build_services
from agent.stores import AgentPaths

FIXTURE = Path(__file__).parent / "fake_mcp_server.py"
NOW = "2026-08-16T00:00:00Z"
pytestmark = pytest.mark.asyncio


@pytest.fixture()
def api(tmp_path, monkeypatch):
    services = build_services(tmp_path / "agent")
    monkeypatch.setattr(router_module, "services", services)
    client = TestClient(__import__("app").app, client=("127.0.0.1", 50001))
    return client, services


def stdio_payload(server_id="fixture") -> dict:
    return {
        "revision": 0,
        "server": {
            "id": server_id,
            "display_name": "本地夹具",
            "enabled": True,
            "transport": {
                "type": "stdio",
                "executable": sys.executable,
                "args": [str(FIXTURE)],
                "env": {},
            },
        },
    }


async def add_server(client, payload=None) -> dict:
    resp = await client_acall(client, "post", "/api/agent/mcp", stdio_payload() if payload is None else payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def client_acall(client, method, url, json=None):
    return getattr(client, method)(url, json=json)


# ---------------------------------------------------------------------------
# 七个路由
# ---------------------------------------------------------------------------

async def test_get_empty_document(api):
    client, _ = api
    resp = client.get("/api/agent/mcp")
    assert resp.status_code == 200
    assert resp.json() == {"schema_version": 1, "revision": 0, "servers": []}


async def test_add_does_not_spawn_and_increments_revision(api):
    client, services = api
    body = await add_server(client)
    assert body["revision"] == 1
    assert body["servers"][0]["id"] == "fixture"
    assert services.registry.process_count == 0


async def test_add_conflicting_id_returns_409(api):
    client, _ = api
    await add_server(client)
    resp = client.post("/api/agent/mcp", json=stdio_payload())
    assert resp.status_code == 409


async def test_patch_updates_with_revision_cas(api):
    client, _ = api
    body = await add_server(client)
    patch = {
        "revision": body["revision"],
        "server": {"display_name": "改名后的夹具"},
    }
    resp = client.patch("/api/agent/mcp/fixture", json=patch)
    assert resp.status_code == 200
    assert resp.json()["servers"][0]["display_name"] == "改名后的夹具"
    stale = client.patch("/api/agent/mcp/fixture", json=patch)
    assert stale.status_code == 409
    assert stale.json()["code"] == "MCP_REVISION_CONFLICT"


async def test_delete_removes_server(api):
    client, _ = api
    body = await add_server(client)
    resp = client.delete("/api/agent/mcp/fixture", params={"revision": body["revision"]})
    assert resp.status_code == 200
    assert client.get("/api/agent/mcp").json()["servers"] == []


async def test_trust_flow(api):
    client, services = api
    body = await add_server(client)
    resp = client.post("/api/agent/mcp/fixture/trust", json={
        "revision": body["revision"], "fingerprint": "stale",
    })
    assert resp.status_code == 409
    assert resp.json()["code"] == "STDIO_FINGERPRINT_MISMATCH"
    # 从错误响应/预览取得正确 fingerprint
    preview = resp.json().get("preview") or {}
    fingerprint = preview.get("fingerprint")
    assert fingerprint
    body = client.get("/api/agent/mcp").json()
    ok = client.post("/api/agent/mcp/fixture/trust", json={
        "revision": body["revision"], "fingerprint": fingerprint,
    })
    assert ok.status_code == 200, ok.text
    assert ok.json()["servers"][0]["trust_fingerprint"] == fingerprint
    await services.registry.shutdown()


async def test_endpoint_requires_trust_and_reports_preview(api):
    client, services = api
    await add_server(client)
    resp = client.post("/api/agent/mcp/fixture/test", json={"revision": 1})
    assert resp.status_code == 409
    body = resp.json()
    assert body["code"] == "STDIO_TRUST_REQUIRED"
    assert body["preview"]["executable"] == sys.executable
    assert body["preview"]["args"] == [str(FIXTURE)]
    assert services.registry.process_count == 0


async def test_refresh_writes_catalog_with_revision(api):
    client, services = api
    await add_server(client)
    doc = client.get("/api/agent/mcp").json()
    preview = client.post("/api/agent/mcp/fixture/trust", json={
        "revision": doc["revision"], "fingerprint": "x"}).json()["preview"]
    doc = client.get("/api/agent/mcp").json()
    client.post("/api/agent/mcp/fixture/trust", json={
        "revision": doc["revision"], "fingerprint": preview["fingerprint"]})
    doc = client.get("/api/agent/mcp").json()
    resp = client.post("/api/agent/mcp/fixture/refresh", json={"revision": doc["revision"]})
    assert resp.status_code == 200, resp.text
    server = resp.json()["servers"][0]
    names = {t["original_name"] for t in server["tools"]}
    assert "echo" in names
    assert all(not t["enabled"] for t in server["tools"])  # 首次默认 disabled
    # 修改后 revision 前进
    assert resp.json()["revision"] > doc["revision"]
    # tool enable 路由（PATCH 复用）：启用 echo
    revision = resp.json()["revision"]
    enable = client.patch("/api/agent/mcp/fixture", json={
        "revision": revision,
        "tool_enabled": {"echo": True},
    })
    assert enable.status_code == 200
    tools = {t["original_name"]: t["enabled"] for t in enable.json()["servers"][0]["tools"]}
    assert tools["echo"] is True
    await services.registry.shutdown()


async def test_missing_server_404(api):
    client, _ = api
    resp = client.post("/api/agent/mcp/ghost/test", json={"revision": 0})
    assert resp.status_code == 404
    assert resp.json()["code"] == "MCP_SERVER_NOT_FOUND"


async def test_corrupt_document_returns_500_with_code(api, tmp_path):
    client, services = api
    services.paths.mcp_config.parent.mkdir(parents=True, exist_ok=True)
    services.paths.mcp_config.write_text("{broken", encoding="utf-8")
    resp = client.get("/api/agent/mcp")
    assert resp.status_code == 500
    assert resp.json()["code"] == "MCP_CONFIG_CORRUPT"


# ---------------------------------------------------------------------------
# 切片 2 隔离：run 不暴露 MCP alias
# ---------------------------------------------------------------------------

async def test_slice_2_run_exposes_no_mcp_alias(api):
    client, services = api
    from agent.capabilities import CapabilityPreview, CapabilityResolver

    # 配置一个健康且启用的 MCP 工具目录（绕过路由直接操作 registry）
    await services.registry.add(__import__("agent.mcp", fromlist=["McpServer"]).McpServer.model_validate(
        stdio_payload()["server"]))
    preview = (await services.registry.trust_preview("fixture")).fingerprint
    await services.registry.trust("fixture", preview, services.registry.store.load().revision)
    catalog = await services.registry.refresh("fixture")
    revision = services.registry.store.load().revision
    await services.registry.patch_server("fixture", revision, lambda s: s.model_copy(update={
        "tools": [t.model_copy(update={"enabled": n == "echo"}) for n, t in
                  zip([x.original_name for x in s.tools], s.tools)],
        "enabled": True,
    }))
    try:
        resolver = CapabilityResolver(services.skills)
        lease = await resolver.acquire(CapabilityPreview(
            thread_id="th-1", thread_revision=0, selected_skills=()))
        names = {t.name for t in lease.tools}
        assert all(not name.startswith("mcp__") for name in names)
        lease.release()
    finally:
        await services.registry.shutdown()
