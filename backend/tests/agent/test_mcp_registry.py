"""Task 1C-8：McpRegistry —— stdio 信任、进程生命周期与恢复。"""
from __future__ import annotations

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
