import json
import tempfile

import pytest
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from starlette.testclient import TestClient

import app as app_module
import agent.router as router_module
from agent.capabilities import StaticCapabilityLease
from agent.governance import ContextAndModelGovernance, ToolExecutionGovernance
from agent.models import RunSecrets
from agent.router import build_services
from tests.agent.conftest import enter_single_loop_client
from tests.agent.fakes import ScriptedChatModel

_SECRETS = RunSecrets.model_validate({"model_api_key": "request-only-key"})


@tool
def order_protected_tool(code: str) -> str:
    """Read one protected fixture value."""
    return "protected-result"


def parse_sse(text: str) -> list[dict]:
    return [json.loads(line[len("data: "):]) for line in text.splitlines()
            if line.startswith("data: ")]


@pytest.mark.asyncio
async def test_composed_middleware_order_and_reservation_counts(tmp_path, monkeypatch):
    """1D Task 6：治理元组顺序 + pending/reject 路径的 reservation 计数。"""
    services = build_services(tempfile.mkdtemp())
    monkeypatch.setattr(router_module, "services", services)

    # 1) 元组顺序：模型治理（最外）→ lease 的 guard/HITL → 工具治理（最内）
    lease = StaticCapabilityLease(tools=(), system_context="", middleware=(
        HumanInTheLoopMiddleware(interrupt_on={
            "order_protected_tool": {"allowed_decisions": ["approve", "reject"]}}),))
    factory = services.coordinator._governed_middleware_factory(
        lease, services.coordinator._new_run_control(), "thread-order", "run-order")
    composed = factory(_SECRETS)
    kinds = [type(item) for item in composed]
    assert kinds[0] is ContextAndModelGovernance
    assert kinds[-1] is ToolExecutionGovernance
    assert any(isinstance(item, HumanInTheLoopMiddleware) for item in composed[1:-1])
    assert composed[0].control is composed[-1]._control

    # 2) pending 阶段零工具 reservation；reject 后仍为零
    monkeypatch.setattr("agent.router.build_builtin_tools", lambda: [order_protected_tool])
    monkeypatch.setattr("agent.router.build_middleware", lambda: (
        HumanInTheLoopMiddleware(interrupt_on={
            "order_protected_tool": {"allowed_decisions": ["approve", "reject"]}}),))
    monkeypatch.setattr("agent.router.build_chat_model", lambda ref, sec: ScriptedChatModel([
        AIMessage(content="", tool_calls=[{"id": "call-resv", "name": "order_protected_tool",
                                           "args": {"code": "600519"}}]),
    ]))
    client = enter_single_loop_client(TestClient(app_module.app, client=("127.0.0.1", 50041)))
    headers = {"X-VR-Agent-Model-Key": "request-only-key", "Accept": "text/event-stream"}
    start = {
        "threadId": "thread-resv", "runId": "protocol-resv", "state": {},
        "messages": [{"id": "user-resv", "role": "user", "content": "hi"}],
        "tools": [], "context": [],
        "forwardedProps": {"runtime": {"model": {
            "provider": "fixture", "baseURL": "https://example.com/v1", "model": "fixture-model"},
            "threadRevision": 0}},
    }
    resp = client.post("/api/agent/run", json=start, headers=headers)
    assert resp.status_code == 200, resp.text
    run_pending = services.runs.list_documents()[0]
    assert run_pending.usage.tool_calls == 0  # pending：零 reservation

    finished = [e for e in parse_sse(resp.text) if e["type"] == "RUN_FINISHED"][0]
    interrupt_id = finished["outcome"]["interrupts"][0]["id"]
    thread = services.threads.get("thread-resv")

    monkeypatch.setattr("agent.router.build_chat_model", lambda ref, sec: ScriptedChatModel([
        AIMessage(content="已拒绝")]))
    reject = dict(start)
    reject["runId"] = "protocol-reject"
    reject["messages"] = []
    reject["forwardedProps"]["runtime"]["threadRevision"] = thread.revision
    reject["forwardedProps"]["command"] = {"resume": [
        {"interruptId": interrupt_id, "status": "resolved",
         "payload": {"decision": "reject", "scope": "once"}}]}
    resp2 = client.post("/api/agent/run", json=reject, headers=headers)
    assert resp2.status_code == 200, resp2.text
    run_rejected = services.runs.list_documents()[0]
    assert run_rejected.usage.tool_calls == 0  # reject：零 reservation
