"""Agent 工作台静态本地设置：JSON 文件 + Pydantic 严格校验 + 密钥安全。

设置只在本机读取一次（LangGraph Server 导入图时），密钥绝不进入
线程元数据、图状态、检查点、日志或前端请求。所有错误信息用中文、
只报路径与字段位置，不回显密钥内容。
"""
from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError


class AgentSettingsError(RuntimeError):
    pass


class ModelSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    provider: Literal["openai"]
    name: str = Field(min_length=1)
    api_key: SecretStr = Field(alias="apiKey")
    base_url: str = Field(alias="baseURL", min_length=1)
    temperature: float = Field(default=0.2, ge=0, le=2)


class SkillsSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: Path


class StdioMcpSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    transport: Literal["stdio"]
    command: str = Field(min_length=1)
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)


class HttpMcpSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    transport: Literal["http"]
    url: str = Field(min_length=1)
    headers: dict[str, str] = Field(default_factory=dict)


McpSettings = Annotated[StdioMcpSettings | HttpMcpSettings, Field(discriminator="transport")]


class TraceSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    dir: Path = Field(default_factory=lambda: Path.home() / ".vibe-research" / "agent" / "traces")


class AgentSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: ModelSettings
    skills: SkillsSettings
    mcp_servers: dict[str, McpSettings] = Field(default_factory=dict, alias="mcpServers")
    trace: TraceSettings = Field(default_factory=TraceSettings)

    def mcp_connections(self) -> dict[str, dict[str, object]]:
        return {name: config.model_dump(mode="python", exclude_none=True)
                for name, config in self.mcp_servers.items()}


def agent_settings_path() -> Path:
    override = os.environ.get("VR_AGENT_SETTINGS", "").strip()
    return Path(override).expanduser() if override else Path.home() / ".vibe-research" / "agent" / "settings.json"


def load_agent_settings(path: Path | None = None) -> AgentSettings:
    resolved = (path or agent_settings_path()).expanduser().resolve()
    try:
        raw = resolved.read_text(encoding="utf-8")
        payload = json.loads(raw)
        settings = AgentSettings.model_validate(payload)
    except FileNotFoundError as exc:
        raise AgentSettingsError(f"Agent 配置文件不存在：{resolved}") from exc
    except OSError as exc:
        raise AgentSettingsError(f"Agent 配置文件不可读：{resolved}（{exc.strerror or '未知错误'}）") from exc
    except json.JSONDecodeError as exc:
        raise AgentSettingsError(f"Agent 配置文件不是合法 JSON：{resolved}（第 {exc.lineno} 行第 {exc.colno} 列）") from exc
    except ValidationError as exc:
        locations = [".".join(str(part) for part in error["loc"]) for error in exc.errors(include_input=False)]
        raise AgentSettingsError(f"Agent 配置字段无效：{resolved}（{', '.join(locations)}）") from exc
    skill_root = settings.skills.path.expanduser().resolve()
    if not skill_root.is_dir() or not os.access(skill_root, os.R_OK):
        raise AgentSettingsError(f"Agent Skills 目录不存在、不可读或不是目录：{skill_root}")
    settings.skills.path = skill_root
    settings.trace.dir = settings.trace.dir.expanduser().resolve()
    try:
        if resolved.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            print(f"警告：Agent 配置包含明文密钥，建议执行 chmod 600 {resolved}", file=sys.stderr)
    except OSError:
        pass
    return settings
