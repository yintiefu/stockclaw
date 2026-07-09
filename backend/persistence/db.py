"""SQLite 连接管理 + WAL 初始化 + PRAGMA user_version migration。"""
from __future__ import annotations

import os

import aiosqlite

# 默认 backend/.cache/stockclaw.db；env 可改 ~/.stockclaw/stockclaw.db
_HERE = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_DB = os.path.normpath(os.path.join(_HERE, "..", ".cache", "stockclaw.db"))


def _resolve_db_path() -> str:
    """每次调用都读 env——测试可 monkeypatch.setenv 切换路径。"""
    return os.environ.get("VR_AGENT_DB", _DEFAULT_DB)


_conn: aiosqlite.Connection | None = None


# Migration 脚本：按版本递增，每版一组 SQL。CREATE TABLE IF NOT EXISTS 保证幂等。
MIGRATIONS: dict[int, list[str]] = {
    1: [
        # threads 表（spec §8 schema）
        """CREATE TABLE IF NOT EXISTS threads (
            id          TEXT PRIMARY KEY,
            title       TEXT NOT NULL,
            model       TEXT NOT NULL,
            created_at  INTEGER NOT NULL,
            updated_at  INTEGER NOT NULL
        );""",
        "CREATE INDEX IF NOT EXISTS idx_threads_updated_at ON threads(updated_at);",

        # conversations 表（spec §8 schema，含 tool_calls_json / artifacts_json）
        """CREATE TABLE IF NOT EXISTS conversations (
            id              TEXT PRIMARY KEY,
            thread_id       TEXT NOT NULL,
            role            TEXT NOT NULL,
            content         TEXT,
            tool_calls_json TEXT,
            tool_call_id    TEXT,
            artifacts_json  TEXT,
            created_at      INTEGER NOT NULL,
            FOREIGN KEY (thread_id) REFERENCES threads(id) ON DELETE CASCADE
        );""",
        "CREATE INDEX IF NOT EXISTS idx_conversations_thread ON conversations(thread_id, created_at);",

        # decisions 表（spec §8 schema）
        """CREATE TABLE IF NOT EXISTS decisions (
            id              TEXT PRIMARY KEY,
            thread_id       TEXT NOT NULL,
            code            TEXT NOT NULL,
            name            TEXT,
            created_at      INTEGER NOT NULL,

            target_price    REAL,
            entry_low       REAL,
            entry_high      REAL,
            stop_loss       REAL,
            take_profit     REAL,
            cadence_json    TEXT,

            basis_type      TEXT NOT NULL,
            model_versions_json  TEXT,
            assumptions_json     TEXT,
            citations_json  TEXT,

            status              TEXT,
            linked_position_code TEXT,
            price_at_creation   REAL,
            current_price       REAL,
            pnl_pct             REAL,
            updated_at          INTEGER,

            raw_artifact_json   TEXT
        );""",
        "CREATE INDEX IF NOT EXISTS idx_decisions_code ON decisions(code);",
        "CREATE INDEX IF NOT EXISTS idx_decisions_status ON decisions(status);",
        "CREATE INDEX IF NOT EXISTS idx_decisions_thread_id ON decisions(thread_id);",
    ],
}


async def _connect() -> aiosqlite.Connection:
    """打开连接 + 设 PRAGMA。"""
    db_path = _resolve_db_path()
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    conn = await aiosqlite.connect(db_path)
    # WAL 模式：写不阻塞读
    await conn.execute("PRAGMA journal_mode=WAL;")
    # 5s 等锁，防 database is locked
    await conn.execute("PRAGMA busy_timeout=5000;")
    # 开外键（默认关，需显式开才有 ON DELETE CASCADE）
    await conn.execute("PRAGMA foreign_keys=ON;")
    return conn


async def init_db() -> None:
    """初始化 + 跑未应用的 migration。幂等。"""
    global _conn
    if _conn is not None:
        # 已开过连接：只确认 schema 最新
        await _run_migrations(_conn)
        return
    _conn = await _connect()
    await _run_migrations(_conn)


async def _run_migrations(conn: aiosqlite.Connection) -> None:
    """对比 PRAGMA user_version 与 MIGRATIONS，顺次执行未应用的 SQL。"""
    async with conn.execute("PRAGMA user_version;") as cur:
        row = await cur.fetchone()
    current = row[0] if row else 0

    for version in sorted(MIGRATIONS.keys()):
        if version <= current:
            continue
        for sql in MIGRATIONS[version]:
            await conn.execute(sql)
        await conn.commit()
        await conn.execute(f"PRAGMA user_version = {version};")
        await conn.commit()


async def get_user_version() -> int:
    """测试用：返回当前 schema 版本。"""
    if _conn is None:
        await init_db()
    async with _conn.execute("PRAGMA user_version;") as cur:
        row = await cur.fetchone()
    return row[0] if row else 0


async def close_db() -> None:
    global _conn
    if _conn is not None:
        await _conn.close()
        _conn = None


async def get_conn() -> aiosqlite.Connection:
    """供其他模块复用。"""
    if _conn is None:
        await init_db()
    return _conn
