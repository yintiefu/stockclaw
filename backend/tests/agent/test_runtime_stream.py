from langchain_core.messages import AIMessage
from langchain_core.tools import tool
import pytest

from agent.runtime import AgentFactory
from tests.agent.fakes import ScriptedChatModel

pytestmark = pytest.mark.asyncio


async def test_create_agent_completes_a_tool_then_text_run():
    calls: list[str] = []

    @tool
    def lookup(code: str) -> str:
        """Look up one test code."""
        calls.append(code)
        return "price=10"

    model = ScriptedChatModel([
        AIMessage(content="", tool_calls=[{"id": "call-1", "name": "lookup", "args": {"code": "600519"}}]),
        AIMessage(content="fixture answer"),
    ])
    handle = AgentFactory().create(model=model, tools=[lookup], thread_id="thread-1")
    events = [event async for event in handle.new_adapter("protocol-1").run(handle.start_input("hello"))]

    assert calls == ["600519"]
    assert [event.type for event in events][0].value == "RUN_STARTED"
    assert [event.type for event in events][-1].value == "RUN_FINISHED"
    text = "".join(
        getattr(event, "delta", "")
        for event in events
        if getattr(event.type, "value", event.type) == "TEXT_MESSAGE_CONTENT"
    )
    assert text == "fixture answer"
