"""Task 1C-12：MCP 参数守卫、穷举 HITL、协议元数据、许可与 fail-closed 准入。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.capabilities import (
    AllowanceRegistry,
    CapabilityPreview,
    CapabilityResolver,
    McpArgumentGuard,
    McpArgumentsTooLarge,
    build_hitl_policy,
)
from agent.mcp import McpRegistry
from agent.protocol import AgentProtocolBridge, PendingInterrupt, interrupt_payloads
from agent.skills import SkillRegistry
from agent.stores import AgentPaths, RunStore, ThreadStore, utc_now
from agent.models import ModelRef, RunSecrets, ThreadDocument
from agent.runs import RunCoordinator

pytestmark = pytest.mark.asyncio
MODEL_REF = ModelRef(provider="fixture", baseURL="https://example.com/v1", model="fixture-model")
SECRETS = RunSecrets(model_api_key="sk-live-key-123")


# ---------------------------------------------------------------------------
# 参数守卫
# ---------------------------------------------------------------------------

class FakeRegistry:
    def __init__(self):
        self.calls: list = []
        self.interrupts: list = []

    def secret_sets(self) -> dict[str, set[str]]:
        return {"fixture": {"SECRET-VALUE"}}


class FakeHandler:
    async def __call__(self, state, runtime, call_llm, **kwargs):
        return await call_llm(**kwargs)


async def guard_response(guard, arguments: dict, tool_name="mcp__fixture__echo"):
    """按 langchain 中间件协议调用：awrap_model_call(request, handler)。"""
    from langchain_core.messages import AIMessage

    response = AIMessage(content="", tool_calls=[{
        "id": "call-1", "name": tool_name, "args": arguments}])

    async def call_llm(request):
        return response

    return await guard.awrap_model_call(object(), call_llm)


async def test_argument_guard_boundary_and_zero_execution():
    fake_registry = FakeRegistry()
    guard = McpArgumentGuard(
        aliases=("mcp__fixture__echo",),
        secrets=SECRETS,
        registry_secrets=fake_registry.secret_sets(),
    )
    accepted = await guard_response(guard, {"value": "x" * 60_000})
    assert accepted is not None
    rejected_size = 65_537  # 编码后超限
    with pytest.raises(McpArgumentsTooLarge):
        await guard_response(guard, {"value": "y" * rejected_size})
    assert fake_registry.calls == []
    assert fake_registry.interrupts == []


async def test_argument_guard_boundary_exact_65536():
    guard = McpArgumentGuard(
        aliases=("mcp__fixture__echo",),
        secrets=SECRETS,
        registry_secrets={"fixture": set()},
    )
    # 构造恰好 65,536 字节的编码参数
    payload = {"value": ""}
    overhead = len(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                              separators=(",", ":")).encode("utf-8"))
    fill = 65_536 - overhead
    payload = {"value": "a" * fill}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    assert len(encoded) == 65_536
    result = await guard_response(guard, payload)
    assert result is not None


async def test_guard_redacts_args_for_size_check_only():
    """guard 只脱敏副本做大小检查，不改原始参数。"""
    guard = McpArgumentGuard(
        aliases=("mcp__fixture__echo",),
        secrets=SECRETS,
        registry_secrets={"fixture": {"NEVER-MATCH-xyz"}},
    )
    arguments = {"value": "sk-live-key-123" * 10}
    response = await guard_response(guard, arguments)
    tool_call = response.tool_calls[0]
    assert tool_call["args"]["value"] == arguments["value"]  # 原始参数未变


# ---------------------------------------------------------------------------
# HITL policy 穷举
# ---------------------------------------------------------------------------

async def test_hitl_policy_is_exhaustive_over_enabled_aliases():
    from agent.mcp import McpToolBinding

    bindings = (
        McpToolBinding(server_id="fixture", original_name="echo",
                       alias="mcp__fixture__echo", description="d",
                       args_schema={}, config_generation=1, catalog_generation=1),
        McpToolBinding(server_id="fixture", original_name="sleep",
                       alias="mcp__fixture__sleep", description="d",
                       args_schema={}, config_generation=1, catalog_generation=1),
    )
    allowances = AllowanceRegistry()
    allowances.grant("th-1", "fixture", "echo")  # echo 有 allowance → 不 interrupt
    policy = build_hitl_policy(thread_id="th-1", bindings=bindings,
                               allowances=allowances)
    assert set(policy.interrupt_on) == {b.alias for b in bindings}
    entry = policy.interrupt_on["mcp__fixture__echo"]
    assert entry["allowed_decisions"] == ["approve", "reject"]

    class _Req:
        def __init__(self, tool_call):
            self.tool_call = tool_call

    assert entry["when"](_Req({"name": "mcp__fixture__echo", "args": {}})) is False
    sleep_entry = policy.interrupt_on["mcp__fixture__sleep"]
    assert sleep_entry["when"](_Req({"name": "mcp__fixture__sleep", "args": {}})) is True


async def test_allowance_registry_lookup_and_clear():
    allowances = AllowanceRegistry()
    assert allowances.has("th-1", "fixture", "echo") is False
    allowances.grant("th-1", "fixture", "echo")
    assert allowances.has("th-1", "fixture", "echo") is True
    assert allowances.clear_thread("th-1") == 1
    assert allowances.has("th-1", "fixture", "echo") is False


# ---------------------------------------------------------------------------
# 协议元数据与 resume 三组合
# ---------------------------------------------------------------------------

def _pending(alias="mcp__fixture__echo") -> PendingInterrupt:
    return PendingInterrupt(
        bridge_interrupt_id="bi-1", order=0, tool_call_id="call-1",
        value={
            "action_requests": [{
                "name": alias, "args": {"symbol": "600519"}, "description": "审批",
            }],
            "review_configs": [{"action_name": alias,
                                "allowed_decisions": ["approve", "reject"]}],
        },
        server_id="fixture", server_name="财报服务",
        original_tool_name="echo", tool_alias=alias,
        arguments={"symbol": "600519"},
    )


def test_interrupt_payloads_include_camel_case_mcp_metadata():
    payloads = interrupt_payloads([_pending()])
    payload = payloads[0]
    assert payload["serverId"] == "fixture"
    assert payload["serverName"] == "财报服务"
    assert payload["toolName"] == "echo"
    assert payload["toolAlias"] == "mcp__fixture__echo"
    assert payload["arguments"] == {"symbol": "600519"}
    assert payload["responseSchema"]["additionalProperties"] is False


async def test_resume_value_accepts_three_combinations_and_returns_allowances():
    bridge = AgentProtocolBridge("th-1", "run-1", pending=[_pending()])
    resume, allowances = bridge.resume_value_with_allowances([
        {"interruptId": "bi-1", "status": "resolved",
         "payload": {"decision": "approve", "scope": "thread_session"}},
    ])
    assert resume["decisions"] == [{"type": "approve"}]
    assert allowances == [("th-1", "fixture", "echo")]

    bridge2 = AgentProtocolBridge("th-1", "run-1", pending=[_pending()])
    resume2, allowances2 = bridge2.resume_value_with_allowances([
        {"interruptId": "bi-1", "status": "resolved",
         "payload": {"decision": "reject", "scope": "once"}},
    ])
    assert allowances2 == []
    assert resume2["decisions"][0]["type"] == "reject"

    with pytest.raises(ValueError):
        AgentProtocolBridge("th-1", "run-1", pending=[_pending()]).resume_value_with_allowances([
            {"interruptId": "bi-1", "status": "resolved",
             "payload": {"decision": "reject", "scope": "thread_session"}},
        ])


async def test_resume_rejects_missing_duplicate_or_unknown_ids():
    bridge = AgentProtocolBridge("th-1", "run-1", pending=[_pending()])
    with pytest.raises(ValueError):
        bridge.resume_value_with_allowances([])
    with pytest.raises(ValueError):
        bridge.resume_value_with_allowances([
            {"interruptId": "bi-1", "status": "resolved",
             "payload": {"decision": "approve", "scope": "once"}},
            {"interruptId": "bi-1", "status": "resolved",
             "payload": {"decision": "approve", "scope": "once"}},
        ])
    with pytest.raises(ValueError):
        bridge.resume_value_with_allowances([
            {"interruptId": "ghost", "status": "resolved",
             "payload": {"decision": "approve", "scope": "once"}},
        ])


# ---------------------------------------------------------------------------
# fail-closed 准入
# ---------------------------------------------------------------------------

async def test_fail_closed_admission_when_relevant_server_unavailable(tmp_path):
    """相关 server 连接失败 → 503 语义错误；无任何用户/run 写入。"""
    from agent.router import build_services

    services = build_services(tmp_path / "agent")
    # 配一个 enabled server + enabled tool，但不信任 → 连接必失败
    from agent.mcp import McpServer, StdioTransport

    await services.registry.add(McpServer.model_validate({
        "id": "broken", "display_name": "坏服务", "enabled": True,
        "transport": {"type": "stdio", "executable": "nonexistent-bin-xyz",
                      "args": [], "env": {}},
        "tools": [{"original_name": "echo", "alias": "mcp__broken__echo",
                   "description": "d", "input_schema": {}, "enabled": True,
                   "discovered_at": ""}],
    }))
    doc = services.registry.store.load()
    services.threads.create(ThreadDocument.new("th-1", "研究", now=utc_now()))

    from agent.capabilities import McpUnavailable
    resolver = CapabilityResolver(services.skills, registry=services.registry)
    with pytest.raises(McpUnavailable):
        await resolver.acquire(CapabilityPreview(
            thread_id="th-1", thread_revision=0, selected_skills=()))
    assert services.runs.list_documents() == []
    assert services.threads.get("th-1").messages == []


async def test_resolver_lease_contains_bindings_with_guard_and_hitl(tmp_path):
    """有健康 MCP 目录时：lease 携带 bindings；请求中间件含 guard+HITL。"""
    import sys as _sys

    from agent.mcp import McpServer, StdioTransport

    services_root = tmp_path / "agent"
    services_root.mkdir(parents=True)
    registry = McpRegistry.for_root(services_root)
    fixture = Path(__file__).parent / "fake_mcp_server.py"
    server = McpServer.model_validate({
        "id": "fixture", "display_name": "夹具", "enabled": True,
        "transport": {"type": "stdio", "executable": _sys.executable,
                      "args": [str(fixture)], "env": {}},
    })
    await registry.add(server)
    fingerprint = (await registry.trust_preview("fixture")).fingerprint
    await registry.trust("fixture", fingerprint, registry.store.load().revision)
    catalog = await registry.refresh("fixture")
    revision = registry.store.load().revision
    await registry.patch_server("fixture", revision, lambda s: s.model_copy(update={
        "tools": [t.model_copy(update={"enabled": t.original_name == "echo"}) for t in s.tools],
    }))

    skills = SkillRegistry(services_root / "skills")
    resolver = CapabilityResolver(skills, registry=registry)
    lease = await resolver.acquire(CapabilityPreview(
        thread_id="th-1", thread_revision=0, selected_skills=()))
    try:
        aliases = [b.alias for b in lease.mcp_bindings]
        assert aliases == ["mcp__fixture__echo"]
        middleware = lease.build_request_middleware(SECRETS)
        types = [type(m).__name__ for m in middleware]
        assert "McpArgumentGuard" in types
        assert any("HumanInTheLoop" in t for t in types)
        # guard 在 HITL 之前
        assert types.index("McpArgumentGuard") < next(
            i for i, t in enumerate(types) if "HumanInTheLoop" in t)
        # lease 上无密钥/session
        assert "sk-live" not in repr(lease)
    finally:
        await lease.aclose()
        await registry.shutdown()


async def test_all_disabled_server_is_not_relevant(tmp_path):
    from agent.mcp import McpServer, StdioTransport

    root = tmp_path / "agent"
    registry = McpRegistry.for_root(root)
    await registry.add(McpServer.model_validate({
        "id": "idle", "display_name": "全禁", "enabled": True,
        "transport": {"type": "stdio", "executable": "nonexistent-bin-xyz",
                      "args": [], "env": {}},
        "tools": [{"original_name": "echo", "alias": "mcp__idle__echo",
                   "description": "d", "input_schema": {}, "enabled": False,
                   "discovered_at": ""}],
    }))
    skills = SkillRegistry(root / "skills")
    resolver = CapabilityResolver(skills, registry=registry)
    lease = await resolver.acquire(CapabilityPreview(
        thread_id="th-1", thread_revision=0, selected_skills=()))
    assert lease.mcp_bindings == ()
    lease.release()


# ---------------------------------------------------------------------------
# Task 13：许可清理 / resume_available / 取消 / steer-away
# ---------------------------------------------------------------------------

@pytest.fixture()
def api_client_with_allowances(tmp_path, monkeypatch):
    from agent.router import build_services

    services = build_services(tmp_path / "agent")
    services.coordinator._allowances = services.coordinator._allowances or AllowanceRegistry()
    monkeypatch.setattr("agent.router.services", services)
    client = __import__("fastapi.testclient", fromlist=["TestClient"]).TestClient(
        __import__("app").app, client=("127.0.0.1", 50001))
    return client, services


async def test_allowance_clear_helpers():
    allowances = AllowanceRegistry()
    allowances.grant("th-1", "fixture", "echo")
    allowances.grant("th-1", "other", "tool")
    allowances.grant("th-2", "fixture", "echo")
    assert allowances.clear_server("fixture") == 2
    assert allowances.clear_thread("th-1") == 1
    allowances.grant("th-3", "fixture", "echo")
    assert allowances.clear_tool("fixture", "echo") == 1
    assert allowances.clear_all() == 0


async def test_allowance_clear_endpoint_and_thread_delete(api_client_with_allowances):
    client, services = api_client_with_allowances
    services.coordinator._allowances.grant("th-1", "fixture", "echo")
    thread = client.post("/api/agent/threads", json={"title": "t"}).json()
    revision_before = client.get(f"/api/agent/threads/{thread['id']}").json()["revision"]
    resp = client.delete(f"/api/agent/threads/{thread['id']}/allowances")
    assert resp.status_code == 200
    assert resp.json() == {"cleared": 0}
    resp2 = client.delete("/api/agent/threads/th-1/allowances")
    assert resp2.json() == {"cleared": 1}
    assert resp2.json() == {"cleared": 1}  # 幂等清零
    revision_after = client.get(f"/api/agent/threads/{thread['id']}").json()["revision"]
    assert revision_after == revision_before  # 不改 thread revision


async def test_thread_resume_available_computed_field(api_client_with_allowances):
    client, services = api_client_with_allowances
    thread = client.post("/api/agent/threads", json={"title": "t"}).json()
    detail = client.get(f"/api/agent/threads/{thread['id']}").json()
    assert detail["resume_available"] is False  # 无活动句柄


async def test_thread_delete_clears_allowances(api_client_with_allowances):
    client, services = api_client_with_allowances
    thread = client.post("/api/agent/threads", json={"title": "t"}).json()
    services.coordinator._allowances.grant(thread["id"], "fixture", "echo")
    resp = client.request("DELETE", f"/api/agent/threads/{thread['id']}",
                          json={"revision": thread["revision"]})
    assert resp.status_code == 204
    assert not services.coordinator._allowances.has(thread["id"], "fixture", "echo")


async def test_mcp_server_change_clears_allowances(api_client_with_allowances):
    import sys as _sys
    from agent.mcp import StdioTransport

    client, services = api_client_with_allowances
    fixture = Path(__file__).parent / "fake_mcp_server.py"
    from agent.mcp import McpServer

    await services.registry.add(McpServer.model_validate({
        "id": "fixture", "display_name": "夹具", "enabled": True,
        "transport": {"type": "stdio", "executable": _sys.executable,
                      "args": [str(fixture)], "env": {}},
    }))
    services.coordinator._allowances.grant("th-1", "fixture", "echo")
    doc = services.registry.store.load()
    updated = await services.registry.patch_server("fixture", doc.revision, lambda s: s.model_copy(
        update={"transport": StdioTransport(executable=_sys.executable,
                                            args=[str(fixture), "--flag"], env={})}))
    assert updated.revision == doc.revision + 1
    assert not services.coordinator._allowances.has("th-1", "fixture", "echo")


async def test_shutdown_clears_allowances(api_client_with_allowances):
    client, services = api_client_with_allowances
    services.coordinator._allowances.grant("th-1", "fixture", "echo")
    await services.coordinator.shutdown()
    assert not services.coordinator._allowances.has("th-1", "fixture", "echo")


# ---------------------------------------------------------------------------
# Review 修复回归：生产路径元数据富集 + cancel_run 租约释放
# ---------------------------------------------------------------------------

async def test_production_capture_path_enriches_mcp_metadata(tmp_path):
    """走真实 _capture（RAW → legacy on_interrupt）再富集，而非手工构造。"""
    from ag_ui.core.events import CustomEvent, EventType, ToolCallArgsEvent, ToolCallEndEvent, ToolCallStartEvent
    from agent.protocol import AgentProtocolBridge, interrupt_payloads

    bridge = AgentProtocolBridge("th-1", "run-1")
    # 生产观察路径：ToolCallStart/Args/End → legacy on_interrupt → _capture
    bridge.convert(ToolCallStartEvent(
        tool_call_id="call-9", tool_call_name="mcp__fixture__echo",
        parent_message_id="assistant-1"))
    bridge.convert(ToolCallArgsEvent(
        tool_call_id="call-9", delta=json.dumps({"value": "sk-live-key-123"})))
    bridge.convert(ToolCallEndEvent(tool_call_id="call-9"))
    bridge.convert(CustomEvent(
        type=EventType.CUSTOM, name="on_interrupt",
        value={
            "action_requests": [{
                "name": "mcp__fixture__echo",
                "args": {"value": "sk-live-key-123"},
                "description": "审批",
            }],
            "review_configs": [{
                "action_name": "mcp__fixture__echo",
                "allowed_decisions": ["approve", "reject"],
            }],
        }))
    assert bridge.pending, "生产 capture 路径必须产生 pending"

    from agent.capabilities import enrich_pending_interrupts
    from agent.mcp import McpToolBinding

    binding = McpToolBinding(
        server_id="fixture", original_name="echo", alias="mcp__fixture__echo",
        description="d", args_schema={}, config_generation=1, catalog_generation=1,
        server_name="财报服务")

    class LeaseStub:
        mcp_bindings = (binding,)
        _registry = None

    enriched = enrich_pending_interrupts(bridge.pending, LeaseStub(), "sk-live-key-123")
    payload = interrupt_payloads(enriched)[0]
    assert payload["serverId"] == "fixture"
    assert payload["serverName"] == "财报服务"
    assert payload["toolName"] == "echo"
    assert payload["toolAlias"] == "mcp__fixture__echo"
    assert payload["arguments"] == {"value": "[redacted]"}  # 模型密钥脱敏
    # resume + thread_session 许可现在能推导出来
    resume, allowances = AgentProtocolBridge(
        "th-1", "run-1", pending=enriched).resume_value_with_allowances([
        {"interruptId": enriched[0].bridge_interrupt_id, "status": "resolved",
         "payload": {"decision": "approve", "scope": "thread_session"}}])
    assert allowances == [("th-1", "fixture", "echo")]


async def test_cancel_run_releases_capability_lease(tmp_path):
    """断连/REST 取消路径必须释放租约（skill_in_use/mcp_server_in_use 复位）。"""
    from agent.router import build_services
    from agent.models import ThreadDocument
    from ag_ui.core.types import UserMessage

    services = build_services(tmp_path / "agent")
    services.threads.create(ThreadDocument.new("th-1", "研究", now=utc_now()))
    from tests.agent.fakes import ScriptedChatModel
    from langchain_core.messages import AIMessage

    admission = await services.coordinator.acquire_start(
        model_ref=MODEL_REF, secrets=SECRETS,
        model_builder=lambda ref, sec: ScriptedChatModel([AIMessage(content="ok")]),
        thread_id="th-1", protocol_run_id="protocol-cancel",
        messages=[UserMessage(id="u-cancel", content="问题")], client_revision=0)
    handle = admission.handle
    lease = handle.capability_lease
    assert lease is not None
    assert lease._released is False
    await services.coordinator.cancel_run("th-1", handle.product_run_id)
    assert lease._released is True
    assert services.coordinator.skill_in_use("any") is False


async def test_allowance_key_uses_real_thread_id(tmp_path):
    """router resume 路径的许可必须登记到真实 thread_id（HITL 查询键）。"""
    from ag_ui.core.events import CustomEvent, EventType, ToolCallArgsEvent, ToolCallEndEvent, ToolCallStartEvent
    from agent.capabilities import AllowanceRegistry, enrich_pending_interrupts, build_hitl_policy
    from agent.mcp import McpToolBinding
    from agent.protocol import AgentProtocolBridge

    bridge = AgentProtocolBridge("th-real", "run-1")
    bridge.convert(ToolCallStartEvent(tool_call_id="c1", tool_call_name="mcp__fixture__echo",
                                      parent_message_id="a1"))
    bridge.convert(ToolCallArgsEvent(tool_call_id="c1", delta='{"value": "x"}'))
    bridge.convert(ToolCallEndEvent(tool_call_id="c1"))
    bridge.convert(CustomEvent(type=EventType.CUSTOM, name="on_interrupt", value={
        "action_requests": [{"name": "mcp__fixture__echo", "args": {"value": "x"}, "description": "d"}],
        "review_configs": [{"action_name": "mcp__fixture__echo", "allowed_decisions": ["approve", "reject"]}],
    }))

    class LeaseStub:
        mcp_bindings = (McpToolBinding(server_id="fixture", original_name="echo",
                                       alias="mcp__fixture__echo", description="d",
                                       args_schema={}, config_generation=1,
                                       catalog_generation=1, server_name="夹具"),)
        _registry = None

    enriched = enrich_pending_interrupts(bridge.pending, LeaseStub(), "sk-x")
    # 与 router 相同的 thread_id 构造 bridge
    validate = AgentProtocolBridge("th-real", "", pending=enriched)
    _, allowances = validate.resume_value_with_allowances([
        {"interruptId": enriched[0].bridge_interrupt_id, "status": "resolved",
         "payload": {"decision": "approve", "scope": "thread_session"}}])
    registry = AllowanceRegistry()
    for thread_id, server_id, tool_name in allowances:
        registry.grant(thread_id, server_id, tool_name)
    # HITL 的 when 谓词在真实 thread_id 下放行
    policy = build_hitl_policy(thread_id="th-real", bindings=LeaseStub.mcp_bindings,
                               allowances=registry)
    entry = policy.interrupt_on["mcp__fixture__echo"]
    assert entry["when"](type("R", (), {"tool_call": {"name": "mcp__fixture__echo"}})) is False
