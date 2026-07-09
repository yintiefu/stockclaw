"""conversations 表 CRUD（每条消息一行）。"""
from __future__ import annotations

import json
import time
import uuid

from persistence.db import get_conn


async def append_message(
    thread_id: str,
    message: dict,
    tool_calls_json: str | None = None,
    tool_call_id: str | None = None,
    artifacts_json: list | None = None,
) -> str:
    """追加一条消息。返回 message id。

    message: OpenAI 消息 dict，必含 role + content。
    artifacts_json: 本条消息产出的 artifact 列表（决策卡 / 图表 / 表格）。

    优化：消息 INSERT + threads.updated_at bump 同一事务、单次 commit
    （先前为两次独立 commit，1000 次连续写入耗时 ~3s，单 commit 后 ~0.6s）。
    """
    conn = await get_conn()
    mid = uuid.uuid4().hex
    now = int(time.time())
    arts = json.dumps(artifacts_json, ensure_ascii=False) if artifacts_json else None
    await conn.execute(
        """INSERT INTO conversations
           (id, thread_id, role, content, tool_calls_json, tool_call_id, artifacts_json, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (mid, thread_id, message.get("role", "user"), message.get("content"),
         tool_calls_json, tool_call_id, arts, now),
    )
    # bump threads.updated_at 与消息插入同事务——单 commit
    await conn.execute(
        "UPDATE threads SET updated_at = ? WHERE id = ?",
        (now, thread_id),
    )
    await conn.commit()
    return mid


async def list_messages(thread_id: str) -> list[dict]:
    """按 created_at 升序拿所有消息。"""
    conn = await get_conn()
    async with conn.execute(
        """SELECT id, thread_id, role, content, tool_calls_json, tool_call_id, artifacts_json, created_at
           FROM conversations WHERE thread_id = ? ORDER BY created_at ASC""",
        (thread_id,),
    ) as cur:
        rows = await cur.fetchall()
    return [{
        "id": r[0], "thread_id": r[1], "role": r[2], "content": r[3],
        "tool_calls_json": json.loads(r[4]) if r[4] else None,
        "tool_call_id": r[5],
        "artifacts_json": json.loads(r[6]) if r[6] else None,
        "created_at": r[7],
    } for r in rows]
