"""Task 5：真实 `langgraph dev` 服务端契约——原生线程/run/HITL、进程重启恢复、
分层 CORS 边界（允许本地前端源、拒绝敌意读取、承认 text/plain 盲写）。"""
from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
import pytest
from langgraph_sdk import get_sync_client

from tests.agent.server_harness import LangGraphServerHarness

BACKEND = Path(__file__).parents[2]
POLL_TIMEOUT_SECONDS = 60.0


# ---------------------------------------------------------------------------
# 配置契约
# ---------------------------------------------------------------------------

def test_production_langgraph_config_is_local_and_persistent_ready():
    config = json.loads((BACKEND / "langgraph.json").read_text(encoding="utf-8"))
    assert config == {
        "dependencies": ["./"],
        "graphs": {
            "agent": "./agent/graph.py:graph",
            "embedded_agent": "./agent/embedded_graph.py:graph",
            "debate": "./agent/workflows_graph.py:debate_graph",
            "reflection": "./agent/workflows_graph.py:reflection_graph",
            "daily_review": "./agent/workflows_graph.py:daily_review_graph",
            "news_digest": "./agent/workflows_graph.py:news_digest_graph",
        },
        "env": {"CORS_ALLOW_ORIGINS": "http://127.0.0.1:5899"},
    }
    # 通配形态覆盖 migrate_agent_data 留下的 .langgraph_api.bak 等残留
    assert ".langgraph_api*" in (BACKEND / ".gitignore").read_text(encoding="utf-8")


def test_server_fixture_config_uses_only_the_server_graph():
    config = json.loads(
        (BACKEND / "tests/agent_e2e/server_langgraph.json").read_text(encoding="utf-8")
    )
    assert config == {
        "dependencies": ["./"],
        "graphs": {"agent": "./server_graph.py:graph"},
        "env": {"CORS_ALLOW_ORIGINS": "http://127.0.0.1:5873"},
    }


# ---------------------------------------------------------------------------
# session 级真实服务进程
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def server(tmp_path_factory):
    harness = LangGraphServerHarness(tmp_path_factory.mktemp("langgraph-server"))
    harness.start()
    try:
        yield harness
    finally:
        harness.stop()


def _client(url: str):
    return get_sync_client(url=url, api_key=None)


def _poll(predicate, description: str, timeout: float = POLL_TIMEOUT_SECONDS):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(0.2)
    raise AssertionError(f"等待超时：{description}（最后状态：{last!r}）")


def wait_until_interrupted(client, thread_id: str, run_id: str | None):
    def check():
        state = client.threads.get_state(thread_id)
        tasks = state.get("tasks") or []
        for task in tasks:
            if task.get("interrupts"):
                return state
        if run_id is not None:
            run = client.runs.get(thread_id, run_id)
            if run.get("status") in {"error", "success"}:
                raise AssertionError(f"run 在中断前进入终态：{run.get('status')}")
        return None

    return _poll(check, f"线程 {thread_id} 出现中断")


def resume_with_decisions(url: str, thread_id: str, decisions: list[dict]) -> None:
    client = _client(url)
    client.runs.create(thread_id, "agent", command={"resume": {"decisions": decisions}})


def wait_for_terminal_state(url: str, thread_id: str):
    def check():
        state = _client(url).threads.get_state(thread_id)
        return state if state.get("next") == [] else None

    return _poll(check, f"线程 {thread_id} 到达终态")


def wait_for_thread_status(url: str, thread_id: str) -> str:
    def check():
        thread = _client(url).threads.get(thread_id)
        return thread["status"] if thread["status"] != "busy" else None

    return _poll(check, f"线程 {thread_id} 离开 busy 状态")


def create_interrupted_thread(url: str) -> str:
    client = _client(url)
    thread = client.threads.create()
    client.runs.create(
        thread["thread_id"], "agent",
        input={"messages": [{"role": "user", "content": "审批测试"}]},
    )
    wait_until_interrupted(client, thread["thread_id"], None)
    return thread["thread_id"]


# ---------------------------------------------------------------------------
# 原生线程 / run / HITL
# ---------------------------------------------------------------------------

def test_native_thread_run_and_interrupt(server):
    client = get_sync_client(url=server.url, api_key=None)
    thread = client.threads.create()
    run = client.runs.create(thread["thread_id"], "agent", input={"messages": [{"role": "user", "content": "审批测试"}]})
    state = wait_until_interrupted(client, thread["thread_id"], run["run_id"])
    assert state["next"]
    assert len(state["tasks"][0]["interrupts"][0]["value"]["action_requests"]) == 1
    assert state["tasks"][0]["interrupts"][0]["value"]["action_requests"][0]["name"] == "fixture_echo"
    resume_with_decisions(server.url, thread["thread_id"], [{"type": "approve"}])
    terminal = wait_for_terminal_state(server.url, thread["thread_id"])
    assert terminal["next"] == []
    assert "approved fixture value" in str(terminal["values"])


def test_interrupt_survives_process_restart(server):
    thread_id = create_interrupted_thread(server.url)
    server.stop()
    assert list(server.cwd.glob(".langgraph_api/**/*.pckl")) or any((server.cwd / ".langgraph_api").iterdir())
    server.start()
    client = get_sync_client(url=server.url, api_key=None)
    assert thread_id in {item["thread_id"] for item in client.threads.search(limit=100)}
    restored = client.threads.get_state(thread_id)
    assert restored["next"]
    assert "审批测试" in str(restored["values"])
    resume_with_decisions(server.url, thread_id, [{"type": "approve"}])
    assert wait_for_terminal_state(server.url, thread_id)["next"] == []


# ---------------------------------------------------------------------------
# 分层 CORS 边界
# ---------------------------------------------------------------------------

def test_cors_blocks_hostile_reads_but_accepts_known_local_origin(server):
    hostile = {"Origin": "https://evil.example.com"}
    preflight = httpx.options(f"{server.url}/threads", headers={**hostile,
        "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type"})
    assert preflight.status_code == 400
    actual = httpx.post(f"{server.url}/threads", headers=hostile, json={})
    assert actual.status_code == 200
    assert "access-control-allow-origin" not in actual.headers
    allowed = httpx.post(f"{server.url}/threads", headers={"Origin": server.frontend_origin}, json={})
    assert allowed.headers["access-control-allow-origin"] == server.frontend_origin


def test_text_plain_simple_posts_can_blind_write(server):
    headers = {"Origin": "https://evil.example.com", "Content-Type": "text/plain"}
    created = httpx.post(f"{server.url}/threads", headers=headers, content="{}")
    assert created.status_code == 200
    thread_id = created.json()["thread_id"]
    submitted = httpx.post(
        f"{server.url}/threads/{thread_id}/runs",
        headers=headers,
        content=json.dumps({"assistant_id": "agent", "input": {"messages": [{"role": "user", "content": "审批测试"}]}}),
    )
    assert submitted.status_code == 200
    assert wait_for_thread_status(server.url, thread_id) == "interrupted"
