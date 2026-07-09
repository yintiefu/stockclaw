"""Decision Node 关键测试——basis_type 归并规则（最大不确定性优先）+ 字段级 model_versions_json。"""
import pytest

from agents.nodes.decision import merge_basis_type, build_decision_card


def test_merge_basis_type_max_uncertainty_wins():
    """归并规则：llm_reasoning > hybrid > model_fallback > model。"""
    assert merge_basis_type(["model", "model", "model"]) == "model"
    assert merge_basis_type(["model", "model_fallback"]) == "model_fallback"
    assert merge_basis_type(["model", "hybrid"]) == "hybrid"
    assert merge_basis_type(["model", "llm_reasoning"]) == "llm_reasoning"
    assert merge_basis_type(["model_fallback", "hybrid", "llm_reasoning"]) == "llm_reasoning"


def test_merge_basis_type_empty_returns_model():
    assert merge_basis_type([]) == "model"


def test_build_decision_card_basic():
    """合并工具结果为决策卡：字段级 model_versions_json + cadence 数组。"""
    tool_results = {
        "target": {  # 来自 forward_pe_target
            "tool": "forward_pe_target", "basis_type": "model",
            "model_version": "forward_pe_target.v1",
            "outputs": {"target_price": 1900.0, "current_price": 1685.0},
        },
        "stop": {  # 来自 atr_stop（fallback）
            "tool": "atr_stop", "basis_type": "model_fallback",
            "model_version": "atr_stop.v1",
            "outputs": {"stop_price": 1550.2, "current_price": 1685.0, "fallback_reason": "no_kline"},
        },
        "entry": {  # 来自 pe_percentile_revert
            "tool": "pe_percentile_revert", "basis_type": "model",
            "model_version": "pe_percentile_revert.v1",
            "outputs": {"target_price": 1900.0, "current_price": 1685.0},
        },
        "position": {  # 来自 risk_based_position
            "tool": "risk_based_position", "basis_type": "model",
            "model_version": "risk_based_position.v1",
            "outputs": {"shares": 125.0, "position_pct_of_equity": 0.21},
        },
    }
    card = build_decision_card(
        code="600519", name="贵州茅台",
        current_price=1685.0,
        target_price=1900.0, entry_low=1685.0, entry_high=1720.0,
        stop_loss=1550.2, take_profit=2080.0,
        cadence=[
            {"batch": 1, "pct": 0.40, "trigger": "immediate", "price": 1685.0},
            {"batch": 2, "pct": 0.30, "trigger": "pullback_to:1650", "price": 1650.0},
            {"batch": 3, "pct": 0.30, "trigger": "breakout_above:1780", "price": 1780.0},
        ],
        tool_results=tool_results,
        explanation="基于 forward PE 目标价 1900 + ATR fallback 止损 1550",
    )
    # 整卡 basis_type = model_fallback（含一个 model_fallback 字段）
    assert card["basis_type"] == "model_fallback"
    # 字段级 model_versions_json
    mv = card["model_versions_json"]
    assert "target_price" in mv and "forward_pe_target.v1" in mv["target_price"]
    assert "stop_loss" in mv and "atr_stop.v1" in mv["stop_loss"]
    assert "model_fallback" in mv["stop_loss"] or "fallback" in mv["stop_loss"].lower()
    # cadence 是数组
    assert isinstance(card["cadence"], list) and len(card["cadence"]) == 3
    # citations 来自所有工具
    assert len(card["citations"]) >= 1
    # code / name / current_price
    assert card["code"] == "600519"
    assert card["name"] == "贵州茅台"
    assert card["current_price"] == 1685.0
