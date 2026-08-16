"""Task 1C-1：本地 mootdx 发行版与锁定 MCP 依赖栈的兼容性测试。

验证：
- 本地 `mootdx==0.11.7+vr1` 与 mcp / langchain-mcp-adapters / httpx 锁定版本可共存；
- vendored Python 源码与上游 0.11.7 wheel 逐字节一致（仅打包元数据不同）；
- 离线 Fake TDX 下 Quotes 的 bars / finance / F10C / F10 可用（不打开 socket）。
"""
from __future__ import annotations

import json
import os
from hashlib import sha256
from importlib.metadata import version
from pathlib import Path

import pandas
import pytest

CODE = "600519"


# ---------------------------------------------------------------------------
# 锁定版本
# ---------------------------------------------------------------------------

def test_locked_mcp_stack_and_local_mootdx_are_importable():
    assert version("mootdx") == "0.11.7+vr1"
    assert version("langchain-mcp-adapters") == "0.3.2"
    assert version("mcp") == "1.26.0"
    assert version("httpx") == "0.28.1"


# ---------------------------------------------------------------------------
# 上游完整性
# ---------------------------------------------------------------------------

def _parse_manifest(path: Path) -> dict[str, str]:
    expected: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        digest, name = line.split("  ", 1)
        expected[name.strip().lstrip("./")] = digest
    return expected


def test_vendored_python_sources_match_upstream_manifest():
    root = Path(__file__).parents[2] / "vendor/mootdx_compat/src/mootdx"
    expected = _parse_manifest(root.parents[1] / "upstream.sha256")
    actual = {p.relative_to(root).as_posix(): sha256(p.read_bytes()).hexdigest()
              for p in root.rglob("*") if p.is_file() and p.suffix in {".py", ".js"}}
    assert actual == expected


def test_local_distribution_metadata_declares_only_httpx_constraint_change():
    pyproject = Path(__file__).parents[2] / "vendor/mootdx_compat/pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    assert 'version = "0.11.7+vr1"' in text
    assert '"httpx>=0.27.1,<1"' in text


# ---------------------------------------------------------------------------
# 离线 Quotes 契约
# ---------------------------------------------------------------------------

class FakeTdxHqApi:
    """确定性本地 TDX 客户端替身：绝不开 socket。"""

    def __init__(self, *args, **kwargs):
        self.connected = False

    def connect(self, ip, port, time_out=None):
        self.connected = True
        return True

    def close(self):
        self.connected = False

    # -- bars ---------------------------------------------------------------
    def get_security_bars(self, frequency, market, code, start, offset):
        rows = []
        for i in range(offset):
            rows.append({
                "open": 1700.0 + i, "close": 1701.0 + i, "high": 1710.0 + i,
                "low": 1690.0 + i, "vol": 1000 + i, "amount": 1.7e6 + i,
                "year": 2026, "month": 1, "day": 2 + i, "hour": 15, "minute": 0,
                "datetime": f"2026-01-{2 + i:02d} 15:00",
            })
        return rows

    # -- finance ------------------------------------------------------------
    def get_finance_info(self, market, code):
        return {"code": code, "liaodong": 1.25e6, "zongguben": 1.256e6,
                "liutongguben": 1.256e6, "meigujingzichan": 120.5}

    # -- F10 ----------------------------------------------------------------
    def get_company_info_category(self, market, code):
        return [{"name": "公司概况", "filename": "gsgk.txt", "start": 0, "length": 100}]

    def get_company_info_content(self, market, code, filename, start, length):
        return "贵州茅台，主营收白酒。"


@pytest.fixture()
def fake_home(tmp_path, monkeypatch):
    """mootdx 会在 Path.home()/.mootdx 写配置——隔离到临时目录。"""
    home = tmp_path / "home"
    home.mkdir()
    conf_dir = home / ".mootdx"
    conf_dir.mkdir()
    (conf_dir / "config.json").write_text(
        json.dumps({"SERVER": {"HQ": [["std", "127.0.0.1", 7709]]}}), encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("mootdx.quotes.TdxHq_API", FakeTdxHqApi)
    return home


def test_mootdx_factory_bars_finance_and_f10_use_offline_tdx_fake(fake_home):
    from mootdx.quotes import Quotes

    quotes = Quotes.factory(market="std", server=("127.0.0.1", 7709))
    assert not quotes.bars(CODE, offset=1).empty
    assert not quotes.finance(CODE).empty
    assert quotes.F10C(CODE)[0]["name"] == "公司概况"
    assert "公司概况" in quotes.F10(CODE)
    # 确认 DataFrame 形状可用（astock.kline 走同一路径）
    records = quotes.bars(CODE, offset=2).to_dict("records")
    assert len(records) == 2 and "close" in records[0]


def test_mootdx_offline_fake_never_opens_socket(fake_home):
    import mootdx.quotes as quotes_mod

    created = quotes_mod.TdxHq_API
    assert created is FakeTdxHqApi
    instance = created()
    assert instance.connect("127.0.0.1", 7709) is True
    assert not hasattr(instance, "socket")
