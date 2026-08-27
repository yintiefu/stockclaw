"""Task 2：Agent 静态设置加载契约——别名映射、MCP 转换、路径校验与密钥不外泄。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.settings import (
    AgentSettings,
    AgentSettingsError,
    agent_settings_path,
    load_agent_settings,
)


def write_settings(tmp_path: Path, *, skills: Path, mcp: dict | None = None, model: dict | None = None) -> Path:
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({
        "model": model or {
            "provider": "openai",
            "name": "test-model",
            "apiKey": "sk-test",
            "baseURL": "https://example.test/v1",
            "temperature": 0.2,
        },
        "skills": {"path": str(skills)},
        "mcpServers": mcp or {},
    }), encoding="utf-8")
    path.chmod(0o600)
    return path


def test_load_settings_maps_aliases_and_mcp_transports(tmp_path, monkeypatch):
    skills = tmp_path / "skills"
    skills.mkdir()
    path = write_settings(tmp_path, skills=skills, mcp={
        "local": {"transport": "stdio", "command": "python", "args": ["server.py"], "env": {"TOKEN": "plain"}},
        "remote": {"transport": "http", "url": "https://example.test/mcp", "headers": {"Authorization": "Bearer plain"}},
    })
    monkeypatch.setenv("VR_AGENT_SETTINGS", str(path))
    settings = load_agent_settings()
    assert settings.model.api_key.get_secret_value() == "sk-test"
    assert settings.model.base_url == "https://example.test/v1"
    assert settings.mcp_connections()["remote"]["transport"] == "http"
    assert settings.mcp_connections()["local"]["env"]["TOKEN"] == "plain"


def test_default_settings_path_lives_under_vibe_research(monkeypatch):
    monkeypatch.delenv("VR_AGENT_SETTINGS", raising=False)
    assert agent_settings_path() == Path.home() / ".vibe-research" / "agent" / "settings.json"
    monkeypatch.setenv("VR_AGENT_SETTINGS", "~/custom/settings.json")
    assert agent_settings_path() == Path.home() / "custom" / "settings.json"


def test_missing_settings_file_reports_path(tmp_path, monkeypatch):
    monkeypatch.setenv("VR_AGENT_SETTINGS", str(tmp_path / "absent.json"))
    with pytest.raises(AgentSettingsError, match="不存在"):
        load_agent_settings()


def test_load_settings_creates_missing_skill_root_with_0700(tmp_path, monkeypatch):
    root = tmp_path / "new" / "skills"
    path = write_settings(tmp_path, skills=root)
    monkeypatch.setenv("VR_AGENT_SETTINGS", str(path))
    loaded = load_agent_settings()
    assert loaded.skills.path == root.resolve()
    assert root.stat().st_mode & 0o777 == 0o700


def test_load_settings_rejects_filesystem_root(tmp_path, monkeypatch):
    path = write_settings(tmp_path, skills=Path(tmp_path.anchor))
    monkeypatch.setenv("VR_AGENT_SETTINGS", str(path))
    with pytest.raises(AgentSettingsError, match="文件系统根"):
        load_agent_settings()


@pytest.mark.parametrize("payload, needle", [
    ("{", "不是合法 JSON"),
    (json.dumps({"model": {"provider": "anthropic"}}), "model.provider"),
])
def test_invalid_settings_report_path_and_field_without_secret(tmp_path, monkeypatch, payload, needle):
    path = tmp_path / "settings.json"
    path.write_text(payload.replace("anthropic", "anthropic-sk-private"), encoding="utf-8")
    monkeypatch.setenv("VR_AGENT_SETTINGS", str(path))
    with pytest.raises(AgentSettingsError) as caught:
        load_agent_settings()
    message = str(caught.value)
    assert str(path) in message
    assert needle in message
    assert "sk-private" not in message


def test_unknown_fields_are_rejected_without_leaking_values(tmp_path, monkeypatch):
    skills = tmp_path / "skills"
    skills.mkdir()
    payload = {
        "model": {
            "provider": "openai", "name": "test-model", "apiKey": "sk-secret-value",
            "baseURL": "https://example.test/v1", "temperature": 0.2, "extra": "field",
        },
        "skills": {"path": str(skills)},
        "mcpServers": {},
    }
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("VR_AGENT_SETTINGS", str(path))
    with pytest.raises(AgentSettingsError) as caught:
        load_agent_settings()
    assert "model.extra" in str(caught.value)
    assert "sk-secret-value" not in str(caught.value)


def test_loose_permission_emits_stderr_warning(tmp_path, monkeypatch, capsys):
    skills = tmp_path / "skills"
    skills.mkdir()
    path = write_settings(tmp_path, skills=skills)
    path.chmod(0o644)
    monkeypatch.setenv("VR_AGENT_SETTINGS", str(path))
    load_agent_settings()
    err = capsys.readouterr().err
    assert "chmod 600" in err and str(path) in err


def test_tight_permission_stays_quiet(tmp_path, monkeypatch, capsys):
    skills = tmp_path / "skills"
    skills.mkdir()
    path = write_settings(tmp_path, skills=skills)
    monkeypatch.setenv("VR_AGENT_SETTINGS", str(path))
    load_agent_settings()
    assert capsys.readouterr().err == ""


def test_thinking_defaults_off_and_can_enable(tmp_path, monkeypatch):
    skills = tmp_path / "skills"
    skills.mkdir()
    path = write_settings(tmp_path, skills=skills)
    monkeypatch.setenv("VR_AGENT_SETTINGS", str(path))
    assert load_agent_settings().model.thinking is False
    path = write_settings(tmp_path, skills=skills, model={
        "provider": "openai", "name": "test-model",
        "apiKey": "sk-test", "baseURL": "https://example.test/v1",
        "thinking": True,
    })
    monkeypatch.setenv("VR_AGENT_SETTINGS", str(path))
    assert load_agent_settings().model.thinking is True
    path = write_settings(tmp_path, skills=skills, model={
        "provider": "openai", "name": "test-model",
        "apiKey": "sk-test", "baseURL": "https://example.test/v1",
        "thinking": "yes",
    })
    monkeypatch.setenv("VR_AGENT_SETTINGS", str(path))
    with pytest.raises(AgentSettingsError) as caught:
        load_agent_settings()
    assert "model.thinking" in str(caught.value)


def test_trace_defaults_to_enabled_and_default_dir(tmp_path, monkeypatch):
    skills = tmp_path / "skills"
    skills.mkdir()
    path = write_settings(tmp_path, skills=skills)
    monkeypatch.setenv("VR_AGENT_SETTINGS", str(path))
    settings = load_agent_settings()
    assert settings.trace.enabled is True
    assert settings.trace.dir == (Path.home() / ".vibe-research" / "agent" / "traces").resolve()


def test_trace_can_be_disabled_and_dir_expanded(tmp_path, monkeypatch):
    skills = tmp_path / "skills"
    skills.mkdir()
    trace_dir = tmp_path / "traces"
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({
        "model": {
            "provider": "openai", "name": "test-model",
            "apiKey": "sk-test", "baseURL": "https://example.test/v1",
        },
        "skills": {"path": str(skills)},
        "mcpServers": {},
        "trace": {"enabled": False, "dir": str(trace_dir)},
    }), encoding="utf-8")
    path.chmod(0o600)
    monkeypatch.setenv("VR_AGENT_SETTINGS", str(path))
    settings = load_agent_settings()
    assert settings.trace.enabled is False
    assert settings.trace.dir == trace_dir.resolve()


def test_trace_unknown_field_reports_location_without_value(tmp_path, monkeypatch):
    skills = tmp_path / "skills"
    skills.mkdir()
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({
        "model": {
            "provider": "openai", "name": "test-model",
            "apiKey": "sk-test", "baseURL": "https://example.test/v1",
        },
        "skills": {"path": str(skills)},
        "mcpServers": {},
        "trace": {"enabled": True, "level": "verbose"},
    }), encoding="utf-8")
    monkeypatch.setenv("VR_AGENT_SETTINGS", str(path))
    with pytest.raises(AgentSettingsError) as caught:
        load_agent_settings()
    message = str(caught.value)
    assert "trace.level" in message
    assert "verbose" not in message


def test_settings_model_never_prints_secret(tmp_path, monkeypatch):
    skills = tmp_path / "skills"
    skills.mkdir()
    path = write_settings(tmp_path, skills=skills)
    monkeypatch.setenv("VR_AGENT_SETTINGS", str(path))
    settings = load_agent_settings()
    assert "sk-test" not in repr(settings)
    assert "sk-test" not in str(settings.model)
    assert isinstance(settings, AgentSettings)
    dumped = settings.mcp_connections()
    assert dumped == {}
