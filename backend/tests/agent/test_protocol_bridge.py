import json

from ag_ui.core.events import (
    CustomEvent, EventType, RunFinishedEvent,
    ToolCallArgsEvent, ToolCallEndEvent, ToolCallStartEvent,
)
from agent.protocol import AgentProtocolBridge


def legacy_interrupt(tool_call_id: str = "call-1") -> CustomEvent:
    return CustomEvent(
        type=EventType.CUSTOM,
        name="on_interrupt",
        value={
            "action_requests": [{"name": "mcp__demo__quote", "args": {"code": "600519"}, "description": "review"}],
            "review_configs": [{"action_name": "mcp__demo__quote", "allowed_decisions": ["approve", "reject"]}],
        },
    )


def observe_tool_call(bridge: AgentProtocolBridge, tool_call_id: str = "call-1") -> None:
    bridge.convert(ToolCallStartEvent(
        tool_call_id=tool_call_id,
        tool_call_name="mcp__demo__quote",
        parent_message_id="assistant-1",
    ))
    bridge.convert(ToolCallArgsEvent(
        tool_call_id=tool_call_id,
        delta=json.dumps({"code": "600519"}),
    ))
    bridge.convert(ToolCallEndEvent(tool_call_id=tool_call_id))


def test_legacy_interrupt_is_suppressed_and_finishes_with_standard_outcome():
    bridge = AgentProtocolBridge("thread-1", "run-1")
    observe_tool_call(bridge)
    assert bridge.convert(legacy_interrupt()) == []
    converted = bridge.convert(RunFinishedEvent(thread_id="thread-1", run_id="run-1"))
    assert len(converted) == 1
    payload = converted[0].model_dump(by_alias=True, mode="json")
    assert payload["outcome"]["type"] == "interrupt"
    assert payload["outcome"]["interrupts"][0]["reason"] == "tool_call"
    assert "expiresAt" not in payload["outcome"]["interrupts"][0]


def test_repeated_observation_reuses_bridge_id():
    bridge = AgentProtocolBridge("thread-1", "run-1")
    observe_tool_call(bridge)
    bridge.convert(legacy_interrupt())
    first = bridge.pending[0].bridge_interrupt_id
    bridge.convert(legacy_interrupt())
    assert bridge.pending[0].bridge_interrupt_id == first


def test_unknown_custom_event_fails_closed():
    bridge = AgentProtocolBridge("thread-1", "run-1")
    event = CustomEvent(type=EventType.CUSTOM, name="surprise", value={})
    assert bridge.convert(event)[0].code == "UNSUPPORTED_CUSTOM_EVENT"


def test_cancelled_event_uses_the_standard_client_event_name():
    payload = AgentProtocolBridge("thread-1", "run-1").cancelled().model_dump(by_alias=True)
    assert payload == {"type": "RUN_CANCELLED", "threadId": "thread-1", "runId": "run-1"}


def test_interleaved_tool_fragments_keep_call_ids_and_order():
    bridge = AgentProtocolBridge("thread-1", "run-1")
    bridge.convert(ToolCallStartEvent(tool_call_id="call-a", tool_call_name="tool_a"))
    bridge.convert(ToolCallStartEvent(tool_call_id="call-b", tool_call_name="tool_b"))
    bridge.convert(ToolCallArgsEvent(tool_call_id="call-a", delta='{"code":'))
    bridge.convert(ToolCallArgsEvent(tool_call_id="call-b", delta='{"symbol":'))
    bridge.convert(ToolCallArgsEvent(tool_call_id="call-a", delta='"600519"}'))
    bridge.convert(ToolCallArgsEvent(tool_call_id="call-b", delta='"AAPL"}'))
    bridge.convert(ToolCallEndEvent(tool_call_id="call-b"))
    bridge.convert(ToolCallEndEvent(tool_call_id="call-a"))
    bridge.convert(CustomEvent(type=EventType.CUSTOM, name="on_interrupt", value={
        "action_requests": [
            {"name": "tool_a", "args": {"code": "600519"}},
            {"name": "tool_b", "args": {"symbol": "AAPL"}},
        ],
        "review_configs": [
            {"action_name": "tool_a", "allowed_decisions": ["approve", "reject"]},
            {"action_name": "tool_b", "allowed_decisions": ["approve", "reject"]},
        ],
    }))
    assert [item.tool_call_id for item in bridge.pending] == ["call-a", "call-b"]


import pytest


def test_resolved_entries_become_ordered_hitl_decisions():
    bridge = AgentProtocolBridge("thread-1", "run-1")
    observe_tool_call(bridge, "call-1")
    bridge.convert(legacy_interrupt("call-1"))
    observe_tool_call(bridge, "call-2")
    bridge.convert(legacy_interrupt("call-2"))
    entries = [
        {"interruptId": bridge.pending[1].bridge_interrupt_id, "status": "resolved", "payload": {"decision": "reject", "scope": "once"}},
        {"interruptId": bridge.pending[0].bridge_interrupt_id, "status": "resolved", "payload": {"decision": "approve", "scope": "once"}},
    ]
    assert bridge.resume_value(entries) == {
        "decisions": [{"type": "approve"}, {"type": "reject", "message": "User rejected the tool call"}]
    }


@pytest.mark.parametrize("entries", [[], [{"interruptId": "unknown", "status": "resolved", "payload": {"decision": "approve", "scope": "once"}}]])
def test_incomplete_or_unknown_resume_fails_closed(entries):
    bridge = AgentProtocolBridge("thread-1", "run-1")
    observe_tool_call(bridge)
    bridge.convert(legacy_interrupt())
    with pytest.raises(ValueError):
        bridge.resume_value(entries)


def test_all_cancelled_is_steer_away_and_never_a_hitl_decision():
    bridge = AgentProtocolBridge("thread-1", "run-1")
    observe_tool_call(bridge)
    bridge.convert(legacy_interrupt())
    entries = [{"interruptId": bridge.pending[0].bridge_interrupt_id, "status": "cancelled"}]
    assert bridge.is_steer_away(entries) is True
    with pytest.raises(ValueError):
        bridge.resume_value(entries)


# ---- 1D：budget.updated 白名单与载荷校验 ----


def budget_payload(**overrides) -> dict:
    payload = {
        "threadId": "thread-1",
        "runId": "run-1",
        "controlRevision": 12,
        "budgetSnapshot": {
            "policy_revision": 1,
            "max_model_calls": 8,
            "max_tool_calls": 16,
            "tool_timeout_seconds": 30,
            "max_active_seconds": 300,
            "max_context_chars": 120000,
        },
        "usage": {"model_calls": 2, "tool_calls": 1, "input_tokens": 10,
                  "output_tokens": 5, "total_tokens": 15, "token_status": "available"},
        "activeElapsedMs": 3100,
        "contextTruncation": {"occurred": False},
    }
    payload.update(overrides)
    return payload


def test_budget_updated_string_payload_passes_with_canonical_value():
    bridge = AgentProtocolBridge("thread-1", "run-1")
    out = bridge.convert(CustomEvent(
        type=EventType.CUSTOM, name="budget.updated",
        value=json.dumps(budget_payload(), ensure_ascii=False)))
    assert len(out) == 1
    assert out[0].name == "budget.updated"
    assert json.loads(out[0].value) == budget_payload()
    assert isinstance(out[0], CustomEvent)


def test_budget_updated_dict_payload_is_accepted_and_reencoded():
    bridge = AgentProtocolBridge("thread-1", "run-1")
    out = bridge.convert(CustomEvent(
        type=EventType.CUSTOM, name="budget.updated", value=budget_payload()))
    assert len(out) == 1
    assert json.loads(out[0].value) == budget_payload()


@pytest.mark.parametrize("mutation", [
    lambda p: {k: v for k, v in p.items() if k != "controlRevision"},
    lambda p: {**p, "controlRevision": -1},
    lambda p: {**p, "surprise": True},
    lambda p: {**p, "usage": {"model_calls": -1}},
    lambda p: {**p, "usage": {"token_status": "bogus"}},
    lambda p: {**p, "budgetSnapshot": {"max_model_calls": 8}},  # 快照缺字段
])
def test_budget_updated_malformed_payload_fails_closed(mutation):
    bridge = AgentProtocolBridge("thread-1", "run-1")
    out = bridge.convert(CustomEvent(
        type=EventType.CUSTOM, name="budget.updated",
        value=json.dumps(mutation(budget_payload()))))
    assert len(out) == 1
    assert out[0].code == "INVALID_CUSTOM_EVENT"


def test_budget_updated_non_object_payload_fails_closed():
    bridge = AgentProtocolBridge("thread-1", "run-1")
    out = bridge.convert(CustomEvent(type=EventType.CUSTOM, name="budget.updated", value="[]"))
    assert out[0].code == "INVALID_CUSTOM_EVENT"


@pytest.mark.parametrize(("name", "payload"), [
    ("sources.updated", {
        "threadId": "thread-1", "runId": "run-1", "sourceCount": 7,
        "sourcesTruncated": False,
    }),
    ("artifact.created", {
        "threadId": "thread-1", "runId": "run-1", "artifactId": "artifact-1",
        "artifactType": "markdown", "parentArtifactId": "artifact-parent", "threadRevision": 3,
    }),
])
def test_persisted_custom_events_accept_only_camel_case_metadata(name, payload):
    out = AgentProtocolBridge("thread-1", "run-1").convert(CustomEvent(
        type=EventType.CUSTOM, name=name, value=json.dumps(payload)))
    assert len(out) == 1
    assert json.loads(out[0].value) == payload


@pytest.mark.parametrize(("name", "payload"), [
    ("sources.updated", {
        "threadId": "thread-1", "runId": "run-1", "sourceCount": 7,
        "sourcesTruncated": False, "sources": [{"url": "https://example.com"}],
    }),
    ("artifact.created", {
        "threadId": "thread-1", "runId": "run-1", "artifactId": "artifact-1",
        "artifactType": "markdown", "threadRevision": 3, "content": "not allowed",
    }),
    ("sources.updated", {
        "thread_id": "thread-1", "runId": "run-1", "sourceCount": 7,
        "sourcesTruncated": False,
    }),
    ("artifact.created", {
        "threadId": "thread-1", "run_id": "run-1", "artifactId": "artifact-1",
        "artifactType": "markdown", "threadRevision": 3,
    }),
])
def test_persisted_custom_events_reject_summaries_and_unknown_fields(name, payload):
    out = AgentProtocolBridge("thread-1", "run-1").convert(CustomEvent(
        type=EventType.CUSTOM, name=name, value=json.dumps(payload)))
    assert out[0].code == "INVALID_CUSTOM_EVENT"


def test_other_custom_events_remain_unsupported():
    bridge = AgentProtocolBridge("thread-1", "run-1")
    out = bridge.convert(CustomEvent(type=EventType.CUSTOM, name="budget.refreshed", value="{}"))
    assert out[0].code == "UNSUPPORTED_CUSTOM_EVENT"
