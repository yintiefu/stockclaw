"""Task 4/5：本地技能管理器——枚举、启停、删除与有界导入，全部使用临时目录。"""
from __future__ import annotations

import base64
import json
import os
import stat
import zipfile
from io import BytesIO
from pathlib import Path

import pytest

from skillmgr import (
    SkillManager,
    SkillManagerError,
    SkillRoots,
    get_skill_manager,
)

VALID_SKILL = "---\nname: sample\ndescription: 用于结构化研究。\n---\n\n# 指令\n"


def write_skill(directory: Path, name: str, *, description: str = "测试技能。") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# 指令\n",
        encoding="utf-8",
    )
    return directory


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


@pytest.fixture
def roots(tmp_path: Path) -> SkillRoots:
    active = tmp_path / "active"
    disabled = tmp_path / "active.disabled"
    active.mkdir()
    disabled.mkdir()
    settings_path = write_settings(tmp_path / "settings.json", active)
    return SkillRoots(settings_path=settings_path, active=active, disabled=disabled)


@pytest.fixture
def manager(tmp_path: Path, roots: SkillRoots, monkeypatch) -> SkillManager:
    builtin = tmp_path / "builtin"
    builtin.mkdir()
    write_skill(builtin / "builtin-skill", "builtin-skill")
    write_skill(builtin / "stock-analysis", "stock-analysis")
    monkeypatch.chdir(tmp_path)
    return SkillManager(builtin, roots=roots)


class TestEnumeration:
    def test_lists_active_and_disabled_with_stable_sort(self, manager: SkillManager) -> None:
        write_skill(manager.active_root / "beta", "beta")
        write_skill(manager.active_root / "alpha", "alpha")
        write_skill(manager.disabled_root / "gamma", "gamma")
        result = manager.list_skills()
        assert [item["name"] for item in result["user"]] == ["alpha", "beta", "gamma"]
        assert [item["enabled"] for item in result["user"]] == [True, True, False]
        assert [item["name"] for item in result["builtin"]] == ["builtin-skill", "stock-analysis"]
        assert result["user_available"] is True
        assert "user_error" not in result

    def test_list_marks_invalid_active_skill_blocked(self, manager: SkillManager) -> None:
        write_skill(manager.active_root / "broken", "mismatched-name")
        result = manager.list_skills()
        item = next(item for item in result["user"] if item["name"] == "broken")
        assert item["enabled"] is True
        assert item["valid"] is False
        assert item["effective"] is False
        assert item["error"] is not None

    def test_list_marks_active_builtin_collision_blocked(self, manager: SkillManager) -> None:
        write_skill(manager.active_root / "stock-analysis", "stock-analysis")
        result = manager.list_skills()
        item = next(item for item in result["user"] if item["name"] == "stock-analysis")
        assert {key: item[key] for key in ("enabled", "valid", "effective")} == {
            "enabled": True, "valid": True, "effective": False,
        }
        assert item["error"] == "与内置技能同名，已阻止加载"

    def test_list_ignores_files_and_invalid_directory_names(self, manager: SkillManager) -> None:
        (manager.active_root / " stray-file.md").write_text("x", encoding="utf-8")
        (manager.active_root / "UPPER").mkdir()
        (manager.disabled_root / "double--dash").mkdir()
        result = manager.list_skills()
        assert result["user"] == []

    def test_list_reports_simultaneous_name_as_blocked(self, manager: SkillManager) -> None:
        write_skill(manager.active_root / "dupe", "dupe")
        write_skill(manager.disabled_root / "dupe", "dupe")
        result = manager.list_skills()
        matches = [item for item in result["user"] if item["name"] == "dupe"]
        assert len(matches) == 1
        assert matches[0]["effective"] is False
        assert matches[0]["error"] is not None

    def test_unavailable_manager_still_lists_builtins(self, tmp_path: Path) -> None:
        builtin = tmp_path / "builtin"
        builtin.mkdir()
        write_skill(builtin / "builtin-skill", "builtin-skill")
        manager = SkillManager(builtin, roots=None, user_error="Agent 设置缺失或无效")
        result = manager.list_skills()
        assert [item["name"] for item in result["builtin"]] == ["builtin-skill"]
        assert result["user"] == []
        assert result["user_available"] is False
        assert result["user_error"] == "Agent 设置缺失或无效"


class TestDetail:
    def test_get_returns_virtual_path_and_instructions(self, manager: SkillManager) -> None:
        write_skill(manager.active_root / "sample", "sample")
        detail = manager.get_skill("user", "sample")
        assert detail["path"] == "/user/sample/SKILL.md"
        assert detail["instructions"] == (manager.active_root / "sample" / "SKILL.md").read_text(encoding="utf-8")
        assert detail["source"] == "user"
        assert detail["enabled"] is True

    def test_get_builtin_detail_uses_builtin_namespace(self, manager: SkillManager) -> None:
        detail = manager.get_skill("builtin", "builtin-skill")
        assert detail["path"].startswith("/builtin/builtin-skill/")
        assert detail["enabled"] is True
        assert detail["error"] is None

    def test_get_invalid_detail_has_null_instructions(self, manager: SkillManager) -> None:
        write_skill(manager.active_root / "broken", "mismatched-name")
        detail = manager.get_skill("user", "broken")
        assert detail["instructions"] is None
        assert detail["error"] is not None
        assert detail["valid"] is False

    def test_get_unknown_skill_raises_not_found(self, manager: SkillManager) -> None:
        with pytest.raises(SkillManagerError) as caught:
            manager.get_skill("user", "absent")
        assert caught.value.kind == "not_found"
        with pytest.raises(SkillManagerError) as caught:
            manager.get_skill("builtin", "absent")
        assert caught.value.kind == "not_found"


class TestToggle:
    def test_set_enabled_moves_between_roots(self, manager: SkillManager) -> None:
        write_skill(manager.disabled_root / "sample", "sample")
        first = manager.set_enabled("sample", True)
        assert (manager.active_root / "sample").is_dir()
        assert not (manager.disabled_root / "sample").exists()
        assert first["enabled"] is True
        second = manager.set_enabled("sample", False)
        assert (manager.disabled_root / "sample").is_dir()
        assert not (manager.active_root / "sample").exists()
        assert second["enabled"] is False

    def test_set_enabled_is_idempotent(self, manager: SkillManager) -> None:
        write_skill(manager.disabled_root / "sample", "sample")
        first = manager.set_enabled("sample", True)
        second = manager.set_enabled("sample", True)
        assert first == second
        assert (manager.active_root / "sample").is_dir()

    def test_set_enabled_rejects_builtin_names(self, manager: SkillManager) -> None:
        with pytest.raises(SkillManagerError) as caught:
            manager.set_enabled("builtin-skill", True)
        assert caught.value.kind in {"bad_request", "not_found"}

    def test_set_enabled_rejects_simultaneous_name(self, manager: SkillManager) -> None:
        write_skill(manager.active_root / "dupe", "dupe")
        write_skill(manager.disabled_root / "dupe", "dupe")
        with pytest.raises(SkillManagerError) as caught:
            manager.set_enabled("dupe", True)
        assert caught.value.kind == "conflict"


class TestDelete:
    def test_delete_removes_user_skill(self, manager: SkillManager) -> None:
        write_skill(manager.active_root / "sample", "sample")
        assert manager.delete("sample") == {"ok": True}
        assert not (manager.active_root / "sample").exists()

    def test_delete_missing_raises_not_found(self, manager: SkillManager) -> None:
        with pytest.raises(SkillManagerError) as caught:
            manager.delete("absent")
        assert caught.value.kind == "not_found"


class TestSnapshot:
    def test_mutation_raises_conflict_when_settings_path_drifts(self, manager: SkillManager, roots: SkillRoots) -> None:
        write_skill(manager.disabled_root / "sample", "sample")
        moved = roots.active.parent / "moved-skills"
        moved.mkdir()
        write_settings(roots.settings_path, moved)
        with pytest.raises(SkillManagerError) as caught:
            manager.set_enabled("sample", True)
        assert caught.value.kind == "conflict"
        assert (manager.disabled_root / "sample").is_dir()

    def test_mutation_allows_unrelated_settings_changes(self, manager: SkillManager, roots: SkillRoots) -> None:
        write_skill(manager.disabled_root / "sample", "sample")
        payload = json.loads(roots.settings_path.read_text(encoding="utf-8"))
        payload["model"]["name"] = "another-model"
        roots.settings_path.write_text(json.dumps(payload), encoding="utf-8")
        manager.set_enabled("sample", True)
        assert (manager.active_root / "sample").is_dir()

    def test_mutation_raises_conflict_when_settings_invalid(self, manager: SkillManager, roots: SkillRoots) -> None:
        write_skill(manager.disabled_root / "sample", "sample")
        roots.settings_path.write_text("{ not json", encoding="utf-8")
        with pytest.raises(SkillManagerError) as caught:
            manager.set_enabled("sample", True)
        assert caught.value.kind == "conflict"

    def test_mutation_raises_conflict_when_settings_missing(self, manager: SkillManager, roots: SkillRoots) -> None:
        write_skill(manager.disabled_root / "sample", "sample")
        roots.settings_path.unlink()
        with pytest.raises(SkillManagerError) as caught:
            manager.set_enabled("sample", True)
        assert caught.value.kind == "conflict"


class TestFactory:
    def test_get_skill_manager_snapshots_process_settings(self, tmp_path: Path, monkeypatch) -> None:
        active = tmp_path / "skills"
        active.mkdir()
        settings_path = write_settings(tmp_path / "settings.json", active)
        monkeypatch.setenv("VR_AGENT_SETTINGS", str(settings_path))
        get_skill_manager.cache_clear()
        manager = get_skill_manager()
        assert manager.active_root == active.resolve()
        get_skill_manager.cache_clear()

    def test_get_skill_manager_falls_back_when_settings_invalid(self, tmp_path: Path, monkeypatch) -> None:
        settings_path = tmp_path / "settings.json"
        settings_path.write_text("{ broken", encoding="utf-8")
        monkeypatch.setenv("VR_AGENT_SETTINGS", str(settings_path))
        get_skill_manager.cache_clear()
        manager = get_skill_manager()
        result = manager.list_skills()
        assert result["user_available"] is False
        assert result["builtin"]
        get_skill_manager.cache_clear()
