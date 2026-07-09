"""threads 表 CRUD（会话列表，用于前端 sidebar 高效渲染）。"""
from __future__ import annotations

import time
import uuid

from persistence.db import get_conn


async def create_thread(title: str = "新会话", model: str = "", tid: str | None = None) -> str:
    """新建会话；返回 thread_id。"""
    conn = await get_conn()
    tid = tid or uuid.uuid4().hex
    now = int(time.time())
    await conn.execute(
        "INSERT INTO threads (id, title, model, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        (tid, title, model, now, now),
    )
    await conn.commit()
    return tid


async def get_thread(tid: str) -> dict | None:
    conn = await get_conn()
    async with conn.execute(
        "SELECT id, title, model, created_at, updated_at FROM threads WHERE id = ?", (tid,),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return None
    return {"id": row[0], "title": row[1], "model": row[2],
            "created_at": row[3], "updated_at": row[4]}


async def list_threads(limit: int = 100) -> list[dict]:
    """按 updated_at 倒序拿会话列表。"""
    conn = await get_conn()
    async with conn.execute(
        "SELECT id, title, model, created_at, updated_at FROM threads ORDER BY updated_at DESC LIMIT ?",
        (limit,),
    ) as cur:
        rows = await cur.fetchall()
    return [{"id": r[0], "title": r[1], "model": r[2],
             "created_at": r[3], "updated_at": r[4]} for r in rows]


async def rename_thread(tid: str, title: str) -> None:
    conn = await get_conn()
    await conn.execute(
        "UPDATE threads SET title = ?, updated_at = ? WHERE id = ?",
        (title, int(time.time()), tid),
    )
    await conn.commit()


async def touch_thread(tid: str) -> None:
    """更新 updated_at（每次新增消息时调）。"""
    conn = await get_conn()
    await conn.execute("UPDATE threads SET updated_at = ? WHERE id = ?",
                       (int(time.time()), tid))
    await conn.commit()


async def delete_thread(tid: str) -> None:
    """删会话；conversations 走 ON DELETE CASCADE 自动清空。"""
    conn = await get_conn()
    await conn.execute("DELETE FROM threads WHERE id = ?", (tid,))
    await conn.commit()
