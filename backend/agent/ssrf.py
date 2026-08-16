"""无副作用共享 SSRF 校验：模型 BaseURL 与 MCP 出站地址共用同一套策略。

不 import app.py，不启动调度器，不做网络请求（域名解析除外——那是校验本身）。
"""
from __future__ import annotations

import ipaddress
import os
import socket
import threading
from urllib.parse import urlparse

_METADATA_NETS = [ipaddress.ip_network("169.254.0.0/16"), ipaddress.ip_network("fe80::/10")]
_PRIVATE_NETS = [ipaddress.ip_network(n) for n in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8", "::1/128",
    "fc00::/7", "fe80::/10",
)]

PUBLIC_MODE_CACHE: bool | None = None
_CACHE_LOCK = threading.Lock()


def public_mode() -> bool:
    """VR_API_KEY 已设 → 公网部署姿态。结果进程内缓存。"""
    global PUBLIC_MODE_CACHE
    with _CACHE_LOCK:
        if PUBLIC_MODE_CACHE is None:
            PUBLIC_MODE_CACHE = bool((os.environ.get("VR_API_KEY") or "").strip())
        return PUBLIC_MODE_CACHE


def _ip_blocked(host: str, *, public: bool) -> bool:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False  # 域名：由调用方决定是否解析核对
    if any(ip in n for n in _METADATA_NETS):
        return True
    if public and any(ip in n for n in _PRIVATE_NETS):
        return True
    return False


def validate_outbound_url(
    url: str,
    *,
    public_mode: bool,
    require_public_https: bool,
    allow_query: bool,
    allow_userinfo: bool,
):
    """校验出站 URL；返回 ParseResult。违规 raise RuntimeError（中文消息）。"""
    p = urlparse(url or "")
    if p.scheme not in ("http", "https"):
        raise RuntimeError("URL 必须以 http:// 或 https:// 开头")
    if require_public_https and public_mode and p.scheme != "https":
        raise RuntimeError("公网部署下该地址只允许 HTTPS")
    host = p.hostname or ""
    if not host:
        raise RuntimeError("URL 缺少主机名")
    if not allow_query and p.query:
        raise RuntimeError("URL 不允许携带 query")
    if not allow_userinfo and (p.username or p.password):
        raise RuntimeError("URL 不允许携带 userinfo")
    if p.fragment:
        raise RuntimeError("URL 不允许携带 fragment")
    if _ip_blocked(host, public=public_mode):
        raise RuntimeError("URL 指向了不允许的地址（云元数据 / 内网）")
    if public_mode:
        try:
            infos = socket.getaddrinfo(host, None)
        except socket.gaierror as e:
            raise RuntimeError("URL 域名无法解析") from e
        for info in infos:
            if _ip_blocked(info[4][0], public=public_mode):
                raise RuntimeError("URL 解析到了不允许的内网地址")
    return p
