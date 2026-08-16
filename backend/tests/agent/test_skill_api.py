"""Task 1C-4：Skill REST 契约 + thread selected_skills revision CAS。"""
from __future__ import annotations

import io
import zipfile

import pytest
from fastapi.testclient import TestClient

import agent.router as router_module
from agent.router import build_services

NOW = "2026-08-16T00:00:00Z"
SKILL_MD = """---
name: quality
description: 质检技能
---

# quality

做质检
"""


@pytest.fixture
def api(tmp_path, monkeypatch):
    services = build_services(tmp_path / "agent")
    # 测试隔离：立即在临时根上预扫描（启动钩子在 lifespan 内才执行）
    services.skills.refresh()
    monkeypatch.setattr(router_module, "services", services)
    client = TestClient(__import__("app").app, client=("127.0.0.1", 50001))
    return client, services


def skill_zip(name: str = "quality", body: str = "做质检") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"{name}/SKILL.md",
                    f"---\nname: {name}\ndescription: {name} 技能\n---\n\n# {name}\n\n{body}")
        zf.writestr(f"{name}/references/note.md", "参考")
    return buf.getvalue()


def import_skill(client, name="quality", body="做质检"):
    return client.post(
        "/api/agent/skills/import",
        files={"archive": (f"{name}.zip", skill_zip(name, body), "application/zip")},
    )


# ---------------------------------------------------------------------------
# 六个路由
# ---------------------------------------------------------------------------

def test_import_and_list_and_detail(api):
    client, _ = api
    resp = import_skill(client)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["record"]["name"] == "quality"
    assert body["record"]["valid"] is True

    listed = client.get("/api/agent/skills").json()
    assert [s["directory"] for s in listed["skills"]] == ["quality"]
    assert listed["generation"] >= 1

    detail = client.get("/api/agent/skills/quality").json()
    assert detail["name"] == "quality"
    assert detail["instructions"].endswith("做质检")
    assert {f["relative_path"] for f in detail["files"]} == {"SKILL.md", "references/note.md"}


def test_import_conflict_and_overwrite_with_digest(api):
    client, _ = api
    import_skill(client, body="old")
    conflict = import_skill(client, body="new")
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "SKILL_CONFLICT"

    digest = client.get("/api/agent/skills/quality").json()["digest"]
    stale = client.post(
        "/api/agent/skills/import",
        files={"archive": ("quality.zip", skill_zip(body="new"), "application/zip")},
        data={"overwrite": "true", "expected_digest": "stale"},
    )
    assert stale.status_code == 409
    ok = client.post(
        "/api/agent/skills/import",
        files={"archive": ("quality.zip", skill_zip(body="new"), "application/zip")},
        data={"overwrite": "true", "expected_digest": digest},
    )
    assert ok.status_code == 200, ok.text
    assert "new" in client.get("/api/agent/skills/quality").json()["instructions"]


def test_refresh_regenerates_generation(api):
    client, _ = api
    import_skill(client)
    g1 = client.get("/api/agent/skills").json()["generation"]
    resp = client.post("/api/agent/skills/refresh")
    assert resp.status_code == 200
    assert resp.json()["generation"] > g1


def test_delete_with_digest_cas(api):
    client, _ = api
    import_skill(client)
    digest = client.get("/api/agent/skills/quality").json()["digest"]
    bad = client.delete("/api/agent/skills/quality", params={"expected_digest": "stale"})
    assert bad.status_code == 409
    ok = client.delete("/api/agent/skills/quality", params={"expected_digest": digest})
    assert ok.status_code == 200
    assert client.get("/api/agent/skills/quality").status_code == 404


def test_file_download_with_safe_headers(api):
    client, _ = api
    import_skill(client)
    resp = client.get("/api/agent/skills/quality/files/references/note.md")
    assert resp.status_code == 200, resp.text
    assert resp.text == "参考"
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert "attachment" in resp.headers.get("content-disposition", "") or \
        "inline" in resp.headers.get("content-disposition", "")
    assert "sandbox" in resp.headers.get("content-security-policy", "")


def test_scripts_and_unlisted_paths_403(api):
    client, _ = api
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("quality/SKILL.md",
                    "---\nname: quality\ndescription: d\n---\n\nx")
        zf.writestr("quality/scripts/run.py", "print('no')")
    resp = client.post("/api/agent/skills/import",
                       files={"archive": ("q.zip", buf.getvalue(), "application/zip")})
    assert resp.status_code == 200
    forbidden = client.get("/api/agent/skills/quality/files/scripts/run.py")
    assert forbidden.status_code == 403
    assert forbidden.json()["code"] == "SKILL_RESOURCE_FORBIDDEN"
    missing = client.get("/api/agent/skills/quality/files/references/nope.md")
    assert missing.status_code in (403, 404)


def test_detail_missing_skill_404(api):
    client, _ = api
    resp = client.get("/api/agent/skills/ghost")
    assert resp.status_code == 404
    assert resp.json()["code"] == "SKILL_UNAVAILABLE"


def test_import_rejects_broken_archive_without_residue(api):
    client, services = api
    buf = io.BytesIO()
    buf.write(b"not a zip")
    resp = client.post("/api/agent/skills/import",
                       files={"archive": ("bad.zip", buf.getvalue(), "application/zip")})
    assert resp.status_code == 400
    assert resp.json()["code"] == "SKILL_ARCHIVE_REJECTED"
    assert not list(services.paths.skills.glob(".skill-upload-*.tmp"))
    assert not list(services.paths.skills.glob(".skill-import-*.tmp"))


def test_invalid_skill_import_reports_skill_invalid(api):
    client, _ = api
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("broken/SKILL.md", "---\nname: Broken_Name\ndescription: d\n---\n\nx")
    resp = client.post("/api/agent/skills/import",
                       files={"archive": ("broken.zip", buf.getvalue(), "application/zip")})
    assert resp.status_code == 400
    assert resp.json()["code"] == "SKILL_ARCHIVE_REJECTED"


# ---------------------------------------------------------------------------
# thread selected_skills CAS
# ---------------------------------------------------------------------------

def test_patch_selected_skills_uses_one_revision_and_rejects_missing(api):
    client, _ = api
    import_skill(client)
    thread = client.post("/api/agent/threads", json={"title": "研究"}).json()
    response = client.patch(f"/api/agent/threads/{thread['id']}", json={
        "revision": thread["revision"], "selected_skills": ["quality"],
    })
    assert response.status_code == 200
    assert response.json()["selected_skills"] == ["quality"]
    assert response.json()["revision"] == thread["revision"] + 1


def test_patch_selected_skills_rejects_unknown_name(api):
    client, _ = api
    thread = client.post("/api/agent/threads", json={"title": "研究"}).json()
    response = client.patch(f"/api/agent/threads/{thread['id']}", json={
        "revision": thread["revision"], "selected_skills": ["ghost"],
    })
    assert response.status_code == 404
    assert response.json()["code"] == "SKILL_UNAVAILABLE"


def test_patch_requires_change_payload(api):
    client, _ = api
    thread = client.post("/api/agent/threads", json={"title": "研究"}).json()
    response = client.patch(f"/api/agent/threads/{thread['id']}", json={
        "revision": thread["revision"],
    })
    assert response.status_code == 422  # pydantic model_validator


def test_title_only_patch_still_works(api):
    client, _ = api
    thread = client.post("/api/agent/threads", json={"title": "研究"}).json()
    response = client.patch(f"/api/agent/threads/{thread['id']}", json={
        "revision": thread["revision"], "title": "改名",
    })
    assert response.status_code == 200
    assert response.json()["title"] == "改名"
    assert response.json()["selected_skills"] == []
