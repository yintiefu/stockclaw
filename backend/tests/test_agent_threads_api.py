"""/api/agent/threads* CRUD 端点协议测试。"""
import asyncio

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch, tmp_path):
    """每个测试一个临时 db（用 pytest tmp_path，避免 tempfile.mktemp 弃用警告）。"""
    monkeypatch.delenv("VR_API_KEY", raising=False)
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("VR_AGENT_DB", str(db_path))
    import app
    from persistence import db
    asyncio.run(db.close_db())
    asyncio.run(db.init_db())
    return TestClient(app.app)


def test_list_threads_empty(client):
    """空 DB 时 GET /api/agent/threads 返回 []。"""
    r = client.get("/api/agent/threads")
    assert r.status_code == 200
    assert r.json() == []


def test_create_thread_returns_id(client):
    """POST /api/agent/threads 返回新 thread_id + 元数据。"""
    r = client.post("/api/agent/threads", json={"title": "测试", "model": "glm-4.6"})
    assert r.status_code == 200
    body = r.json()
    assert "id" in body
    assert body["title"] == "测试"
    assert body["model"] == "glm-4.6"
    assert "created_at" in body


def test_create_thread_accepts_client_id(client):
    """前端可选用 crypto.randomUUID 提供 id，省去 local-xxx → real-xxx 映射。"""
    r = client.post("/api/agent/threads", json={
        "id": "client-uuid-1234",
        "title": "前端生成 ID",
        "model": "m",
    })
    assert r.status_code == 200
    assert r.json()["id"] == "client-uuid-1234"


def test_list_threads_after_create(client):
    """创建后 list 能看到，按 updated_at 倒序。"""
    client.post("/api/agent/threads", json={"title": "A", "model": "m"})
    client.post("/api/agent/threads", json={"title": "B", "model": "m"})
    r = client.get("/api/agent/threads")
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 2
    titles = [t["title"] for t in items]
    assert titles == ["B", "A"]


def test_rename_thread(client):
    """PATCH /api/agent/threads/:id 改标题。"""
    r = client.post("/api/agent/threads", json={"title": "old", "model": "m"})
    tid = r.json()["id"]
    r2 = client.patch(f"/api/agent/threads/{tid}", json={"title": "new"})
    assert r2.status_code == 200
    assert r2.json() == {"ok": True}


def test_delete_thread(client):
    """DELETE /api/agent/threads/:id 删除 + CASCADE 清消息。"""
    r = client.post("/api/agent/threads", json={"title": "x", "model": "m"})
    tid = r.json()["id"]
    r2 = client.delete(f"/api/agent/threads/{tid}")
    assert r2.status_code == 200
    assert r2.json() == {"ok": True}
    assert all(t["id"] != tid for t in client.get("/api/agent/threads").json())


def test_list_messages_empty(client):
    """空 thread GET messages 返回 []。"""
    r = client.post("/api/agent/threads", json={"title": "x", "model": "m"})
    tid = r.json()["id"]
    r2 = client.get(f"/api/agent/threads/{tid}/messages")
    assert r2.status_code == 200
    assert r2.json() == []


def test_append_message(client):
    """POST /api/agent/threads/:id/messages 归档一条消息。"""
    r = client.post("/api/agent/threads", json={"title": "x", "model": "m"})
    tid = r.json()["id"]
    r2 = client.post(f"/api/agent/threads/{tid}/messages", json={"role": "user", "content": "你好"})
    assert r2.status_code == 200
    assert "id" in r2.json()
    # 列表能查到
    msgs = client.get(f"/api/agent/threads/{tid}/messages").json()
    assert len(msgs) == 1
    assert msgs[0]["content"] == "你好"
    assert msgs[0]["role"] == "user"
