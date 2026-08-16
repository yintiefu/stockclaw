"""Task 1C-8：McpRegistry —— stdio 信任、进程生命周期与恢复。"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

import pytest

from agent.mcp import (
    McpRegistry,
    McpServer,
    StdioTrustRequired,
    StdioTransport,
    stdio_fingerprint,
)

FIXTURE = Path(__file__).parent / "fake_mcp_server.py"

pytestmark = pytest.mark.asyncio


def registry_for(tmp_path: Path) -> McpRegistry:
    return McpRegistry.for_root(tmp_path)


def stdio_server(args: list[str] | None = None, env: dict | None = None) -> McpServer:
    return McpServer.model_validate({
        "id": "fixture",
        "display_name": "本地夹具",
        "enabled": True,
        "transport": {
            "type": "stdio",
            "executable": sys.executable,
            "args": args if args is not None else [str(FIXTURE)],
            "env": env or {},
        },
    })


# ---------------------------------------------------------------------------
# 信任前置
# ---------------------------------------------------------------------------

async def test_stdio_add_never_spawns_before_matching_trust(tmp_path, monkeypatch):
    registry = registry_for(tmp_path)
    await registry.add(stdio_server())
    assert registry.process_count == 0
    with pytest.raises(StdioTrustRequired) as exc:
        await registry.test("fixture")
    assert exc.value.preview.args == [str(FIXTURE)]
    assert exc.value.preview.executable == sys.executable
    assert exc.value.preview.resolved_executable.endswith(sys.executable) or \
        Path(exc.value.preview.resolved_executable).is_absolute()
    assert exc.value.preview.fingerprint
    assert registry.process_count == 0


async def test_fingerprint_covers_resolved_executable_and_args():
    resolved = sys.executable
    fingerprint = stdio_fingerprint(resolved, ["a", "b"])
    assert fingerprint == stdio_fingerprint(resolved, ["a", "b"])
    assert fingerprint != stdio_fingerprint(resolved, ["a", "c"])
    assert fingerprint != stdio_fingerprint("/other/python", ["a", "b"])


async def test_trust_requires_exact_fingerprint(tmp_path):
    registry = registry_for(tmp_path)
    await registry.add(stdio_server())
    doc = registry.store.load()
    with pytest.raises(Exception):
        await registry.trust("fixture", "stale-fingerprint", doc.revision)
    fingerprint = (await registry.trust_preview("fixture")).fingerprint
    await registry.trust("fixture", fingerprint, doc.revision)
    trusted = registry.store.load()
    server = next(s for s in trusted.servers if s.id == "fixture")
    assert server.trust_fingerprint == fingerprint
    assert server.trusted_at


async def test_args_change_invalidates_trust(tmp_path):
    registry = registry_for(tmp_path)
    await registry.add(stdio_server())
    fingerprint = (await registry.trust_preview("fixture")).fingerprint
    doc = registry.store.load()
    await registry.trust("fixture", fingerprint, doc.revision)
    doc2 = registry.store.load()
    await registry.patch_server("fixture", doc2.revision, lambda s: s.model_copy(update={
        "transport": StdioTransport(executable=sys.executable, args=[str(FIXTURE), "--flag"], env={}),
    }))
    changed = registry.store.load()
    server = next(s for s in changed.servers if s.id == "fixture")
    assert server.trust_fingerprint is None


# ---------------------------------------------------------------------------
# 连接与 env
# ---------------------------------------------------------------------------

async def test_stdio_test_connects_after_trust(tmp_path):
    registry = registry_for(tmp_path)
    await registry.add(stdio_server())
    fingerprint = (await registry.trust_preview("fixture")).fingerprint
    await registry.trust("fixture", fingerprint, registry.store.load().revision)
    health = await registry.test("fixture")
    assert health.state == "ok", health.detail
    await registry.shutdown()


async def test_missing_env_prevents_spawn(tmp_path, monkeypatch):
    monkeypatch.delenv("VR_1C_FIXTURE_TOKEN", raising=False)
    registry = registry_for(tmp_path)
    await registry.add(stdio_server(env={"TOKEN": {"from_env": "VR_1C_FIXTURE_TOKEN"}}))
    fingerprint = (await registry.trust_preview("fixture")).fingerprint
    await registry.trust("fixture", fingerprint, registry.store.load().revision)
    with pytest.raises(Exception) as exc:
        await registry.test("fixture")
    assert "VR_1C_FIXTURE_TOKEN" in str(exc.value)
    assert registry.process_count == 0


async def test_env_resolves_at_connection_time(tmp_path, monkeypatch):
    monkeypatch.setenv("VR_1C_FIXTURE_TOKEN", "tok-1")
    registry = registry_for(tmp_path)
    await registry.add(stdio_server(env={"TOKEN": {"from_env": "VR_1C_FIXTURE_TOKEN"}}))
    fingerprint = (await registry.trust_preview("fixture")).fingerprint
    await registry.trust("fixture", fingerprint, registry.store.load().revision)
    health = await registry.test("fixture")
    assert health.state == "ok"
    await registry.shutdown()


# ---------------------------------------------------------------------------
# 工作目录与关闭
# ---------------------------------------------------------------------------

async def test_delete_removes_empty_workdir_and_keeps_nonempty(tmp_path):
    registry = registry_for(tmp_path)
    await registry.add(stdio_server())
    fingerprint = (await registry.trust_preview("fixture")).fingerprint
    await registry.trust("fixture", fingerprint, registry.store.load().revision)
    await registry.test("fixture")
    work = tmp_path / "mcp-work" / "fixture"
    assert work.is_dir()

    doc = registry.store.load()
    warnings = await registry.delete("fixture", doc.revision)
    assert not work.exists()
    assert warnings == []

    # 非空工作目录：保留并给出相对名警告
    await registry.add(stdio_server())
    await registry.trust("fixture", fingerprint, registry.store.load().revision)
    work.mkdir(parents=True, exist_ok=True)
    (work / "user-data.txt").write_text("外部程序写入", encoding="utf-8")
    doc = registry.store.load()
    warnings = await registry.delete("fixture", doc.revision)
    assert work.exists()
    assert any("fixture" in w for w in warnings)


async def test_shutdown_leaves_no_child_process(tmp_path):
    registry = registry_for(tmp_path)
    await registry.add(stdio_server())
    fingerprint = (await registry.trust_preview("fixture")).fingerprint
    await registry.trust("fixture", fingerprint, registry.store.load().revision)
    await registry.test("fixture")
    assert registry.process_count >= 1
    await registry.shutdown()
    assert registry.process_count == 0


async def test_stubborn_child_shutdown_is_bounded(tmp_path):
    """SDK 关闭合同：stdin close → 2s 等 → terminate → 2s 等 → kill。"""
    script = tmp_path / "stubborn.py"
    script.write_text(
        "import signal, sys, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "print('ready', file=sys.stderr, flush=True)\n"
        "while True:\n"
        "    time.sleep(0.1)\n",
        encoding="utf-8")
    registry = registry_for(tmp_path)
    await registry.add(stdio_server(args=[str(script)]))
    fingerprint = (await registry.trust_preview("fixture")).fingerprint
    await registry.trust("fixture", fingerprint, registry.store.load().revision)
    started = time.monotonic()
    health = await registry.test("fixture")
    # initialize 会因 stdin 协议无响应超时或失败——进程必须仍被清理
    await registry.shutdown()
    elapsed = time.monotonic() - started
    assert elapsed < 30  # 有界
    assert registry.process_count == 0
    assert health.state != "ok"


# ---------------------------------------------------------------------------
# Task 9：HTTP / catalog / generation / redact
# ---------------------------------------------------------------------------

async def _start_http_fixture() -> int:
    """启动 Streamable HTTP 夹具，返回端口（stderr 行 PORT=<n>）。"""
    import subprocess

    proc = subprocess.Popen(
        [sys.executable, str(FIXTURE), "--http"],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    deadline = time.monotonic() + 20
    port = None
    while time.monotonic() < deadline:
        line = proc.stderr.readline()
        if line.startswith("PORT="):
            port = int(line.strip().split("=")[1])
            break
    assert port is not None
    _HTTP_PROCS.append(proc)
    return port


_HTTP_PROCS: list = []


@pytest.fixture(autouse=True)
def _kill_http_procs():
    yield
    for proc in _HTTP_PROCS:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            proc.kill()
    _HTTP_PROCS.clear()


def http_server_for(port: int, server_id="httpfix") -> McpServer:
    return McpServer.model_validate({
        "id": server_id,
        "display_name": "HTTP 夹具",
        "enabled": True,
        "transport": {
            "type": "streamable_http",
            "url": f"http://127.0.0.1:{port}/mcp",
            "headers": {},
        },
    })


async def test_http_test_and_refresh_catalog(tmp_path):
    registry = registry_for(tmp_path)
    port = await _start_http_fixture()
    await registry.add(http_server_for(port))
    health = await registry.test("httpfix")
    assert health.state == "ok", health.detail

    catalog = await registry.refresh("httpfix")
    names = {t.original_name for t in catalog.tools}
    assert {"echo", "echo_secret", "sleep", "fail", "large", "unsupported"} <= names
    # 首次发现默认 disabled；alias 确定性
    entry = next(t for t in catalog.tools if t.original_name == "echo")
    assert entry.enabled is False
    assert entry.alias == "mcp__httpfix__echo"
    await registry.shutdown()


async def test_refresh_preserves_enabled_and_removes_gone(tmp_path):
    registry = registry_for(tmp_path)
    port = await _start_http_fixture()
    await registry.add(http_server_for(port))
    await registry.refresh("httpfix")
    doc = registry.store.load()
    server = next(s for s in doc.servers if s.id == "httpfix")
    # enable echo
    tools = [t.model_copy(update={"enabled": n == "echo"}) for n, t in
             zip([t.original_name for t in server.tools], server.tools)]
    await registry.patch_server("httpfix", doc.revision,
                                lambda s: s.model_copy(update={"tools": tools}))
    refreshed = await registry.refresh("httpfix")
    entries = {t.original_name: t.enabled for t in refreshed.tools}
    assert entries["echo"] is True
    assert entries["sleep"] is False
    await registry.shutdown()


async def test_refresh_replaces_generation_and_drains_old(tmp_path):
    registry = registry_for(tmp_path)
    port = await _start_http_fixture()
    await registry.add(http_server_for(port))
    await registry.refresh("httpfix")
    first = registry._sessions["httpfix"].number
    await registry.refresh("httpfix")
    second = registry._sessions["httpfix"].number
    assert second > first
    await registry.shutdown()


async def test_health_only_update_keeps_generation_compatible(tmp_path):
    registry = registry_for(tmp_path)
    port = await _start_http_fixture()
    await registry.add(http_server_for(port))
    await registry.refresh("httpfix")
    before = registry._sessions["httpfix"].number
    health = await registry.test("httpfix")
    assert health.state == "ok"
    after = registry._sessions["httpfix"].number
    # health-only 写递增文档 revision，但兼容的 generation 不失效
    assert after == before
    doc = registry.store.load()
    assert doc.revision >= 3
    await registry.shutdown()


async def test_redaction_before_truncation_and_unsupported_content(tmp_path, monkeypatch):
    """官方 adapter 工具返回 secret → Registry 边界只剩 [redacted]。"""
    monkeypatch.setenv("VR_1C_SECRET_TOKEN", "SUPER-SECRET-123")
    registry = registry_for(tmp_path)
    server = stdio_server(env={"TOKEN": {"from_env": "VR_1C_SECRET_TOKEN"}})
    await registry.add(server)
    fingerprint = (await registry.trust_preview("fixture")).fingerprint
    await registry.trust("fixture", fingerprint, registry.store.load().revision)
    catalog = await registry.refresh("fixture")
    entries = {t.original_name: t for t in catalog.tools}

    env_entry = entries["env_value"]
    result = await registry.call_tool("fixture", env_entry.alias, {"name": "TOKEN"})
    assert "SUPER-SECRET-123" not in result
    assert "[redacted]" in result

    # 截断边界：先脱敏再截断到 6000
    large = entries["large"]
    big = await registry.call_tool("fixture", large.alias, {"n": 100})
    assert len(big) <= 6000

    # 非文本内容（HTTP fixture 侧同构，这里用 unsupported 工具）
    unsupported = entries["unsupported"]
    out = await registry.call_tool("fixture", unsupported.alias, {})
    assert "MCP_CONTENT_UNSUPPORTED" in out
    await registry.shutdown()


async def test_call_tool_error_is_bounded_and_redacted(tmp_path, monkeypatch):
    monkeypatch.setenv("VR_1C_SECRET_TOKEN", "SECRET-ERR-9")
    registry = registry_for(tmp_path)
    server = stdio_server(env={"TOKEN": {"from_env": "VR_1C_SECRET_TOKEN"}})
    await registry.add(server)
    fingerprint = (await registry.trust_preview("fixture")).fingerprint
    await registry.trust("fixture", fingerprint, registry.store.load().revision)
    catalog = await registry.refresh("fixture")
    fail = next(t2 for t2 in catalog.tools if t2.original_name == "fail")
    # 让错误消息里带上 secret：fail 消息经服务端 RuntimeError 传播
    result = await registry.call_tool("fixture", fail.alias, {"message": "SECRET-ERR-9"})
    assert "SECRET-ERR-9" not in result
    await registry.shutdown()


async def test_http_missing_secret_makes_zero_requests(tmp_path):
    import os as _os

    _os.environ.pop("VR_1C_HTTP_AUTH", None)
    registry = registry_for(tmp_path)
    server = McpServer.model_validate({
        "id": "needauth",
        "display_name": "带鉴权",
        "enabled": True,
        "transport": {
            "type": "streamable_http",
            "url": "http://127.0.0.1:9/mcp",
            "headers": {"Authorization": {"from_env": "VR_1C_HTTP_AUTH"}},
        },
    })
    await registry.add(server)
    with pytest.raises(Exception) as exc:
        await registry.test("needauth")
    assert "VR_1C_HTTP_AUTH" in str(exc.value)
    assert registry.process_count == 0


async def test_same_server_calls_are_serial(tmp_path):
    registry = registry_for(tmp_path)
    port = await _start_http_fixture()
    await registry.add(http_server_for(port))
    catalog = await registry.refresh("httpfix")
    sleep_entry = next(t for t in catalog.tools if t.original_name == "sleep")
    active: list[int] = []
    peak = 0

    original_call = registry._call_with_session

    async def tracking_call(server_id, alias, arguments):
        nonlocal peak
        active.append(1)
        peak = max(peak, len(active))
        try:
            return await original_call(server_id, alias, arguments)
        finally:
            active.pop()

    registry._call_with_session = tracking_call
    results = await asyncio.gather(*[
        registry.call_tool("httpfix", sleep_entry.alias, {"seconds": 0.05}) for _ in range(3)
    ])
    assert all("slept" in r for r in results)
    assert peak == 1  # 同 server 串行
    await registry.shutdown()


async def test_http_redirect_refused_and_no_follow(tmp_path):
    from agent.mcp import _build_http_client_options

    options = _build_http_client_options("https://x/mcp", {})
    assert options["follow_redirects"] is False
    assert options["trust_env"] is False
    assert options["timeout"].connect == 15.0
    assert options["timeout"].read == 60.0


async def test_shutdown_rejects_new_calls_with_bounded_error(tmp_path):
    registry = registry_for(tmp_path)
    port = await _start_http_fixture()
    await registry.add(http_server_for(port))
    catalog = await registry.refresh("httpfix")
    await registry.shutdown()
    entry = next(t for t in catalog.tools if t.original_name == "echo")
    result = await registry.call_tool("httpfix", entry.alias, {"value": "x"})
    assert "MCP_UNAVAILABLE" in result


# ---------------------------------------------------------------------------
# Task 11：休眠稳定绑定（不进生产 Graph）
# ---------------------------------------------------------------------------

async def _ready_fixture_registry(tmp_path, monkeypatch=None):
    registry = registry_for(tmp_path)
    await registry.add(stdio_server())
    fingerprint = (await registry.trust_preview("fixture")).fingerprint
    await registry.trust("fixture", fingerprint, registry.store.load().revision)
    catalog = await registry.refresh("fixture")
    return registry, catalog


async def test_binding_metadata_is_secret_free_and_official(tmp_path):
    from agent.mcp import McpToolBinding

    registry, catalog = await _ready_fixture_registry(tmp_path)
    entry = next(t for t in catalog.tools if t.original_name == "echo")
    binding = McpToolBinding(
        server_id="fixture", original_name="echo", alias=entry.alias,
        description=entry.description, args_schema=entry.input_schema,
        config_generation=1, catalog_generation=registry._sessions["fixture"].number,
    )
    tool = binding.as_langchain_tool(registry)
    assert tool.name == binding.alias
    assert "ClientSession" not in repr(binding) and "secret" not in repr(binding).lower()
    result = await tool.ainvoke({"value": "hi"})
    assert "hi" in result
    await registry.shutdown()


async def test_binding_late_result_after_shutdown_is_bounded(tmp_path):
    from agent.mcp import McpToolBinding

    registry, catalog = await _ready_fixture_registry(tmp_path)
    entry = next(t for t in catalog.tools if t.original_name == "echo")
    binding = McpToolBinding(
        server_id="fixture", original_name="echo", alias=entry.alias,
        description=entry.description, args_schema=entry.input_schema,
        config_generation=1, catalog_generation=1,
    )
    await registry.shutdown()
    tool = binding.as_langchain_tool(registry)
    result = await tool.ainvoke({"value": "x"})
    assert "MCP_UNAVAILABLE" in result


async def test_binding_call_budget_is_bounded(tmp_path):
    """60s 预算端到端覆盖队列等待；这里验证结构存在（不全等 60s）。"""
    from agent.mcp import CALL_TIMEOUT

    assert CALL_TIMEOUT == 60.0


async def test_production_resolver_still_returns_no_mcp_alias(tmp_path):
    from agent.capabilities import CapabilityPreview, CapabilityResolver
    from agent.router import build_services

    services = build_services(tmp_path / "agent")
    registry, catalog = await _ready_fixture_registry(tmp_path)
    services.registry = registry  # 即便有健康目录也不暴露
    try:
        resolver = CapabilityResolver(services.skills)
        lease = await resolver.acquire(CapabilityPreview(
            thread_id="th", thread_revision=0, selected_skills=()))
        assert all(not t.name.startswith("mcp__") for t in lease.tools)
        lease.release()
    finally:
        await registry.shutdown()
