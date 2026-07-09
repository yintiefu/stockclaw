"""portfolio.json totals 字段扩展——向后兼容老 JSON。"""
import json

import portfolio as pf


def test_get_portfolio_returns_totals_with_new_fields(tmp_path):
    """totals 必须含 available_cash / risk_tolerance_pct / total_equity_override。"""
    pf_file = tmp_path / "portfolio.json"
    pf_file.write_text(json.dumps({
        "holdings": [],
        "totals": {
            "available_cash": 100000.0,
            "risk_tolerance_pct": 0.01,
            "total_equity_override": None,
        },
    }, ensure_ascii=False))
    # Patch BOTH PF_FILE (used by _load) and the import-time cached path
    import unittest.mock as mock
    with mock.patch.object(pf, "PF_FILE", str(pf_file)):
        result = pf.get_portfolio()
    t = result["totals"]
    assert t["available_cash"] == 100000.0
    assert t["risk_tolerance_pct"] == 0.01
    assert "total_equity_override" in t  # None 也算有


def test_get_portfolio_backward_compat_old_json_without_totals(tmp_path):
    """老 JSON 没有 totals 字段 → defaults 兼容填充。"""
    pf_file = tmp_path / "portfolio.json"
    pf_file.write_text(json.dumps({"holdings": [], "last_refresh": None}))
    import unittest.mock as mock
    with mock.patch.object(pf, "PF_FILE", str(pf_file)):
        result = pf.get_portfolio()
    t = result["totals"]
    # 老字段保留
    assert "market_value" in t and "cost" in t and "pnl" in t
    # 新字段给默认值
    assert t["available_cash"] == 0.0
    assert t["risk_tolerance_pct"] == 0.01  # 默认 1%
    assert t["total_equity_override"] is None


def test_get_portfolio_partial_totals_only_some_fields(tmp_path):
    """只填了 available_cash 没 risk_tolerance_pct → 后者用默认。"""
    pf_file = tmp_path / "portfolio.json"
    pf_file.write_text(json.dumps({
        "holdings": [],
        "totals": {"available_cash": 50000.0},
    }))
    import unittest.mock as mock
    with mock.patch.object(pf, "PF_FILE", str(pf_file)):
        result = pf.get_portfolio()
    t = result["totals"]
    assert t["available_cash"] == 50000.0
    assert t["risk_tolerance_pct"] == 0.01
    assert t["total_equity_override"] is None
