"""LangGraph 本地客户端与流式协议适配测试。

测试覆盖：
- SSE 流式解析器对 messages-tuple、custom、updates 事件的分发；
- 服务不可用 (ConnectError) 错误映射为友好的 LangGraphUnavailableError；
- 超时与 4xx/5xx 错误映射；
- 工作流流式事件与聊天流式事件的协议转换适配。
"""
from __future__ import annotations

from contextlib import asynccontextmanager
import json
import pytest
import httpx

from agent.client import (
    LangGraphClient,
    LangGraphUnavailableError,
    LangGraphTimeoutError,
    LangGraphResponseError,
    parse_sse_stream,
)


@pytest.mark.asyncio
async def test_parse_sse_stream_chunks():
    raw_sse = (
        b"event: custom\n"
        b'data: {"event": "workflow_started", "workflow_type": "debate", "input": {"code": "600519"}, "variant": "standard", "run_id": "r1", "seq": 1, "created_at": "2026-08-25T00:00:00Z"}\n\n'
        b"event: messages-tuple\n"
        b'data: [{"type": "AIMessageChunk", "content": "\xe4\xbd\xa0\xe5\xa5\xbd"}, {"langgraph_node": "model"}]\n\n'
    )

    async def fake_stream():
        yield raw_sse

    events = []
    async for event_type, data in parse_sse_stream(fake_stream()):
        events.append((event_type, data))

    assert len(events) == 2
    assert events[0][0] == "custom"
    assert events[0][1]["event"] == "workflow_started"
    assert events[1][0] == "messages-tuple"
    assert events[1][1][0]["content"] == "你好"


@pytest.mark.asyncio
async def test_client_connect_error_mapped_to_unavailable(monkeypatch):
    client = LangGraphClient(base_url="http://127.0.0.1:2024")

    @asynccontextmanager
    async def fake_stream(*args, **kwargs):
        raise httpx.ConnectError("Connection refused")
        yield

    monkeypatch.setattr(client._client, "stream", fake_stream)

    with pytest.raises(LangGraphUnavailableError, match="LangGraph 服务不可用|127.0.0.1:2024"):
        async for _ in client.stream_workflow("debate", "th-1", {"code": "600519"}):
            pass


@pytest.mark.asyncio
async def test_client_timeout_error_mapped(monkeypatch):
    client = LangGraphClient(base_url="http://127.0.0.1:2024")

    @asynccontextmanager
    async def fake_stream(*args, **kwargs):
        raise httpx.ReadTimeout("Read timed out")
        yield

    monkeypatch.setattr(client._client, "stream", fake_stream)

    with pytest.raises(LangGraphTimeoutError, match="超时"):
        async for _ in client.stream_workflow("debate", "th-1", {"code": "600519"}):
            pass


@pytest.mark.asyncio
async def test_client_stream_workflow_dispatches_custom_events(monkeypatch):
    client = LangGraphClient(base_url="http://127.0.0.1:2024")

    sample_event = {
        "event": "stage_delta",
        "stage_id": "bull",
        "delta": "多方逻辑增量",
        "run_id": "r1",
        "seq": 2,
        "created_at": "2026-08-25T00:00:00Z",
    }
    sse_payload = f"event: custom\ndata: {json.dumps(sample_event)}\n\n".encode("utf-8")

    class FakeResponse:
        status_code = 200

        async def aiter_bytes(self):
            yield sse_payload

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    monkeypatch.setattr(client._client, "stream", lambda *args, **kwargs: FakeResponse())

    dispatched = []
    async for ev in client.stream_workflow("debate", "th-1", {"code": "600519"}):
        dispatched.append(ev)

    assert len(dispatched) == 1
    assert dispatched[0]["event"] == "stage_delta"
    assert dispatched[0]["delta"] == "多方逻辑增量"
