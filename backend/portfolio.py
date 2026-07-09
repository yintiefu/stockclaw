"""持仓数据层 —— 用户自己录入的持仓 + 实时行情叠加浮动盈亏。

合规：持仓是用户主动录入的自己的标的（存本地 .cache/portfolio.json，
gitignore、不上传、不进仓库），不预置任何标的、不含 _SEED 兜底、不做推荐。
盈亏红涨绿跌（A股口径）。含每半小时后台定时刷新 + 手动刷新。
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone, timedelta

import astock

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(HERE, ".cache")
PF_FILE = os.path.join(CACHE_DIR, "portfolio.json")
BEIJING = timezone(timedelta(hours=8))
_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(BEIJING).strftime("%Y-%m-%d %H:%M")


def _load() -> dict:
    try:
        with open(PF_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"holdings": [], "last_refresh": None}


def _save(d: dict) -> None:
    # 先写临时文件再原子改名：并发读若撞上写中途的半截 JSON，会被 _load 静默当成空持仓
    os.makedirs(CACHE_DIR, exist_ok=True)
    tmp = PF_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    os.replace(tmp, PF_FILE)


def add_holding(code: str, shares: float, cost: float) -> dict:
    """加一笔持仓；同代码则按加权平均成本合并（加仓）。"""
    with _LOCK:
        d = _load()
        for h in d["holdings"]:
            if h["code"] == code:
                total = h["shares"] + shares
                h["cost"] = round((h["shares"] * h["cost"] + shares * cost) / total, 3) if total else cost
                h["shares"] = total
                break
        else:
            d["holdings"].append({"code": code, "shares": shares, "cost": cost})
        _save(d)
    return get_portfolio()


def remove_holding(code: str) -> dict:
    with _LOCK:
        d = _load()
        d["holdings"] = [h for h in d["holdings"] if h["code"] != code]
        _save(d)
    return get_portfolio()


def close_position(code: str, date: str, price: float, shares: float, cost: float) -> dict:
    """记一笔已清仓：算已实现盈亏，存入 closed 列表。"""
    pnl = (price - cost) * shares
    with _LOCK:
        d = _load()
        d.setdefault("closed", [])
        try:
            name = astock.tencent_quote([code]).get(code, {}).get("name", code)
        except Exception:
            name = code
        d["closed"].append({
            "code": code, "name": name, "date": date, "price": price,
            "shares": shares, "cost": cost, "pnl": round(pnl, 2),
            "pnl_pct": round((price - cost) / cost * 100, 2) if cost else 0.0,
        })
        _save(d)
    return get_portfolio()


def remove_closed(index: int) -> dict:
    with _LOCK:
        d = _load()
        cl = d.get("closed", [])
        if 0 <= index < len(cl):
            cl.pop(index)
            _save(d)
    return get_portfolio()


def get_portfolio() -> dict:
    """读持仓 + 实时行情，算每笔与汇总的市值/浮动盈亏。

    totals 字段含 spec §8 的扩展字段：
    - available_cash：可用现金（用户手输，给 risk_based_position 用）
    - risk_tolerance_pct：单笔风险容忍度，默认 1%
    - total_equity_override：手动覆盖总净值（含港股美股时填）
    老无这些字段的 JSON 兼容默认值。
    """
    with _LOCK:
        d = _load()
    # 用户在 portfolio.json 手填的 totals（spec §8 扩展字段）；老 JSON 无此键 → 空字典兼容
    user_totals = d.get("totals") or {}
    hs = d.get("holdings", [])
    rows, tmv, tcost = [], 0.0, 0.0
    if hs:
        try:
            quotes = astock.tencent_quote([h["code"] for h in hs])
        except Exception:
            quotes = {}
        for h in hs:
            q = quotes.get(h["code"], {})
            price = q.get("price", 0.0)
            mv = price * h["shares"]
            cv = h["cost"] * h["shares"]
            pnl = mv - cv
            rows.append({
                "code": h["code"], "name": q.get("name", h["code"]),
                "price": price, "shares": h["shares"], "cost": h["cost"],
                "market_value": round(mv, 2), "pnl": round(pnl, 2),
                "pnl_pct": round(pnl / cv * 100, 2) if cv else 0.0,
            })
            tmv += mv
            tcost += cv
    total_pnl = tmv - tcost
    closed = d.get("closed", [])
    return {
        "holdings": rows,
        "totals": {
            "market_value": round(tmv, 2), "cost": round(tcost, 2),
            "pnl": round(total_pnl, 2),
            "pnl_pct": round(total_pnl / tcost * 100, 2) if tcost else 0.0,
            # spec §8 漏洞五修补：账户基础字段（risk_based_position 用）
            "available_cash": float(user_totals.get("available_cash", 0.0) or 0.0),
            "risk_tolerance_pct": float(user_totals.get("risk_tolerance_pct", 0.01) or 0.01),
            "total_equity_override": user_totals.get("total_equity_override"),
        },
        "closed": closed,
        "realized_pnl": round(sum(c.get("pnl", 0) for c in closed), 2),
        "updated": _now(),
        "last_refresh": d.get("last_refresh"),
    }


def _refresh_snapshot() -> None:
    """后台定时任务：刷新时间戳（GET 本就实时算，这里记录后台刷新点）。"""
    with _LOCK:
        d = _load()
        d["last_refresh"] = _now()
        _save(d)


def start_scheduler(interval: int = 1800) -> None:
    """每半小时后台刷新一次持仓数据（daemon 线程）。"""
    def loop():
        while True:
            time.sleep(interval)
            try:
                _refresh_snapshot()
            except Exception:
                pass
    threading.Thread(target=loop, daemon=True).start()
