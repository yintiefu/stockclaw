"""Task 6：/api/skills 管理路由契约——磁盘操作、稳定错误码与鉴权，全部离线。"""
from __future__ import annotations

import base64
import json
import zipfile
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app as app_module
from skillmgr import SkillManager, SkillRoots

client = TestClient(app_module.app)

VALID_SKILL = "---\nname: sample\ndescription: 用于结构化研究。\n---\n\n# 指令\n"


def write_skill(directory: Path, name: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {name} 技能。\n---\n\n# 指令\n",
        encoding="utf-8",
    )


def write_settings(path: Path, skills_root: Path) -> Path:
    path.write_text(json.dumps({
        "model": {
            "provider": "openai", "name": "test-model",
            "apiKey": "sk-test", "baseURL": "https://example.test/v1",
        },
        "skills": {"path": str(skills_root)},
        "mcpServers": {},
    }), encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def no_api_key(monkeypatch):
    monkeypatch.setattr(app_module, "_API_KEY", "")


@pytest.fixture
def manager(tmp_path: Path) -> SkillManager:
    builtin = tmp_path / "builtin"
    builtin.mkdir()
    write_skill(builtin / "builtin-skill", "builtin-skill")
    active = tmp_path / "active"
    active.mkdir()
    disabled = tmp_path / "active.disabled"
    disabled.mkdir()
    settings = write_settings(tmp_path / "settings.json", active)
    return SkillManager(builtin, roots=SkillRoots(settings_path=settings, active=active, disabled=disabled))


def use_manager(monkeypatch, value: SkillManager) -> None:
    monkeypatch.setattr(app_module, "get_skill_manager", lambda: value)


def b64(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return base64.b64encode(data).decode("ascii")


class TestList:
    def test_skill_list_returns_manager_shape(self, manager, monkeypatch) -> None:
        write_skill(manager.disabled_root / "sample", "sample")
        use_manager(monkeypatch, manager)
        response = client.get("/api/skills")
        assert response.status_code == 200
        body = response.json()
        assert [item["name"] for item in body["builtin"]] == ["builtin-skill"]
        assert [item["name"] for item in body["user"]] == ["sample"]
        assert body["user_available"] is True
        assert set(body["user"][0]) == {
            "name", "description", "source", "enabled", "valid", "effective", "error",
        }

    def test_skill_list_keeps_builtins_when_user_unavailable(self, monkeypatch, tmp_path) -> None:
        builtin = tmp_path / "builtin"
        builtin.mkdir()
        write_skill(builtin / "builtin-skill", "builtin-skill")
        unavailable = SkillManager(builtin, roots=None, user_error="Agent 设置缺失或无效")
        use_manager(monkeypatch, unavailable)
        response = client.get("/api/skills")
        assert response.status_code == 200
        body = response.json()
        assert body["builtin"]
        assert body["user_available"] is False

    def test_skill_routes_require_auth_when_api_key_set(self, manager, monkeypatch) -> None:
        use_manager(monkeypatch, manager)
        monkeypatch.setattr(app_module, "_API_KEY", "test-key")
        assert client.get("/api/skills").status_code == 401
        assert client.get("/api/skills/user/sample").status_code == 401
        assert client.patch("/api/skills/user/sample", json={"enabled": True}).status_code == 401
        assert client.delete("/api/skills/user/sample").status_code == 401
        assert client.post("/api/skills/import", json={}).status_code == 401


class TestDetail:
    def test_user_detail_shape(self, manager, monkeypatch) -> None:
        write_skill(manager.active_root / "sample", "sample")
        use_manager(monkeypatch, manager)
        response = client.get("/api/skills/user/sample")
        assert response.status_code == 200
        body = response.json()
        assert body["path"] == "/user/sample/SKILL.md"
        assert body["instructions"].startswith("---\nname: sample")
        assert body["source"] == "user"

    def test_builtin_detail_shape(self, manager, monkeypatch) -> None:
        use_manager(monkeypatch, manager)
        response = client.get("/api/skills/builtin/builtin-skill")
        assert response.status_code == 200
        assert response.json()["path"].startswith("/builtin/builtin-skill/")

    def test_invalid_detail_has_null_instructions_and_body(self, manager, monkeypatch) -> None:
        write_skill(manager.active_root / "broken", "mismatched-name")
        use_manager(monkeypatch, manager)
        response = client.get("/api/skills/user/broken")
        assert response.status_code == 200
        body = response.json()
        assert body["instructions"] is None
        assert body["description"] is None
        assert body["error"] is not None

    def test_unknown_skill_maps_to_404(self, manager, monkeypatch) -> None:
        use_manager(monkeypatch, manager)
        assert client.get("/api/skills/user/absent").status_code == 404
        assert client.get("/api/skills/builtin/absent").status_code == 404

    def test_invalid_source_maps_to_400(self, manager, monkeypatch) -> None:
        use_manager(monkeypatch, manager)
        assert client.get("/api/skills/other/sample").status_code == 400


class TestPatchAndDelete:
    def test_skill_patch_is_idempotent(self, manager, monkeypatch) -> None:
        write_skill(manager.disabled_root / "sample", "sample")
        use_manager(monkeypatch, manager)
        first = client.patch("/api/skills/user/sample", json={"enabled": True})
        second = client.patch("/api/skills/user/sample", json={"enabled": True})
        assert first.status_code == second.status_code == 200
        assert first.json() == second.json()
        assert (manager.active_root / "sample").is_dir()

    def test_skill_patch_rejects_builtin(self, manager, monkeypatch) -> None:
        use_manager(monkeypatch, manager)
        response = client.patch("/api/skills/user/builtin-skill", json={"enabled": True})
        assert response.status_code == 400

    def test_skill_patch_unknown_maps_to_404(self, manager, monkeypatch) -> None:
        use_manager(monkeypatch, manager)
        assert client.patch("/api/skills/user/absent", json={"enabled": True}).status_code == 404

    def test_skill_delete_removes_and_404_after(self, manager, monkeypatch) -> None:
        write_skill(manager.disabled_root / "sample", "sample")
        use_manager(monkeypatch, manager)
        assert client.delete("/api/skills/user/sample").status_code == 200
        assert client.delete("/api/skills/user/sample").status_code == 404

    def test_conflict_maps_to_409(self, manager, monkeypatch) -> None:
        write_skill(manager.active_root / "dupe", "dupe")
        write_skill(manager.disabled_root / "dupe", "dupe")
        use_manager(monkeypatch, manager)
        assert client.patch("/api/skills/user/dupe", json={"enabled": False}).status_code == 409


class TestImport:
    def test_import_folder_defaults_disabled(self, manager, monkeypatch) -> None:
        use_manager(monkeypatch, manager)
        payload = {
            "kind": "folder",
            "files": [{"path": "sample/SKILL.md", "content_b64": b64(VALID_SKILL)}],
        }
        response = client.post("/api/skills/import", json=payload)
        assert response.status_code == 200
        assert response.json()["enabled"] is False
        assert (manager.disabled_root / "sample/SKILL.md").is_file()

    def test_import_zip_defaults_disabled(self, manager, monkeypatch) -> None:
        use_manager(monkeypatch, manager)
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("sample/SKILL.md", VALID_SKILL)
        response = client.post("/api/skills/import", json={
            "kind": "zip", "filename": "sample.zip", "content_b64": b64(buffer.getvalue()),
        })
        assert response.status_code == 200
        assert (manager.disabled_root / "sample/SKILL.md").is_file()

    def test_import_rejects_unknown_kind(self, manager, monkeypatch) -> None:
        use_manager(monkeypatch, manager)
        response = client.post("/api/skills/import", json={"kind": "git", "url": "https://x"})
        assert response.status_code == 400

    def test_import_rejects_malformed_body(self, manager, monkeypatch) -> None:
        use_manager(monkeypatch, manager)
        response = client.post(
            "/api/skills/import",
            content=b"{ not json",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 400

    def test_import_rejects_oversize_body(self, manager, monkeypatch) -> None:
        use_manager(monkeypatch, manager)
        huge = "A" * (skillmgr_max_body() + 1024)
        response = client.post("/api/skills/import", json={
            "kind": "folder",
            "files": [{"path": "sample/SKILL.md", "content_b64": huge}],
        })
        assert response.status_code == 413


def skillmgr_max_body() -> int:
    import skillmgr

    return skillmgr.MAX_BODY_BYTES
