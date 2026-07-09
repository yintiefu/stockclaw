"""LangChain @tool 包装，连接 astock / gstock / quant 工具。

硬约束（焊死）：所有调 astock.*/gstock.*/market.*/newsradar.* 的 @tool 必须用
`await asyncio.to_thread(fn, ...)` 包装。原因：
- astock.kline() 走 mootdx 同步阻塞 TCP
- astock.tencent_quote() 走 urllib.request.urlopen 同步
- astock.em_get() 走 requests 同步
- quant.* 全部基于这些同步 API
直接 await sync 函数会 TypeError；直接调用会冻结 event loop，Rate Limiter 的 sleep 无法调度。

Phase 1 验收：grep -E "await (astock|gstock|market|newsradable)" backend/agents/tools.py 必须无命中。
"""
from __future__ import annotations

import asyncio
from typing import Any

from langchain_core.tools import tool

import astock
import quant.valuation as q_val
import quant.stops as q_stops
import quant.cadence as q_cad
from agents.rate_limiter import eastmoney_limiter


async def _run_sync(fn, *args, **kwargs):
    """把同步函数卸载到默认线程池，且 Rate Limiter 横跨整个调用。"""
    async with eastmoney_limiter:
        return await asyncio.to_thread(fn, *args, **kwargs)


# ---------------------------------------------------------------------------
# 数据查询工具（直接调 astock / gstock）
# ---------------------------------------------------------------------------

@tool
async def query_quote(codes: list[str]) -> dict:
    """查 A 股实时行情：现价/涨跌/PE/PB/市值/换手/涨跌停。可批量。

    codes: 6 位 A 股代码列表，如 ['600519', '000858']
    """
    return await _run_sync(astock.tencent_quote, codes)


@tool
async def query_global_stock(symbol: str) -> dict:
    """查美股/港股/韩股个股行情 + 关键财务指标。

    symbol: 美股字母（AAPL）/ 港股数字（00700）/ 韩股 6 位.KS（005930.KS）
    """
    import gstock
    return await _run_sync(gstock.us_hk_stock, symbol)


@tool
async def query_valuation(code: str) -> dict:
    """查 A 股完整估值：行情 + 一致预期 EPS + 前向 PE/PEG/消化年数。

    code: 6 位 A 股代码
    """
    return await _run_sync(astock.full_valuation, code)


@tool
async def query_kline(code: str, days: int = 60) -> list[dict]:
    """查 A 股日 K 线（用于 ATR 计算 / 结构止损）。

    code: 6 位 A 股代码
    days: 拉取天数（默认 60）
    """
    return await _run_sync(astock.kline, code, 4, days)


# ---------------------------------------------------------------------------
# quant 工具（纯 Python 函数，本身不调网络；但内部调 astock，故也走 Rate Limiter + to_thread）
# ---------------------------------------------------------------------------

@tool
async def forward_pe_target(code: str, target_pe: float = 20.0, eps_year: str = "27e") -> dict:
    """前向 PE × 一致 EPS = 目标价。A 股数据齐走完整公式（model）。

    一致 EPS 缺失（美港股 / 无覆盖）时抛 DataUnavailable——上层 Decision Node 会走 model_fallback 或 llm_reasoning。
    code: 6 位 A 股代码
    target_pe: 目标前向 PE（用户/LLM 给定，如行业均值 20x）
    eps_year: 用哪年的一致 EPS（"26e" / "27e"）
    返回：{tool, inputs, outputs, basis_type, model_version, citations, explanation}
    """
    return await _run_sync(q_val.forward_pe_target, code, target_pe, eps_year)


@tool
async def pe_percentile_revert(code: str, revert_to: float = 0.50) -> dict:
    """PE 历史分位回复：当前 PE 处于 X 分位 → 回复到目标分位的目标价。

    code: 6 位 A 股代码
    revert_to: 目标分位（0.0-1.0），默认 0.50（中位数）
    """
    return await _run_sync(q_val.pe_percentile_revert, code, revert_to)


@tool
async def atr_stop(code: str, period: int = 14, multiplier: float = 2.0) -> dict:
    """ATR 止损价。A 股主路径走 14 日 ATR × 倍数；K 线缺失或美港股自动降级为 model_fallback（固定 -8%）。

    code: 股票代码（A 股 6 位 / 美股字母 / 港股数字）
    period: ATR 周期（默认 14）
    multiplier: ATR 倍数（保守 2.0、激进 1.5）
    """
    return await _run_sync(q_stops.atr_stop, code, period, multiplier)


@tool
async def structure_stop(code: str, lookback: int = 60) -> dict:
    """结构止损：近 lookback 日最低价。无 K 线时复用 atr_stop fallback。

    code: 6 位 A 股代码
    lookback: 回看天数（默认 60）
    """
    return await _run_sync(q_stops.structure_stop, code, lookback)


@tool
async def risk_based_position(entry_price: float, stop_price: float) -> dict:
    """按止损距离反推仓位：股数 = 总净值 × 风险容忍度 / 单股止损距离。

    entry_price / stop_price: 入场价 / 止损价
    总净值 / 风险容忍度未传时，自动读 portfolio.json::totals
    两者都缺 → 返回 model_fallback，仅给比例（30%/30%/40%）
    """
    return await _run_sync(q_stops.risk_based_position, entry_price, stop_price)


@tool
async def pyramid_buy(current_price: float, total_budget: float,
                      batches: int, ratios: list[float], triggers: list[str]) -> dict:
    """金字塔加仓：底部仓位大、顶部小；触发价递增。

    ratios: 每批占比，和必须 = 1.0（如 [0.40, 0.30, 0.30]）
    triggers: 触发条件（如 ["immediate", "pullback_to:95", "breakout_above:105"]）
    """
    return await _run_sync(q_cad.pyramid_buy, current_price, total_budget, batches, ratios, triggers)


@tool
async def batch_build(total_budget: float, batches: int, schedule: str, start_price: float) -> dict:
    """分批建仓：等额 N 批，按周期触发（不择时，平摊成本）。

    schedule: "weekly" / "biweekly" / "monthly"
    """
    return await _run_sync(q_cad.batch_build, total_budget, batches, schedule, start_price)


@tool
async def dca_plan(periodic_amount: float, periods: int, schedule: str) -> dict:
    """定投：固定周期 + 固定金额。

    periodic_amount: 每次定投金额
    periods: 期数
    schedule: "weekly" / "biweekly" / "monthly"
    """
    return await _run_sync(q_cad.dca_plan, periodic_amount, periods, schedule)


# Phase 1 暴露给 LLM 的全部工具列表
ALL_TOOLS = [
    query_quote, query_global_stock, query_valuation, query_kline,
    forward_pe_target, pe_percentile_revert,
    atr_stop, structure_stop, risk_based_position,
    pyramid_buy, batch_build, dca_plan,
]
