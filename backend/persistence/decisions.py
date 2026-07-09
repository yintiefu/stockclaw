"""decisions 表 CRUD（决策卡归档 + 收益追踪字段）。"""
from __future__ import annotations

import json
import time
import uuid

from persistence.db import get_conn


async def create_decision(
    thread_id: str, code: str, name: str | None,
    target_price: float, entry_low: float, entry_high: float,
    stop_loss: float, take_profit: float,
    cadence: list[dict], basis_type: str,
    model_versions_json: dict, assumptions: list[str], citations: list[dict],
    raw_artifact: dict,
) -> str:
    conn = await get_conn()
    did = uuid.uuid4().hex
    now = int(time.time())
    await conn.execute(
        """INSERT INTO decisions (
            id, thread_id, code, name, created_at,
            target_price, entry_low, entry_high, stop_loss, take_profit, cadence_json,
            basis_type, model_versions_json, assumptions_json, citations_json,
            status, raw_artifact_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (did, thread_id, code, name, now,
         target_price, entry_low, entry_high, stop_loss, take_profit,
         json.dumps(cadence, ensure_ascii=False),
         basis_type,
         json.dumps(model_versions_json, ensure_ascii=False),
         json.dumps(assumptions, ensure_ascii=False),
         json.dumps(citations, ensure_ascii=False),
         "active",
         json.dumps(raw_artifact, ensure_ascii=False)),
    )
    await conn.commit()
    return did


async def get_decision(did: str) -> dict | None:
    conn = await get_conn()
    async with conn.execute("SELECT * FROM decisions WHERE id = ?", (did,)) as cur:
        row = await cur.fetchone()
    if not row:
        return None
    cols = [d[0] for d in cur.description]
    out = dict(zip(cols, row))
    # JSON 字段反序列化
    for k in ("cadence_json", "model_versions_json", "assumptions_json", "citations_json", "raw_artifact_json"):
        if out.get(k):
            try:
                out[k] = json.loads(out[k])
            except (TypeError, json.JSONDecodeError):
                pass
    return out


async def list_by_code(code: str, limit: int = 50) -> list[dict]:
    conn = await get_conn()
    async with conn.execute(
        "SELECT * FROM decisions WHERE code = ? ORDER BY created_at DESC LIMIT ?",
        (code, limit),
    ) as cur:
        rows = await cur.fetchall()
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in rows]


async def list_active() -> list[dict]:
    """Phase 3 scheduler 用：拿所有待追踪的 active 决策。"""
    conn = await get_conn()
    async with conn.execute("SELECT * FROM decisions WHERE status = 'active'") as cur:
        rows = await cur.fetchall()
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in rows]
