"""持久化层测试——aiosqlite + WAL + migration 幂等 + 表 schema + CRUD。

注：db fixture 用 @pytest_asyncio.fixture —— pytest-asyncio 1.x strict mode 下
async fixture 必须显式标记（普通 @pytest.fixture 的 async def 会被拒绝）。
4 个测试函数保持 @pytest.mark.asyncio 不变。
"""
import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def db(tmp_path, monkeypatch):
    """每个测试一个临时 db 文件。"""
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("VR_AGENT_DB", str(db_path))
    # 延迟 import 让 env 生效
    from persistence import db as db_mod
    # 重置全局连接（之前测试可能已开过）
    await db_mod.close_db()
    await db_mod.init_db()
    yield db_mod
    await db_mod.close_db()


@pytest.mark.asyncio
async def test_migration_is_idempotent(db):
    """连跑两次 init 不报错（CREATE TABLE IF NOT EXISTS 幂等）。"""
    await db.init_db()
    await db.init_db()
    version = await db.get_user_version()
    assert version == 1


@pytest.mark.asyncio
async def test_threads_crud(db):
    """threads 表 CRUD：create / list / rename / delete（含 ON DELETE CASCADE）。"""
    from persistence import threads, conversations
    tid = await threads.create_thread(title="测试会话", model="gpt-4o")
    assert tid
    items = await threads.list_threads()
    assert any(t["id"] == tid for t in items)
    await threads.rename_thread(tid, "新标题")
    item = await threads.get_thread(tid)
    assert item["title"] == "新标题"
    # CASCADE 验证：先塞一条 conversation，再删 thread
    await conversations.append_message(tid, {"role": "user", "content": "hi"})
    await threads.delete_thread(tid)
    items2 = await threads.list_threads()
    assert not any(t["id"] == tid for t in items2)
    # conversations 也应被 CASCADE 清空
    msgs = await conversations.list_messages(tid)
    assert msgs == []


@pytest.mark.asyncio
async def test_conversations_crud(db):
    """conversations 表：append_message + list_messages（按时间排序）。"""
    from persistence import conversations, threads
    tid = "test-thread-id"
    await threads.create_thread(tid=tid, title="t", model="m")

    await conversations.append_message(tid, {"role": "user", "content": "第一条"})
    await conversations.append_message(tid, {"role": "assistant", "content": "回复"})
    msgs = await conversations.list_messages(tid)
    assert len(msgs) == 2
    assert msgs[0]["role"] == "user"
    assert msgs[1]["role"] == "assistant"
    assert "tool_calls_json" in msgs[0]
    assert "artifacts_json" in msgs[0]


@pytest.mark.asyncio
async def test_decisions_crud(db):
    """decisions 表：create + list_by_code + 收益追踪字段。"""
    from persistence import decisions, threads
    await threads.create_thread(tid="t1", title="t", model="m")
    did = await decisions.create_decision(
        thread_id="t1", code="600519", name="茅台",
        target_price=1900.0, entry_low=1685.0, entry_high=1720.0,
        stop_loss=1550.0, take_profit=2080.0,
        cadence=[{"batch": 1, "pct": 0.4, "trigger": "immediate", "price": 1685.0}],
        basis_type="model",
        model_versions_json={"target_price": "model(forward_pe_target.v1)"},
        assumptions=["14-day ATR"],
        citations=[{"source": "astock.kline", "code": "600519"}],
        raw_artifact={"code": "600519"},
    )
    items = await decisions.list_by_code("600519")
    assert any(d["id"] == did for d in items)
    item = await decisions.get_decision(did)
    assert item["status"] == "active"
    assert item["price_at_creation"] is None
    assert item["current_price"] is None
