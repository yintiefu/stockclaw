"""1B 线程/运行 REST 契约：CRUD、revision 冲突、busy 删除、启动对账。"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import agent.router as router_module
from agent.router import build_services
from agent.models import ModelRef, RunDocument, ThreadDocument
from agent.stores import AgentPaths, RunStore, ThreadStore, utc_now

NOW = "2026-08-15T12:00:00Z"


@pytest.fixture
def api(tmp_path, monkeypatch):
    services = build_services(tmp_path / "agent")
    monkeypatch.setattr(router_module, "services", services)
    client = TestClient(__import__("app").app, client=("127.0.0.1", 50001))
    return client, services


def seed_run(services, run_id="run-1", thread_id="thread-1", status="completed"):
    run = RunDocument.start(
        run_id=run_id,
        thread_id=thread_id,
        protocol_run_id=f"protocol-{run_id}",
        model_ref=ModelRef(provider="fixture", baseURL="https://example.com/v1", model="fixture-model"),
        trigger_message_id="user-1",
        history_head_id="user-1",
        now=NOW,
    )
    services.runs.replace(run.model_copy(update={"status": status}))
    return run


def test_create_thread_returns_201_revision_zero(api):
    client, _ = api
    resp = client.post("/api/agent/threads", json={"title": "现金流研究"})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["revision"] == 0
    assert body["title"] == "现金流研究"
    assert body["messages"] == []
    assert body["schema_version"] == 1


def test_list_threads_sorted_desc_with_recovery_warnings(api):
    client, services = api
    services.threads.create(ThreadDocument.new("th-old", "旧", now=NOW).model_copy(
        update={"updated_at": "2026-08-14T00:00:00Z"}))
    services.threads.create(ThreadDocument.new("th-new", "新", now=NOW).model_copy(
        update={"updated_at": "2026-08-15T12:00:00Z"}))
    broken = services.paths.threads / "th-bad.json"
    broken.parent.mkdir(parents=True, exist_ok=True)
    broken.write_text("{broken", encoding="utf-8")

    resp = client.get("/api/agent/threads")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [t["id"] for t in body["threads"]] == ["th-new", "th-old"]
    assert len(body["warnings"]) == 1
    warning = body["warnings"][0]
    assert warning["code"] == "DOCUMENT_CORRUPT"
    assert warning["document_type"] == "thread"
    assert warning["filename"].startswith("th-bad.json.corrupt-")
    # 损坏文件绝不泄漏绝对路径
    assert "/" not in warning["filename"]


def test_get_thread_returns_authoritative_messages(api):
    client, services = api
    doc = ThreadDocument.new("th-1", "研究", now=NOW).model_copy(update={"messages": [
        {"id": "u1", "role": "user", "content": "问题"},
        {"id": "a1", "role": "assistant", "content": "部分", "partial": True},
    ]})
    services.threads.create(doc)

    resp = client.get("/api/agent/threads/th-1")
    assert resp.status_code == 200
    body = resp.json()
    assert [m["id"] for m in body["messages"]] == ["u1", "a1"]
    assert body["messages"][1]["partial"] is True


def test_patch_title_with_current_revision_advances(api):
    client, services = api
    services.threads.create(ThreadDocument.new("th-1", "旧标题", now=NOW))

    resp = client.patch("/api/agent/threads/th-1", json={"revision": 0, "title": "现金流与资本开支"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["title"] == "现金流与资本开支"
    assert resp.json()["revision"] == 1

    stale = client.patch("/api/agent/threads/th-1", json={"revision": 0, "title": "再次改名"})
    assert stale.status_code == 409
    assert stale.json()["code"] == "THREAD_REVISION_CONFLICT"


def test_patch_title_during_active_run_updates_handle_revision(api):
    client, services = api
    services.threads.create(ThreadDocument.new("th-1", "标题", now=NOW))
    coordinator = services.coordinator
    handle = coordinator._make_running_handle("th-1")
    handle.thread_revision = 0
    coordinator._handles["th-1"] = handle

    resp = client.patch("/api/agent/threads/th-1", json={"revision": 0, "title": "运行中改名"})
    assert resp.status_code == 200, resp.text
    assert handle.thread_revision == 1
    assert coordinator.active("th-1") is handle


def test_delete_idle_thread_removes_run_files(api):
    client, services = api
    services.threads.create(ThreadDocument.new("th-1", "待删", now=NOW))
    seed_run(services, run_id="run-1", thread_id="th-1")
    seed_run(services, run_id="run-2", thread_id="th-1")

    revision = services.threads.get("th-1").revision
    resp = client.request("DELETE", "/api/agent/threads/th-1", json={"revision": revision})
    assert resp.status_code == 204
    assert not (services.paths.threads / "th-1.json").exists()
    assert not (services.paths.runs / "run-1.json").exists()
    assert not (services.paths.runs / "run-2.json").exists()
    assert client.get("/api/agent/threads/th-1").status_code == 404


def test_delete_active_thread_conflicts(api):
    client, services = api
    services.threads.create(ThreadDocument.new("th-1", "活跃", now=NOW))
    coordinator = services.coordinator
    coordinator._handles["th-1"] = coordinator._make_running_handle("th-1")

    resp = client.request("DELETE", "/api/agent/threads/th-1", json={"revision": 0})
    assert resp.status_code == 409
    assert resp.json()["code"] == "THREAD_BUSY"
    assert (services.paths.threads / "th-1.json").exists()


def test_get_run_returns_persisted_document(api):
    client, services = api
    seed_run(services, run_id="run-1", thread_id="th-1")
    resp = client.get("/api/agent/runs/run-1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "run-1"
    assert body["thread_id"] == "th-1"
    assert body["protocol_run_ids"] == ["protocol-run-1"]
    assert "api_key" not in resp.text.lower()


def test_missing_thread_is_structured_404(api):
    client, _ = api
    resp = client.get("/api/agent/threads/nope")
    assert resp.status_code == 404
    assert resp.json()["code"] == "DOCUMENT_NOT_FOUND"


def test_directly_read_corrupt_document_is_structured_500(api):
    client, services = api
    services.threads.create(ThreadDocument.new("th-1", "占位", now=NOW))
    (services.paths.threads / "th-1.json").write_text("{broken", encoding="utf-8")

    resp = client.get("/api/agent/threads/th-1")
    assert resp.status_code == 500
    assert resp.json()["code"] == "DOCUMENT_CORRUPT"


def test_invalid_path_id_rejected_before_store_access(api):
    client, services = api
    resp = client.get("/api/agent/threads/" + "x" * 129)
    assert resp.status_code == 400
    assert resp.json()["code"] == "INVALID_DOCUMENT_ID"
    assert not (services.paths.threads / ("x" * 129 + ".json")).exists()
    assert not list(services.paths.threads.glob("**/escape*"))


def test_app_lifespan_reconciles_injected_services(tmp_path, monkeypatch):
    import app as app_module

    services = build_services(tmp_path / "agent")
    monkeypatch.setattr(router_module, "services", services)
    services.threads.create(ThreadDocument.new("th-1", "活跃", now=NOW))
    run = RunDocument.start(
        run_id="run-active",
        thread_id="th-1",
        protocol_run_id="protocol-run-active",
        model_ref=ModelRef(provider="fixture", baseURL="https://example.com/v1", model="fixture-model"),
        trigger_message_id="user-1",
        history_head_id="user-1",
        now=NOW,
    )
    services.runs.replace(run)

    with TestClient(app_module.app, client=("127.0.0.1", 50002)) as client:
        assert client.get("/api/health").status_code == 200

    reconciled = services.runs.get("run-active")
    assert reconciled.status == "interrupted"
    assert reconciled.error_code == "BACKEND_RESTARTED"
    assert services.threads.get("th-1").last_run.status == "interrupted"


def test_delete_with_stale_revision_is_409(api):
    client, services = api
    services.threads.create(ThreadDocument.new("th-1", "待删", now=NOW))
    services.threads.update("th-1", 0, lambda d: d)  # revision → 1
    resp = client.request("DELETE", "/api/agent/threads/th-1", json={"revision": 0})
    assert resp.status_code == 409
    assert resp.json()["code"] == "THREAD_REVISION_CONFLICT"
    assert (services.paths.threads / "th-1.json").exists()
