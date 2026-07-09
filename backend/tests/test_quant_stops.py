"""quant.stops 单测——含 A 股主路径 + 美港股 model_fallback 降级链。"""
from unittest.mock import patch

import pytest

import quant.stops as s


def _mock_kline(rows=30, base=1685.0):
    """构造 astock.kline 的 mock 返回（rows 条日 K，含 high/low/close）。"""
    out = []
    for i in range(rows):
        # 模拟波动：日 high/low 上下浮动
        out.append({
            "date": f"2026-06-{i+1:02d}",
            "open": base - 5, "close": base + (i % 3 - 1) * 3,
            "high": base + 20, "low": base - 25,
            "vol": 100000, "amount": 168500000.0,
        })
    return out


def test_atr_stop_main_path_a_share():
    """A 股主路径：14 日 ATR × 2.0 倍数 → stop_price。basis_type: model。"""
    with patch("quant.stops.astock.kline", return_value=_mock_kline(rows=30)), \
         patch("quant.stops.astock.tencent_quote", return_value={"600519": {"price": 1700.0, "name": "测试"}}):
        result = s.atr_stop("600519", period=14, multiplier=2.0)
    assert result["basis_type"] == "model"
    assert result["model_version"] == "atr_stop.v1"
    assert "stop_price" in result["outputs"]
    assert "current_price" in result["outputs"]
    assert "distance_pct" in result["outputs"]
    assert result["outputs"]["stop_price"] < result["outputs"]["current_price"]


def test_atr_stop_no_kline_falls_back_to_fixed_pct():
    """K 线数据不足 → model_fallback：当前价 × (1 - 0.08)。"""
    # 模拟空 K 线（mootdx 未装或返回空）
    with patch("quant.stops.astock.kline", return_value=[]), \
         patch("quant.stops.astock.tencent_quote", return_value={"600519": {"price": 100.0, "name": "测试"}}):
        result = s.atr_stop("600519", period=14, multiplier=2.0)
    assert result["basis_type"] == "model_fallback"
    assert result["outputs"]["stop_price"] == pytest.approx(92.0, rel=1e-3)  # 100 × 0.92
    assert "fallback_reason" in result["outputs"]
    assert result["outputs"]["fallback_reason"]


def test_atr_stop_us_stock_falls_back():
    """美港股代码（非 6 位数字）→ 直接走 model_fallback。"""
    with patch("gstock.us_hk_stock", return_value={"quote": {"price": 200.0}}):
        result = s.atr_stop("AAPL", period=14, multiplier=2.0)
    assert result["basis_type"] == "model_fallback"
    assert result["outputs"]["stop_price"] == pytest.approx(184.0, rel=1e-3)


def test_structure_stop_uses_recent_low():
    """结构止损 = 近 60 日最低价。"""
    with patch("quant.stops.astock.kline", return_value=_mock_kline(rows=60)), \
         patch("quant.stops.astock.tencent_quote", return_value={"600519": {"price": 1700.0, "name": "测试"}}):
        result = s.structure_stop("600519", lookback=60)
    assert result["basis_type"] == "model"
    assert "stop_price" in result["outputs"]
    # _mock_kline 的 low 都是 base - 25 = 1660
    assert result["outputs"]["stop_price"] == pytest.approx(1660.0, rel=1e-3)


def test_risk_based_position_basic():
    """按止损距离反推仓位：风险 1% × 总净值 / 单股止损距离 = 股数。"""
    result = s.risk_based_position(
        entry_price=100.0, stop_price=92.0,
        total_equity=100000.0, risk_tolerance_pct=0.01,
    )
    assert result["basis_type"] == "model"
    # 单股风险 = 100 - 92 = 8；总风险 = 100000 × 0.01 = 1000；股数 = 1000 / 8 = 125
    assert result["outputs"]["shares"] == pytest.approx(125.0, rel=1e-3)
    assert result["outputs"]["position_value"] == pytest.approx(12500.0, rel=1e-3)


def test_risk_based_position_falls_back_to_pct_when_no_cash():
    """无 cash/equity → 用比例表达（30%/30%/40%），不算绝对股数。"""
    result = s.risk_based_position(entry_price=100.0, stop_price=92.0)
    assert result["basis_type"] == "model_fallback"
    assert "shares" not in result["outputs"] or result["outputs"]["shares"] is None
    assert "position_pct" in result["outputs"]
