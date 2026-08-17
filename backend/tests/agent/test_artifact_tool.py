"""1D Task 11：create_artifact 事务（计划 → staging → 提交 → 补偿）。"""

from __future__ import annotations

import asyncio
import json
import tempfile

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import tool as lc_tool
from starlette.testclient import TestClient

import app as app_module
import agent.router as router_module
from agent.router import build_services
from agent.artifacts import ArtifactPersistenceFailed
from agent.tool_executor import ToolExecutionContext, install_tool_execution_context, reset_tool_execution_context
from tests.agent.conftest import enter_single_loop_client
from tests.agent.fakes import ScriptedChatModel

HEADERS = {"X-VR-Agent-Model-Key": "request-only-key", "Accept": "text/event-stream"}
NOW = "2026-08-16T12:00:00Z"


def create_args(**overrides) -> dict:
    values = {
        "type": "markdown",
        "title": "研究摘录",
        "content": {"markdown": "# 客观摘要"},
        "sources": [],
    }
    values.update(overrides)
    return values


@pytest.fixture()
def services(monkeypatch):
    built = build_services(tempfile.mkdtemp())
    monkeypatch.setattr(router_module, "services", built)
    monkeypatch.setattr("agent.router.build_middleware", lambda: ())
    return built


@pytest.fixture()
def client():
    return enter_single_loop_client(TestClient(app_module.app, client=("127.0.0.1", 50070)))


def _artifact_tool_result(text: str) -> dict | None:
    for line in text.splitlines():
        if line.startswith("data: ") and "TOOL_CALL_RESULT" in line:
            payload = json.loads(line[len("data: "):])
            try:
                return json.loads(payload["content"])
            except (json.JSONDecodeError, TypeError):
                return None
    return None


def _events(text: str) -> list[dict]:
    return [json.loads(line[len("data: "):]) for line in text.splitlines()
            if line.startswith("data: ")]


async def _run_create(services, context, args) -> dict:
    artifact_tool = next(
        t for t in services.coordinator._resolver._tools_provider() if t.name == "create_artifact")
    token = install_tool_execution_context(context)
    try:
        raw = await artifact_tool.ainvoke(args)
        return json.loads(raw)
    finally:
        reset_tool_execution_context(token)


def _make_context(services, handle) -> ToolExecutionContext:
    return ToolExecutionContext(
        thread_id=handle.runtime.thread_id,
        product_run_id=handle.product_run_id,
        execution_lock=handle.control.execution_lock,
        builtin_serial_lock=services.coordinator._builtin_serial_lock or asyncio.Lock(),
        executor=services.executor,
        tool_deadline=__import__("time").monotonic() + 30,
        capacity_lease=None,  # 计划路径在锁内串行执行，无需容量租约
        control=handle.control,
        artifact_service=services.artifacts_service,
    )


@pytest.mark.asyncio
async def test_create_artifact_success_commits_sources_artifact_and_thread(services, client, monkeypatch):
    @lc_tool
    def probe_tool(code: str) -> str:
        """查一个值"""
        return json.dumps({"price": 10})

    monkeypatch.setattr("agent.router.build_builtin_tools", lambda: [probe_tool])
    monkeypatch.setattr("agent.router.build_chat_model", lambda ref, sec: ScriptedChatModel([
        AIMessage(content="", tool_calls=[{"id": "call-t1", "name": "probe_tool",
                                           "args": {"code": "600519"}}]),
        AIMessage(content="见 https://example.com/report 与 https://other.example/x"),
    ]))
    response = client.post("/api/agent/run", json={
        "threadId": "thread-art", "runId": "protocol-art", "state": {},
        "messages": [{"id": "user-art", "role": "user", "content": "查"}],
        "tools": [], "context": [],
        "forwardedProps": {"runtime": {"model": {
            "provider": "fixture", "baseURL": "https://example.com/v1", "model": "m"},
            "threadRevision": 0}},
    }, headers=HEADERS)
    assert response.status_code == 200, response.text
    run = services.runs.list_documents()[0]
    from agent.governance import DEFAULT_POLICY_SNAPSHOT, RunControl
    context = ToolExecutionContext(
        thread_id="thread-art", product_run_id=run.id,
        execution_lock=asyncio.Lock(), builtin_serial_lock=asyncio.Lock(),
        executor=services.executor,
        tool_deadline=__import__("time").monotonic() + 30,
        control=RunControl(DEFAULT_POLICY_SNAPSHOT),
        artifact_service=services.artifacts_service)

    result = await _run_create(services, context, create_args(
        type="sources", title="来源清单",
        content={"items": [{"source_index": 0, "note": "核对"}]},
        sources=[
            {"kind": "tool_call", "tool_call_id": "call-t1"},
            {"kind": "url", "url": "https://example.com/report#frag"},
            {"kind": "url", "url": "https://new.example/y"},
        ]))
    assert result["ok"] is True, result
    artifact = result["artifact"]
    assert artifact["type"] == "sources"
    updated_thread = services.threads.get("thread-art")
    assert artifact["id"] in updated_thread.artifact_ids
    assert result["thread_revision"] == updated_thread.revision
    stored = services.artifacts_service.store.get("thread-art", artifact["id"])
    # source_index 已重写为不可变 source_id，且 ⊆ source_ids
    item = stored.content.items[0]
    assert item.source_id in stored.source_ids
    # 新 URL 已进入 run.sources；复用 URL 不重复
    refreshed_run = services.runs.get(run.id)
    urls = [s.url for s in refreshed_run.sources if s.kind == "model_url"]
    assert "https://new.example/y" in urls
    assert urls.count("https://example.com/report") == 1


def test_graph_artifact_event_is_validated_and_metadata_only(services, client, monkeypatch):
    monkeypatch.setattr("agent.router.build_chat_model", lambda ref, sec: ScriptedChatModel([
        AIMessage(content="", tool_calls=[{
            "id": "call-artifact", "name": "create_artifact", "args": create_args(),
        }]),
        AIMessage(content="已整理"),
    ]))
    response = client.post("/api/agent/run", json={
        "threadId": "thread-graph-art", "runId": "protocol-graph-art", "state": {},
        "messages": [{"id": "user-graph-art", "role": "user", "content": "整理"}],
        "tools": [], "context": [],
        "forwardedProps": {"runtime": {"model": {
            "provider": "fixture", "baseURL": "https://example.com/v1", "model": "m"},
            "threadRevision": 0}},
    }, headers=HEADERS)
    assert response.status_code == 200, response.text
    artifacts = [event for event in _events(response.text)
                 if event.get("type") == "CUSTOM" and event.get("name") == "artifact.created"]
    assert len(artifacts) == 1
    payload = json.loads(artifacts[0]["value"])
    assert set(payload) == {
        "threadId", "runId", "artifactId", "type", "title", "threadRevision"}
    assert payload["threadId"] == "thread-graph-art"
    assert payload["runId"] == services.runs.list_documents()[0].id
    assert payload["type"] == "markdown"
    assert payload["title"] == "研究摘录"
    assert "content" not in payload


def test_artifact_publish_failure_emits_sources_after_terminal_commit(services, client, monkeypatch):
    monkeypatch.setattr("agent.router.build_chat_model", lambda ref, sec: ScriptedChatModel([
        AIMessage(content="", tool_calls=[{
            "id": "call-artifact-fail", "name": "create_artifact", "args": create_args(
                sources=[{"kind": "url", "url": "https://new.example/fail"}]),
        }]),
    ]))
    monkeypatch.setattr(services.artifacts_service.store, "publish",
                        lambda staged: (_ for _ in ()).throw(OSError("disk full")))
    response = client.post("/api/agent/run", json={
        "threadId": "thread-graph-fail", "runId": "protocol-graph-fail", "state": {},
        "messages": [{"id": "user-graph-fail", "role": "user", "content": "整理"}],
        "tools": [], "context": [],
        "forwardedProps": {"runtime": {"model": {
            "provider": "fixture", "baseURL": "https://example.com/v1", "model": "m"},
            "threadRevision": 0}},
    }, headers=HEADERS)
    assert response.status_code == 200, response.text
    events = _events(response.text)
    sources = [i for i, event in enumerate(events) if event.get("name") == "sources.updated"]
    assert len(sources) == 1
    assert not [event for event in events if event.get("name") == "artifact.created"]
    final_thread = max(i for i, event in enumerate(events)
                       if event.get("name") == "thread.revision.updated")
    final_budget = max(i for i, event in enumerate(events)
                       if event.get("name") == "budget.updated")
    terminal = next(i for i, event in enumerate(events)
                    if event["type"] in ("RUN_FINISHED", "RUN_ERROR"))
    assert final_thread < final_budget < sources[0] < terminal


@pytest.mark.asyncio
async def test_cancel_after_artifact_reference_uses_current_handle_revision(services, client, monkeypatch):
    from langchain.agents.middleware import HumanInTheLoopMiddleware

    @lc_tool
    def approval_tool(code: str) -> str:
        """需要审批的测试工具。"""
        return code

    monkeypatch.setattr("agent.router.build_builtin_tools", lambda: [approval_tool])
    monkeypatch.setattr("agent.router.build_middleware", lambda: (
        HumanInTheLoopMiddleware(interrupt_on={
            "approval_tool": {"allowed_decisions": ["approve", "reject"]},
        }),))
    monkeypatch.setattr("agent.router.build_chat_model", lambda ref, sec: ScriptedChatModel([
        AIMessage(content="", tool_calls=[{
            "id": "call-pause", "name": "approval_tool", "args": {"code": "600519"},
        }]),
    ]))
    response = client.post("/api/agent/run", json={
        "threadId": "thread-artifact-cancel", "runId": "protocol-artifact-cancel", "state": {},
        "messages": [{"id": "user-artifact-cancel", "role": "user", "content": "整理"}],
        "tools": [], "context": [],
        "forwardedProps": {"runtime": {"model": {
            "provider": "fixture", "baseURL": "https://example.com/v1", "model": "m"},
            "threadRevision": 0}},
    }, headers=HEADERS)
    assert response.status_code == 200
    handle = services.coordinator.active("thread-artifact-cancel")
    assert handle is not None and handle.phase == "awaiting_approval"

    result = await _run_create(services, _make_context(services, handle), create_args())
    assert result["ok"] is True

    await services.coordinator.cancel_run("thread-artifact-cancel", handle.product_run_id)
    assert services.coordinator.active("thread-artifact-cancel") is None
    assert services.runs.get(handle.product_run_id).status == "cancelled"
    thread = services.threads.get("thread-artifact-cancel")
    assert result["artifact"]["id"] in thread.artifact_ids
    assert thread.last_run is not None and thread.last_run.status == "cancelled"


def test_artifact_sources_emit_before_interrupt_terminal(services, client, monkeypatch):
    from langchain.agents.middleware import HumanInTheLoopMiddleware

    @lc_tool
    def approval_tool(code: str) -> str:
        """需要审批的测试工具。"""
        return code

    monkeypatch.setattr("agent.router.build_builtin_tools", lambda: [approval_tool])
    monkeypatch.setattr("agent.router.build_middleware", lambda: (
        HumanInTheLoopMiddleware(interrupt_on={
            "approval_tool": {"allowed_decisions": ["approve", "reject"]},
        }),))
    monkeypatch.setattr("agent.router.build_chat_model", lambda ref, sec: ScriptedChatModel([
        AIMessage(content="", tool_calls=[{
            "id": "call-artifact-source", "name": "create_artifact", "args": create_args(
                sources=[{"kind": "url", "url": "https://new.example/interrupt"}]),
        }]),
        AIMessage(content="", tool_calls=[{
            "id": "call-approval", "name": "approval_tool", "args": {"code": "600519"},
        }]),
    ]))
    response = client.post("/api/agent/run", json={
        "threadId": "thread-artifact-interrupt", "runId": "protocol-artifact-interrupt", "state": {},
        "messages": [{"id": "user-artifact-interrupt", "role": "user", "content": "整理"}],
        "tools": [], "context": [],
        "forwardedProps": {"runtime": {"model": {
            "provider": "fixture", "baseURL": "https://example.com/v1", "model": "m"},
            "threadRevision": 0}},
    }, headers=HEADERS)
    assert response.status_code == 200
    events = _events(response.text)
    run = services.runs.list_documents()[0]
    assert run.status == "awaiting_approval"
    sources = [i for i, event in enumerate(events) if event.get("name") == "sources.updated"]
    assert len(sources) == 1
    assert json.loads(events[sources[0]]["value"]) == {
        "threadId": "thread-artifact-interrupt", "runId": run.id,
        "controlRevision": run.control_revision,
        "sourceCount": len(run.sources), "sourcesTruncated": False,
    }
    final_thread = max(i for i, event in enumerate(events)
                       if event.get("name") == "thread.revision.updated")
    final_budget = max(i for i, event in enumerate(events)
                       if event.get("name") == "budget.updated")
    terminal = next(i for i, event in enumerate(events) if event["type"] == "RUN_FINISHED")
    assert events[terminal]["outcome"]["type"] == "interrupt"
    assert final_thread < final_budget < sources[0] < terminal


@pytest.mark.asyncio
async def test_create_artifact_records_events_only_after_durable_facts(services, client, monkeypatch):
    monkeypatch.setattr("agent.router.build_chat_model", lambda ref, sec: ScriptedChatModel([
        AIMessage(content="完成")]))
    response = client.post("/api/agent/run", json={
        "threadId": "thread-events", "runId": "protocol-events", "state": {},
        "messages": [{"id": "user-events", "role": "user", "content": "问"}],
        "tools": [], "context": [],
        "forwardedProps": {"runtime": {"model": {
            "provider": "fixture", "baseURL": "https://example.com/v1", "model": "m"},
            "threadRevision": 0}},
    }, headers=HEADERS)
    assert response.status_code == 200
    run = services.runs.list_documents()[0]
    from agent.governance import DEFAULT_POLICY_SNAPSHOT, RunControl
    context = ToolExecutionContext(
        thread_id="thread-events", product_run_id=run.id,
        execution_lock=asyncio.Lock(), builtin_serial_lock=asyncio.Lock(),
        executor=services.executor, tool_deadline=__import__("time").monotonic() + 30,
        control=RunControl(DEFAULT_POLICY_SNAPSHOT), artifact_service=services.artifacts_service)
    result = await _run_create(services, context, create_args(
        sources=[{"kind": "url", "url": "https://new.example/report"}]))
    assert result["ok"] is True
    facts = context.control.drain_persisted_event_facts()
    assert [fact["name"] for fact in facts] == ["sources.updated", "artifact.created"]
    assert facts[0]["payload"] == {
        "threadId": "thread-events", "runId": run.id,
        "controlRevision": 1, "sourceCount": 1, "sourcesTruncated": False,
    }
    assert facts[1]["payload"] == {
        "threadId": "thread-events", "runId": run.id,
        "artifactId": result["artifact"]["id"], "type": "markdown",
        "title": "研究摘录", "threadRevision": result["thread_revision"],
    }


@pytest.mark.asyncio
async def test_publish_failure_after_source_commit_records_sources_only(services, client, monkeypatch):
    monkeypatch.setattr("agent.router.build_chat_model", lambda ref, sec: ScriptedChatModel([
        AIMessage(content="完成")]))
    response = client.post("/api/agent/run", json={
        "threadId": "thread-event-fail", "runId": "protocol-event-fail", "state": {},
        "messages": [{"id": "user-event-fail", "role": "user", "content": "问"}],
        "tools": [], "context": [],
        "forwardedProps": {"runtime": {"model": {
            "provider": "fixture", "baseURL": "https://example.com/v1", "model": "m"},
            "threadRevision": 0}},
    }, headers=HEADERS)
    assert response.status_code == 200
    run = services.runs.list_documents()[0]
    from agent.governance import DEFAULT_POLICY_SNAPSHOT, RunControl
    context = ToolExecutionContext(
        thread_id="thread-event-fail", product_run_id=run.id,
        execution_lock=asyncio.Lock(), builtin_serial_lock=asyncio.Lock(),
        executor=services.executor, tool_deadline=__import__("time").monotonic() + 30,
        control=RunControl(DEFAULT_POLICY_SNAPSHOT), artifact_service=services.artifacts_service)
    def fail_publish(staged):
        raise OSError("disk full")

    monkeypatch.setattr(services.artifacts_service.store, "publish", fail_publish)
    with pytest.raises(ArtifactPersistenceFailed):
        await _run_create(services, context, create_args(
            sources=[{"kind": "url", "url": "https://new.example/fail"}]))
    facts = context.control.drain_persisted_event_facts()
    assert [fact["name"] for fact in facts] == ["sources.updated"]
    assert facts[0]["payload"]["sourceCount"] == 1
    assert services.threads.get("thread-event-fail").artifact_ids == []


@pytest.mark.asyncio
async def test_source_capacity_failure_is_atomic(services, client, monkeypatch):
    monkeypatch.setattr("agent.router.build_builtin_tools", lambda: [])
    monkeypatch.setattr("agent.router.build_chat_model", lambda ref, sec: ScriptedChatModel([
        AIMessage(content="完成")]))
    response = client.post("/api/agent/run", json={
        "threadId": "thread-cap", "runId": "protocol-cap", "state": {},
        "messages": [{"id": "user-cap", "role": "user", "content": "问"}],
        "tools": [], "context": [],
        "forwardedProps": {"runtime": {"model": {
            "provider": "fixture", "baseURL": "https://example.com/v1", "model": "m"},
            "threadRevision": 0}},
    }, headers=HEADERS)
    assert response.status_code == 200
    run = services.runs.list_documents()[0]

    from agent.models import ModelUrlSource
    seeded = [ModelUrlSource(id=f"source-{i}", kind="model_url",
                             url=f"https://seed.example/{i}", created_at=NOW)
              for i in range(197)]
    from agent.stores import RunStore
    services.runs.replace(run.model_copy(update={"sources": seeded}))
    refreshed = services.runs.get(run.id)

    from agent.governance import DEFAULT_POLICY_SNAPSHOT, RunControl
    context = ToolExecutionContext(
        thread_id="thread-cap", product_run_id=run.id,
        execution_lock=asyncio.Lock(), builtin_serial_lock=asyncio.Lock(),
        executor=services.executor,
        tool_deadline=__import__("time").monotonic() + 30,
        control=RunControl(DEFAULT_POLICY_SNAPSHOT),
        artifact_service=services.artifacts_service)

    result = await _run_create(services, context, create_args(sources=[
        {"kind": "url", "url": "https://d0.example/a"},
        {"kind": "url", "url": "https://d1.example/b"},
        {"kind": "url", "url": "https://d2.example/c"},
        {"kind": "url", "url": "https://d3.example/d"},
    ]))
    assert result == {
        "ok": False,
        "code": "ARTIFACT_SOURCE_INVALID",
        "descriptor_index": 3,
        "reason": "source_capacity_exceeded",
        "remaining_capacity": 0,
    }
    assert len(services.runs.get(run.id).sources) == 197  # 零部分写入
    assert services.threads.get("thread-cap").artifact_ids == []


@pytest.mark.asyncio
async def test_invalid_schema_and_parent_return_structured_errors(services, client, monkeypatch):
    monkeypatch.setattr("agent.router.build_builtin_tools", lambda: [])
    monkeypatch.setattr("agent.router.build_chat_model", lambda ref, sec: ScriptedChatModel([
        AIMessage(content="完成")]))
    response = client.post("/api/agent/run", json={
        "threadId": "thread-err", "runId": "protocol-err", "state": {},
        "messages": [{"id": "user-err", "role": "user", "content": "问"}],
        "tools": [], "context": [],
        "forwardedProps": {"runtime": {"model": {
            "provider": "fixture", "baseURL": "https://example.com/v1", "model": "m"},
            "threadRevision": 0}},
    }, headers=HEADERS)
    assert response.status_code == 200
    run = services.runs.list_documents()[0]

    from agent.governance import RunControl, DEFAULT_POLICY_SNAPSHOT
    context = ToolExecutionContext(
        thread_id="thread-err", product_run_id=run.id,
        execution_lock=asyncio.Lock(), builtin_serial_lock=asyncio.Lock(),
        executor=services.executor,
        tool_deadline=__import__("time").monotonic() + 30,
        control=RunControl(DEFAULT_POLICY_SNAPSHOT),
        artifact_service=services.artifacts_service)

    # 未知类型（schema 层拒绝：真实 handler 未被调，返回原生 error ToolMessage）
    unknown = await _run_create(services, context, create_args(type="bogus"))
    assert unknown.get("ok") is False and unknown.get("code") == "ARTIFACT_INVALID"

    # 不存在的 parent
    missing_parent = await _run_create(services, context, create_args(
        parent_artifact_id="artifact-missing"))
    assert missing_parent["ok"] is False
    assert missing_parent["code"] == "ARTIFACT_INVALID"

    # 不存在的 tool_call 描述符
    bad_tool = await _run_create(services, context, create_args(
        sources=[{"kind": "tool_call", "tool_call_id": "call-nope"}]))
    assert bad_tool["ok"] is False
    assert bad_tool["code"] == "ARTIFACT_SOURCE_INVALID"
    assert bad_tool["descriptor_index"] == 0

    # 超大内容（>1MB）
    huge = await _run_create(services, context, create_args(
        content={"markdown": "x" * 1_100_000}))
    assert huge["ok"] is False and huge["code"] == "ARTIFACT_INVALID"
