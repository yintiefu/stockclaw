"""quant.cadence 单测——纯逻辑、无网络。"""
import pytest

import quant.cadence as c


def test_pyramid_buy_basic():
    """金字塔加仓：3 批，比例 40%/30%/30%，触发价递增。"""
    result = c.pyramid_buy(
        current_price=100.0,
        total_budget=100000.0,
        batches=3,
        ratios=[0.40, 0.30, 0.30],
        triggers=["immediate", "pullback_to:95", "breakout_above:105"],
    )
    assert result["basis_type"] == "model"
    plan = result["outputs"]["plan"]
    assert len(plan) == 3
    assert plan[0]["batch"] == 1
    assert plan[0]["pct"] == 0.40
    assert plan[0]["amount"] == pytest.approx(40000.0, rel=1e-3)
    assert plan[0]["trigger"] == "immediate"
    assert plan[0]["ref_price"] == 100.0
    assert plan[1]["trigger"] == "pullback_to:95"
    assert plan[1]["ref_price"] == 95.0
    assert plan[2]["trigger"] == "breakout_above:105"
    assert plan[2]["ref_price"] == 105.0


def test_pyramid_buy_rejects_invalid_ratios():
    """比例之和 != 1.0 → 抛 ValueError。"""
    with pytest.raises(ValueError, match="比例之和"):
        c.pyramid_buy(
            current_price=100.0, total_budget=100000.0, batches=3,
            ratios=[0.5, 0.3, 0.3],  # 和 = 1.1
            triggers=["immediate", "pullback_to:95", "breakout_above:105"],
        )


def test_batch_build_basic():
    """分批建仓：等额 4 批，每周一批。"""
    result = c.batch_build(
        total_budget=80000.0, batches=4,
        schedule="weekly", start_price=100.0,
    )
    assert result["basis_type"] == "model"
    plan = result["outputs"]["plan"]
    assert len(plan) == 4
    for i, batch in enumerate(plan):
        assert batch["pct"] == pytest.approx(0.25, rel=1e-3)
        assert batch["amount"] == pytest.approx(20000.0, rel=1e-3)
        assert batch["trigger"] == f"day_offset:{i*7}"


def test_dca_plan_basic():
    """定投：12 周，每周 5000 元。"""
    result = c.dca_plan(
        periodic_amount=5000.0, periods=12,
        schedule="weekly",
    )
    assert result["basis_type"] == "model"
    plan = result["outputs"]["plan"]
    assert len(plan) == 12
    assert plan[0]["amount"] == 5000.0
    assert plan[0]["trigger"] == "day_offset:0"
    assert plan[11]["trigger"] == "day_offset:77"  # 11 周 × 7 天
    assert result["outputs"]["total_invested"] == 60000.0
