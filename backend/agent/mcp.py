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


# ---------------------------------------------------------------------------
# Registry：连接、信任与会话世代（Task 8 先落 stdio；Task 9 补 HTTP/目录）
# ---------------------------------------------------------------------------

import asyncio
import hashlib
import shutil as _shutil
from dataclasses import dataclass, field

CONNECT_TIMEOUT = 15.0
CALL_TIMEOUT = 60.0
HEALTH_DETAIL_LIMIT = 500


@dataclass(frozen=True)
class StdioTrustPreview:
    executable: str
    resolved_executable: str
    args: list[str]
    fingerprint: str


class StdioTrustRequired(McpError):
    code = "STDIO_TRUST_REQUIRED"

    def __init__(self, preview: StdioTrustPreview):
        super().__init__(f"{self.code}: stdio server 需要信任确认后才能启动")
        self.preview = preview


def stdio_fingerprint(resolved_executable: str, args: list[str] | tuple[str, ...]) -> str:
    canonical = json.dumps(
        {"resolved_executable": resolved_executable, "args": list(args)},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _resolve_executable(executable: str) -> str:
    """PATH 查找 + 绝对化；不解析符号链接（venv python 的 symlink 语义必须保留）。"""
    import os

    resolved = _shutil.which(executable) or executable
    return os.path.abspath(resolved)


def _redact_text(text: str, secrets: set[str]) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[redacted]")
    return text


@dataclass
class _SessionGeneration:
    number: int
    server_id: str = ""
    state: str = "accepting"  # accepting | draining | closed
    in_flight: int = 0
    client: "object | None" = None
    tools: dict = field(default_factory=dict)
    supervisor: "asyncio.Task | None" = None
    ready: "asyncio.Event" = field(default_factory=asyncio.Event)
    stop_event: "asyncio.Event" = field(default_factory=asyncio.Event)
    error: "str | None" = None


class _AsyncExitStack:
    """占位兼容（真实类型来自 contextlib）。"""

    def __init__(self):
        from contextlib import AsyncExitStack as _Real

        self._real = _Real()

    async def aclose(self):
        await self._real.__aexit__(None, None, None)


class McpRegistry:
    """管理面 MCP 连接注册表：信任、会话世代与受限调用包装。

    每个会话世代由独立的 supervisor task 持有（anyio cancel scope 要求
    进入/退出同一 task）；关闭 = 置位 stop 事件并在有界时间内等待。
    secret set 只在实例内用于脱敏，关闭时清空。
    """

    def __init__(self, store: McpConfigStore, work_root: Path):
        self.store = store
        self._work_root = Path(work_root)
        self._sessions: dict[str, _SessionGeneration] = {}
        self._server_locks: dict[str, asyncio.Lock] = {}
        self._state_lock = asyncio.Lock()
        self._counter = 0
        self._secret_sets: dict[str, set[str]] = {}
        self._shutting_down = False

    @classmethod
    def for_root(cls, root: Path) -> "McpRegistry":
        root = Path(root)
        return cls(McpConfigStore(root / "mcp.json"), root / "mcp-work")

    # ---- 状态 ----

    @property
    def process_count(self) -> int:
        return sum(1 for gen in self._sessions.values() if gen.state != "closed")

    def _server_lock(self, server_id: str) -> asyncio.Lock:
        if server_id not in self._server_locks:
            self._server_locks[server_id] = asyncio.Lock()
        return self._server_locks[server_id]

    def _require_server(self, doc: McpDocument, server_id: str) -> McpServer:
        server = next((s for s in doc.servers if s.id == server_id), None)
        if server is None:
            raise McpServerNotFound(f"MCP server 不存在: {server_id}")
        return server

    # ---- 配置面 ----

    async def add(self, server: McpServer) -> McpDocument:
        def mutate(doc: McpDocument) -> McpDocument:
            if any(s.id == server.id for s in doc.servers):
                raise McpError(f"server 已存在: {server.id}")
            return doc.model_copy(update={"servers": [*doc.servers, server]})

        return await asyncio.to_thread(self.store.update, self.store.load().revision, mutate)

    async def patch_server(self, server_id: str, revision: int, mutate) -> McpDocument:
        def change(doc: McpDocument) -> McpDocument:
            server = self._require_server(doc, server_id)
            updated = mutate(server)
            old_transport = server.transport
            new_transport = updated.transport
            fingerprint_cleared = (
                isinstance(old_transport, StdioTransport)
                and isinstance(new_transport, StdioTransport)
                and (old_transport.executable != new_transport.executable
                     or old_transport.args != new_transport.args)
            )
            if fingerprint_cleared:
                updated = updated.model_copy(update={"trust_fingerprint": None, "trusted_at": None})
            servers = [updated if s.id == server_id else s for s in doc.servers]
            return doc.model_copy(update={"servers": servers})

        return await asyncio.to_thread(self.store.update, revision, change)

    async def delete(self, server_id: str, revision: int) -> list[str]:
        await self._close_server(server_id)
        warnings: list[str] = []

        def change(doc: McpDocument) -> McpDocument:
            self._require_server(doc, server_id)
            servers = [s for s in doc.servers if s.id != server_id]
            return doc.model_copy(update={"servers": servers})

        await asyncio.to_thread(self.store.update, revision, change)
        work = self._work_root / server_id
        if work.is_dir():
            try:
                next(work.iterdir())
                non_empty = True
            except StopIteration:
                non_empty = False
            if non_empty:
                warnings.append(f"mcp-work/{server_id} 非空，已保留为用户数据")
            else:
                import shutil

                shutil.rmtree(work, ignore_errors=True)
        self._secret_sets.pop(server_id, None)
        return warnings

    async def trust(self, server_id: str, fingerprint: str, revision: int) -> McpDocument:
        doc = await asyncio.to_thread(self.store.load)
        server = self._require_server(doc, server_id)
        if not isinstance(server.transport, StdioTransport):
            raise McpError("只有 stdio server 需要信任")
        resolved = await asyncio.to_thread(_resolve_executable, server.transport.executable)
        current = stdio_fingerprint(resolved, server.transport.args)
        if current != fingerprint:
            raise McpError("STDIO_FINGERPRINT_MISMATCH: 页面显示的指纹与当前解析结果不一致")

        def change(d: McpDocument) -> McpDocument:
            servers = []
            for s in d.servers:
                if s.id == server_id:
                    s = s.model_copy(update={"trust_fingerprint": current, "trusted_at": _utc_now()})
                servers.append(s)
            return d.model_copy(update={"servers": servers})

        return await asyncio.to_thread(self.store.update, revision, change)

    # ---- 连接面 ----

    def _stdio_trust_preview(self, server: McpServer) -> StdioTrustPreview:
        transport = server.transport
        assert isinstance(transport, StdioTransport)
        resolved = _resolve_executable(transport.executable)
        return StdioTrustPreview(
            executable=transport.executable,
            resolved_executable=resolved,
            args=list(transport.args),
            fingerprint=stdio_fingerprint(resolved, transport.args),
        )

    async def trust_preview(self, server_id: str) -> StdioTrustPreview:
        """当前解析出的信任预览（UI 显示与 POST /trust 的一致性来源）。"""
        doc = await asyncio.to_thread(self.store.load)
        server = self._require_server(doc, server_id)
        if not isinstance(server.transport, StdioTransport):
            raise McpError("只有 stdio server 需要信任")
        return await asyncio.to_thread(self._stdio_trust_preview, server)

    def _spawn_supervisor(self, server: McpServer, generation: _SessionGeneration) -> None:
        """supervisor task：同一 task 内进出 SDK context（anyio cancel scope 约束）。"""

        async def _run() -> None:
            from contextlib import AsyncExitStack

            stack = AsyncExitStack()
            try:
                resolved_secrets: set[str] = set()
                if isinstance(server.transport, StdioTransport):
                    transport = server.transport
                    env = _default_environment()
                    for name, ref in transport.env.items():
                        env[name] = ref.resolve()
                        resolved_secrets.add(env[name])
                    work = self._work_root / server.id
                    work.mkdir(parents=True, exist_ok=True)
                    resolved = _resolve_executable(transport.executable)
                    params = _stdio_parameters(resolved, list(transport.args), env, str(work))
                    streams = await asyncio.wait_for(
                        stack.enter_async_context(_stdio_client(params)), timeout=CONNECT_TIMEOUT)
                else:
                    transport = server.transport
                    assert isinstance(transport, StreamableHttpTransport)
                    headers = {}
                    for name, ref in transport.headers.items():
                        headers[name] = ref.resolve()
                        resolved_secrets.add(headers[name])
                    transport.validate_public(public_mode=_public_mode())
                    streams = await asyncio.wait_for(
                        stack.enter_async_context(_streamable_http_client(transport.url, headers)),
                        timeout=CONNECT_TIMEOUT)
                read_stream, write_stream = streams[0], streams[1]
                client = await stack.enter_async_context(_client_session(read_stream, write_stream))
                await asyncio.wait_for(client.initialize(), timeout=CONNECT_TIMEOUT)
                generation.client = client
                self._secret_sets.setdefault(server.id, set()).update(resolved_secrets)
                generation.ready.set()
                await generation.stop_event.wait()
            except BaseException as exc:  # noqa: BLE001 —— 记录脱敏错误供等待方消费
                generation.error = _redact_text(str(exc), self._secret_sets.get(server.id, set()))
                generation.ready.set()
            finally:
                generation.state = "closed"
                try:
                    await asyncio.wait_for(stack.aclose(), timeout=2 * CONNECT_TIMEOUT)
                except Exception:  # noqa: BLE001 —— 有界尽力关闭，不留孤儿
                    pass

        generation.supervisor = asyncio.create_task(_run())

    async def _build_session(self, server: McpServer) -> _SessionGeneration:
        if self._shutting_down:
            raise McpError("MCP_UNAVAILABLE: Registry 正在关闭")
        # 信任/env 校验先行（无 spawn / 无网络）
        if isinstance(server.transport, StdioTransport):
            preview = self._stdio_trust_preview(server)
            if server.trust_fingerprint != preview.fingerprint:
                raise StdioTrustRequired(preview)
        # 预解析 env/header，缺失 secret 不建立 transport
        if isinstance(server.transport, StdioTransport):
            for ref in server.transport.env.values():
                ref.resolve()
        else:
            transport = server.transport
            assert isinstance(transport, StreamableHttpTransport)
            transport.validate_public(public_mode=_public_mode())
            for ref in transport.headers.values():
                ref.resolve()

        self._counter += 1
        generation = _SessionGeneration(number=self._counter, server_id=server.id)
        self._spawn_supervisor(server, generation)
        try:
            await asyncio.wait_for(generation.ready.wait(), timeout=2 * CONNECT_TIMEOUT)
        except asyncio.TimeoutError as exc:
            generation.stop_event.set()
            raise McpError("连接超时") from exc
        if generation.error is not None:
            raise McpError(f"连接失败: {generation.error[:HEALTH_DETAIL_LIMIT]}")
        self._secret_sets.setdefault(server.id, set())
        return generation

    async def test(self, server_id: str) -> McpHealth:
        """initialize + 基础能力检查；health 写回配置文档（revision +1）。"""
        doc = await asyncio.to_thread(self.store.load)
        server = self._require_server(doc, server_id)
        try:
            generation = self._sessions.get(server_id)
            if generation is None or generation.state != "accepting" or generation.client is None:
                generation = await self._build_session(server)
                await self._replace_session(server_id, generation)
            tool_count = 0
            try:
                tools = await asyncio.wait_for(generation.client.list_tools(), timeout=CONNECT_TIMEOUT)
                tool_count = len(tools.tools)
            except Exception:  # noqa: BLE001
                tool_count = -1
            health = McpHealth(state="ok", detail=f"tools={tool_count}", checked_at=_utc_now())
        except (StdioTrustRequired, McpSecretMissing):
            raise
        except McpError as exc:
            health = McpHealth(state="unreachable", detail=str(exc)[:HEALTH_DETAIL_LIMIT],
                               checked_at=_utc_now())
        await self._write_health(server_id, health)
        return health

    async def _replace_session(self, server_id: str, generation: _SessionGeneration) -> None:
        async with self._state_lock:
            old = self._sessions.get(server_id)
            if old is not None and old.state == "accepting":
                old.state = "draining"
                old.stop_event.set()
            self._sessions[server_id] = generation

    async def _close_generation(self, generation: _SessionGeneration) -> None:
        if generation.state == "closed" and generation.supervisor is None:
            return
        generation.stop_event.set()
        supervisor = generation.supervisor
        if supervisor is None:
            return
        try:
            await asyncio.wait_for(asyncio.shield(supervisor), timeout=2 * CONNECT_TIMEOUT)
        except (asyncio.TimeoutError, Exception):  # noqa: BLE001
            supervisor.cancel()
            try:
                await supervisor
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    async def _close_server(self, server_id: str) -> None:
        async with self._state_lock:
            generation = self._sessions.pop(server_id, None)
        if generation is not None:
            await self._close_generation(generation)
        self._secret_sets.pop(server_id, None)

    async def shutdown(self) -> None:
        """有界关闭全部世代（SDK 的 stdin close → terminate → kill 合同）。"""
        self._shutting_down = True
        generations = list(self._sessions.values())
        self._sessions.clear()
        await asyncio.wait_for(
            asyncio.gather(*(self._close_generation(g) for g in generations),
                           return_exceptions=True),
            timeout=2 * CONNECT_TIMEOUT + 5,
        )
        self._secret_sets.clear()

    # ---- 目录与调用（Task 9） ----

    async def refresh(self, server_id: str) -> McpServer:
        """连接 + load_mcp_tools 发现目录；同名继承 enabled；失败保留旧目录。"""
        doc = await asyncio.to_thread(self.store.load)
        server = self._require_server(doc, server_id)
        generation = await self._build_session(server)
        secrets = self._secret_sets.get(server_id, set())
        try:
            tools = await asyncio.wait_for(
                _load_mcp_tools(session=generation.client, server_name=server_id),
                timeout=CONNECT_TIMEOUT)
        except BaseException as exc:
            await self._close_generation(generation)
            detail = _redact_text(str(exc), secrets)[:HEALTH_DETAIL_LIMIT]
            raise McpError(f"目录发现失败: {detail}") from exc
        discovered = []
        for tool in tools:
            description = _redact_text(getattr(tool, "description", "") or "", secrets)
            discovered.append({
                "name": getattr(tool, "name", ""),
                "description": description[:TOOL_DESCRIPTION_LIMIT],
                "input_schema": _tool_args_schema(tool),
                "discovered_at": _utc_now(),
            })
        try:
            updated = await asyncio.to_thread(
                self.store.apply_tool_refresh, server_id, discovered,
                expected_revision=(await asyncio.to_thread(self.store.load)).revision,
            )
        except Exception:
            await self._close_generation(generation)
            raise
        generation.tools = {tool.name: tool for tool in tools}
        await self._replace_session(server_id, generation)
        refreshed = next(s for s in updated.servers if s.id == server_id)
        await self._write_health(server_id, McpHealth(
            state="ok", detail=f"tools={len(refreshed.tools)}", checked_at=_utc_now()))
        return refreshed

    async def call_tool(self, server_id: str, alias: str, arguments: dict) -> str:
        """绑定调用入口：server 串行锁 + 60s 端到端预算 + 脱敏/内容/截断。"""
        loop = asyncio.get_event_loop()
        deadline = loop.time() + CALL_TIMEOUT

        async with self._server_lock(server_id):
            remaining = deadline - loop.time()
            if remaining <= 0:
                return _tool_error("MCP_UNAVAILABLE", "调用超时（等待 server 锁）")
            return await asyncio.wait_for(
                self._call_with_session(server_id, alias, arguments),
                timeout=remaining,
            )

    async def _call_with_session(self, server_id: str, alias: str, arguments: dict) -> str:
        loop = asyncio.get_event_loop()
        deadline = loop.time() + CALL_TIMEOUT
        async with self._state_lock:
            generation = self._sessions.get(server_id)
        if self._shutting_down or generation is None or generation.state != "accepting":
            return _tool_error("MCP_UNAVAILABLE", "MCP 会话不可用（关闭中或未连接）")
        original = _original_name_for_alias(server_id, alias, generation.tools) or alias
        tool = generation.tools.get(original)
        if tool is None:
            return _tool_error("MCP_UNAVAILABLE", f"工具不在当前目录: {alias}")
        secrets = self._secret_sets.get(server_id, set())
        generation.in_flight += 1
        try:
            remaining = deadline - loop.time()
            if remaining <= 0:
                return _tool_error("MCP_UNAVAILABLE", "调用超时")
            try:
                result = await asyncio.wait_for(
                    tool.ainvoke(arguments), timeout=remaining)
            except BaseException as exc:
                detail = _redact_text(str(exc), secrets)[:HEALTH_DETAIL_LIMIT]
                return _tool_error("MCP_TOOL_ERROR", detail)
        finally:
            generation.in_flight -= 1
        return _normalize_tool_result(result, secrets)

    async def _write_health(self, server_id: str, health: McpHealth) -> None:
        def change(doc: McpDocument) -> McpDocument:
            servers = []
            for s in doc.servers:
                if s.id == server_id:
                    s = s.model_copy(update={"health": health})
                servers.append(s)
            return doc.model_copy(update={"servers": servers})

        revision = (await asyncio.to_thread(self.store.load)).revision
        await asyncio.to_thread(self.store.update, revision, change)


def _utc_now() -> str:
    from agent.stores import utc_now

    return utc_now()


def _default_environment() -> dict[str, str]:
    from mcp.client.stdio import get_default_environment

    return dict(get_default_environment())


def _stdio_parameters(command: str, args: list[str], env: dict[str, str], cwd: str):
    from mcp import StdioServerParameters

    return StdioServerParameters(command=command, args=args, env=env, cwd=cwd)


def _stdio_client(params):
    from mcp import StdioServerParameters  # noqa: F401

    from mcp.client.stdio import stdio_client

    return stdio_client(params)


def _streamable_http_client(url: str, headers: dict[str, str]):
    from mcp.client.streamable_http import streamablehttp_client

    return streamablehttp_client(url, headers=headers)


def _client_session(read_stream, write_stream):
    from mcp.client.session import ClientSession

    return ClientSession(read_stream, write_stream)


def _public_mode() -> bool:
    from agent.ssrf import public_mode

    return public_mode()


async def _initialize(client) -> None:
    await client.initialize()


async def _safe_aclose(stack) -> None:
    if stack is None:
        return
    try:
        await asyncio.wait_for(stack.aclose(), timeout=2 * CONNECT_TIMEOUT)
    except Exception:  # noqa: BLE001 —— 关闭路径尽力而为，不留孤儿
        pass


# ---------------------------------------------------------------------------
# adapter 工具辅助
# ---------------------------------------------------------------------------

def _tool_args_schema(tool) -> dict:
    schema = getattr(tool, "args_schema", None)
    if isinstance(schema, dict):
        return schema
    if schema is not None and hasattr(schema, "model_json_schema"):
        try:
            return schema.model_json_schema()
        except Exception:  # noqa: BLE001
            return {}
    return {}


def _original_name_of(alias: str, tools: dict) -> str | None:
    if alias in tools:
        return alias
    return None


def _original_name_for_alias(server_id: str, alias: str, tools: dict) -> str | None:
    for original in tools:
        if mcp_alias(server_id, original) == alias:
            return original
    return None


def _tool_error(code: str, detail: str) -> str:
    return json.dumps({"error": code, "detail": detail}, ensure_ascii=False)


def _recursive_redact(value, secrets: set[str]):
    if isinstance(value, str):
        text = value
        for secret in secrets:
            if secret:
                text = text.replace(secret, "[redacted]")
        return text
    if isinstance(value, dict):
        return {k: _recursive_redact(v, secrets) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_recursive_redact(v, secrets) for v in value]
    return value


def _normalize_tool_result(result, secrets: set[str]) -> str:
    """先递归脱敏 → 支持内容检查 → 编码 → 截断 6000（顺序不可颠倒）。"""
    RESULT_LIMIT = 6000
    redacted = _recursive_redact(result, secrets)
    if isinstance(redacted, str):
        payload = redacted
    elif isinstance(redacted, list):
        # adapter 工具返回内容块列表
        texts = []
        for block in redacted:
            if isinstance(block, dict):
                if block.get("type") not in ("text",):
                    return _tool_error(
                        "MCP_CONTENT_UNSUPPORTED", "仅支持文本与 JSON structuredContent")
                texts.append(block.get("text", ""))
            elif isinstance(block, str):
                texts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                texts.append(block.get("text", ""))
            else:
                # MCP 对象内容块：仅 text 可用，image/audio/resource 一律拒绝
                if getattr(block, "type", "text") != "text":
                    return _tool_error(
                        "MCP_CONTENT_UNSUPPORTED", "仅支持文本与 JSON structuredContent")
                texts.append(getattr(block, "text", "") or "")
        payload = "".join(texts)
    else:
        payload = json.dumps(redacted, ensure_ascii=False, default=str)
    if len(payload) > RESULT_LIMIT:
        payload = payload[: RESULT_LIMIT - len("...[truncated]")] + "...[truncated]"
    return payload


def _load_mcp_tools(*, session, server_name: str):
    from langchain_mcp_adapters.tools import load_mcp_tools

    return load_mcp_tools(session=session, server_name=server_name)


def _build_http_client_options(url: str, headers: dict[str, str]) -> dict:
    """Streamable HTTP 的自定义 client 参数：不跟随 redirect、不用系统代理、
    连接 15s / 读 60s。URL/地址校验在连接前完成。"""
    import httpx

    return {
        "follow_redirects": False,
        "trust_env": False,
        "timeout": httpx.Timeout(connect=15.0, read=60.0, write=15.0, pool=15.0),
    }


def _streamable_http_client(url: str, headers: dict[str, str]):
    from mcp.client.streamable_http import streamablehttp_client

    import httpx

    options = _build_http_client_options(url, headers)

    def _client_factory(headers=None, timeout=None, auth=None) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            follow_redirects=options["follow_redirects"],
            trust_env=options["trust_env"],
            timeout=timeout if timeout is not None else options["timeout"],
            headers=headers or {},
            auth=auth,
        )

    return streamablehttp_client(url, headers=headers, httpx_client_factory=_client_factory)
