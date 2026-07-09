"""quant.valuation 单测——纯逻辑、无网络（mock astock.full_valuation）。"""
from unittest.mock import patch

import pytest

import quant.valuation as v


def _mock_full_valuation(price=1685.0, pe_ttm=18.0, eps_26e=85.0, eps_27e=95.0):
    """构造 astock.full_valuation 的 mock 返回。"""
    return {
        "code": "600519", "name": "贵州茅台", "price": price,
        "pe_ttm": pe_ttm, "pb": 6.0,
        "eps_26e": eps_26e, "eps_27e": eps_27e,
        "pe_26e": price / eps_26e if eps_26e else None,
        "cagr_pct": 0.15, "peg": None, "digest_years": None,
        "analyst_count": 30, "mcap_yi": 15000,
    }


def test_forward_pe_target_basic():
    """前向 PE × 一致 EPS = 目标价。"""
    with patch("quant.valuation.astock.full_valuation", return_value=_mock_full_valuation(price=1685.0, eps_27e=95.0)):
        result = v.forward_pe_target("600519", target_pe=20.0, eps_year="27e")
    assert result["tool"] == "forward_pe_target"
    assert result["basis_type"] == "model"
    assert result["model_version"] == "forward_pe_target.v1"
    assert result["outputs"]["target_price"] == pytest.approx(20.0 * 95.0, rel=1e-3)  # 1900
    assert result["outputs"]["current_price"] == 1685.0
    assert "citations" in result and result["citations"][0]["source"] == "astock.full_valuation"


def test_forward_pe_target_data_unavailable_when_no_eps():
    """一致 EPS 缺失（None）→ 抛 DataUnavailable，由上层降级。"""
    with patch("quant.valuation.astock.full_valuation", return_value=_mock_full_valuation(eps_27e=None)):
        with pytest.raises(v.DataUnavailable) as exc_info:
            v.forward_pe_target("600519", target_pe=20.0, eps_year="27e")
        assert "一致 EPS" in str(exc_info.value)


def test_pe_percentile_revert_basic():
    """PE 处于历史 80 分位 → 回复到 50 分位的目标价。"""
    mock_pct = {
        "metrics": {
            "pe_ttm": {
                "current": 30.0, "percentile": 80.0,   # astock 返回 [0, 100]
                "p20": 15.0, "p50": 22.0, "p80": 32.0, "min": 10.0, "max": 40.0, "n": 1200,
            }
        }
    }
    mock_quote = {"600519": {"price": 1685.0, "pe_ttm": 30.0, "name": "贵州茅台"}}
    with patch("quant.valuation.astock.valuation_percentile", return_value=mock_pct), \
         patch("quant.valuation.astock.tencent_quote", return_value=mock_quote):
        result = v.pe_percentile_revert("600519", revert_to=0.50)
    assert result["basis_type"] == "model"
    # 目标价 = 当前价 × (50 分位 PE / 当前 PE) = 1685 × (22 / 30) ≈ 1235.67
    assert result["outputs"]["target_price"] == pytest.approx(1685.0 * 22.0 / 30.0, rel=1e-3)
    assert result["outputs"]["current_percentile"] == 0.80   # 内部 /100 后应等于 0.80
    assert result["outputs"]["revert_to"] == 0.50


def test_pe_percentile_revert_zero_pe_raises_data_unavailable():
    """亏损股 current_pe=0 → 抛 DataUnavailable（防 ZeroDivisionError）。"""
    mock_pct = {
        "metrics": {
            "pe_ttm": {
                "current": 0.0, "percentile": 0.0,
                "p20": 15.0, "p50": 22.0, "p80": 32.0, "min": 10.0, "max": 40.0, "n": 1200,
            }
        }
    }
    mock_quote = {"600519": {"price": 1685.0, "pe_ttm": 0.0, "name": "贵州茅台"}}
    with patch("quant.valuation.astock.valuation_percentile", return_value=mock_pct), \
         patch("quant.valuation.astock.tencent_quote", return_value=mock_quote):
        with pytest.raises(v.DataUnavailable) as exc_info:
            v.pe_percentile_revert("600519", revert_to=0.50)
        assert "current_pe" in str(exc_info.value)


def test_forward_pe_target_negative_eps_raises_data_unavailable():
    """亏损公司一致 EPS<0 → 抛 DataUnavailable（防负目标价）。"""
    with patch("quant.valuation.astock.full_valuation",
               return_value=_mock_full_valuation(eps_27e=-5.0)):
        with pytest.raises(v.DataUnavailable) as exc_info:
            v.forward_pe_target("600519", target_pe=20.0, eps_year="27e")
        assert "非正 EPS" in str(exc_info.value)
