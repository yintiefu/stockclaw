"""止损类工具：ATR 止损 / 结构止损 / 风险反推仓位。

A 股主路径走完整公式（model）；K 线缺失或美港股代码 → Python 简化公式降级（model_fallback）。
不让 LLM 编 ATR / 历史分位——硬约束：stop_loss 字段必须由 Python 算。
"""
from __future__ import annotations

import json
import os
from typing import Any

import astock


# portfolio.json 路径（spec §8：backend/.cache/portfolio.json）
_HERE = os.path.dirname(os.path.abspath(__file__))
_PF_FILE = os.path.normpath(os.path.join(_HERE, "..", ".cache", "portfolio.json"))

# 默认 fallback 止损百分比（spec §6 约束 1：8% 固定止损）
_FALLBACK_STOP_PCT = 0.08


class DataUnavailable(Exception):
    """止损价等硬性字段算不出（如当前价缺失）——直接拒答，不输出半成品。"""


def _is_a_share_code(code: str) -> bool:
    return code.isdigit() and len(code) == 6


def _contract(tool, inputs, outputs, model_version, assumptions, citations, explanation, basis_type="model"):
    return {
        "tool": tool, "inputs": inputs, "outputs": outputs,
        "basis_type": basis_type, "model_version": model_version,
        "model_assumptions": assumptions, "citations": citations, "explanation": explanation,
    }


def _atr_stop_main_path(klines: list[dict], period: int, multiplier: float) -> tuple[float, float] | None:
    """计算 ATR × multiplier 止损距离。返回 (atr, stop_distance)。失败返回 None。

    ATR = True Range 的 N 日均值。TR = max(high-low, |high-prev_close|, |low-prev_close|)。
    """
    if len(klines) < period + 1:
        return None
    trs = []
    for i in range(1, len(klines)):
        prev_close = klines[i - 1].get("close")
        high = klines[i].get("high")
        low = klines[i].get("low")
        if prev_close is None or high is None or low is None:
            continue
        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close),
        )
        trs.append(tr)
    if len(trs) < period:
        return None
    atr = sum(trs[-period:]) / period
    return atr, atr * multiplier


def atr_stop(code: str, period: int = 14, multiplier: float = 2.0) -> dict[str, Any]:
    """ATR 止损：A 股主路径走 K 线计算，K 线缺失/美港股降级为 model_fallback（固定 -8%）。

    code: 股票代码（A 股 6 位 / 美股字母 / 港股数字）
    period: ATR 周期（默认 14）
    multiplier: ATR 倍数（保守 2.0、激进 1.5）

    返回 outputs: {stop_price, current_price, distance_pct, basis_type, atr?, fallback_reason?}
    """
    # L1 model：A 股代码 + K 线齐全
    if _is_a_share_code(code):
        try:
            klines = astock.kline(code, category=4, offset=period + 30)
        except Exception:
            klines = []
        result = _atr_stop_main_path(klines, period, multiplier)
        if result is not None:
            atr, stop_distance = result
            quote = astock.tencent_quote([code])
            current = quote.get(code, {}).get("price")
            if current:
                stop_price = round(current - stop_distance, 2)
                return _contract(
                    tool="atr_stop",
                    inputs={"code": code, "period": period, "multiplier": multiplier},
                    outputs={
                        "stop_price": stop_price,
                        "current_price": current,
                        "distance_pct": round((stop_price - current) / current * 100, 2),
                        "atr": round(atr, 4),
                    },
                    model_version="atr_stop.v1",
                    assumptions=[f"{period}-day ATR", f"{multiplier}x multiplier"],
                    citations=[{"source": "astock.kline", "code": code, "range": f"近 {period + 30} 日"}],
                    explanation=f"基于 {period} 日 ATR={atr:.2f}，乘以 {multiplier}x 倍数，止损价 {current:.2f} - {stop_distance:.2f} = {stop_price:.2f}",
                )

    # L2 model_fallback：固定百分比止损
    current = _fetch_current_price(code)
    if not current:
        # L4 直接拒答：连当前价都拿不到
        raise DataUnavailable(f"{code} 当前价缺失，无法计算止损")

    stop_price = round(current * (1 - _FALLBACK_STOP_PCT), 2)
    fallback_reason = "no_kline" if _is_a_share_code(code) else "non_a_share_code"
    return _contract(
        tool="atr_stop",
        inputs={"code": code, "period": period, "multiplier": multiplier},
        outputs={
            "stop_price": stop_price,
            "current_price": current,
            "distance_pct": round((stop_price - current) / current * 100, 2),
            "fallback_reason": fallback_reason,
        },
        model_version="atr_stop.v1",
        assumptions=[f"数据不足降级：固定 -{_FALLBACK_STOP_PCT:.0%} 止损"],
        citations=[{"source": "astock.tencent_quote", "code": code}],
        explanation=f"K 线/历史数据不足（{fallback_reason}），降级为固定 -{_FALLBACK_STOP_PCT:.0%} 止损，止损价 {stop_price}",
        basis_type="model_fallback",
    )


def _fetch_current_price(code: str) -> float | None:
    """统一获取当前价（A 股走 tencent_quote；美港股/港股走 gstock.us_hk_stock）。"""
    if _is_a_share_code(code):
        try:
            q = astock.tencent_quote([code])
            return q.get(code, {}).get("price")
        except Exception:
            return None
    try:
        import gstock
        data = gstock.us_hk_stock(code)
        return (data or {}).get("quote", {}).get("price")
    except Exception:
        return None


def structure_stop(code: str, lookback: int = 60) -> dict[str, Any]:
    """结构止损：近 lookback 日最低价。无 K 线时复用 atr_stop fallback。"""
    if _is_a_share_code(code):
        try:
            klines = astock.kline(code, category=4, offset=lookback)
        except Exception:
            klines = []
        lows = [k.get("low") for k in klines if k.get("low") is not None]
        if lows:
            stop = min(lows)
            quote = astock.tencent_quote([code])
            current = quote.get(code, {}).get("price")
            if current:
                return _contract(
                    tool="structure_stop",
                    inputs={"code": code, "lookback": lookback},
                    outputs={
                        "stop_price": stop,
                        "current_price": current,
                        "distance_pct": round((stop - current) / current * 100, 2),
                    },
                    model_version="structure_stop.v1",
                    assumptions=[f"近 {lookback} 日最低价"],
                    citations=[{"source": "astock.kline", "code": code, "range": f"近 {lookback} 日"}],
                    explanation=f"结构止损 = 近 {lookback} 日最低价 {stop}",
                )

    # 降级：复用 atr_stop 的 fallback
    return atr_stop(code, period=14, multiplier=2.0)


def risk_based_position(entry_price: float, stop_price: float,
                        total_equity: float | None = None,
                        risk_tolerance_pct: float | None = None) -> dict[str, Any]:
    """按止损距离反推仓位：单笔风险 = 总净值 × 风险容忍度；股数 = 单笔风险 / 单股止损距离。

    未传 total_equity / risk_tolerance_pct 时，尝试从 portfolio.json::totals 读。
    两者都缺 → 返回 model_fallback，仅给比例（30%/30%/40%），不算绝对股数。
    """
    # 读 portfolio.json::totals（若未显式传参）
    if total_equity is None or risk_tolerance_pct is None:
        totals = _read_portfolio_totals()
        if total_equity is None:
            total_equity = totals.get("total_equity_override") or totals.get("available_cash")
        if risk_tolerance_pct is None:
            risk_tolerance_pct = totals.get("risk_tolerance_pct", 0.01)

    per_share_risk = entry_price - stop_price
    if per_share_risk <= 0:
        raise DataUnavailable(f"止损价 {stop_price} ≥ 入场价 {entry_price}，单股风险非正")

    if not total_equity:
        # 降级：比例表达
        return _contract(
            tool="risk_based_position",
            inputs={"entry_price": entry_price, "stop_price": stop_price},
            outputs={
                "position_pct": [0.30, 0.30, 0.40],  # 默认 3 批：30/30/40
                "per_share_risk": round(per_share_risk, 4),
                "fallback_reason": "no_total_equity",
            },
            model_version="risk_based_position.v1",
            assumptions=["缺总净值/可用现金，降级为默认比例 30%/30%/40%"],
            citations=[{"source": "portfolio.json::totals", "note": "available_cash / risk_tolerance_pct 未设"}],
            explanation="portfolio.json::totals 未配 available_cash，无法算绝对股数；按 30%/30%/40% 比例表达",
            basis_type="model_fallback",
        )

    if risk_tolerance_pct is None:
        risk_tolerance_pct = 0.01
    total_risk = total_equity * risk_tolerance_pct
    shares = total_risk / per_share_risk
    return _contract(
        tool="risk_based_position",
        inputs={
            "entry_price": entry_price, "stop_price": stop_price,
            "total_equity": total_equity, "risk_tolerance_pct": risk_tolerance_pct,
        },
        outputs={
            "shares": round(shares, 0),
            "position_value": round(shares * entry_price, 2),
            "position_pct_of_equity": round(shares * entry_price / total_equity, 4),
            "per_share_risk": round(per_share_risk, 4),
            "total_risk": round(total_risk, 2),
        },
        model_version="risk_based_position.v1",
        assumptions=[f"单笔风险容忍 = 总净值 × {risk_tolerance_pct:.1%}", f"单股止损距离 = {per_share_risk:.2f}"],
        citations=[{"source": "portfolio.json::totals", "fields": "available_cash / risk_tolerance_pct"}],
        explanation=f"总风险预算 {total_risk:.0f}（总净值 {total_equity:.0f} × {risk_tolerance_pct:.1%}）/ 单股风险 {per_share_risk:.2f} = {shares:.0f} 股",
    )


def _read_portfolio_totals() -> dict:
    """读 portfolio.json::totals（向后兼容老 JSON 无此字段时返回 {})."""
    try:
        with open(_PF_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data.get("totals") or {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
