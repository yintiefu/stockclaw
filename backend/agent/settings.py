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
from urllib.parse import urlsplit

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
    # 思考输出开关：开启后请求带 thinking 参数并把 reasoning_content 增量转成
    # thinking content block 供前端展示；计入 output tokens，默认关闭。
    # strict：拒绝 "yes"/1 之类的宽松布尔转换，配置错误尽早暴露。
    thinking: Annotated[bool, Field(strict=True)] = False


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


def resolve_skills_path(value: Path, *, create: bool) -> Path:
    """解析并校验 Agent Skills 根目录：拒绝文件系统根，缺失时按需以 0700 创建。"""
    resolved = value.expanduser().resolve()
    if resolved.parent == resolved:
        raise AgentSettingsError("Agent Skills 路径不能是文件系统根")
    if create and not resolved.exists():
        resolved.mkdir(parents=True, mode=0o700)
        resolved.chmod(0o700)
    if not resolved.is_dir() or not os.access(resolved, os.R_OK | os.W_OK):
        raise AgentSettingsError("Agent Skills 目录不存在、不可读写或不是目录")
    return resolved


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
    settings.skills.path = resolve_skills_path(settings.skills.path, create=True)
    settings.trace.dir = settings.trace.dir.expanduser().resolve()
    try:
        if resolved.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            print(f"警告：Agent 配置包含明文密钥，建议执行 chmod 600 {resolved}", file=sys.stderr)
    except OSError:
        pass
    return settings


# 缺配置时给用户复制的最小模板：api_key 只能是显式占位符，绝不预填真实密钥。
CONFIG_TEMPLATE = (
    '{\n  "model": {\n    "provider": "openai",\n    "name": "your-model",\n'
    '    "apiKey": "YOUR_API_KEY",\n'
    '    "baseURL": "https://your-provider.example/v1"\n  },\n'
    '  "skills": {\n    "path": "~/.vibe-research/agent/skills"\n  },\n'
    '  "mcpServers": {}\n}'
)


def _builtin_skill_count() -> int:
    """统计仓库内置 Skill 目录数（只数目录，不读内容）。"""
    builtin_root = Path(__file__).resolve().parent / "builtin_skills"
    try:
        return sum(1 for child in builtin_root.iterdir() if child.is_dir())
    except OSError:
        return 0


def _base_url_host(base_url: str) -> str | None:
    try:
        host = urlsplit(base_url).hostname
    except ValueError:
        return None
    return host or None


def agent_status_summary(
    settings: AgentSettings | None,
    *,
    path: Path | None = None,
) -> dict[str, object]:
    """只读脱敏摘要：FastAPI `/api/agent/status` 的唯一数据源。

    - settings 为 None（配置缺失/无效）时返回安全形状与中文原因，不抛异常；
    - 永不包含 api_key、MCP header / env 等任何密钥；
    - 模板 api_key 固定为 YOUR_API_KEY 占位符。
    """
    resolved = (path or agent_settings_path()).expanduser().resolve()
    base: dict[str, object] = {
        "configured": False,
        "settings_path": str(resolved),
        "model_name": None,
        "base_url_host": None,
        "builtin_skill_count": 0,
        "mcp_server_count": 0,
        "restart_required": True,
        "config_template": CONFIG_TEMPLATE,
        "reason": "Agent 配置缺失或无效，LangGraph Server 未就绪；请按模板创建配置后重启。",
    }
    if settings is None:
        return base
    return {
        "configured": True,
        "settings_path": str(resolved),
        "model_name": settings.model.name,
        "base_url_host": _base_url_host(settings.model.base_url),
        "builtin_skill_count": _builtin_skill_count(),
        "mcp_server_count": len(settings.mcp_servers),
        "restart_required": True,
        "config_template": CONFIG_TEMPLATE,
    }
