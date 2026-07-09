"""估值类工具：前向 PE 目标价 + 历史分位回复。

纯 Python 函数，无 LLM。A 股数据齐走完整公式（model）；美港股或数据缺失抛 DataUnavailable，
由调用方（agents.tools）决定走 model_fallback 还是 llm_reasoning。
"""
from __future__ import annotations

from typing import Any

import astock


class DataUnavailable(Exception):
    """quant 工具因数据不足无法走完整公式。调用方应降级为 model_fallback 或 llm_reasoning。"""


def _contract(tool: str, inputs: dict, outputs: dict, model_version: str,
              assumptions: list[str], citations: list[dict], explanation: str,
              basis_type: str = "model") -> dict[str, Any]:
    """统一 contract 拼装。"""
    return {
        "tool": tool,
        "inputs": inputs,
        "outputs": outputs,
        "basis_type": basis_type,
        "model_version": model_version,
        "model_assumptions": assumptions,
        "citations": citations,
        "explanation": explanation,
    }


def forward_pe_target(code: str, target_pe: float = 20.0, eps_year: str = "27e") -> dict:
    """前向 PE × 一致 EPS = 目标价。

    code: A 股 6 位代码
    target_pe: 目标前向 PE（用户/LLM 给定，如行业均值 20x）
    eps_year: 用哪年的一致 EPS（"26e" / "27e"）

    返回 outputs: {target_price, current_price, current_pe, eps_used, target_pe}
    抛 DataUnavailable：当一致 EPS 缺失时（美港股 / A 股无覆盖）。
    """
    fv = astock.full_valuation(code)
    eps = fv.get(f"eps_{eps_year}")
    if not eps:
        raise DataUnavailable(f"{code} 缺 {eps_year} 一致 EPS，前向 PE 目标价不可计算")
    target_price = target_pe * eps
    return _contract(
        tool="forward_pe_target",
        inputs={"code": code, "target_pe": target_pe, "eps_year": eps_year},
        outputs={
            "target_price": round(target_price, 2),
            "current_price": fv.get("price"),
            "current_pe": fv.get("pe_ttm"),
            "eps_used": eps,
            "target_pe": target_pe,
        },
        model_version="forward_pe_target.v1",
        assumptions=[f"目标前向 PE = {target_pe}x", f"一致 EPS（{eps_year}）= {eps}"],
        citations=[{"source": "astock.full_valuation", "code": code}],
        explanation=f"目标价 {target_price:.2f} = 目标 PE {target_pe}x × 一致 EPS {eps}（{eps_year}）",
    )


def pe_percentile_revert(code: str, revert_to: float = 0.50, period: str = "近五年") -> dict:
    """PE 历史分位回复：当前 PE 处于 X 分位 → 回复到 revert_to 分位的目标价。

    code: A 股 6 位代码
    revert_to: 目标分位（0.0-1.0），默认 0.50（中位数）
    period: 历史窗口（"近五年" / "近十年"）

    返回 outputs: {target_price, current_price, current_percentile, revert_to, current_pe, revert_pe}
    抛 DataUnavailable：当历史分位数据不可用时。
    """
    pct = astock.valuation_percentile(code, period=period)
    pe_metric = (pct.get("metrics") or {}).get("pe_ttm")
    if not pe_metric or pe_metric.get("current") is None:
        raise DataUnavailable(f"{code} 缺 PE 历史分位数据")

    # 选目标分位对应的 PE：用线性插值近似（p20/p50/p80 是已知锚点）
    current_pe = pe_metric["current"]
    current_pct = pe_metric.get("percentile", 0.5)
    p20, p50, p80 = pe_metric.get("p20"), pe_metric.get("p50"), pe_metric.get("p80")
    revert_pe = _interp_percentile(revert_to, p20, p50, p80, pe_metric.get("min"), pe_metric.get("max"))
    if revert_pe is None:
        raise DataUnavailable(f"{code} PE 分位锚点不足，无法插值到 {revert_to}")

    quote = astock.tencent_quote([code])
    current_price = quote.get(code, {}).get("price")
    if not current_price:
        raise DataUnavailable(f"{code} 当前价缺失")

    # 目标价 = 当前价 × (目标 PE / 当前 PE)
    target_price = current_price * revert_pe / current_pe

    return _contract(
        tool="pe_percentile_revert",
        inputs={"code": code, "revert_to": revert_to, "period": period},
        outputs={
            "target_price": round(target_price, 2),
            "current_price": current_price,
            "current_percentile": current_pct,
            "revert_to": revert_to,
            "current_pe": current_pe,
            "revert_pe": revert_pe,
        },
        model_version="pe_percentile_revert.v1",
        assumptions=[f"目标分位 {revert_to}（{period}）", f"线性插值（p20={p20} / p50={p50} / p80={p80}）"],
        citations=[
            {"source": "astock.valuation_percentile", "code": code, "range": period},
            {"source": "astock.tencent_quote", "code": code},
        ],
        explanation=f"当前 PE {current_pe}（{current_pct:.0%} 分位），回复到 {revert_to:.0%} 分位 PE {revert_pe:.2f}，目标价 {target_price:.2f}",
    )


def pb_percentile_revert(code: str, revert_to: float = 0.50, period: str = "近五年") -> dict:
    """PB 历史分位回复：同上，但用 PB。重资产行业（银行/钢铁）适用。"""
    pct = astock.valuation_percentile(code, period=period)
    pb_metric = (pct.get("metrics") or {}).get("pb")
    if not pb_metric or pb_metric.get("current") is None:
        raise DataUnavailable(f"{code} 缺 PB 历史分位数据")

    current_pb = pb_metric["current"]
    current_pct = pb_metric.get("percentile", 0.5)
    p20, p50, p80 = pb_metric.get("p20"), pb_metric.get("p50"), pb_metric.get("p80")
    revert_pb = _interp_percentile(revert_to, p20, p50, p80, pb_metric.get("min"), pb_metric.get("max"))
    if revert_pb is None:
        raise DataUnavailable(f"{code} PB 分位锚点不足")

    quote = astock.tencent_quote([code])
    current_price = quote.get(code, {}).get("price")
    if not current_price:
        raise DataUnavailable(f"{code} 当前价缺失")

    target_price = current_price * revert_pb / current_pb

    return _contract(
        tool="pb_percentile_revert",
        inputs={"code": code, "revert_to": revert_to, "period": period},
        outputs={
            "target_price": round(target_price, 2),
            "current_price": current_price,
            "current_percentile": current_pct,
            "revert_to": revert_to,
            "current_pb": current_pb,
            "revert_pb": revert_pb,
        },
        model_version="pb_percentile_revert.v1",
        assumptions=[f"目标分位 {revert_to}（{period}）"],
        citations=[
            {"source": "astock.valuation_percentile", "code": code, "range": period},
            {"source": "astock.tencent_quote", "code": code},
        ],
        explanation=f"当前 PB {current_pb}（{current_pct:.0%} 分位），回复到 {revert_to:.0%} 分位 PB {revert_pb:.2f}，目标价 {target_price:.2f}",
    )


def _interp_percentile(target: float, p20, p50, p80, p_min, p_max) -> float | None:
    """对分位目标做线性插值（用已知锚点）。

    target ∈ [0, 1]。锚点：min=0%, p20=20%, p50=50%, p80=80%, max=100%。
    缺锚点则用相邻已知点线性外推。
    """
    anchors = []
    if p_min is not None: anchors.append((0.0, p_min))
    if p20 is not None: anchors.append((0.20, p20))
    if p50 is not None: anchors.append((0.50, p50))
    if p80 is not None: anchors.append((0.80, p80))
    if p_max is not None: anchors.append((1.0, p_max))
    if len(anchors) < 2:
        return None
    anchors.sort()
    # 边界外推
    if target <= anchors[0][0]:
        x0, y0 = anchors[0]; x1, y1 = anchors[1]
    elif target >= anchors[-1][0]:
        x0, y0 = anchors[-2]; x1, y1 = anchors[-1]
    else:
        for i in range(len(anchors) - 1):
            if anchors[i][0] <= target <= anchors[i + 1][0]:
                x0, y0 = anchors[i]; x1, y1 = anchors[i + 1]
                break
        else:
            return None
    if x1 == x0:
        return y0
    return y0 + (y1 - y0) * (target - x0) / (x1 - x0)
