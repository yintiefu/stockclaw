"""仓位节奏类工具：金字塔加仓 / 分批建仓 / 定投。

纯 Python 函数，无 LLM。返回统一 contract（basis_type 永远是 model——这些是策略规则，
不依赖外部数据可用性；具体执行时的价格由 Decision Node 在合并时给定）。
"""
from __future__ import annotations

from typing import Any


def _contract(tool, inputs, outputs, model_version, assumptions, explanation, basis_type="model"):
    return {
        "tool": tool, "inputs": inputs, "outputs": outputs,
        "basis_type": basis_type, "model_version": model_version,
        "model_assumptions": assumptions,
        "citations": [{"source": "internal.strategy", "note": "用户配置的策略规则"}],
        "explanation": explanation,
    }


def _parse_trigger_price(trigger: str, default: float) -> float:
    """从 trigger 字符串解析参考价。

    支持 "immediate" / "pullback_to:95" / "breakout_above:105" / "day_offset:7"
    """
    if ":" in trigger:
        try:
            return float(trigger.split(":", 1)[1])
        except (ValueError, IndexError):
            return default
    return default


def pyramid_buy(current_price: float, total_budget: float, batches: int,
                ratios: list[float], triggers: list[str]) -> dict[str, Any]:
    """金字塔加仓：底部仓位大、顶部小；触发价递增。

    current_price: 当前价（决定第一批 ref_price）
    total_budget: 总预算
    batches: 批次数（必须 == len(ratios) == len(triggers)）
    ratios: 每批占比，和必须 = 1.0（如 [0.40, 0.30, 0.30]）
    triggers: 触发条件（如 ["immediate", "pullback_to:95", "breakout_above:105"]）

    返回 outputs: {plan: [{batch, pct, amount, trigger, ref_price}, ...]}
    """
    if not (len(ratios) == batches == len(triggers)):
        raise ValueError(f"批次数 / ratios / triggers 长度不一致：{batches} / {len(ratios)} / {len(triggers)}")
    if abs(sum(ratios) - 1.0) > 1e-6:
        raise ValueError(f"比例之和必须 = 1.0，当前 = {sum(ratios):.4f}")

    plan = []
    for i, (ratio, trigger) in enumerate(zip(ratios, triggers)):
        ref_price = current_price if i == 0 else _parse_trigger_price(trigger, current_price)
        plan.append({
            "batch": i + 1,
            "pct": round(ratio, 4),
            "amount": round(total_budget * ratio, 2),
            "trigger": trigger,
            "ref_price": ref_price,
        })

    return _contract(
        tool="pyramid_buy",
        inputs={"current_price": current_price, "total_budget": total_budget, "batches": batches,
                "ratios": ratios, "triggers": triggers},
        outputs={"plan": plan},
        model_version="pyramid_buy.v1",
        assumptions=[f"{batches} 批金字塔", f"比例 {ratios}", "触发条件见 plan"],
        explanation=f"金字塔加仓：底部 {ratios[0]:.0%}、递增触发；总预算 {total_budget} 拆为 {batches} 批",
    )


def batch_build(total_budget: float, batches: int, schedule: str, start_price: float) -> dict[str, Any]:
    """分批建仓：等额 N 批，按周期触发（不择时，平摊成本）。

    schedule: "weekly" / "biweekly" / "monthly"
    """
    if batches <= 0:
        raise ValueError("批次数必须 > 0")
    schedule_days = {"weekly": 7, "biweekly": 14, "monthly": 30}
    days = schedule_days.get(schedule, 7)
    per = total_budget / batches
    plan = []
    for i in range(batches):
        plan.append({
            "batch": i + 1,
            "pct": round(1.0 / batches, 4),
            "amount": round(per, 2),
            "trigger": f"day_offset:{i * days}",
            "ref_price": start_price,  # 不择时，按当时市价
        })

    return _contract(
        tool="batch_build",
        inputs={"total_budget": total_budget, "batches": batches, "schedule": schedule, "start_price": start_price},
        outputs={"plan": plan},
        model_version="batch_build.v1",
        assumptions=[f"等额 {batches} 批", f"{schedule} 触发", "不择时，平摊成本"],
        explanation=f"分批建仓：{total_budget} 拆为 {batches} 批 × {per:.2f}，每 {days} 天一批",
    )


def dca_plan(periodic_amount: float, periods: int, schedule: str) -> dict[str, Any]:
    """定投：固定周期 + 固定金额。

    periodic_amount: 每次定投金额
    periods: 期数
    schedule: "weekly" / "biweekly" / "monthly"
    """
    if periodic_amount <= 0 or periods <= 0:
        raise ValueError("金额和期数必须 > 0")
    schedule_days = {"weekly": 7, "biweekly": 14, "monthly": 30}
    days = schedule_days.get(schedule, 7)
    plan = []
    for i in range(periods):
        plan.append({
            "batch": i + 1,
            "amount": periodic_amount,
            "trigger": f"day_offset:{i * days}",
        })

    return _contract(
        tool="dca_plan",
        inputs={"periodic_amount": periodic_amount, "periods": periods, "schedule": schedule},
        outputs={"plan": plan, "total_invested": round(periodic_amount * periods, 2)},
        model_version="dca_plan.v1",
        assumptions=[f"每 {schedule} 投 {periodic_amount}", f"共 {periods} 期"],
        explanation=f"定投：每 {days} 天投 {periodic_amount}，共 {periods} 期，总投入 {periodic_amount * periods:.2f}",
    )
