import asyncio
import json
from copy import deepcopy

import pytest

from fastapi.testclient import TestClient
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langchain_core.messages import AIMessage
from langchain_core.tools import tool

import app as app_module
import agent.router as router_module
from agent.runs import RunCoordinator
from tests.agent.fakes import PausingChatModel, ScriptedChatModel

PROTECTED_CALLS: list[str] = []


@tool
def protected_tool(code: str) -> str:
    """Read one protected fixture value."""
    PROTECTED_CALLS.append(code)
    return "protected-result"


def make_client(host: str = "127.0.0.1") -> TestClient:
    return TestClient(app_module.app, client=(host, 50000))


client = make_client()

HEADERS = {"X-VR-Agent-Model-Key": "request-only-key", "Accept": "text/event-stream"}

START = {
    "threadId": "thread-endpoint",
    "runId": "protocol-endpoint",
    "state": {},
    "messages": [{"id": "user-endpoint", "role": "user", "content": "hello"}],
    "tools": [],
    "context": [],
    "forwardedProps": {
        "runtime": {
            "model": {
                "provider": "fixture",
                "baseURL": "https://example.com/v1",
                "model": "fixture-model",
            }
        }
    },
}


def plain_model(messages=None):
    return ScriptedChatModel(messages or [AIMessage(content="endpoint answer")])


def patch_plain(monkeypatch):
    monkeypatch.setattr("agent.router.build_chat_model", lambda model_ref, secrets: plain_model())
    monkeypatch.setattr("agent.router.build_builtin_tools", lambda: [])
    monkeypatch.setattr("agent.router.build_middleware", lambda: ())


def patch_interrupting(monkeypatch):
    """模型先请求一个受保护工具调用，审批通过后输出文本。"""
    monkeypatch.setattr(
        "agent.router.build_chat_model",
        lambda model_ref, secrets: ScriptedChatModel([
            AIMessage(content="", tool_calls=[{"id": "call-p", "name": "protected_tool", "args": {"code": "600519"}}]),
        ]),
    )
    monkeypatch.setattr("agent.router.build_builtin_tools", lambda: [protected_tool])
    monkeypatch.setattr(
        "agent.router.build_middleware",
        lambda: (HumanInTheLoopMiddleware(interrupt_on={
            "protected_tool": {"allowed_decisions": ["approve", "reject"]},
        }),),
    )


def reset_coordinator():
    router_module.coordinator = RunCoordinator()


def parse_events(text: str) -> list[dict]:
    events = []
    for line in text.splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[len("data: "):]))
    return events


def test_start_streams_standard_ag_ui_events(monkeypatch):
    reset_coordinator()
    patch_plain(monkeypatch)
    response = client.post("/api/agent/run", json=START, headers=HEADERS)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "RUN_STARTED" in response.text
    assert "endpoint answer" in response.text
    assert "RUN_FINISHED" in response.text
    assert "request-only-key" not in response.text


def test_missing_model_key_fails_before_model_build(monkeypatch):
    reset_coordinator()
    called = False

    def fail_if_called(*args):
        nonlocal called
        called = True
        raise AssertionError("model builder ran")

    monkeypatch.setattr("agent.router.build_chat_model", fail_if_called)
    response = client.post("/api/agent/run", json=START)
    assert response.status_code == 400
    assert called is False


def test_second_start_while_running_returns_409(monkeypatch):
    reset_coordinator()
    patch_plain(monkeypatch)
    # 直接占住线程，模拟运行中的 run
    coordinator = router_module.coordinator
    handle = coordinator._handles["thread-endpoint"] = coordinator._make_running_handle(
        thread_id="thread-endpoint",
    )
    response = client.post("/api/agent/run", json=START, headers=HEADERS)
    assert response.status_code == 409
    assert response.json()["code"] == "THREAD_BUSY"
    del coordinator._handles["thread-endpoint"]


def interrupt_and_get_pending(monkeypatch, client):
    patch_interrupting(monkeypatch)
    response = client.post("/api/agent/run", json=START, headers=HEADERS)
    assert response.status_code == 200
    events = parse_events(response.text)
    finished = [e for e in events if e["type"] == "RUN_FINISHED"][0]
    assert finished["outcome"]["type"] == "interrupt"
    return finished["outcome"]["interrupts"][0]["id"]


def test_valid_full_resume_uses_same_handle_and_empty_messages(monkeypatch):
    reset_coordinator()
    PROTECTED_CALLS.clear()
    pending_id = interrupt_and_get_pending(monkeypatch, client)
    old_handle = router_module.coordinator._handles["thread-endpoint"]
    assert old_handle.phase == "awaiting_approval"
    assert old_handle.runtime.graph is None

    monkeypatch.setattr(
        "agent.router.build_chat_model",
        lambda model_ref, secrets: ScriptedChatModel([AIMessage(content="resumed answer")]),
    )
    resume = deepcopy(START)
    resume["runId"] = "protocol-resume"
    resume["messages"] = []
    resume["forwardedProps"]["command"] = {
        "resume": [{"interruptId": pending_id, "status": "resolved", "payload": {"decision": "approve", "scope": "once"}}],
    }
    response = client.post("/api/agent/run", json=resume, headers=HEADERS)
    assert response.status_code == 200
    assert "resumed answer" in response.text
    assert "RUN_FINISHED" in response.text
    assert PROTECTED_CALLS == ["600519"]
    assert router_module.coordinator._handles.get("thread-endpoint") is None


def test_steer_away_closes_old_handle_and_starts_fresh_run(monkeypatch):
    reset_coordinator()
    PROTECTED_CALLS.clear()
    pending_id = interrupt_and_get_pending(monkeypatch, client)
    old_handle = router_module.coordinator._handles["thread-endpoint"]

    monkeypatch.setattr(
        "agent.router.build_chat_model",
        lambda model_ref, secrets: ScriptedChatModel([AIMessage(content="fresh answer")]),
    )
    steer = deepcopy(START)
    steer["runId"] = "protocol-steer-away"
    steer["messages"] = [{"id": "user-steer", "role": "user", "content": "use a different approach"}]
    steer["forwardedProps"]["command"] = {
        "resume": [{"interruptId": pending_id, "status": "cancelled"}],
    }
    response = client.post("/api/agent/run", json=steer, headers=HEADERS)
    assert response.status_code == 200
    assert old_handle.phase == "cancelled"
    assert old_handle.runtime.graph is None and old_handle.runtime.model is None
    assert "RUN_STARTED" in response.text and "RUN_FINISHED" in response.text
    assert PROTECTED_CALLS == []


def test_partial_or_unknown_resume_fails_closed(monkeypatch):
    reset_coordinator()
    PROTECTED_CALLS.clear()
    interrupt_and_get_pending(monkeypatch, client)
    resume = deepcopy(START)
    resume["runId"] = "protocol-bad-resume"
    resume["messages"] = []
    resume["forwardedProps"]["command"] = {
        "resume": [{"interruptId": "unknown", "status": "resolved", "payload": {"decision": "approve", "scope": "once"}}],
    }
    response = client.post("/api/agent/run", json=resume, headers=HEADERS)
    assert response.status_code == 400
    assert PROTECTED_CALLS == []


def test_model_exception_is_redacted_run_error(monkeypatch):
    reset_coordinator()
    monkeypatch.setattr("agent.router.build_chat_model", lambda model_ref, secrets: plain_model())

    def boom(*args, **kwargs):
        raise RuntimeError("secret request-only-key leaked in error")

    monkeypatch.setattr("agent.router.build_middleware", lambda: ())
    monkeypatch.setattr("agent.router.build_builtin_tools", lambda: [])
    response = client.post("/api/agent/run", json=START, headers=HEADERS)
    # 直接用会抛错的适配器再测一轮
    reset_coordinator()
    monkeypatch.setattr(
        "agent.router.build_chat_model",
        lambda model_ref, secrets: ScriptedChatModel([]),  # 空脚本 → 越界异常
    )
    response = client.post("/api/agent/run", json=START, headers=HEADERS)
    assert response.status_code == 200
    assert "RUN_ERROR" in response.text
    assert "request-only-key" not in response.text


def test_non_loopback_http_with_model_key_is_rejected(monkeypatch):
    reset_coordinator()
    patch_plain(monkeypatch)
    remote = make_client("203.0.113.9")
    response = remote.post("/api/agent/run", json=START, headers=HEADERS)
    assert response.status_code == 400
    assert response.json()["code"] == "INSECURE_MODEL_KEY_TRANSPORT"


def test_loopback_http_is_allowed(monkeypatch):
    reset_coordinator()
    patch_plain(monkeypatch)
    response = client.post("/api/agent/run", json=START, headers=HEADERS)
    assert response.status_code == 200


def test_untrusted_forwarded_proto_is_rejected(monkeypatch):
    reset_coordinator()
    patch_plain(monkeypatch)
    monkeypatch.setenv("VR_TRUST_PROXY_HEADERS", "1")
    monkeypatch.setenv("VR_TRUSTED_PROXY_IPS", "10.0.0.1")
    remote = make_client("203.0.113.9")
    response = remote.post(
        "/api/agent/run",
        json=START,
        headers={**HEADERS, "X-Forwarded-Proto": "https"},
    )
    assert response.status_code == 400
    assert response.json()["code"] == "INSECURE_MODEL_KEY_TRANSPORT"


def test_trusted_proxy_https_is_allowed(monkeypatch):
    reset_coordinator()
    patch_plain(monkeypatch)
    monkeypatch.setenv("VR_TRUST_PROXY_HEADERS", "1")
    monkeypatch.setenv("VR_TRUSTED_PROXY_IPS", "10.0.0.1")
    remote = make_client("10.0.0.1")
    response = remote.post(
        "/api/agent/run",
        json=START,
        headers={**HEADERS, "X-Forwarded-Proto": "https"},
    )
    assert response.status_code == 200


def test_retry_is_rejected_until_1b(monkeypatch):
    reset_coordinator()
    patch_plain(monkeypatch)
    payload = deepcopy(START)
    payload["forwardedProps"]["runtime"]["retryOf"] = "run-old"
    response = client.post("/api/agent/run", json=payload, headers=HEADERS)
    assert response.status_code == 400
    assert response.json()["code"] == "RETRY_REQUIRES_DURABLE_HISTORY"


async def post_then_disconnect(app, payload: dict) -> list[dict]:
    body = json.dumps(payload).encode()
    incoming = iter([
        {"type": "http.request", "body": body, "more_body": False},
        {"type": "http.disconnect"},
    ])
    sent: list[dict] = []

    async def receive() -> dict:
        # 用带默认值的 next：StopIteration 在 anyio 取消作用域里会破坏 is_disconnected 探测。
        return next(incoming, {"type": "http.disconnect"})

    async def send(message: dict) -> None:
        sent.append(message)

    await app({
        "type": "http",
        "asgi": {"version": "3.0"},
        # 不声明 spec_version 2.4：该版本下 Starlette 不再监听断连，测试无法覆盖取消路径
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/agent/run",
        "raw_path": b"/api/agent/run",
        "query_string": b"",
        "root_path": "",
        "state": {},
        "extensions": {},
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
        "headers": [
            (b"host", b"testserver"),
            (b"content-type", b"application/json"),
            (b"accept", b"text/event-stream"),
            (b"x-vr-agent-model-key", b"request-only-key"),
            (b"content-length", str(len(body)).encode()),
        ],
    }, receive, send)
    return sent


@pytest.mark.asyncio
async def test_client_disconnect_cancels_run(monkeypatch):
    reset_coordinator()
    PROTECTED_CALLS.clear()
    coordinator = router_module.coordinator
    cancels: list[str] = []
    captured_handles: list = []
    original_cancel = coordinator.cancel_sync

    def spy_cancel(thread_id):
        cancels.append(thread_id)
        handle = coordinator._handles.get(thread_id)
        if handle is not None:
            captured_handles.append(handle)
        original_cancel(thread_id)

    coordinator.cancel_sync = spy_cancel
    # 首个事件后暂停，让 http.disconnect 在流中途被处理
    monkeypatch.setattr(
        "agent.router.build_chat_model",
        lambda model_ref, secrets: PausingChatModel([
            AIMessage(content="", tool_calls=[{"id": "call-p", "name": "protected_tool", "args": {"code": "600519"}}]),
        ]),
    )
    monkeypatch.setattr("agent.router.build_builtin_tools", lambda: [protected_tool])
    monkeypatch.setattr(
        "agent.router.build_middleware",
        lambda: (HumanInTheLoopMiddleware(interrupt_on={
            "protected_tool": {"allowed_decisions": ["approve", "reject"]},
        }),),
    )

    sent = await post_then_disconnect(app_module.app, START)
    # 断言：恰好一次 cancel；handle 进入 cancelled 且图已释放；无断连后的事件帧。
    assert cancels == ["thread-endpoint"]
    for handle in captured_handles:
        assert handle.phase == "cancelled"
        assert handle.runtime.graph is None
        assert handle.runtime.model is None
    bodies = [m for m in sent if m.get("type") == "http.response.body"]
    for message in bodies:
        assert "protected-result" not in message.get("body", b"").decode("utf-8", "ignore")
        assert "RUN_FINISHED" not in message.get("body", b"").decode("utf-8", "ignore")
    assert PROTECTED_CALLS == []


def test_malformed_shapes_fail_closed(monkeypatch):
    reset_coordinator()
    patch_plain(monkeypatch)

    # steer-away 没有/多于一条新消息
    no_msg = deepcopy(START)
    no_msg["runId"] = "r-steer-nomsg"
    no_msg["messages"] = []
    no_msg["forwardedProps"]["command"] = {"resume": [{"interruptId": "x", "status": "cancelled"}]}
    assert client.post("/api/agent/run", json=no_msg, headers=HEADERS).status_code == 400

    two_msgs = deepcopy(START)
    two_msgs["runId"] = "r-start-two"
    two_msgs["messages"] = [
        {"id": "u1", "role": "user", "content": "a"},
        {"id": "u2", "role": "user", "content": "b"},
    ]
    assert client.post("/api/agent/run", json=two_msgs, headers=HEADERS).status_code == 400


def test_resume_with_new_message_fails_closed(monkeypatch):
    reset_coordinator()
    patch_plain(monkeypatch)
    pending_id = interrupt_and_get_pending(monkeypatch, client)
    resume = deepcopy(START)
    resume["runId"] = "r-resume-with-msg"
    resume["messages"] = [{"id": "u9", "role": "user", "content": "extra"}]
    resume["forwardedProps"]["command"] = {
        "resume": [{"interruptId": pending_id, "status": "resolved", "payload": {"decision": "approve", "scope": "once"}}],
    }
    assert client.post("/api/agent/run", json=resume, headers=HEADERS).status_code == 400
    assert PROTECTED_CALLS == []


def test_resume_model_mismatch_returns_409(monkeypatch):
    reset_coordinator()
    pending_id = interrupt_and_get_pending(monkeypatch, client)
    monkeypatch.setattr(
        "agent.router.build_chat_model",
        lambda model_ref, secrets: ScriptedChatModel([AIMessage(content="resumed")]),
    )
    resume = deepcopy(START)
    resume["runId"] = "r-mismatch"
    resume["messages"] = []
    resume["forwardedProps"]["runtime"]["model"]["model"] = "different-model"
    resume["forwardedProps"]["command"] = {
        "resume": [{"interruptId": pending_id, "status": "resolved", "payload": {"decision": "approve", "scope": "once"}}],
    }
    response = client.post("/api/agent/run", json=resume, headers=HEADERS)
    assert response.status_code == 409
    assert response.json()["code"] == "RUN_CONFIG_MISMATCH"
    # 原句柄保持 awaiting_approval，pending 不变
    handle = router_module.coordinator._handles["thread-endpoint"]
    assert handle.phase == "awaiting_approval"
    assert len(handle.pending_interrupts) == 1


@pytest.mark.asyncio
async def test_concurrent_resume_is_atomic(monkeypatch):
    """两个并发 resume 只能有一个成功（单锁原子迁移）。"""
    import httpx
    reset_coordinator()
    pending_id = interrupt_and_get_pending(monkeypatch, client)
    build_calls = []

    def counting_builder(model_ref, secrets):
        build_calls.append(model_ref.model)
        return ScriptedChatModel([AIMessage(content="resumed")])

    monkeypatch.setattr("agent.router.build_chat_model", counting_builder)
    resume = deepcopy(START)
    resume["runId"] = "r-atomic"
    resume["messages"] = []
    resume["forwardedProps"]["command"] = {
        "resume": [{"interruptId": pending_id, "status": "resolved", "payload": {"decision": "approve", "scope": "once"}}],
    }

    async def fire(app):
        body = json.dumps(resume).encode()
        incoming = iter([{"type": "http.request", "body": body, "more_body": False}])
        sent = []

        async def receive():
            return next(incoming, {"type": "http.disconnect"})

        async def send(message):
            sent.append(message)

        await app({
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": "POST", "scheme": "http", "path": "/api/agent/run",
            "raw_path": b"/api/agent/run", "query_string": b"", "root_path": "",
            "state": {}, "extensions": {}, "client": ("127.0.0.1", 50000), "server": ("t", 80),
            "headers": [
                (b"host", b"t"), (b"content-type", b"application/json"),
                (b"accept", b"text/event-stream"), (b"x-vr-agent-model-key", b"request-only-key"),
                (b"content-length", str(len(body)).encode()),
            ],
        }, receive, send)
        status = next((m["status"] for m in sent if m["type"] == "http.response.start"), None)
        return status

    statuses = await asyncio.gather(fire(app_module.app), fire(app_module.app))
    statuses = sorted(statuses)
    assert statuses == [200, 409]
    assert len(build_calls) == 1
