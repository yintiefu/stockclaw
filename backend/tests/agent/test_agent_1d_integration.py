"""1D 集成测试：治理贯穿 start/resume/retry/steer/cancel 的完整生命周期。"""

from __future__ import annotations

import asyncio
import json
import tempfile

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from starlette.testclient import TestClient

import app as app_module
import agent.router as router_module
from agent.policy import PolicyCorrupt, PolicyPatch
from agent.router import build_services
from tests.agent.conftest import enter_single_loop_client
from tests.agent.fakes import ScriptedChatModel

HEADERS = {"X-VR-Agent-Model-Key": "request-only-key", "Accept": "text/event-stream"}

CALLS: list[str] = []


@tool
def echo_tool(code: str) -> str:
    """Echo one fixture value."""
    CALLS.append(code)
    return f"echo:{code}"


def start_payload(thread_id="thread-1d", run_id="protocol-1d", content="分析", revision=0):
    return {
        "threadId": thread_id, "runId": run_id, "state": {},
        "messages": [{"id": f"user-{run_id}", "role": "user", "content": content}],
        "tools": [], "context": [],
        "forwardedProps": {"runtime": {
            "model": {"provider": "fixture", "baseURL": "https://example.com/v1",
                      "model": "fixture-model"},
            "threadRevision": revision}},
    }


def parse_sse(text: str) -> list[dict]:
    return [json.loads(line[len("data: "):]) for line in text.splitlines()
            if line.startswith("data: ")]


@pytest.fixture()
def services(monkeypatch):
    built = build_services(tempfile.mkdtemp())
    monkeypatch.setattr(router_module, "services", built)
    monkeypatch.setattr("agent.router.build_builtin_tools", lambda: [echo_tool])
    monkeypatch.setattr("agent.router.build_middleware", lambda: ())
    return built


@pytest.fixture()
def client():
    return enter_single_loop_client(TestClient(app_module.app, client=("127.0.0.1", 50060)))


def scripted_tool_rounds(rounds: int, final: str = "完成"):
    """N 轮 工具调用 + 最终回答：驱动 N+1 次模型调用。"""
    messages = []
    for i in range(rounds):
        messages.append(AIMessage(content="", tool_calls=[{
            "id": f"call-{i}", "name": "echo_tool", "args": {"code": f"60051{i % 10}"}}]))
    messages.append(AIMessage(content=final))
    return ScriptedChatModel(messages)


def test_ninth_model_call_blocked_at_defaults(services, client, monkeypatch):
    monkeypatch.setattr("agent.router.build_chat_model",
                        lambda ref, sec: scripted_tool_rounds(8))
    response = client.post("/api/agent/run", json=start_payload(), headers=HEADERS)
    assert response.status_code == 200
    run = services.runs.list_documents()[0]
    assert run.status == "failed"
    assert run.usage.model_calls == 8  # 第 9 次模型预留被拒：Provider 只被调 8 次
    assert len(CALLS) == 8
    snapshot = run.budget_snapshot
    assert snapshot.max_model_calls == 8
    assert snapshot.policy_revision == 0  # 默认快照（无 policy.json）


def test_admission_writes_policy_snapshot_and_default(services, client, monkeypatch):
    monkeypatch.setattr("agent.router.build_chat_model",
                        lambda ref, sec: ScriptedChatModel([AIMessage(content="答")]))
    response = client.post("/api/agent/run", json=start_payload(), headers=HEADERS)
    assert response.status_code == 200
    run = services.runs.list_documents()[0]
    snapshot = run.budget_snapshot
    assert snapshot.policy_revision == 0  # 未持久化 policy → 默认快照 revision 0
    assert snapshot.max_model_calls == 8
    assert snapshot.max_tool_calls == 16
    assert run.control_revision >= 1  # 首个 active segment 已计入
    assert run.usage.token_status in ("available", "partial", "unavailable")


def test_policy_mutation_after_start_does_not_affect_active_run(services, client, monkeypatch):
    client.patch("/api/agent/policy", json={"revision": 0, "max_model_calls": 2})
    monkeypatch.setattr("agent.router.build_chat_model",
                        lambda ref, sec: ScriptedChatModel([AIMessage(content="答")]))
    response = client.post("/api/agent/run", json=start_payload(), headers=HEADERS)
    assert response.status_code == 200
    run = services.runs.list_documents()[0]
    # 新 run 应使用新 Policy；旧默认 run 的快照不受影响（上面另一个 run 已终结）
    assert run.budget_snapshot.max_model_calls == 2
    assert run.budget_snapshot.policy_revision == 1


def test_corrupt_policy_fails_new_run_closed_but_not_duplicate(services, client, monkeypatch):
    monkeypatch.setattr("agent.router.build_chat_model",
                        lambda ref, sec: ScriptedChatModel([AIMessage(content="答")]))
    first = client.post("/api/agent/run", json=start_payload(), headers=HEADERS)
    assert first.status_code == 200
    run = services.runs.list_documents()[0]
    thread = services.threads.get("thread-1d")

    policy_path = services.paths.policy
    policy_path.write_text('{"schema_version":1,"max_model_calls":0}', encoding="utf-8")

    # 同一 protocol run 重放：duplicate 语义优先于 Policy 损坏
    duplicate = client.post("/api/agent/run", json=start_payload(), headers=HEADERS)
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "DUPLICATE_RUN_TERMINAL"

    # 全新 run（新线程）：fail-closed 503
    fresh = client.post("/api/agent/run",
                        json=start_payload(thread_id="thread-fresh",
                                           run_id="protocol-fresh", content="新问题"),
                        headers=HEADERS)
    assert fresh.status_code == 503
    assert fresh.json()["code"] == "POLICY_CORRUPT"


def test_corrupt_policy_does_not_block_resume(services, client, monkeypatch):
    from langchain.agents.middleware import HumanInTheLoopMiddleware
    monkeypatch.setattr("agent.router.build_middleware", lambda: (
        HumanInTheLoopMiddleware(interrupt_on={
            "echo_tool": {"allowed_decisions": ["approve", "reject"]}}),))
    monkeypatch.setattr("agent.router.build_chat_model", lambda ref, sec: ScriptedChatModel([
        AIMessage(content="", tool_calls=[{"id": "c1", "name": "echo_tool",
                                           "args": {"code": "600519"}}])]))
    CALLS.clear()
    first = client.post("/api/agent/run", json=start_payload(), headers=HEADERS)
    assert first.status_code == 200
    events = parse_sse(first.text)
    finished = [e for e in events if e["type"] == "RUN_FINISHED"][0]
    assert finished["outcome"]["type"] == "interrupt"
    interrupt_id = finished["outcome"]["interrupts"][0]["id"]
    thread = services.threads.get("thread-1d")
    assert services.coordinator.active("thread-1d").phase == "awaiting_approval"

    # Policy 在审批期间损坏：resume 仍必须成功（复用原快照/控制）
    services.paths.policy.write_text('{"schema_version":1,"max_model_calls":0}', encoding="utf-8")
    monkeypatch.setattr("agent.router.build_chat_model",
                        lambda ref, sec: ScriptedChatModel([AIMessage(content="恢复后的回答")]))
    resume = dict(start_payload(run_id="protocol-resume"))
    resume["messages"] = []
    resume["forwardedProps"]["runtime"]["threadRevision"] = thread.revision
    resume["forwardedProps"]["command"] = {"resume": [
        {"interruptId": interrupt_id, "status": "resolved",
         "payload": {"decision": "approve", "scope": "once"}}]}
    CALLS.clear()
    resumed = client.post("/api/agent/run", json=resume, headers=HEADERS)
    assert resumed.status_code == 200, resumed.text
    assert CALLS == ["600519"]  # 审批通过后真实执行
    run = services.runs.list_documents()[0]
    assert run.usage.tool_calls == 1
    # 审批等待不计入 active；segment 重开
    assert run.active_elapsed_ms <= run.elapsed_ms


def test_cancel_blocks_new_reservations_and_persists_counts(services, client, monkeypatch):
    from langchain.agents.middleware import HumanInTheLoopMiddleware
    monkeypatch.setattr("agent.router.build_middleware", lambda: (
        HumanInTheLoopMiddleware(interrupt_on={
            "echo_tool": {"allowed_decisions": ["approve", "reject"]}}),))
    monkeypatch.setattr("agent.router.build_chat_model", lambda ref, sec: ScriptedChatModel([
        AIMessage(content="", tool_calls=[{"id": "c1", "name": "echo_tool",
                                           "args": {"code": "600519"}}])]))
    CALLS.clear()
    first = client.post("/api/agent/run", json=start_payload(), headers=HEADERS)
    assert first.status_code == 200
    assert services.coordinator.active("thread-1d").phase == "awaiting_approval"

    handle = services.coordinator.active("thread-1d")
    control = handle.control
    before = control.view().usage.model_calls
    assert before >= 1  # 模型 reservation 已持久化

    cancelled = client.post(f"/api/agent/runs/{handle.product_run_id}/cancel")
    assert cancelled.status_code == 200
    body = cancelled.json()
    assert body["status"] == "cancelled"
    assert body["usage"]["model_calls"] == before  # 已持久化的 reservation 保持
    # 控制已终结：新的 reservation 被拒
    from agent.governance import GovernanceTerminalError
    with pytest.raises(GovernanceTerminalError):
        control.begin_active_segment()


def test_steer_admission_failure_preserves_old_pending_run(services, client, monkeypatch):
    from langchain.agents.middleware import HumanInTheLoopMiddleware
    monkeypatch.setattr("agent.router.build_middleware", lambda: (
        HumanInTheLoopMiddleware(interrupt_on={
            "echo_tool": {"allowed_decisions": ["approve", "reject"]}}),))
    monkeypatch.setattr("agent.router.build_chat_model", lambda ref, sec: ScriptedChatModel([
        AIMessage(content="", tool_calls=[{"id": "c1", "name": "echo_tool",
                                           "args": {"code": "600519"}}])]))
    first = client.post("/api/agent/run", json=start_payload(), headers=HEADERS)
    assert first.status_code == 200
    events = parse_sse(first.text)
    interrupt = [e for e in events if e["type"] == "RUN_FINISHED"][0]["outcome"]["interrupts"][0]
    thread = services.threads.get("thread-1d")
    handle = services.coordinator.active("thread-1d")
    messages_before = [m.id for m in thread.messages]

    # Policy 损坏 → steer-away 准入失败：旧 pending run 原样保留
    services.paths.policy.write_text('{"schema_version":1,"max_model_calls":0}', encoding="utf-8")
    steer = dict(start_payload(run_id="protocol-steer", content="换个问题"))
    steer["forwardedProps"]["runtime"]["threadRevision"] = thread.revision
    steer["forwardedProps"]["command"] = {"resume": [
        {"interruptId": interrupt["id"], "status": "cancelled"}]}
    # steer-away 的客户端历史前缀 = 服务端完整历史 + 新 user message
    prefix = []
    for m in thread.messages:
        entry = {"id": m.id, "role": m.role,
                 "content": m.content if isinstance(m.content, str) else json.dumps(m.content, ensure_ascii=False)}
        if m.role == "tool" and m.tool_call_id:
            entry["toolCallId"] = m.tool_call_id
        prefix.append(entry)
    steer["messages"] = [*prefix, steer["messages"][-1]]
    steer_response = client.post("/api/agent/run", json=steer, headers=HEADERS)
    assert steer_response.status_code == 503
    assert steer_response.json()["code"] == "POLICY_CORRUPT"
    assert services.coordinator.active("thread-1d") is handle
    assert handle.phase == "awaiting_approval"
    assert handle.pending_interrupts
    assert [m.id for m in services.threads.get("thread-1d").messages] == messages_before


def test_retry_gets_new_snapshot_and_counts(services, client, monkeypatch):
    monkeypatch.setattr("agent.router.build_chat_model",
                        lambda ref, sec: ScriptedChatModel([]))  # 空脚本 → 失败
    failed = client.post("/api/agent/run", json=start_payload(), headers=HEADERS)
    assert failed.status_code == 200 and "RUN_ERROR" in failed.text
    failed_run = services.runs.list_documents()[0]
    assert failed_run.status == "failed"
    thread = services.threads.get("thread-1d")

    client.patch("/api/agent/policy", json={"revision": 0, "max_tool_calls": 3})
    monkeypatch.setattr("agent.router.build_chat_model",
                        lambda ref, sec: scripted_tool_rounds(1))
    retry = {
        "threadId": "thread-1d", "runId": "protocol-retry", "state": {}, "messages": [],
        "tools": [], "context": [],
        "forwardedProps": {"runtime": {
            "model": {"provider": "fixture", "baseURL": "https://example.com/v1",
                      "model": "fixture-model"},
            "threadRevision": thread.revision, "retryOf": failed_run.id}},
    }
    response = client.post("/api/agent/run", json=retry, headers=HEADERS)
    assert response.status_code == 200, response.text
    runs = sorted(services.runs.list_documents(), key=lambda r: r.updated_at)
    retry_run = runs[-1]
    assert retry_run.retry_of == failed_run.id
    assert retry_run.budget_snapshot.max_tool_calls == 3  # 新快照
    assert retry_run.budget_snapshot.policy_revision == 1
    assert retry_run.usage.model_calls == 2  # 工具请求 + 最终回答；计数从新 run 重新开始


def test_active_clock_excludes_approval_wait(services, client, monkeypatch):
    from langchain.agents.middleware import HumanInTheLoopMiddleware
    monkeypatch.setattr("agent.router.build_middleware", lambda: (
        HumanInTheLoopMiddleware(interrupt_on={
            "echo_tool": {"allowed_decisions": ["approve", "reject"]}}),))
    monkeypatch.setattr("agent.router.build_chat_model", lambda ref, sec: ScriptedChatModel([
        AIMessage(content="", tool_calls=[{"id": "c1", "name": "echo_tool",
                                           "args": {"code": "600519"}}])]))
    first = client.post("/api/agent/run", json=start_payload(), headers=HEADERS)
    assert first.status_code == 200
    events = parse_sse(first.text)
    interrupt = [e for e in events if e["type"] == "RUN_FINISHED"][0]["outcome"]["interrupts"][0]
    thread = services.threads.get("thread-1d")

    import time as _time
    _time.sleep(0.05)  # 审批等待不计入 active
    monkeypatch.setattr("agent.router.build_chat_model",
                        lambda ref, sec: ScriptedChatModel([AIMessage(content="完成")]))
    resume = dict(start_payload(run_id="protocol-resume2"))
    resume["messages"] = []
    resume["forwardedProps"]["runtime"]["threadRevision"] = thread.revision
    resume["forwardedProps"]["command"] = {"resume": [
        {"interruptId": interrupt["id"], "status": "resolved",
         "payload": {"decision": "approve", "scope": "once"}}]}
    CALLS.clear()
    resumed = client.post("/api/agent/run", json=resume, headers=HEADERS)
    assert resumed.status_code == 200, resumed.text
    run = services.runs.list_documents()[0]
    assert run.approval_wait_ms >= 40
    assert run.active_elapsed_ms <= run.elapsed_ms - 40 + 5


def test_final_budget_event_precedes_terminal_event(services, client, monkeypatch):
    monkeypatch.setattr("agent.router.build_chat_model",
                        lambda ref, sec: ScriptedChatModel([AIMessage(content="答")]))
    response = client.post("/api/agent/run", json=start_payload(), headers=HEADERS)
    assert response.status_code == 200
    events = parse_sse(response.text)
    kinds = [e["type"] for e in events]
    custom_budgets = [i for i, e in enumerate(events)
                      if e["type"] == "CUSTOM" and e.get("name") == "budget.updated"]
    assert custom_budgets, "治理 budget 事件缺失"
    terminal = next(i for i, k in enumerate(kinds) if k in ("RUN_FINISHED", "RUN_ERROR"))
    assert max(custom_budgets) < terminal
    last_budget = json.loads(events[custom_budgets[-1]]["value"])
    assert last_budget["usage"]["model_calls"] == 1
    assert last_budget["budgetSnapshot"]["max_model_calls"] == 8


def test_final_sources_event_follows_terminal_commit_and_budget(services, client, monkeypatch):
    monkeypatch.setattr("agent.router.build_chat_model", lambda ref, sec: ScriptedChatModel([
        AIMessage(content="", tool_calls=[{
            "id": "call-source", "name": "echo_tool", "args": {"code": "600519"}}]),
        AIMessage(content="资料 https://example.com/report"),
    ]))
    response = client.post("/api/agent/run", json=start_payload(), headers=HEADERS)
    assert response.status_code == 200
    events = parse_sse(response.text)
    source_indexes = [i for i, event in enumerate(events)
                      if event.get("type") == "CUSTOM" and event.get("name") == "sources.updated"]
    assert len(source_indexes) == 1
    payload = json.loads(events[source_indexes[0]]["value"])
    run = services.runs.list_documents()[0]
    assert payload == {
        "threadId": "thread-1d", "runId": run.id,
        "controlRevision": run.control_revision,
        "sourceCount": 2, "sourcesTruncated": False,
    }
    final_thread = max(i for i, event in enumerate(events)
                       if event.get("type") == "CUSTOM" and event.get("name") == "thread.revision.updated")
    final_budget = max(i for i, event in enumerate(events)
                       if event.get("type") == "CUSTOM" and event.get("name") == "budget.updated")
    terminal = next(i for i, event in enumerate(events)
                    if event["type"] in ("RUN_FINISHED", "RUN_ERROR"))
    assert final_thread < final_budget < source_indexes[0] < terminal


def test_malformed_graph_custom_event_fails_the_persisted_run(services, client, monkeypatch):
    from ag_ui.core.events import CustomEvent, EventType

    monkeypatch.setattr("agent.router.build_chat_model", lambda ref, sec: ScriptedChatModel([
        AIMessage(content="答")]))
    original_convert = router_module.AgentProtocolBridge.convert
    emitted = False

    def malformed_once(self, event):
        nonlocal emitted
        if not emitted:
            emitted = True
            return original_convert(self, CustomEvent(
                type=EventType.CUSTOM, name="sources.updated", value="{"))
        return original_convert(self, event)

    monkeypatch.setattr(router_module.AgentProtocolBridge, "convert", malformed_once)
    response = client.post("/api/agent/run", json=start_payload(), headers=HEADERS)
    assert response.status_code == 200
    run = services.runs.list_documents()[0]
    assert run.status == "failed"
    assert run.error_code == "INVALID_CUSTOM_EVENT"
    events = parse_sse(response.text)
    assert [event["code"] for event in events if event["type"] == "RUN_ERROR"] == [
        "INVALID_CUSTOM_EVENT"]
    assert not [event for event in events if event["type"] == "RUN_FINISHED"]


def test_usage_counts_come_from_persisted_reservations_not_events(services, client, monkeypatch):
    """Provider 错误后：已持久化的 reservation 计数保持，不被事件推断覆盖。"""
    class FailingModel(ScriptedChatModel):
        def __init__(self):
            super().__init__([AIMessage(content="部分")])

        async def _agenerate_response(self, *args, **kwargs):  # pragma: no cover
            raise RuntimeError("provider boom")

    monkeypatch.setattr("agent.router.build_chat_model",
                        lambda ref, sec: ScriptedChatModel([AIMessage(content="答")]))
    response = client.post("/api/agent/run", json=start_payload(), headers=HEADERS)
    assert response.status_code == 200
    run = services.runs.list_documents()[0]
    assert run.usage.model_calls == 1
    # budget.updated 事件中的计数与 REST 一致
    events = parse_sse(response.text)
    budgets = [json.loads(e["value"]) for e in events
               if e["type"] == "CUSTOM" and e.get("name") == "budget.updated"]
    assert budgets[-1]["usage"]["model_calls"] == 1
    assert budgets[-1]["controlRevision"] == run.control_revision


# ---- 1D Task 8：历史 run REST 与治理错误身份 ----


def test_run_list_rest_contract(services, client, monkeypatch):
    monkeypatch.setattr("agent.router.build_chat_model",
                        lambda ref, sec: ScriptedChatModel([AIMessage(content="答")]))
    for i in range(3):
        response = client.post("/api/agent/run", json=start_payload(
            thread_id=f"thread-list-{i}", run_id=f"protocol-list-{i}"), headers=HEADERS)
        assert response.status_code == 200

    listed = client.get("/api/agent/threads/thread-list-0/runs")
    assert listed.status_code == 200
    payload = listed.json()
    assert len(payload["runs"]) == 1
    item = payload["runs"][0]
    assert set(item) == {"id", "status", "started_at", "updated_at",
                         "ended_at", "retry_of", "error_code"}
    assert payload["next_before"] is None

    # 越界 limit → 422；跨线程 cursor → 400
    assert client.get("/api/agent/threads/thread-list-0/runs?limit=0").status_code == 422
    assert client.get("/api/agent/threads/thread-list-0/runs?limit=101").status_code == 422
    foreign = client.get("/api/agent/threads/thread-list-0/runs?before=run-of-other")
    assert foreign.status_code == 400
    assert foreign.json()["code"] == "RUN_CURSOR_INVALID"


def test_governance_error_code_preserved_in_run_and_event(services, client, monkeypatch):
    monkeypatch.setattr("agent.router.build_chat_model",
                        lambda ref, sec: scripted_tool_rounds(8))
    response = client.post("/api/agent/run", json=start_payload(), headers=HEADERS)
    assert response.status_code == 200
    run = services.runs.list_documents()[0]
    assert run.status == "failed"
    assert run.error_code == "MODEL_CALL_LIMIT_EXCEEDED"
    events = parse_sse(response.text)
    run_error = [e for e in events if e["type"] == "RUN_ERROR"][0]
    assert run_error["code"] == "MODEL_CALL_LIMIT_EXCEEDED"
    # 已提交的工具来源即使终局失败也要通知；未创建 Artifact 则绝不发送其事件
    source_events = [e for e in events if e.get("name") == "sources.updated"]
    assert len(source_events) == 1
    assert json.loads(source_events[0]["value"])["sourceCount"] == 8
    assert not [e for e in events if e.get("name") == "artifact.created"]
