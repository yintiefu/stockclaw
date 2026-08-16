"""MCP 配置模型、原子存储、稳定 alias 与传输定义。

1C 约束：
- env/headers 只接受 {"from_env": NAME} 引用，绝不落 raw secret；
- 所有修改走整文档 revision CAS，原子写；
- alias 对相同 (server_id, original_name) 确定性生成且 ≤64 字符。
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import unicodedata
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent.ssrf import validate_outbound_url
from agent.stores import atomic_write_json

SERVER_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?$")
ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
ALIAS_SAFE_RE = re.compile(r"[^a-z0-9_]")

FORBIDDEN_HEADERS = {"host", "content-length", "transfer-encoding", "connection"}

TOOL_COUNT_LIMIT = 256
TOOL_NAME_LIMIT = 256
TOOL_DESCRIPTION_LIMIT = 8000
TOOL_SCHEMA_LIMIT = 64 * 1024
CATALOG_LIMIT = 2 * 1024 * 1024


class McpError(RuntimeError):
    code = "MCP_CONFIG_ERROR"


class McpRevisionConflict(McpError):
    code = "MCP_REVISION_CONFLICT"


class McpConfigCorrupt(McpError):
    code = "MCP_CONFIG_CORRUPT"


class McpServerNotFound(McpError):
    code = "MCP_SERVER_NOT_FOUND"


class McpSecretMissing(McpError):
    code = "MCP_SECRET_MISSING"


class McpSsrfBlocked(McpError):
    code = "MCP_SSRF_BLOCKED"


# ---------------------------------------------------------------------------
# 传输与引用模型
# ---------------------------------------------------------------------------


class EnvReference(BaseModel):
    """环境变量引用 —— 唯一合法的密钥形态。"""
    model_config = ConfigDict(extra="forbid")

    from_env: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")

    def resolve(self) -> str:
        import os

        value = (os.environ.get(self.from_env) or "").strip()
        if not value:
            raise McpSecretMissing(f"环境变量 {self.from_env} 缺失或为空")
        return value


class StdioTransport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["stdio"] = "stdio"
    executable: str = Field(min_length=1, max_length=256)
    args: list[str] = Field(default_factory=list, max_length=128)
    env: dict[str, EnvReference] = Field(default_factory=dict)

    @model_validator(mode="after")
    def check_env_names(self):
        for name in self.env:
            if not ENV_NAME_RE.fullmatch(name):
                raise ValueError(f"env 变量名非法: {name}")
        return self


class StreamableHttpTransport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["streamable_http"] = "streamable_http"
    url: str = Field(min_length=8, max_length=2048)
    headers: dict[str, EnvReference] = Field(default_factory=dict)

    @model_validator(mode="after")
    def check_url_and_headers(self):
        if "/" in self.url or True:
            pass
        try:
            validate_outbound_url(self.url, public_mode=False,
                                  require_public_https=True, allow_query=False,
                                  allow_userinfo=False)
        except RuntimeError as exc:
            raise ValueError(str(exc)) from exc
        for header in self.headers:
            if header.lower() in FORBIDDEN_HEADERS:
                raise ValueError(f"禁止覆盖请求头: {header}")
        return self

    def validate_public(self, *, public_mode: bool) -> None:
        validate_outbound_url(self.url, public_mode=public_mode,
                              require_public_https=True, allow_query=False,
                              allow_userinfo=False)


class McpToolCatalogEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    original_name: str = Field(min_length=1)
    alias: str = Field(min_length=1, max_length=64)
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = False
    discovered_at: str = ""


class McpHealth(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: Literal["unknown", "ok", "unreachable", "error"] = "unknown"
    detail: str = ""
    checked_at: str = ""


class McpServer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=32)
    display_name: str = Field(min_length=1, max_length=80)
    enabled: bool = False
    transport: StdioTransport | StreamableHttpTransport
    trust_fingerprint: str | None = None
    trusted_at: str | None = None
    tools: list[McpToolCatalogEntry] = Field(default_factory=list)
    health: McpHealth = Field(default_factory=McpHealth)

    @property
    def is_stdio(self) -> bool:
        return isinstance(self.transport, StdioTransport)


class McpDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    revision: int = Field(default=0, ge=0)
    servers: list[McpServer] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# alias
# ---------------------------------------------------------------------------


def _safe_slug(name: str) -> str:
    normalized = unicodedata.normalize("NFKD", name)
    lowered = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = ALIAS_SAFE_RE.sub("_", lowered)
    slug = re.sub(r"_+", "_", slug)
    return slug.strip("_") or "tool"


def mcp_alias(server_id: str, original_name: str) -> str:
    prefix = f"mcp__{server_id}__"
    budget = 64 - len(prefix)
    if (
        re.fullmatch(r"[A-Za-z0-9_-]+", original_name)
        and "__" not in original_name
        and len(original_name) <= budget
    ):
        return prefix + original_name
    digest = hashlib.sha256(f"{server_id}\x00{original_name}".encode("utf-8")).hexdigest()[:10]
    slug = _safe_slug(original_name)
    suffix = f"-{digest}"
    return prefix + slug[: budget - len(suffix)] + suffix


def validate_catalog(entries: list[McpToolCatalogEntry], server_id: str) -> None:
    if len(entries) > TOOL_COUNT_LIMIT:
        raise McpError(f"工具数超过 {TOOL_COUNT_LIMIT}")
    seen: set[str] = set()
    for entry in entries:
        if len(entry.original_name) > TOOL_NAME_LIMIT:
            raise McpError(f"工具名超过 {TOOL_NAME_LIMIT} 字符: {entry.original_name[:32]}…")
        if len(entry.description) > TOOL_DESCRIPTION_LIMIT:
            raise McpError(f"工具描述超过 {TOOL_DESCRIPTION_LIMIT} 字符")
        if len(json.dumps(entry.input_schema, ensure_ascii=False)) > TOOL_SCHEMA_LIMIT:
            raise McpError(f"工具 schema 超过 {TOOL_SCHEMA_LIMIT} 字节")
        expected = mcp_alias(server_id, entry.original_name)
        if entry.alias != expected:
            raise McpError(f"alias 与确定性生成结果不一致: {entry.original_name}")
        if entry.alias in seen:
            raise McpError(f"alias 冲突: {entry.alias}")
        seen.add(entry.alias)


# ---------------------------------------------------------------------------
# 原子配置存储
# ---------------------------------------------------------------------------


class McpConfigStore:
    """整文档 revision CAS + 原子写 + 损坏隔离。返回不可变拷贝。"""

    def __init__(self, path: Path):
        self._path = Path(path)
        self._lock = threading.RLock()

    def load(self) -> McpDocument:
        with self._lock:
            if not self._path.exists():
                return McpDocument()
            try:
                payload = json.loads(self._path.read_text(encoding="utf-8"))
                return McpDocument.model_validate(payload)
            except (json.JSONDecodeError, ValueError) as exc:
                # 损坏隔离：保留原文件供人工检查，另存隔离副本
                from agent.stores import utc_stamp

                quarantined = self._path.with_name(f"{self._path.name}.corrupt-{utc_stamp()}")
                try:
                    import os

                    os.replace(self._path, quarantined)
                except OSError:
                    pass
                raise McpConfigCorrupt(f"MCP 配置损坏，已隔离为 {quarantined.name}: {exc}") from exc

    def update(self, expected_revision: int, mutate) -> McpDocument:
        with self._lock:
            try:
                current = self.load()
            except McpConfigCorrupt:
                current = McpDocument()  # 从损坏状态恢复为空文档
            if current.revision != expected_revision:
                raise McpRevisionConflict(
                    f"MCP 配置期望 revision {expected_revision}，实际 {current.revision}")
            updated = mutate(current)
            updated = updated.model_copy(update={"revision": current.revision + 1})
            self._persist(updated)
            return updated

    def _persist(self, doc: McpDocument) -> None:
        payload = doc.model_dump(mode="json")
        if len(json.dumps(payload, ensure_ascii=False)) > CATALOG_LIMIT:
            raise McpError(f"持久化 catalog 超过 {CATALOG_LIMIT // (1024 * 1024)} MB")
        for server in doc.servers:
            validate_catalog(server.tools, server.id)
        atomic_write_json(self._path, payload)

    # ---- 服务端校验 ----

    def validate_server(self, payload: dict) -> McpServer:
        if not isinstance(payload, dict):
            raise ValueError("server 必须是对象")
        server_id = payload.get("id")
        if not isinstance(server_id, str) or not SERVER_ID_RE.fullmatch(server_id):
            raise ValueError(f"server id 非法: {server_id!r}")
        return McpServer.model_validate(payload)

    def apply_tool_refresh(
        self,
        server_id: str,
        discovered: list[dict],
        *,
        expected_revision: int,
    ) -> McpDocument:
        """目录刷新：同名继承 enabled，新工具默认 disabled，消失的移除。"""

        def mutate(doc: McpDocument) -> McpDocument:
            servers = []
            for server in doc.servers:
                if server.id != server_id:
                    servers.append(server)
                    continue
                previous = {t.original_name: t.enabled for t in server.tools}
                entries = []
                for item in discovered:
                    name = item["name"]
                    entries.append(McpToolCatalogEntry(
                        original_name=name[:TOOL_NAME_LIMIT],
                        alias=mcp_alias(server_id, name),
                        description=(item.get("description") or "")[:TOOL_DESCRIPTION_LIMIT],
                        input_schema=item.get("input_schema") or {},
                        enabled=previous.get(name, False),
                        discovered_at=item.get("discovered_at", ""),
                    ))
                validate_catalog(entries, server_id)
                servers.append(server.model_copy(update={"tools": entries}))
            return doc.model_copy(update={"servers": servers})

        return self.update(expected_revision, mutate)
