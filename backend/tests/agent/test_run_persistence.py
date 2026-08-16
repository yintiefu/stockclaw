"""1B run 准入持久化契约：准入先落盘、服务端权威历史、重复/冲突拒绝。"""

import asyncio
import json
from pathlib import Path

import pytest
from ag_ui.core.types import UserMessage

from agent.models import AgentMessage, ModelRef, RunDocument, RunSecrets, ThreadDocument
from agent.protocol import PendingInterrupt
from agent.runs import (
    DuplicateRunActive,
    DuplicateRunTerminal,
    MessageConflict,
    RunCoordinator,
)
from agent.stores import AgentPaths, RevisionConflict, RunStore, ThreadStore, utc_now
from tests.agent.fakes import ScriptedChatModel

NOW = "2026-08-15T12:00:00Z"
MODEL_REF = ModelRef(provider="fixture", baseURL="https://example.com/v1", model="fixture-model")
SECRETS = RunSecrets.model_validate({"model_api_key": "request-only-key"})


class CountingBuilder:
    def __init__(self):
        self.count = 0
        self.observed: list[dict] = []

    def __call__(self, model_ref, secrets):
        self.count += 1
        self.observed.append({})
        return ScriptedChatModel([])


def make_coordinator(tmp_path: Path):
    paths = AgentPaths(tmp_path / "agent")
    threads = ThreadStore(paths)
    runs = RunStore(paths)
    return RunCoordinator(threads=threads, runs=runs), threads, runs


def user_msg(msg_id: str, content: str) -> UserMessage:
    return UserMessage(id=msg_id, content=content)


async def start(coordinator, *, thread_id="thread-1", protocol_run_id="protocol-1",
                user_id="user-1", content="分析现金流", revision=0, builder=None):
    return await coordinator.acquire_start(
        model_ref=MODEL_REF,
        secrets=SECRETS,
        model_builder=builder or CountingBuilder(),
        tools=[],
        thread_id=thread_id,
        middleware=(),
        protocol_run_id=protocol_run_id,
        messages=[user_msg(user_id, content)],
        client_revision=revision,
    )


@pytest.mark.asyncio
async def test_start_persists_user_and_running_run_before_model_execution(tmp_path):
    coordinator, threads, runs = make_coordinator(tmp_path)
    observed: dict[str, object] = {}

    def observing_builder(model_ref, secrets):
        observed["thread_bytes"] = (threads._docs._path("thread-1")).read_bytes()
        observed["run"] = runs.get("run-observed") if False else None
        return ScriptedChatModel([])

    admission = await coordinator.acquire_start(
        model_ref=MODEL_REF,
        secrets=SECRETS,
        model_builder=observing_builder,
        tools=[],
        thread_id="thread-1",
        middleware=(),
        protocol_run_id="protocol-1",
        messages=[user_msg("user-1", "分析现金流")],
        client_revision=None,
    )
    # model_builder 在持久化之后运行：此处线程文档已经包含被接受的用户消息
    thread = threads.get("thread-1")
    assert [m.id for m in thread.messages] == ["user-1"]
    assert thread.revision == 1
    assert admission.run.status == "running"
    assert admission.run.protocol_run_ids == ["protocol-1"]
    assert admission.run.trigger_message_id == "user-1"
    assert admission.handle.product_run_id == admission.run.id
    assert admission.handle.thread_revision == 1
    persisted_runs = runs.list_documents()
    assert len(persisted_runs) == 1 and persisted_runs[0].status == "running"
    assert "request-only-key" not in json.dumps(persisted_runs[0].model_dump(mode="json"))


@pytest.mark.asyncio
async def test_start_rebuilds_graph_input_from_complete_server_history(tmp_path):
    coordinator, threads, runs = make_coordinator(tmp_path)
    seed = ThreadDocument.new("thread-1", "研究", now=NOW).model_copy(update={"messages": [
        AgentMessage(id="user-old", role="user", content="上一轮问题"),
        AgentMessage(id="assistant-partial", role="assistant", content="未完成回答", partial=True),
    ], "revision": 0})
    threads.create(seed)

    admission = await coordinator.acquire_start(
        model_ref=MODEL_REF,
        secrets=SECRETS,
        model_builder=CountingBuilder(),
        tools=[],
        thread_id="thread-1",
        middleware=(),
        protocol_run_id="protocol-1",
        messages=[user_msg("user-old", "上一轮问题"), user_msg("user-new", "新问题")],
        client_revision=0,
    )

    ids = [m.id for m in admission.input.messages]
    assert ids == ["user-old", "user-new"]  # partial 不进入模型输入
    assert admission.input.messages[-1].content == "新问题"


@pytest.mark.asyncio
async def test_start_rejects_stale_revision_and_forked_head_before_model_builder(tmp_path):
    coordinator, threads, runs = make_coordinator(tmp_path)
    builder = CountingBuilder()
    seed = ThreadDocument.new("thread-1", "研究", now=NOW).model_copy(update={"messages": [
        AgentMessage(id="user-old", role="user", content="上一轮"),
    ], "revision": 3})
    threads.create(seed)

    with pytest.raises(RevisionConflict):
        await start(coordinator, user_id="user-new", content="新问题", revision=0, builder=builder)

    # forked head：客户端前缀与服务端历史不一致
    forked_messages = [user_msg("user-old", "被篡改的内容"), user_msg("user-new", "新问题")]
    with pytest.raises(MessageConflict):
        await coordinator.acquire_start(
            model_ref=MODEL_REF, secrets=SECRETS, model_builder=builder, tools=[],
            thread_id="thread-1", middleware=(),
            protocol_run_id="protocol-x", messages=forked_messages, client_revision=3,
        )

    assert builder.count == 0
    assert threads.get("thread-1").revision == 3
    assert [m.id for m in threads.get("thread-1").messages] == ["user-old"]
    assert runs.list_documents() == []


@pytest.mark.asyncio
async def test_active_and_terminal_duplicates_do_not_write_or_build_a_graph(tmp_path):
    coordinator, threads, runs = make_coordinator(tmp_path)

    admission = await start(coordinator, protocol_run_id="protocol-1", revision=None)
    thread_bytes = (threads._docs._path("thread-1")).read_bytes()
    builder = CountingBuilder()

    with pytest.raises(DuplicateRunActive):
        await start(coordinator, protocol_run_id="protocol-1", revision=1, builder=builder)
    assert (threads._docs._path("thread-1")).read_bytes() == thread_bytes
    assert builder.count == 0

    # 结束旧 run 后，同一 protocol run id 重放 → 终态重复
    await coordinator.finish_if_terminal("thread-1", "completed")
    thread_bytes = (threads._docs._path("thread-1")).read_bytes()  # 终态落盘后的新基线
    with pytest.raises(DuplicateRunTerminal):
        await start(coordinator, protocol_run_id="protocol-1", revision=None, builder=builder)
    assert (threads._docs._path("thread-1")).read_bytes() == thread_bytes
    assert builder.count == 0


@pytest.mark.asyncio
async def test_existing_message_id_with_different_content_is_message_conflict(tmp_path):
    coordinator, threads, runs = make_coordinator(tmp_path)
    builder = CountingBuilder()
    seed = ThreadDocument.new("thread-1", "研究", now=NOW).model_copy(update={"messages": [
        AgentMessage(id="user-1", role="user", content="原始内容"),
    ], "revision": 0})
    threads.create(seed)

    with pytest.raises(MessageConflict):
        await start(coordinator, user_id="user-1", content="不同内容", revision=0, builder=builder)

    assert builder.count == 0
    assert threads.get("thread-1").messages[0].content == "原始内容"
    assert threads.get("thread-1").revision == 0
    assert runs.list_documents() == []


@pytest.mark.asyncio
async def test_resume_appends_protocol_id_to_same_product_run(tmp_path):
    coordinator, threads, runs = make_coordinator(tmp_path)
    admission = await start(coordinator, protocol_run_id="protocol-1", revision=None)

    handle = coordinator.active("thread-1")
    handle.phase = "awaiting_approval"
    handle.pending_interrupts = [PendingInterrupt(bridge_interrupt_id="int-1", order=0, tool_call_id="call-1", value={})]

    await coordinator.acquire_resume(
        "thread-1",
        model_ref=MODEL_REF,
        secrets=SECRETS,
        model_builder=CountingBuilder(),
        validate=lambda pending: {"int-1": {"accepted": True}},
        client_revision=handle.thread_revision,
        protocol_run_id="protocol-2",
    )

    run = runs.get(admission.run.id)
    assert run.id == admission.run.id  # 同一个产品 run
    assert run.protocol_run_ids == ["protocol-1", "protocol-2"]


@pytest.mark.asyncio
async def test_steer_away_cancels_old_run_before_persisting_new_user_and_run(tmp_path):
    coordinator, threads, runs = make_coordinator(tmp_path)
    admission = await start(coordinator, protocol_run_id="protocol-1", revision=None)

    handle = coordinator.active("thread-1")
    handle.phase = "awaiting_approval"
    handle.pending_interrupts = [PendingInterrupt(bridge_interrupt_id="int-1", order=0, tool_call_id="call-1", value={})]
    old_run_id = admission.run.id

    statuses_seen_in_builder: list[str] = []

    def checking_builder(model_ref, secrets):
        statuses_seen_in_builder.append(runs.get(old_run_id).status)
        return ScriptedChatModel([])

    new_admission = await coordinator.acquire_steer_away(
        "thread-1",
        entries=[{"interruptId": "int-1", "status": "cancelled"}],
        validate=lambda pending, entries: None,
        model_ref=MODEL_REF,
        secrets=SECRETS,
        model_builder=checking_builder,
        tools=[],
        middleware=(),
        messages=[user_msg("user-1", "分析现金流"), user_msg("user-2", "换个方向")],
        client_revision=handle.thread_revision,
        protocol_run_id="protocol-2",
    )

    assert statuses_seen_in_builder == ["cancelled"]  # 新 builder 运行前旧 run 已是 cancelled
    assert runs.get(old_run_id).status == "cancelled"
    thread = threads.get("thread-1")
    assert [m.id for m in thread.messages] == ["user-1", "user-2"]
    assert new_admission.handle.product_run_id != old_run_id
    assert runs.get(new_admission.handle.product_run_id).status == "running"
    assert new_admission.handle.runtime.graph is not None
    assert handle.runtime.graph is None  # 旧句柄已释放


@pytest.mark.asyncio
async def test_graph_failure_after_persistence_finalizes_run_failed(tmp_path):
    coordinator, threads, runs = make_coordinator(tmp_path)

    def failing_builder(model_ref, secrets):
        raise RuntimeError("provider unavailable")

    with pytest.raises(RuntimeError):
        await coordinator.acquire_start(
            model_ref=MODEL_REF, secrets=SECRETS, model_builder=failing_builder, tools=[],
            thread_id="thread-1", middleware=(),
            protocol_run_id="protocol-1",
            messages=[user_msg("user-1", "分析")], client_revision=None,
        )

    # 用户消息保留，run 终态 failed，线程 last_run 同步
    thread = threads.get("thread-1")
    assert [m.id for m in thread.messages] == ["user-1"]
    assert coordinator.active("thread-1") is None
    persisted = runs.list_documents()
    assert persisted[0].status == "failed"
    assert thread.last_run is not None and thread.last_run.status == "failed"


# ---- Task 5：流式边界持久化 / 部分消息 / 取消 ----


import asyncio

import app as app_module
import agent.router as router_module
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from agent.router import build_services
from tests.agent.fakes import PausingChatModel

RECORDED_INPUTS: list = []


@tool
def journal_tool(code: str) -> str:
    """Return a fixture result containing no secrets."""
    return f"journal-result-{code}"


@tool
def leaking_tool(code: str) -> str:
    """Echo the request key back to verify redaction at the persistence boundary."""
    return "leaked request-only-key inside tool result"


class RecordingModel(ScriptedChatModel):
    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        RECORDED_INPUTS.append(list(messages))
        return super()._generate(messages, stop, run_manager, **kwargs)

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        RECORDED_INPUTS.append(list(messages))
        return super()._stream(messages, stop, run_manager, **kwargs)


@pytest.fixture
def http(tmp_path, monkeypatch):
    services = build_services(tmp_path / "agent")
    monkeypatch.setattr(router_module, "services", services)
    monkeypatch.setattr("agent.router.build_builtin_tools", lambda: [journal_tool])
    monkeypatch.setattr("agent.router.build_middleware", lambda: ())
    client = TestClient(app_module.app, client=("127.0.0.1", 50010))
    return client, services


def start_payload_json(thread_id="thread-http", run_id="protocol-http", content="hello", revision=0, prefix=(), user_id="user-http"):
    # prefix 元素：(id, content) 视为 user；(id, role, content) 保留原角色
    prefix_messages = []
    for item in prefix:
        if len(item) == 3:
            prefix_messages.append({"id": item[0], "role": item[1], "content": item[2]})
        else:
            prefix_messages.append({"id": item[0], "role": "user", "content": item[1]})
    messages = prefix_messages
    messages.append({"id": user_id, "role": "user", "content": content})
    return {
        "threadId": thread_id,
        "runId": run_id,
        "state": {},
        "messages": messages,
        "tools": [],
        "context": [],
        "forwardedProps": {"runtime": {
            "model": {"provider": "fixture", "baseURL": "https://example.com/v1", "model": "fixture-model"},
            "threadRevision": revision,
        }},
    }


def parse_sse(text: str) -> list[dict]:
    return [json.loads(line[len("data: "):]) for line in text.splitlines() if line.startswith("data: ")]


def test_event_boundary_revisions_and_message_persistence(http, monkeypatch):
    client, services = http
    RECORDED_INPUTS.clear()
    monkeypatch.setattr(
        "agent.router.build_chat_model",
        lambda ref, sec: RecordingModel([
            AIMessage(content="", tool_calls=[{"id": "call-1", "name": "journal_tool", "args": {"code": "600519"}}]),
            AIMessage(content="最终回答"),
        ]),
    )
    resp = client.post("/api/agent/run", json=start_payload_json(), headers={
        "X-VR-Agent-Model-Key": "request-only-key", "Accept": "text/event-stream"})
    assert resp.status_code == 200, resp.text

    events = parse_sse(resp.text)
    revisions = [e for e in events if e.get("type") == "CUSTOM" and e.get("name") == "thread.revision.updated"]
    finishes = [i for i, e in enumerate(events) if e.get("type") == "RUN_FINISHED"]
    assert finishes and revisions
    last_rev_index = max(i for i, e in enumerate(events) if e.get("type") == "CUSTOM" and e.get("name") == "thread.revision.updated")
    assert last_rev_index < finishes[-1]
    values = [json.loads(e["value"])["revision"] if isinstance(e.get("value"), str) else e["value"]["revision"] for e in revisions]
    assert values == sorted(values)
    assert len(values) == len(set(values))

    thread = services.threads.get("thread-http")
    ids = [m.id for m in thread.messages]
    assert ids[0] == "user-http"
    roles = {m.role for m in thread.messages}
    assert "assistant" in roles
    # 模型协议：tool result 之前必须存在携带 tool_calls 的 assistant 请求消息，
    # 最终回答不得重复声明已完成调用
    tool_msgs = [m for m in thread.messages if m.role == "tool"]
    for tool_msg in tool_msgs:
        request_msgs = [
            m for m in thread.messages
            if m.role == "assistant"
            and any(call.get("id") == tool_msg.tool_call_id for call in m.tool_calls)
        ]
        assert request_msgs, "缺少携带 tool_calls 的 assistant 请求消息"
        assert thread.messages.index(request_msgs[0]) < thread.messages.index(tool_msg)
    final = [m for m in thread.messages if m.role == "assistant" and m.content]
    assert all(not m.tool_calls for m in final), "最终回答不应重复携带 tool_calls"
    completed = [m for m in thread.messages if m.role == "assistant" and not m.partial]
    assert completed and completed[-1].content == "最终回答"
    run = services.runs.list_documents()[0]
    assert run.status == "completed"
    assert thread.last_run.status == "completed"
    assert "request-only-key" not in resp.text
    assert "request-only-key" not in (services.paths.threads / "thread-http.json").read_text()
    assert "request-only-key" not in (services.paths.runs / f"{run.id}.json").read_text()


def test_redaction_at_persistence_boundary(http, monkeypatch):
    client, services = http
    monkeypatch.setattr("agent.router.build_builtin_tools", lambda: [leaking_tool])
    monkeypatch.setattr(
        "agent.router.build_chat_model",
        lambda ref, sec: RecordingModel([
            AIMessage(content="", tool_calls=[{"id": "call-leak", "name": "leaking_tool", "args": {"code": "1"}}]),
            AIMessage(content="回答里也带 request-only-key"),
        ]),
    )
    resp = client.post("/api/agent/run", json=start_payload_json(run_id="protocol-leak"), headers={
        "X-VR-Agent-Model-Key": "request-only-key", "Accept": "text/event-stream"})
    assert resp.status_code == 200
    assert "request-only-key" not in resp.text
    assert "[redacted]" in resp.text
    thread_text = (services.paths.threads / "thread-http.json").read_text()
    run_file = services.runs.list_documents()[0]
    run_text = (services.paths.runs / f"{run_file.id}.json").read_text()
    assert "request-only-key" not in thread_text
    assert "request-only-key" not in run_text
    assert "[redacted]" in thread_text


async def drive_asgi(app, payload: dict, *, mode: str = "plain"):
    """raw ASGI 驱动：plain=发完 body 即等；disconnect=首个 delta 后断连；hold=永不返回。"""
    body = json.dumps(payload).encode()
    incoming = iter([{"type": "http.request", "body": body, "more_body": False}])
    sent: list[dict] = []
    got_delta = asyncio.Event()

    body_sent = False

    async def receive() -> dict:
        nonlocal body_sent
        if not body_sent:
            body_sent = True
            return next(incoming, {"type": "http.disconnect"})
        if mode == "hold":
            await asyncio.Event().wait()  # 模拟保持连接、既不响应也不断开
        if mode == "disconnect":
            # 首个 delta 之后才断连；Event 已置位时 wait() 不挂起，
            # is_disconnected 的已取消作用域仍能拿到同步返回的断连消息
            await got_delta.wait()
            return {"type": "http.disconnect"}
        return {"type": "http.disconnect"}

    async def send(message: dict) -> None:
        sent.append(message)
        raw = message.get("body", b"")
        body_text = raw.decode("utf-8", "ignore") if isinstance(raw, (bytes, str)) else ""
        if "TEXT_MESSAGE_CONTENT" in body_text:
            got_delta.set()

    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "POST", "scheme": "http", "path": "/api/agent/run",
        "raw_path": b"/api/agent/run", "query_string": b"", "root_path": "",
        "state": {}, "extensions": {},
        "client": ("127.0.0.1", 50011), "server": ("testserver", 80),
        "headers": [
            (b"host", b"testserver"), (b"content-type", b"application/json"),
            (b"accept", b"text/event-stream"), (b"x-vr-agent-model-key", b"request-only-key"),
            (b"content-length", str(len(body)).encode()),
        ],
    }
    task = asyncio.create_task(app(scope, receive, send))
    return task, sent, got_delta


@pytest.mark.asyncio
async def test_disconnect_persists_partial_then_excludes_from_next_input(tmp_path, monkeypatch):
    services = build_services(tmp_path / "agent")
    monkeypatch.setattr(router_module, "services", services)
    monkeypatch.setattr("agent.router.build_builtin_tools", lambda: [])
    monkeypatch.setattr("agent.router.build_middleware", lambda: ())
    RECORDED_INPUTS.clear()
    monkeypatch.setattr(
        "agent.router.build_chat_model",
        lambda ref, sec: PausingChatModel([AIMessage(content="部分回答内容")]),
    )
    task, sent, got_delta = await drive_asgi(
        app_module.app, start_payload_json(thread_id="thread-disc", run_id="protocol-disc"),
        mode="disconnect",
    )
    await asyncio.wait_for(got_delta.wait(), timeout=5)
    await asyncio.wait_for(task, timeout=10)

    thread = services.threads.get("thread-disc")
    partials = [m for m in thread.messages if m.role == "assistant"]
    assert partials and partials[0].partial is True
    assert partials[0].content
    run = services.runs.list_documents()[0]
    assert run.status == "cancelled"
    assert services.coordinator.active("thread-disc") is None

    monkeypatch.setattr(
        "agent.router.build_chat_model",
        lambda ref, sec: RecordingModel([AIMessage(content="下一轮回答")]),
    )
    client = TestClient(app_module.app, client=("127.0.0.1", 50012))
    resp = client.post("/api/agent/run", json=start_payload_json(
        thread_id="thread-disc", run_id="protocol-disc-2", content="继续", user_id="user-disc-2",
        revision=thread.revision, prefix=[("user-http", "hello")],
    ), headers={"X-VR-Agent-Model-Key": "request-only-key", "Accept": "text/event-stream"})
    assert resp.status_code == 200, resp.text
    model_input_ids = [getattr(m, "id", None) for m in RECORDED_INPUTS[-1]]
    assert all(p.id not in model_input_ids for p in partials)


@pytest.mark.asyncio
async def test_cancelled_stream_shields_partial_persistence_before_reraising(tmp_path, monkeypatch):
    services = build_services(tmp_path / "agent")
    monkeypatch.setattr(router_module, "services", services)
    monkeypatch.setattr("agent.router.build_builtin_tools", lambda: [])
    monkeypatch.setattr("agent.router.build_middleware", lambda: ())
    monkeypatch.setattr(
        "agent.router.build_chat_model",
        lambda ref, sec: PausingChatModel([AIMessage(content="将被取消的部分文本")]),
    )
    task, sent, got_delta = await drive_asgi(
        app_module.app, start_payload_json(thread_id="thread-cancel", run_id="protocol-cancel"),
        mode="hold",
    )
    await asyncio.wait_for(got_delta.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=10)

    partials: list = []
    for _ in range(50):
        thread = services.threads.get("thread-cancel")
        partials = [m for m in thread.messages if m.role == "assistant" and m.partial]
        if partials:
            break
        await asyncio.sleep(0.1)
    assert partials, "取消后的部分 assistant 消息应当落盘"
    run = services.runs.list_documents()[0]
    assert run.status == "cancelled"
    assert services.coordinator.active("thread-cancel") is None


@pytest.mark.asyncio
async def test_patch_during_active_run_adopts_revision(tmp_path, monkeypatch):
    services = build_services(tmp_path / "agent")
    monkeypatch.setattr(router_module, "services", services)
    monkeypatch.setattr("agent.router.build_builtin_tools", lambda: [])
    monkeypatch.setattr("agent.router.build_middleware", lambda: ())
    slow = PausingChatModel([AIMessage(content="慢速回答")])
    slow.pause_seconds = 0.5
    monkeypatch.setattr("agent.router.build_chat_model", lambda ref, sec: slow)

    task, sent, got_delta = await drive_asgi(
        app_module.app, start_payload_json(thread_id="thread-patch", run_id="protocol-patch"),
        mode="hold",
    )
    await asyncio.wait_for(got_delta.wait(), timeout=5)
    patched = await services.coordinator.patch_thread("thread-patch", 1, "运行中改名")
    assert patched.revision == 2
    await asyncio.wait_for(task, timeout=15)

    thread = services.threads.get("thread-patch")
    assert thread.title == "运行中改名"
    assert thread.last_run.status == "completed"
    assert thread.revision >= 3


# ---- Task 6：严格重试与完整准入 ----


def retry_payload(thread_id, protocol_run_id, revision, retry_of):
    return {
        "threadId": thread_id,
        "runId": protocol_run_id,
        "state": {},
        "messages": [],
        "tools": [],
        "context": [],
        "forwardedProps": {"runtime": {
            "model": {"provider": "fixture", "baseURL": "https://example.com/v1", "model": "fixture-model"},
            "threadRevision": revision,
            "retryOf": retry_of,
        }},
    }


@pytest.mark.asyncio
async def test_retry_creates_new_product_run_without_duplicate_user_message(tmp_path, monkeypatch):
    services = build_services(tmp_path / "agent")
    monkeypatch.setattr(router_module, "services", services)
    monkeypatch.setattr("agent.router.build_builtin_tools", lambda: [])
    monkeypatch.setattr("agent.router.build_middleware", lambda: ())
    RECORDED_INPUTS.clear()
    monkeypatch.setattr(
        "agent.router.build_chat_model",
        lambda ref, sec: ScriptedChatModel([]),  # 空脚本 → 模型异常
    )
    client = TestClient(app_module.app, client=("127.0.0.1", 50013))
    failed = client.post("/api/agent/run", json=start_payload_json(thread_id="thread-retry", run_id="protocol-retry-1"), headers={
        "X-VR-Agent-Model-Key": "request-only-key", "Accept": "text/event-stream"})
    assert failed.status_code == 200 and "RUN_ERROR" in failed.text
    failed_run = services.runs.list_documents()[0]
    assert failed_run.status == "failed"
    thread = services.threads.get("thread-retry")

    monkeypatch.setattr(
        "agent.router.build_chat_model",
        lambda ref, sec: RecordingModel([AIMessage(content="重试后的完整回答")]),
    )
    retry_response = client.post("/api/agent/run", json=retry_payload(
        "thread-retry", "protocol-retry", thread.revision, failed_run.id,
    ), headers={"X-VR-Agent-Model-Key": "request-only-key", "Accept": "text/event-stream"})
    assert retry_response.status_code == 200, retry_response.text

    retry_run = [r for r in services.runs.list_documents() if r.id != failed_run.id][0]
    assert retry_run.retry_of == failed_run.id
    assert retry_run.id != failed_run.id
    assert retry_run.protocol_run_ids == ["protocol-retry"]
    thread = services.threads.get("thread-retry")
    assert [m.id for m in thread.messages].count("user-http") == 1
    first_user_input = RECORDED_INPUTS[0]
    retry_input = RECORDED_INPUTS[-1]
    assert [getattr(m, "content", None) for m in retry_input] == [getattr(m, "content", None) for m in first_user_input]
    assert thread.last_run.id == retry_run.id
    assert thread.last_run.status == "completed"
    assert services.runs.get(failed_run.id).status == "failed"  # 原运行不被改动


@pytest.mark.asyncio
async def test_retry_rejections_are_409_with_structured_body(tmp_path, monkeypatch):
    services = build_services(tmp_path / "agent")
    monkeypatch.setattr(router_module, "services", services)
    monkeypatch.setattr("agent.router.build_builtin_tools", lambda: [])
    monkeypatch.setattr("agent.router.build_middleware", lambda: ())
    monkeypatch.setattr("agent.router.build_chat_model", lambda ref, sec: PausingChatModel([AIMessage(content="ok")]))
    client = TestClient(app_module.app, client=("127.0.0.1", 50014))
    hdrs = {"X-VR-Agent-Model-Key": "request-only-key", "Accept": "text/event-stream"}
    ok = client.post("/api/agent/run", json=start_payload_json(thread_id="thread-r2", run_id="protocol-r2-1"), headers=hdrs)
    assert ok.status_code == 200
    completed = sorted(services.runs.list_documents(), key=lambda r: r.updated_at)[-1]
    thread = services.threads.get("thread-r2")

    older = RunDocument.start(
        run_id="run-older", thread_id="thread-r2", protocol_run_id="protocol-old",
        model_ref=MODEL_REF, trigger_message_id="user-http", history_head_id=None, now="2026-08-15T00:00:00Z",
    )
    services.runs.replace(older.model_copy(update={"status": "failed", "updated_at": "2026-08-15T00:00:01Z"}))
    resp = client.post("/api/agent/run", json=retry_payload("thread-r2", "protocol-x", thread.revision, "run-older"), headers=hdrs)
    assert resp.status_code == 409 and resp.json()["code"] == "RETRY_NOT_ALLOWED"
    assert set(resp.json().keys()) == {"code", "detail", "thread_id", "product_run_id", "status"}

    resp = client.post("/api/agent/run", json=retry_payload("thread-r2", "protocol-y", thread.revision, completed.id), headers=hdrs)
    assert resp.status_code == 409 and resp.json()["code"] == "RETRY_NOT_ALLOWED"

    services.threads.create(ThreadDocument.new("thread-other", "别的线程", now="2026-08-15T00:00:00Z"))
    other = RunDocument.start(
        run_id="run-other", thread_id="thread-other", protocol_run_id="protocol-other",
        model_ref=MODEL_REF, trigger_message_id="u-o", history_head_id=None, now="2026-08-15T00:00:00Z",
    )
    services.runs.replace(other.model_copy(update={"status": "failed", "updated_at": "2026-08-15T00:00:02Z"}))
    resp = client.post("/api/agent/run", json=retry_payload("thread-r2", "protocol-z", thread.revision, "run-other"), headers=hdrs)
    assert resp.status_code == 409 and resp.json()["code"] == "RETRY_NOT_ALLOWED"

    def _content(m):
        return m.content if isinstance(m.content, str) else json.dumps(m.content, ensure_ascii=False)
    prefix = [(m.id, m.role, _content(m)) for m in thread.messages]
    resp = client.post("/api/agent/run", json=start_payload_json(
        thread_id="thread-r2", run_id="protocol-r2-2", content="第二轮", user_id="user-r2-2",
        revision=thread.revision, prefix=prefix,
    ), headers=hdrs)
    assert resp.status_code == 200, resp.text
    thread = services.threads.get("thread-r2")
    resp = client.post("/api/agent/run", json=retry_payload("thread-r2", "protocol-w", thread.revision, completed.id), headers=hdrs)
    assert resp.status_code == 409 and resp.json()["code"] == "RETRY_NOT_ALLOWED"

    latest = sorted(services.runs.list_documents(), key=lambda r: r.updated_at)[-1]
    latest_failed = latest.model_copy(update={
        "status": "failed",
        "updated_at": utc_now(),
    })
    services.runs.replace(latest_failed)
    thread = services.threads.get("thread-r2")
    resp = client.post("/api/agent/run", json=retry_payload("thread-r2", "protocol-v", 0, latest_failed.id), headers=hdrs)
    assert resp.status_code == 409 and resp.json()["code"] == "THREAD_REVISION_CONFLICT"
    assert thread.revision >= 1


def test_retry_without_revision_is_invalid_runtime_props(tmp_path, monkeypatch):
    services = build_services(tmp_path / "agent")
    monkeypatch.setattr(router_module, "services", services)
    monkeypatch.setattr("agent.router.build_builtin_tools", lambda: [])
    monkeypatch.setattr("agent.router.build_middleware", lambda: ())
    monkeypatch.setattr("agent.router.build_chat_model", lambda ref, sec: ScriptedChatModel([]))
    client = TestClient(app_module.app, client=("127.0.0.1", 50015))
    payload = retry_payload("thread-r3", "protocol-r3", None, "run-missing")
    resp = client.post("/api/agent/run", json=payload, headers={
        "X-VR-Agent-Model-Key": "request-only-key", "Accept": "text/event-stream"})
    assert resp.status_code == 400
    assert resp.json()["code"] == "INVALID_RUNTIME_PROPS"


@pytest.mark.asyncio
async def test_lost_revision_event_converges_via_409_without_duplicate_write(tmp_path, monkeypatch):
    services = build_services(tmp_path / "agent")
    monkeypatch.setattr(router_module, "services", services)
    monkeypatch.setattr("agent.router.build_builtin_tools", lambda: [])
    monkeypatch.setattr("agent.router.build_middleware", lambda: ())
    monkeypatch.setattr("agent.router.build_chat_model", lambda ref, sec: PausingChatModel([AIMessage(content="ok")]))
    client = TestClient(app_module.app, client=("127.0.0.1", 50016))
    hdrs = {"X-VR-Agent-Model-Key": "request-only-key", "Accept": "text/event-stream"}
    ok = client.post("/api/agent/run", json=start_payload_json(thread_id="thread-cv", run_id="protocol-cv-1"), headers=hdrs)
    assert ok.status_code == 200
    server_revision = services.threads.get("thread-cv").revision

    stale = client.post("/api/agent/run", json=start_payload_json(
        thread_id="thread-cv", run_id="protocol-cv-2", content="第二轮", user_id="user-cv-2", revision=0,
    ), headers=hdrs)
    assert stale.status_code == 409
    assert stale.json()["code"] == "THREAD_REVISION_CONFLICT"
    assert services.threads.get("thread-cv").revision == server_revision
    assert len(services.runs.list_documents()) == 1


# ---- Task 8：离线端到端生命周期 ----


@pytest.mark.asyncio
async def test_full_lifecycle_create_partial_retry_restart_next_turn(tmp_path, monkeypatch):
    """create → start→tool→partial→disconnect → reload → retry → reload → 重启 → 下一轮。"""
    services = build_services(tmp_path / "agent")
    monkeypatch.setattr(router_module, "services", services)
    monkeypatch.setattr("agent.router.build_builtin_tools", lambda: [journal_tool])
    monkeypatch.setattr("agent.router.build_middleware", lambda: ())
    hdrs = {"X-VR-Agent-Model-Key": "request-only-key", "Accept": "text/event-stream"}
    revisions_seen: list[int] = []

    # 1) 创建线程
    client = TestClient(app_module.app, client=("127.0.0.1", 50020))
    created = client.post("/api/agent/threads", json={"title": "生命周期"})
    assert created.status_code == 201
    thread_id = created.json()["id"]
    assert created.json()["revision"] == 0

    def payload(run_id, content, user_id, revision, prefix):
        return start_payload_json(thread_id=thread_id, run_id=run_id, content=content,
                                  user_id=user_id, revision=revision, prefix=prefix)

    # 2) start → 工具 → 部分文本 → 断连
    RECORDED_INPUTS.clear()
    monkeypatch.setattr(
        "agent.router.build_chat_model",
        lambda ref, sec: PausingChatModel([
            AIMessage(content="", tool_calls=[{"id": "call-life", "name": "journal_tool", "args": {"code": "600519"}}]),
            AIMessage(content="将被中断的部分回答"),
        ]),
    )
    task, sent, got_delta = await drive_asgi(
        app_module.app, payload("protocol-life-1", "原始问题", "user-life", 0, []),
        mode="disconnect",
    )
    await asyncio.wait_for(got_delta.wait(), timeout=5)
    await asyncio.wait_for(task, timeout=10)

    # 3) reload：partial + cancelled
    thread = services.threads.get(thread_id)
    partials = [m for m in thread.messages if m.role == "assistant" and m.partial]
    assert partials and partials[0].content
    cancelled_run = sorted(services.runs.list_documents(), key=lambda r: r.updated_at)[-1]
    assert cancelled_run.status == "cancelled"
    get_resp = client.get(f"/api/agent/threads/{thread_id}")
    assert get_resp.status_code == 200
    assert any(m.get("partial") for m in get_resp.json()["messages"])

    # 4) retry：全新 protocol/product ID → 成功终局
    monkeypatch.setattr(
        "agent.router.build_chat_model",
        lambda ref, sec: RecordingModel([AIMessage(content="重试后的完整回答")]),
    )
    retry = client.post("/api/agent/run", json=retry_payload(
        thread_id, "protocol-life-retry", thread.revision, cancelled_run.id,
    ), headers=hdrs)
    assert retry.status_code == 200, retry.text
    for event in parse_sse(retry.text):
        if event.get("type") == "CUSTOM" and event.get("name") == "thread.revision.updated":
            revisions_seen.append(json.loads(event["value"])["revision"])

    retry_run = sorted(services.runs.list_documents(), key=lambda r: r.updated_at)[-1]
    assert retry_run.id != cancelled_run.id and retry_run.retry_of == cancelled_run.id

    # 5) reload：一条原始 user + 完整重试回答
    thread = services.threads.get(thread_id)
    assert [m.id for m in thread.messages].count("user-life") == 1
    completed = [m for m in thread.messages if m.role == "assistant" and not m.partial and not m.pending_interrupt]
    assert completed and completed[-1].content == "重试后的完整回答"

    # 6) 模拟进程重启：新 services + 对账 → 无活动句柄、历史不变
    from agent.stores import reconcile_agent_data
    fresh = build_services(tmp_path / "agent")
    reconcile_agent_data(fresh.paths, fresh.threads, fresh.runs)
    assert fresh.coordinator.active(thread_id) is None
    assert [m.id for m in fresh.threads.get(thread_id).messages] == [m.id for m in thread.messages]

    # 7) 下一轮正常提问：旧 partial 不进入模型输入
    monkeypatch.setattr(router_module, "services", fresh)
    RECORDED_INPUTS.clear()
    monkeypatch.setattr(
        "agent.router.build_chat_model",
        lambda ref, sec: RecordingModel([AIMessage(content="下一轮回答")]),
    )
    thread = fresh.threads.get(thread_id)
    def _c(m):
        return m.content if isinstance(m.content, str) else json.dumps(m.content, ensure_ascii=False)
    prefix_messages = []
    for m in thread.messages:
        entry = {"id": m.id, "role": m.role, "content": _c(m)}
        if m.role == "tool" and m.tool_call_id:
            entry["toolCallId"] = m.tool_call_id
        prefix_messages.append(entry)
    body = payload("protocol-life-2", "下一轮问题", "user-life-2", thread.revision, [])
    body["messages"] = [*prefix_messages, {"id": "user-life-2", "role": "user", "content": "下一轮问题"}]
    next_resp = client.post("/api/agent/run", json=body, headers=hdrs)
    assert next_resp.status_code == 200, next_resp.text
    model_ids = [getattr(m, "id", None) for m in RECORDED_INPUTS[-1]]
    assert all(p.id not in model_ids for p in partials)
    for event in parse_sse(next_resp.text):
        if event.get("type") == "CUSTOM" and event.get("name") == "thread.revision.updated":
            revisions_seen.append(json.loads(event["value"])["revision"])

    # revision 在每个提交边界严格递增，并与 REST 文档一致
    assert revisions_seen == sorted(revisions_seen) and len(revisions_seen) == len(set(revisions_seen))
    final_thread = fresh.threads.get(thread_id)
    assert final_thread.revision == revisions_seen[-1]


def test_retry_input_excludes_failed_runs_own_outputs(tmp_path, monkeypatch):
    """失败 run 自己已落盘的 tool 输出不得进入它自己的重试输入。"""
    services = build_services(tmp_path / "agent")
    monkeypatch.setattr(router_module, "services", services)
    monkeypatch.setattr("agent.router.build_builtin_tools", lambda: [journal_tool])
    monkeypatch.setattr("agent.router.build_middleware", lambda: ())
    RECORDED_INPUTS.clear()
    # 第一轮：工具成功 → 第二次模型调用越界异常 → run failed（tool 输出已落盘）
    monkeypatch.setattr(
        "agent.router.build_chat_model",
        lambda ref, sec: ScriptedChatModel([
            AIMessage(content="", tool_calls=[{"id": "call-x", "name": "journal_tool", "args": {"code": "1"}}]),
        ]),
    )
    client = TestClient(app_module.app, client=("127.0.0.1", 50030))
    hdrs = {"X-VR-Agent-Model-Key": "request-only-key", "Accept": "text/event-stream"}
    failed = client.post("/api/agent/run", json=start_payload_json(thread_id="thread-rx", run_id="protocol-rx-1"), headers=hdrs)
    assert failed.status_code == 200 and "RUN_ERROR" in failed.text
    thread = services.threads.get("thread-rx")
    assert any(m.role == "tool" for m in thread.messages)  # 失败轮的工具输出已落盘
    failed_run = services.runs.list_documents()[0]
    assert failed_run.status == "failed"

    monkeypatch.setattr(
        "agent.router.build_chat_model",
        lambda ref, sec: RecordingModel([AIMessage(content="重试回答")]),
    )
    retry = client.post("/api/agent/run", json=retry_payload(
        "thread-rx", "protocol-rx-2", thread.revision, failed_run.id), headers=hdrs)
    assert retry.status_code == 200, retry.text
    retry_input = RECORDED_INPUTS[-1]
    non_system = [m for m in retry_input if getattr(m, "type", "") != "system"]
    assert len(non_system) == 1  # 只有原始 user 消息（不含 system prompt）
    assert getattr(non_system[0], "content", None) == "hello"


@pytest.mark.asyncio
async def test_cancel_old_run_does_not_kill_active_new_run(tmp_path):
    """评审修复1：取消已终结的旧 run 不得误杀同线程当前在跑的新 run。"""
    coordinator, threads, runs = make_coordinator(tmp_path)
    first = await start(coordinator, protocol_run_id="protocol-1", revision=None)
    await coordinator.finish_if_terminal("thread-1", "completed")
    revision = threads.get("thread-1").revision
    second = await coordinator.acquire_start(
        model_ref=MODEL_REF, secrets=SECRETS, model_builder=CountingBuilder(), tools=[],
        thread_id="thread-1", middleware=(),
        protocol_run_id="protocol-2",
        messages=[user_msg("user-1", "分析现金流"), user_msg("user-2", "第二个问题")],
        client_revision=revision,
    )

    cancelled = await coordinator.cancel_run("thread-1", first.run.id)

    assert cancelled is None  # 旧 run 已终结：不做任何取消
    assert runs.get(second.run.id).status == "running"
    assert coordinator.active("thread-1") is second.handle
    # 不带匹配 ID 的调用（当前流自身的断连路径）仍取消当前 run
    assert await coordinator.cancel_run("thread-1", second.run.id) == second.run.id
    assert runs.get(second.run.id).status == "cancelled"


@pytest.mark.asyncio
async def test_steer_away_preflight_failure_leaves_old_run_intact(tmp_path):
    """评审修复2：steer-away 校验失败（陈旧 revision）时旧审批运行原封不动。"""
    coordinator, threads, runs = make_coordinator(tmp_path)
    admission = await start(coordinator, protocol_run_id="protocol-1", revision=None)
    handle = coordinator.active("thread-1")
    handle.phase = "awaiting_approval"
    handle.pending_interrupts = [PendingInterrupt(bridge_interrupt_id="int-1", order=0, tool_call_id="call-1", value={})]
    builder = CountingBuilder()

    with pytest.raises(RevisionConflict):
        await coordinator.acquire_steer_away(
            "thread-1",
            entries=[{"interruptId": "int-1", "status": "cancelled"}],
            validate=lambda pending, entries: None,
            model_ref=MODEL_REF,
            secrets=SECRETS,
            model_builder=builder,
            tools=[],
            middleware=(),
            messages=[user_msg("user-1", "分析现金流"), user_msg("user-2", "换个方向")],
            client_revision=0,  # 陈旧：服务端已是 1
            protocol_run_id="protocol-2",
        )

    assert builder.count == 0
    assert runs.get(admission.run.id).status == "running"  # 旧 run 未被取消
    assert coordinator.active("thread-1") is handle
    assert handle.phase == "awaiting_approval"
    thread = threads.get("thread-1")
    assert [m.id for m in thread.messages] == ["user-1"]  # 无新消息写入


@pytest.mark.asyncio
async def test_replay_with_stale_revision_returns_duplicate_not_revision_conflict(tmp_path):
    """评审修复5：重放已接受消息（带陈旧 revision）→ DUPLICATE_RUN_TERMINAL。"""
    coordinator, threads, runs = make_coordinator(tmp_path)
    await start(coordinator, protocol_run_id="protocol-1", revision=None)
    await coordinator.finish_if_terminal("thread-1", "completed")

    with pytest.raises(DuplicateRunTerminal):
        await start(coordinator, protocol_run_id="protocol-replay", user_id="user-1",
                    content="分析现金流", revision=0)  # 陈旧 revision + 已接受消息 ID


def test_terminal_run_finished_event_is_redacted():
    """评审修复1：中断/终局事件出口统一脱敏。

    RunFinishedEvent.outcome 在序列化时组装（不在 __dict__），事件级
    redact_event 覆盖不到，出口的帧级 _redact_frame 是最终兜底。
    """
    import sys
    sys.path.insert(0, ".")
    from agent.router import _redact_frame
    from ag_ui.encoder import EventEncoder
    from ag_ui.core.events import RunFinishedEvent

    event = RunFinishedEvent(
        thread_id="thread-1", run_id="run-1",
        outcome={"type": "interrupt", "interrupts": [{
            "id": "i1", "reason": "tool_call",
            "message": "approve request-only-key tool", "toolCallId": "c1",
        }]},
    )
    frame = _redact_frame(EventEncoder().encode(event), "request-only-key")
    assert "request-only-key" not in frame
    assert "[redacted]" in frame


@pytest.mark.asyncio
async def test_approval_wait_accounted_and_active_excludes_it(tmp_path):
    """评审修复6：审批等待计入 approval_wait_ms，active 时长扣除审批等待。"""
    import time as _time
    coordinator, threads, runs = make_coordinator(tmp_path)
    admission = await start(coordinator, protocol_run_id="protocol-1", revision=None)
    handle = coordinator.active("thread-1")
    handle.phase = "awaiting_approval"
    handle.pending_interrupts = [PendingInterrupt(
        bridge_interrupt_id="int-1", order=0, tool_call_id="call-1",
        value={"action_requests": [{"name": "t", "args": {}, "description": "d"}], "review_configs": [{}]},
    )]
    handle.journal.persist_interrupt(handle.pending_interrupts)
    _time.sleep(0.05)
    await coordinator.acquire_resume(
        "thread-1",
        model_ref=MODEL_REF,
        secrets=SECRETS,
        model_builder=CountingBuilder(),
        validate=lambda pending: {"int-1": {"accepted": True}},
        client_revision=handle.thread_revision,
        protocol_run_id="protocol-2",
    )
    await coordinator.finish_if_terminal("thread-1", "completed")

    run = runs.get(admission.run.id)
    assert run.approval_wait_ms >= 40  # 审批等待已被结算
    assert run.active_elapsed_ms <= run.elapsed_ms  # active 扣除了审批等待
    # 直接驱动协调器（无流式事件）时 usage 计数为 0 也必须持久化，字段不缺失
    assert set(run.usage.model_dump()) == {"model_calls", "tool_calls", "input_tokens", "output_tokens"}
