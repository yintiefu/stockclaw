"""Task 1C-7：共享 SSRF 策略 —— 模型 BaseURL 行为不变 + MCP 本地/公网规则。"""
from __future__ import annotations

import pytest

import chat
from agent.ssrf import validate_outbound_url


# ---------------------------------------------------------------------------
# 模型 Base URL：现有行为回归（chat._check_base_url 兼容包装）
# ---------------------------------------------------------------------------

def test_model_base_url_accepts_https_public():
    validate_outbound_url("https://api.deepseek.com/v1", public_mode=False,
                          require_public_https=False, allow_query=False, allow_userinfo=False)


def test_model_base_url_rejects_non_http_and_missing_host():
    for bad in ("ftp://x", "https://", ""):
        with pytest.raises(RuntimeError):
            validate_outbound_url(bad, public_mode=False,
                                  require_public_https=False, allow_query=False, allow_userinfo=False)


def test_model_base_url_metadata_blocked_in_local_mode():
    with pytest.raises(RuntimeError):
        validate_outbound_url("http://169.254.169.254/latest", public_mode=False,
                              require_public_https=False, allow_query=False, allow_userinfo=False)
    with pytest.raises(RuntimeError):
        validate_outbound_url("http://[fe80::1]/mcp", public_mode=False,
                              require_public_https=False, allow_query=False, allow_userinfo=False)


def test_model_base_url_local_mode_allows_loopback():
    validate_outbound_url("http://127.0.0.1:11434/v1", public_mode=False,
                          require_public_https=False, allow_query=False, allow_userinfo=False)


def test_chat_check_base_url_still_works(monkeypatch):
    monkeypatch.setattr(chat, "_PUBLIC_MODE", False)
    chat._check_base_url("https://api.example.com/v1")
    with pytest.raises(RuntimeError):
        chat._check_base_url("http://169.254.169.254/x")


# ---------------------------------------------------------------------------
# MCP 规则：query/userinfo/fragment 禁止；public HTTPS 必须
# ---------------------------------------------------------------------------

def test_mcp_url_forbids_query_userinfo_fragment():
    for bad in (
        "https://mcp.example.com/mcp?token=x",
        "https://user:pass@mcp.example.com/mcp",
        "https://mcp.example.com/mcp#frag",
    ):
        with pytest.raises(RuntimeError):
            validate_outbound_url(bad, public_mode=False, require_public_https=True,
                                  allow_query=False, allow_userinfo=False)


def test_mcp_local_mode_allows_http_loopback():
    validate_outbound_url("http://127.0.0.1:8000/mcp", public_mode=False,
                          require_public_https=True, allow_query=False, allow_userinfo=False)


def test_mcp_public_mode_requires_https_and_blocks_private():
    with pytest.raises(RuntimeError):  # HTTP 且公网
        validate_outbound_url("http://mcp.example.com/mcp", public_mode=True,
                              require_public_https=True, allow_query=False, allow_userinfo=False)
    with pytest.raises(RuntimeError):  # 私网
        validate_outbound_url("https://192.168.1.5/mcp", public_mode=True,
                              require_public_https=True, allow_query=False, allow_userinfo=False)
    with pytest.raises(RuntimeError):  # loopback 公网禁
        validate_outbound_url("https://127.0.0.1/mcp", public_mode=True,
                              require_public_https=True, allow_query=False, allow_userinfo=False)
    validate_outbound_url("https://example.com/mcp", public_mode=True,
                          require_public_https=True, allow_query=False, allow_userinfo=False)
