"""Agent 只读状态摘要：脱敏契约测试。

关键红线：
- 永不回读 / 序列化模型 API Key 或 MCP header / env 密钥；
- 未配置 / 配置无效时返回安全形状（configured=false、计数为 0、字段为 null、中文原因）；
- 模板中的 api_key 只能是显式占位符 YOUR_API_KEY。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.settings import AgentSettingsError, agent_status_summary, load_agent_settings


def _write_settings(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _valid_payload(skills_dir: Path, mcp: dict | None = None) -> dict:
    return {
        "model": {
            "provider": "openai",
            "name": "test-model",
            "apiKey": "sk-live-secret-do-not-leak",
            "baseURL": "https://example.invalid/v1",
            "temperature": 0.2,
        },
        "skills": {"path": str(skills_dir)},
        "mcpServers": mcp or {},
        "trace": {"enabled": False},
    }


EXPECTED_TEMPLATE = (
    '{\n  "model": {\n    "provider": "openai",\n    "name": "your-model",\n'
    '    "apiKey": "YOUR_API_KEY",\n'
    '    "baseURL": "https://your-provider.example/v1"\n  },\n'
    '  "skills": {\n    "path": "~/.vibe-research/agent/skills"\n  },\n'
    '  "mcpServers": {}\n}'
)


def test_summary_configured_exact_payload(tmp_path: Path) -> None:
    skills = tmp_path / "skills"
    skills.mkdir()
    settings = tmp_path / "settings.json"
    _write_settings(settings, _valid_payload(skills))

    summary = agent_status_summary(load_agent_settings(settings), path=settings)

    assert summary == {
        "configured": True,
        "settings_path": str(settings),
        "model_name": "test-model",
        "base_url_host": "example.invalid",
        "builtin_skill_count": 5,
        "mcp_server_count": 0,
        "restart_required": True,
        "config_template": EXPECTED_TEMPLATE,
    }


def test_summary_counts_configured_mcp_servers(tmp_path: Path) -> None:
    skills = tmp_path / "skills"
    skills.mkdir()
    settings = tmp_path / "settings.json"
    _write_settings(settings, _valid_payload(skills, mcp={
        "search": {"transport": "http", "url": "https://mcp.example/s", "headers": {"Authorization": "Bearer mcp-header-secret"}},
        "fs": {"transport": "stdio", "command": "npx", "args": ["-y", "fs-mcp"], "env": {"TOKEN": "mcp-env-secret"}},
    }))

    summary = agent_status_summary(load_agent_settings(settings))

    assert summary["mcp_server_count"] == 2
    assert summary["configured"] is True


def test_summary_never_leaks_secrets(tmp_path: Path) -> None:
    skills = tmp_path / "skills"
    skills.mkdir()
    settings = tmp_path / "settings.json"
    _write_settings(settings, _valid_payload(skills, mcp={
        "search": {"transport": "http", "url": "https://mcp.example/s", "headers": {"X-Key": "mcp-header-secret"}},
    }))

    summary = agent_status_summary(load_agent_settings(settings))
    serialized = json.dumps(summary, ensure_ascii=False)

    for secret in ("sk-live-secret-do-not-leak", "mcp-header-secret", "mcp-env-secret"):
        assert secret not in serialized
    # 模板中的 apiKey 只能是占位符，绝不出现真实密钥形值
    assert '"apiKey": "YOUR_API_KEY"' in summary["config_template"]


def test_summary_missing_settings_is_safe(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"
    with pytest.raises(AgentSettingsError):
        load_agent_settings(missing)

    summary = agent_status_summary(None, path=missing)

    assert summary["configured"] is False
    assert summary["model_name"] is None
    assert summary["base_url_host"] is None
    assert summary["builtin_skill_count"] == 0
    assert summary["mcp_server_count"] == 0
    assert summary["settings_path"] == str(missing)
    assert summary["config_template"] == EXPECTED_TEMPLATE
    assert summary["restart_required"] is True
    assert "原因" in summary or "reason" in summary


def test_summary_invalid_settings_is_safe(tmp_path: Path) -> None:
    skills = tmp_path / "skills"
    skills.mkdir()
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")

    with pytest.raises(AgentSettingsError):
        load_agent_settings(broken)

    summary = agent_status_summary(None, path=broken)

    assert summary["configured"] is False
    assert summary["model_name"] is None
    assert "broken.json" in json.dumps(summary, ensure_ascii=False) or summary["settings_path"].endswith("broken.json")


def test_summary_redacted_reason_mentions_path_not_secret(tmp_path: Path) -> None:
    missing = tmp_path / "nope" / "settings.json"
    summary = agent_status_summary(None, path=missing)
    reason = summary.get("reason") or summary.get("原因") or ""

    assert isinstance(reason, str) and reason
    assert "sk-" not in reason
