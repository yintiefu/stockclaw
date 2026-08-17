"""1D Task 12：线程范围 Artifact REST、删除 tombstone 与启动对账。"""

from __future__ import annotations

import json
import tempfile

import pytest
from starlette.testclient import TestClient

import app as app_module
import agent.router as router_module
from agent.router import build_services
from agent.models import ArtifactDocument, MarkdownContent, TableContent, TableColumn
from tests.agent.conftest import enter_single_loop_client


@pytest.fixture()
def seeded(monkeypatch, tmp_path):
    services = build_services(tempfile.mkdtemp())
    monkeypatch.setattr(router_module, "services", services)
    thread_dir = services.paths.threads
    thread_dir.mkdir(parents=True, exist_ok=True)
    (thread_dir / "thread-a.json").write_text(json.dumps({
        "schema_version": 1, "id": "thread-a", "title": "t", "created_at": "n",
        "updated_at": "n", "revision": 3, "artifact_ids": ["artifact-1", "artifact-2"],
    }, ensure_ascii=False), encoding="utf-8")
    artifact_dir = services.paths.artifacts_dir / "thread-a"
    artifact_dir.mkdir(parents=True)
    for artifact_id, doc_type, content in [
        ("artifact-1", "markdown", MarkdownContent(markdown="# 摘录").model_dump()),
        ("artifact-2", "table", TableContent(
            columns=[TableColumn(key="k", label="列")], rows=[{"k": "v"}]).model_dump()),
    ]:
        (artifact_dir / f"{artifact_id}.json").write_text(json.dumps({
            "schema_version": 1, "id": artifact_id, "thread_id": "thread-a",
            "run_id": "run-1", "type": doc_type, "title": "标题", "created_at": "n",
            "parent_artifact_id": None, "content": content, "source_ids": [],
        }, ensure_ascii=False) + "\n", encoding="utf-8")
    return services


@pytest.fixture()
def client(seeded):
    return enter_single_loop_client(TestClient(app_module.app, client=("127.0.0.1", 50080)))


def test_list_detail_download_and_delete_contract(seeded, client, monkeypatch):
    # download 不扫描其他 thread 文档
    def forbidden_scan(*args, **kwargs):
        raise AssertionError("不得扫描全部 thread 文档")

    monkeypatch.setattr(seeded.threads, "list_documents", forbidden_scan)

    listed = client.get("/api/agent/threads/thread-a/artifacts")
    assert listed.status_code == 200
    payload = listed.json()
    assert {item["id"] for item in payload["artifacts"]} == {"artifact-1", "artifact-2"}

    detail = client.get("/api/agent/threads/thread-a/artifacts/artifact-1")
    assert detail.status_code == 200
    assert detail.json()["type"] == "markdown"

    # 未引用 → 404
    missing = client.get("/api/agent/threads/thread-a/artifacts/artifact-9")
    assert missing.status_code == 404

    download = client.get("/api/agent/threads/thread-a/artifacts/artifact-1/download")
    assert download.status_code == 200
    assert download.headers["content-type"].startswith("text/markdown")
    assert download.headers["x-content-type-options"] == "nosniff"
    assert "default-src 'none'" in download.headers["content-security-policy"]
    assert download.headers["content-disposition"] == 'attachment; filename="artifact-1.md"'
    assert "标题" not in download.headers["content-disposition"]
    table_download = client.get("/api/agent/threads/thread-a/artifacts/artifact-2/download")
    assert table_download.headers["content-type"].startswith("application/json")
    assert table_download.headers["content-disposition"].endswith('artifact-2.json"')

    # 删除：stale revision → 409；正确 revision → 引用先行移除
    stale = client.request("DELETE", "/api/agent/threads/thread-a/artifacts/artifact-1",
                           json={"thread_revision": 1})
    assert stale.status_code == 409
    deleted = client.request("DELETE", "/api/agent/threads/thread-a/artifacts/artifact-1",
                             json={"thread_revision": 3})
    assert deleted.status_code == 200
    assert deleted.json()["thread_revision"] == 4
    assert not (seeded.paths.artifacts_dir / "thread-a" / "artifact-1.json").exists()
    refreshed = seeded.threads.get("thread-a")
    assert refreshed.artifact_ids == ["artifact-2"]


def test_thread_delete_tombstone_and_recovery(seeded, client):
    deleted = client.request("DELETE", "/api/agent/threads/thread-a", json={"revision": 3})
    assert deleted.status_code == 204
    # thread、runs 与 artifacts 目录都被清掉
    assert not (seeded.paths.threads / "thread-a.json").exists()
    assert not (seeded.paths.artifacts_dir / "thread-a").exists()
    assert list((seeded.paths.artifacts_dir).glob("thread-a*")) == []


def test_restart_reconciliation_preserves_healthy_artifacts(seeded, client):
    from agent.stores import reconcile_agent_data, reconcile_artifacts

    reconcile_agent_data(seeded.paths, seeded.threads, seeded.runs)
    warnings = reconcile_artifacts(seeded.paths, seeded.threads, seeded.artifacts_service.store)
    # 健康历史 Artifact 原样保留
    raw = (seeded.paths.artifacts_dir / "thread-a" / "artifact-1.json").read_bytes()
    stored = seeded.artifacts_service.store.get("thread-a", "artifact-1")
    assert stored.id == "artifact-1"
    assert raw.endswith(b"\n")
    assert seeded.threads.get("thread-a").artifact_ids == ["artifact-1", "artifact-2"]
    # 缺失引用与 orphan 只产生告警
    (seeded.paths.artifacts_dir / "thread-a" / "artifact-2.json").unlink()
    warnings = reconcile_artifacts(seeded.paths, seeded.threads, seeded.artifacts_service.store)
    assert any(w.code == "ARTIFACT_MISSING_REF" and "artifact-2" in w.filename for w in warnings)
    # staging 残留（无最终文件）被清理；有最终文件的只告警
    leftover = seeded.paths.artifacts_dir / "thread-a" / "artifact-7.abcdef0123456789abcdef0123456789.artifact.tmp"
    leftover.write_text("{}", encoding="utf-8")
    withfinal = seeded.paths.artifacts_dir / "thread-a" / "artifact-1.abcdef0123456789abcdef0123456789.artifact.tmp"
    withfinal.write_text("{}", encoding="utf-8")
    reconcile_artifacts(seeded.paths, seeded.threads, seeded.artifacts_service.store)
    assert not leftover.exists()
    assert withfinal.exists()
