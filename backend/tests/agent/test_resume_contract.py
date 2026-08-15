import gc
import weakref

import pytest
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from agent.models import ModelRef, RunSecrets
from agent.protocol import AgentProtocolBridge
from agent.runtime import AgentFactory, RunConfigMismatch
from tests.agent.fakes import ScriptedChatModel

pytestmark = pytest.mark.asyncio
SENTINEL = "sk-agent-spike-do-not-persist"


def assert_secret_absent(secret: str, *values: object) -> None:
    for value in values:
        rendered = repr(value)
        if hasattr(value, "model_dump_json"):
            rendered += value.model_dump_json()
        assert secret not in rendered


class MetadataRecorder(BaseCallbackHandler):
    def __init__(self):
        self.metadata: list[dict] = []

    def on_chain_start(self, serialized, inputs, **kwargs):
        self.metadata.append(dict(kwargs.get("metadata") or {}))


async def test_resume_uses_empty_messages_new_graph_and_new_adapter():
    executed: list[str] = []

    @tool
    def approval_tool(code: str) -> str:
        """Read one protected fixture value."""
        executed.append(code)
        return "approved-result"

    ref = ModelRef(provider="fixture", base_url="https://example.com/v1", model="fixture-model")
    first_model = ScriptedChatModel([AIMessage(
        content="",
        tool_calls=[{"id": "call-approval", "name": "approval_tool", "args": {"code": "600519"}}],
    )])
    factory = AgentFactory()
    handle = factory.create(
        model_ref=ref,
        secrets=RunSecrets(model_api_key=SENTINEL),
        model_builder=lambda model_ref, secrets: first_model,
        tools=[approval_tool],
        thread_id="thread-resume",
        middleware=[HumanInTheLoopMiddleware(interrupt_on={
            "approval_tool": {"allowed_decisions": ["approve", "reject"]},
        })],
    )
    assert "secrets" not in handle.__dataclass_fields__
    assert "model_api_key" not in handle.__dataclass_fields__
    bridge = AgentProtocolBridge("thread-resume", "protocol-start")
    recorder = MetadataRecorder()
    first_adapter = handle.new_adapter("protocol-start", callbacks=[recorder])
    first_events = []
    async for event in first_adapter.run(handle.start_input("approve fixture", "protocol-start")):
        first_events.extend(bridge.convert(event))

    assert executed == []
    assert len(bridge.pending) == 1
    assert first_events[-1].model_dump()["outcome"]["type"] == "interrupt"
    first_adapter_ref = weakref.ref(first_adapter)
    saver = handle.checkpointer
    first_state = await handle.graph.aget_state({"configurable": {"thread_id": handle.thread_id}})
    assert_secret_absent(SENTINEL, first_events, saver.storage, handle.snapshot, first_state.values)

    handle.release_graph()
    del first_adapter, first_model
    gc.collect()
    assert first_adapter_ref() is None
    assert handle.graph is None and handle.model is None

    final_model = ScriptedChatModel([AIMessage(content="fixture complete")])
    factory.resume(
        handle=handle,
        model_ref=ref,
        secrets=RunSecrets(model_api_key="second-secret"),
        model_builder=lambda model_ref, secrets: final_model,
    )
    second_adapter = handle.new_adapter("protocol-resume", callbacks=[recorder])

    async def fail_regenerate(*args, **kwargs):
        raise AssertionError("resume entered regenerate path")

    assert callable(getattr(second_adapter, "prepare_regenerate_stream", None))
    second_adapter.prepare_regenerate_stream = fail_regenerate
    resume_value = bridge.resume_value([{
        "interruptId": bridge.pending[0].bridge_interrupt_id,
        "status": "resolved",
        "payload": {"decision": "approve", "scope": "once"},
    }])
    resume_input = handle.resume_input("protocol-resume", resume_value)
    assert resume_input.messages == []

    resumed_events = [event async for event in second_adapter.run(resume_input)]
    assert handle.checkpointer is saver
    assert executed == ["600519"]
    text = "".join(
        getattr(event, "delta", "")
        for event in resumed_events
        if getattr(event.type, "value", event.type) == "TEXT_MESSAGE_CONTENT"
    )
    assert text == "fixture complete"
    resumed_state = await handle.graph.aget_state({"configurable": {"thread_id": handle.thread_id}})
    assert_secret_absent(SENTINEL, resumed_events, saver.storage, handle.snapshot, resumed_state.values)
    assert_secret_absent(SENTINEL, recorder.metadata, repr(handle))

    handle.release_graph()
    builder_called = False

    def builder_must_not_run(model_ref, secrets):
        nonlocal builder_called
        builder_called = True
        raise AssertionError("model builder ran before config validation")

    with pytest.raises(RunConfigMismatch) as exc:
        factory.resume(
            handle=handle,
            model_ref=ref.model_copy(update={"model": "changed-model"}),
            secrets=RunSecrets(model_api_key="third-secret"),
            model_builder=builder_must_not_run,
        )
    assert exc.value.code == "RUN_CONFIG_MISMATCH"
    assert builder_called is False
    assert_secret_absent(SENTINEL, exc.value, saver.storage, handle.snapshot)
