"""1A 垂直切片：离线驱动 POST /api/agent/run，校验事件顺序与边界行为。"""

import json

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langchain_core.tools import tool

import app as app_module
import agent.router as router_module
from agent.runs import RunCoordinator
from tests.agent.fakes import PausingChatModel, ScriptedChatModel

client = TestClient(app_module.app, client=("127.0.0.1", 50000))
HEADERS = {"X-VR-Agent-Model-Key": "request-only-key", "Accept": "text/event-stream"}

CALLS: list[str] = []


@tool
def slice_tool(code: str) -> str:
    """Look up a fixture value."""
    CALLS.append(code)
    return "slice-result"


def start_payload(content="hello", run_id="protocol-slice"):
    return {
        "threadId": "thread-slice",
        "runId": run_id,
        "state": {},
        "messages": [{"id": "user-slice", "role": "user", "content": content}],
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


def parse_events(text: str) -> list[dict]:
    return [json.loads(line[len("data: "):]) for line in text.splitlines() if line.startswith("data: ")]


def patch_model(monkeypatch, replies):
    router_module.coordinator = RunCoordinator()
    CALLS.clear()
    monkeypatch.setattr("agent.router.build_chat_model", lambda a, b: ScriptedChatModel(replies))
    monkeypatch.setattr("agent.router.build_builtin_tools", lambda: [slice_tool])
    monkeypatch.setattr("agent.router.build_middleware", lambda: ())


def test_vertical_slice_event_order(monkeypatch):
    patch_model(monkeypatch, [
        AIMessage(content="", tool_calls=[{"id": "call-1", "name": "slice_tool", "args": {"code": "600519"}}]),
        AIMessage(content="slice answer"),
    ])
    response = client.post("/api/agent/run", json=start_payload(), headers=HEADERS)
    assert response.status_code == 200
    types = [event["type"] for event in parse_events(response.text)]
    # 锁定版本（ag-ui-langgraph 0.0.42）不发独立 TOOL_CALL_ARGS：
    # 参数内嵌在 TOOL_CALL_START.rawEvent 的 chunk 里（见 Task 7 spike 记录）。
    expected_order = [
        "RUN_STARTED",
        "TOOL_CALL_START",
        "TOOL_CALL_END",
        "TOOL_CALL_RESULT",
        "TEXT_MESSAGE_START",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_END",
        "RUN_FINISHED",
    ]
    positions = []
    for event_type in expected_order:
        assert event_type in types, f"缺少 {event_type}: {types}"
        positions.append(types.index(event_type))
    assert positions == sorted(positions), f"事件顺序异常: {types}"
    # 工具参数仍需在流中可见（rawEvent 内嵌）
    assert "600519" in response.text
    assert CALLS == ["600519"]
    assert "request-only-key" not in response.text


def test_tool_failure_returns_structured_content(monkeypatch):
    patch_model(monkeypatch, [
        AIMessage(content="", tool_calls=[{"id": "call-2", "name": "slice_tool", "args": {"code": "000000"}}]),
        AIMessage(content="handled failure"),
    ])

    def failing(code):
        raise RuntimeError("boom")

    import tools as legacy_tools
    monkeypatch.setattr("agent.router.build_builtin_tools", lambda: [_failing_tool(failing)])

    response = client.post("/api/agent/run", json=start_payload(), headers=HEADERS)
    assert response.status_code == 200
    events = parse_events(response.text)
    results = [e for e in events if e["type"] == "TOOL_CALL_RESULT"]
    assert results and "error" in json.dumps(results[0])
    assert any(e["type"] == "RUN_FINISHED" for e in events)


def _failing_tool(fn):
    from langchain_core.tools import StructuredTool
    import asyncio

    async def invoke(**kwargs):
        try:
            return json.dumps(await asyncio.to_thread(fn, kwargs.get("code")), ensure_ascii=False)
        except Exception as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)

    return StructuredTool.from_function(
        coroutine=invoke,
        name="slice_tool",
        description="Look up a fixture value.",
        args_schema={"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]},
    )


import json as _json  # noqa: E402


def test_model_failure_returns_run_error(monkeypatch):
    patch_model(monkeypatch, [])  # 空脚本队列 → 模型调用异常
    response = client.post("/api/agent/run", json=start_payload(), headers=HEADERS)
    assert response.status_code == 200
    events = parse_events(response.text)
    assert any(e["type"] == "RUN_ERROR" for e in events)
    assert "request-only-key" not in response.text
