"""Decision Node：合并 quant 工具结果为决策卡。

核心职责：
1. basis_type 归并规则（最大不确定性优先）：llm_reasoning > hybrid > model_fallback > model
2. 字段级 model_versions_json：记录每个决策字段来自哪个工具版本
3. cadence 数组组装（来自 cadence 工具或 LLM 提议）
"""
from __future__ import annotations

import asyncio
import re
from typing import Any

from agents.state import AgentState
from agents.tools import (
    forward_pe_target, pe_percentile_revert, atr_stop,
    risk_based_position, batch_build,
    # Phase 2 reserved: structure_stop, pyramid_buy, dca_plan (LLM tool selection)
)

# 归并优先级（越大越优先）
_BASIS_PRIORITY = {
    "model": 0,
    "model_fallback": 1,
    "hybrid": 2,
    "llm_reasoning": 3,
}


def merge_basis_type(field_basis: list[str]) -> str:
    """归并多个字段的 basis_type 为整卡 basis_type。

    规则（spec §6 约束 3）：
    - 任意字段为 llm_reasoning → 整卡 llm_reasoning
    - 否则，任意字段为 hybrid → 整卡 hybrid
    - 否则，任意字段为 model_fallback → 整卡 model_fallback
    - 全部为 model → 整卡 model
    空列表 → "model"（默认）
    """
    if not field_basis:
        return "model"
    valid = [b for b in field_basis if b in _BASIS_PRIORITY]
    if not valid:
        return "model"
    return max(valid, key=lambda b: _BASIS_PRIORITY[b])


def _version_label(tool_result: dict) -> str:
    """生成字段级版本标签，如 'model(forward_pe_target.v1)' 或 'model_fallback(atr_stop.v1, reason=no_kline)'."""
    basis = tool_result.get("basis_type", "model")
    version = tool_result.get("model_version", "unknown")
    outputs = tool_result.get("outputs") or {}
    reason = outputs.get("fallback_reason")
    if reason:
        return f"{basis}({version}, reason={reason})"
    return f"{basis}({version})"


def build_decision_card(
    code: str,
    name: str,
    current_price: float,
    target_price: float,
    entry_low: float,
    entry_high: float,
    stop_loss: float,
    take_profit: float,
    cadence: list[dict],
    tool_results: dict[str, dict],
    explanation: str,
) -> dict[str, Any]:
    """组装决策卡，含字段级 model_versions_json + 归并后的 basis_type。

    tool_results 形如 {"target": forward_pe_target 结果, "stop": atr_stop 结果, ...}
    """
    # 字段级 model_versions_json：每个数字字段记录来源工具
    target_tool = tool_results.get("target", {})
    entry_tool = tool_results.get("entry", target_tool)
    stop_tool = tool_results.get("stop", {})
    take_profit_tool = tool_results.get("take_profit", target_tool)
    position_tool = tool_results.get("position", {})

    model_versions_json = {
        "target_price": _version_label(target_tool) if target_tool else "unknown",
        "entry_low": _version_label(entry_tool) if entry_tool else "unknown",
        "entry_high": _version_label(entry_tool) if entry_tool else "unknown",
        "stop_loss": _version_label(stop_tool) if stop_tool else "unknown",
        "take_profit": _version_label(take_profit_tool) if take_profit_tool else "unknown",
    }
    if position_tool:
        model_versions_json["cadence[0].pct"] = _version_label(position_tool)

    # 收集所有字段 basis_type 用于归并
    field_basis = [r.get("basis_type") for r in tool_results.values() if r.get("basis_type")]
    merged_basis = merge_basis_type(field_basis)

    # 收集所有 citations（审计来源必须可追溯，不能凭空生成）
    citations = []
    for r in tool_results.values():
        for c in (r.get("citations") or []):
            if c not in citations:
                citations.append(c)

    # 收集所有 model_assumptions
    assumptions = []
    for r in tool_results.values():
        for a in (r.get("model_assumptions") or []):
            if a not in assumptions:
                assumptions.append(a)

    return {
        "code": code,
        "name": name,
        "current_price": current_price,
        "target_price": target_price,
        "entry_low": entry_low,
        "entry_high": entry_high,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "cadence": cadence,
        "basis_type": merged_basis,
        "model_versions_json": model_versions_json,
        "assumptions": assumptions,
        "citations": citations,
        "explanation": explanation,
    }


# ---------------------------------------------------------------------------
# Graph node implementation
# ---------------------------------------------------------------------------

_CODE_PATTERN = re.compile(r"\b(\d{6}|[A-Z]{1,5})\b")


async def _invoke(tool, **kwargs) -> dict | None:
    """安全调用 tool，失败返回降级 dict。"""
    try:
        return await tool.ainvoke(kwargs)
    except Exception as e:
        return {
            "tool": tool.name, "error": f"{tool.name} failed: {e}",
            "basis_type": "model_fallback",
            "model_version": f"{tool.name}.v1",
            "outputs": {"fallback_reason": f"tool_error: {type(e).__name__}"},
            "citations": [], "model_assumptions": [f"工具失败：{e}"],
        }


def _extract_code_from_messages(msgs) -> str | None:
    """从消息文本里抓 6 位 A 股代码 / 美股字母代码。"""
    for m in reversed(msgs):
        text = m.get("content", "") if isinstance(m, dict) else getattr(m, "content", "")
        hit = _CODE_PATTERN.search(text or "")
        if hit:
            return hit.group(1)
    return None


async def _lookup_name(code: str) -> str:
    """从 astock.tencent_quote 查股票名（同步调用，由 to_thread 卸载）。"""
    if code.isdigit() and len(code) == 6:
        try:
            from agents.rate_limiter import eastmoney_limiter
            import astock
            async with eastmoney_limiter:
                q = await asyncio.to_thread(astock.tencent_quote, [code])
            return q.get(code, {}).get("name", code)
        except Exception:
            pass
    return code


async def decision_node(state: AgentState) -> dict:
    """LangGraph 节点：调 quant 工具集 + 合并决策卡。

    Phase 1 简化版：固定调 forward_pe_target + atr_stop + pe_percentile_revert + risk_based_position + batch_build。
    Phase 2 改为 LLM 决定调哪些工具。
    """
    context_codes = state.get("context_codes") or []
    msgs = state.get("messages") or []
    code = context_codes[0] if context_codes else _extract_code_from_messages(msgs)
    if not code:
        return {"decision_card": None}

    # 并发调工具（每个工具内部走 Rate Limiter + asyncio.to_thread）
    target_r = await _invoke(forward_pe_target, code=code, target_pe=20.0, eps_year="27e")
    stop_r = await _invoke(atr_stop, code=code, period=14, multiplier=2.0)
    entry_r = await _invoke(pe_percentile_revert, code=code, revert_to=0.50)

    # 取当前价 + 名称
    current_price = (target_r or {}).get("outputs", {}).get("current_price") or \
                    (stop_r or {}).get("outputs", {}).get("current_price") or 0.0
    name = await _lookup_name(code)

    # 若工具都失败，current_price=0；用 fallback 值兜底，让卡仍可生成
    target_price = (target_r or {}).get("outputs", {}).get("target_price") or (current_price * 1.15 if current_price else 0.0)
    stop_loss = (stop_r or {}).get("outputs", {}).get("stop_price") or (current_price * 0.92 if current_price else 0.0)
    entry_low = current_price * 0.98 if current_price else 0.0
    entry_high = current_price * 1.02 if current_price else 0.0
    take_profit = current_price * 1.20 if current_price else 0.0

    # 所有工具失败（current_price=0）→ 不推半成品决策卡，标记 intent 让 runner 发 error 事件
    if not current_price:
        return {
            "decision_card": None,
            "intent": "decision_failed",
        }

    # 仓位 + 节奏
    pos_r = await _invoke(risk_based_position, entry_price=current_price or 100.0, stop_price=stop_loss or 92.0)
    cad_r = await _invoke(batch_build, total_budget=100000.0, batches=3, schedule="weekly", start_price=current_price or 100.0)
    cadence = (cad_r or {}).get("outputs", {}).get("plan") or []

    # 合并
    card = build_decision_card(
        code=code, name=name, current_price=current_price,
        target_price=target_price, entry_low=entry_low, entry_high=entry_high,
        stop_loss=stop_loss, take_profit=take_profit,
        cadence=cadence,
        tool_results={
            "target": target_r or {}, "entry": entry_r or {},
            "stop": stop_r or {}, "position": pos_r or {},
            "take_profit": target_r or {},  # 复用 target
        },
        explanation=f"基于前向 PE 目标价 {target_price:.2f} + ATR 止损 {stop_loss:.2f} + 分批 3 期建仓",
    )
    return {"decision_card": card}
