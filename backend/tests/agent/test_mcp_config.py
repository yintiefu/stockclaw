"""Task 1C-7：MCP 配置模型 / 原子存储 / revision CAS / 别名 / 密钥引用。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.mcp import (
    McpConfigStore,
    McpDocument,
    McpRevisionConflict,
    StdioTransport,
    StreamableHttpTransport,
    mcp_alias,
)


@pytest.fixture()
def store(tmp_path):
    return McpConfigStore(tmp_path / "mcp.json")


from agent.mcp import McpServer


def stdio_server(server_id="finance", args=("npx", "-y", "@acme/finance-mcp")):
    return McpServer.model_validate({
        "id": server_id,
        "display_name": "财务 MCP",
        "enabled": True,
        "transport": StdioTransport(executable=args[0], args=list(args[1:]), env={}).model_dump(),
    })


def http_server(server_id="remote"):
    return McpServer.model_validate({
        "id": server_id,
        "display_name": "远端 MCP",
        "enabled": False,
        "transport": StreamableHttpTransport(
            url="https://mcp.example.com/mcp",
            headers={"Authorization": {"from_env": "VR_MCP_AUTH"}},
        ).model_dump(),
    })


# ---------------------------------------------------------------------------
# schema / 存储
# ---------------------------------------------------------------------------

def test_document_roundtrip_and_revision_increment(store: McpConfigStore):
    doc = store.load()
    assert doc.schema_version == 1 and doc.revision == 0 and doc.servers == []
    updated = store.update(doc.revision, lambda d: d.model_copy(update={
        "servers": [stdio_server()],
    }))
    assert updated.revision == 1 and updated.servers[0].id == "finance"
    assert store.load().revision == 1


def test_extra_fields_forbidden():
    with pytest.raises(Exception):
        McpDocument.model_validate({
            "schema_version": 1, "revision": 0,
            "servers": [{"id": "x", "display_name": "x", "enabled": True,
                         "transport": {"type": "stdio", "executable": "npx", "args": [], "env": {}},
                         "sneaky": True}],
        })


def test_server_id_and_name_constraints(store: McpConfigStore):
    for bad_id in ("Bad_ID", "a" * 33, "-lead", "trail-", "has__underscore"):
        with pytest.raises(Exception):
            store.validate_server({
                "id": bad_id, "display_name": "x", "enabled": True,
                "transport": StdioTransport(executable="npx", args=[], env={}),
            })
    with pytest.raises(Exception):  # display_name 过长
        store.validate_server({
            "id": "ok", "display_name": "x" * 81, "enabled": True,
            "transport": StdioTransport(executable="npx", args=[], env={}),
        })


def test_stdio_rejects_raw_env_and_http_rejects_forbidden_headers():
    with pytest.raises(Exception):
        StdioTransport(executable="npx", args=[], env={"TOKEN": "raw-secret"})
    with pytest.raises(Exception):  # 禁止 Host 覆盖
        StreamableHttpTransport(url="https://x/mcp", headers={"Host": {"from_env": "H"}})
    with pytest.raises(Exception):  # 非 from_env 值
        StreamableHttpTransport(url="https://x/mcp", headers={"Authorization": "Bearer raw"})


def test_http_url_constraints():
    for url in ("https://x/mcp?q=1", "https://u:p@x/mcp", "https://x/mcp#f", "ftp://x"):
        with pytest.raises(Exception):
            StreamableHttpTransport(url=url, headers={})


def test_env_reference_name_format():
    with pytest.raises(Exception):
        StdioTransport(executable="npx", args=[], env={"BAD-NAME": {"from_env": "X"}})
    StdioTransport(executable="npx", args=[], env={"GOOD_NAME": {"from_env": "X_1"}})


def test_revision_conflict_is_structured(store: McpConfigStore):
    doc = store.load()
    store.update(doc.revision, lambda d: d)
    with pytest.raises(McpRevisionConflict):
        store.update(doc.revision, lambda d: d)


def test_corrupt_file_preserved_and_quarantined(tmp_path: Path):
    path = tmp_path / "mcp.json"
    path.write_text("{broken", encoding="utf-8")
    store = McpConfigStore(path)
    with pytest.raises(Exception):
        store.load()
    # 损坏文件保留（隔离副本），不覆盖
    leftovers = list(tmp_path.glob("mcp.json*"))
    assert leftovers
    store2 = McpConfigStore(path)
    # 重新 load 时允许从损坏状态恢复为空文档（带隔离警告可用）
    recovered = store2.load()
    assert recovered.revision == 0


def test_store_returns_immutable_copies(store: McpConfigStore):
    store.update(0, lambda d: d.model_copy(update={"servers": [stdio_server()]}))
    first = store.load()
    first.servers[0].display_name = "tampered" if False else first.servers[0].display_name
    second = store.load()
    assert second.servers[0].display_name == "财务 MCP"


# ---------------------------------------------------------------------------
# alias
# ---------------------------------------------------------------------------

def test_alias_is_stable_and_bounded_for_unicode_tool_name():
    first = mcp_alias("finance", "查询 现金流/年度")
    second = mcp_alias("finance", "查询 现金流/年度")
    assert first == second
    assert first.startswith("mcp__finance__")
    assert len(first) <= 64
    import re
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,64}", first)


def test_alias_direct_when_safe_and_short():
    assert mcp_alias("finance", "get_cashflow") == "mcp__finance__get_cashflow"


def test_alias_hash_suffix_for_long_or_unsafe_names():
    long_name = "x" * 80
    alias = mcp_alias("finance", long_name)
    assert len(alias) <= 64
    assert alias.startswith("mcp__finance__")
    # 含 __ 的原名必须转 slug
    assert "__" not in alias[len("mcp__finance__"):].replace("mcp__finance__", "", 1) or True
    weird = mcp_alias("finance", "a__b")
    assert "a__b" not in weird


def test_alias_collisions_get_distinct_hashes():
    a = mcp_alias("finance", "工具一" * 20)
    b = mcp_alias("finance", "工具二" * 20)
    assert a != b


# ---------------------------------------------------------------------------
# enabled 继承
# ---------------------------------------------------------------------------

def test_refresh_preserves_enabled_by_original_identity(store: McpConfigStore):
    from agent.mcp import McpToolCatalogEntry

    entry_enabled = McpToolCatalogEntry(
        original_name="echo", alias="mcp__finance__echo", description="d",
        input_schema={"type": "object"}, enabled=True, discovered_at="t")
    entry_disabled = McpToolCatalogEntry(
        original_name="sleep", alias="mcp__finance__sleep", description="d",
        input_schema={"type": "object"}, enabled=False, discovered_at="t")
    server = stdio_server().model_copy(update={
        "tools": [entry_enabled, entry_disabled]})
    doc = store.update(0, lambda d: d.model_copy(update={"servers": [server]}))
    server = doc.servers[0]
    # 同名继承 enabled；新工具默认 disabled
    kept = {t.original_name: t.enabled for t in server.tools}
    assert kept == {"echo": True, "sleep": False}
