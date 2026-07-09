"""runner.run_agent 单测——NDJSON 事件流结构。"""
import json
from contextlib import asynccontextmanager

import pytest

import runner


class _FakeStreamResponse:
    """模拟 httpx 流式响应——吐预设的 SSE 行。"""
    def __init__(self, lines: list[str], status_code: int = 200):
        self._lines = lines
        self.status_code = status_code

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def aread(self):
        return b""


@asynccontextmanager
async def _fake_stream_factory(lines: list[str], status_code: int = 200):
    """构造一个假 httpx.AsyncClient.stream context manager。"""
    async def _stream(method, url, **kwargs):
        return _FakeStreamResponse(lines, status_code)
    yield _FakeStreamResponse(lines, status_code)


def _patch_httpx_stream(monkeypatch, sse_lines: list[str], status_code: int = 200):
    """Patch runner.httpx.AsyncClient.stream 返回预设 SSE 流。"""
    @asynccontextmanager
    async def fake_stream(self, method, url, **kwargs):
        yield _FakeStreamResponse(sse_lines, status_code)

    monkeypatch.setattr("runner.httpx.AsyncClient.stream", fake_stream)


def _sse_delta(text: str) -> str:
    """构造一个 OpenAI 兼容的 SSE data 行。"""
    payload = {"choices": [{"delta": {"content": text}}]}
    return f"data: {json.dumps(payload)}"


@pytest.mark.asyncio
async def test_run_agent_emits_decision_artifact_for_decision_intent(monkeypatch):
    """decision 路径 → text_delta + decision_artifact + done。
    真 _stream_llm_text 被跑——只 mock httpx.AsyncClient.stream。"""
    async def fake_graph_ainvoke(input_state):
        return {"intent": "decision", "decision_card": {
            "code": "600519", "name": "茅台", "current_price": 1685.0,
            "target_price": 1900.0, "entry_low": 1685.0, "entry_high": 1720.0,
            "stop_loss": 1550.0, "take_profit": 2080.0, "cadence": [],
            "basis_type": "model", "model_versions_json": {},
            "assumptions": [], "citations": [], "explanation": "测试"
        }, "tool_traces": [
            {"tool": "forward_pe_target", "status": "ok", "args": {"code": "600519"}},
            {"tool": "atr_stop", "status": "ok", "args": {"code": "600519"}},
        ]}

    # 真 SSE 流：吐 "分析中" + [DONE]
    sse_lines = [_sse_delta("分析中"), "data: [DONE]"]
    _patch_httpx_stream(monkeypatch, sse_lines)

    monkeypatch.setattr("runner.agent_graph.ainvoke", fake_graph_ainvoke)

    req = runner.AgentChatReq(
        thread_id=None,
        messages=[{"role": "user", "content": "分析茅台 给目标价"}],
        context_codes=["600519"],
        llm={"provider": "", "baseURL": "https://api.example.com",
             "apiKey": "k", "model": "gpt-4o"},
        style="balanced",
    )
    events = []
    async for ev in runner.run_agent(req):
        events.append(ev)

    types = [e["type"] for e in events]
    assert "text_delta" in types
    assert "decision_artifact" in types
    assert "done" in types
    # text_delta 的内容必须是 "分析中"——证明 _stream_llm_text 真的被跑、SSE 真被解析
    text_events = [e for e in events if e["type"] == "text_delta"]
    assert any("分析中" in e["text"] for e in text_events)


@pytest.mark.asyncio
async def test_run_agent_no_decision_card_for_general_intent(monkeypatch):
    """general 路径 → 只有 text_delta + done，无 decision_artifact。"""
    async def fake_graph_ainvoke(input_state):
        return {"intent": "general", "decision_card": None}

    sse_lines = [_sse_delta("你好"), "data: [DONE]"]
    _patch_httpx_stream(monkeypatch, sse_lines)
    monkeypatch.setattr("runner.agent_graph.ainvoke", fake_graph_ainvoke)

    req = runner.AgentChatReq(
        thread_id=None,
        messages=[{"role": "user", "content": "你好"}],
        llm={"provider": "", "baseURL": "https://api.example.com",
             "apiKey": "k", "model": "gpt-4o"},
    )
    events = []
    async for ev in runner.run_agent(req):
        events.append(ev)

    types = [e["type"] for e in events]
    assert "decision_artifact" not in types
    assert "text_delta" in types
    assert "done" in types
    text_events = [e for e in events if e["type"] == "text_delta"]
    assert any("你好" in e["text"] for e in text_events)


@pytest.mark.asyncio
async def test_run_agent_streams_llm_before_graph_finishes(monkeypatch):
    """LLM 流必须在 graph 完成前就开始——验证并发。"""
    import asyncio
    graph_done_time = []
    llm_start_time = []

    async def slow_graph(input_state):
        await asyncio.sleep(0.5)  # 模拟 5 工具 × 0.1s
        graph_done_time.append(asyncio.get_event_loop().time())
        return {"intent": "decision", "decision_card": None, "tool_traces": []}

    async def fake_stream(*args, **kwargs):
        llm_start_time.append(asyncio.get_event_loop().time())
        yield "首字"

    _patch_httpx_stream(monkeypatch, [_sse_delta("首字"), "data: [DONE]"])
    monkeypatch.setattr("runner.agent_graph.ainvoke", slow_graph)
    monkeypatch.setattr("runner._stream_llm_text", fake_stream)

    req = runner.AgentChatReq(
        thread_id=None,
        messages=[{"role": "user", "content": "分析"}],
        llm={"provider": "", "baseURL": "http://x", "apiKey": "k", "model": "m"},
    )
    events = []
    async for ev in runner.run_agent(req):
        events.append(ev)

    # LLM 必须在 graph 完成前开始
    assert llm_start_time and graph_done_time
    assert llm_start_time[0] < graph_done_time[0], "LLM 应在 graph 完成前开始流式"


@pytest.mark.asyncio
async def test_run_agent_emits_error_when_decision_fails(monkeypatch):
    """Task 2 的 guard 触发时（intent=decision_failed），runner 必须发 error 事件。"""
    async def failing_graph(input_state):
        return {
            "intent": "decision_failed",
            "decision_card": None,
            "tool_traces": [
                {"tool": "forward_pe_target", "status": "error", "args": {}},
            ],
        }

    _patch_httpx_stream(monkeypatch, [_sse_delta("分析中..."), "data: [DONE]"])
    monkeypatch.setattr("runner.agent_graph.ainvoke", failing_graph)

    req = runner.AgentChatReq(
        thread_id=None,
        messages=[{"role": "user", "content": "分析 XXXXXX"}],
        llm={"provider": "", "baseURL": "http://x", "apiKey": "k", "model": "m"},
    )
    events = []
    async for ev in runner.run_agent(req):
        events.append(ev)

    types = [e["type"] for e in events]
    assert "error" in types, f"决策失败应发 error 事件，实际 {types}"
    err = next(e for e in events if e["type"] == "error")
    assert "数据失败" in err["message"] or "无法生成" in err["message"]
    # 失败时仍应推 tool_trace（让用户看到哪些工具挂了）
    assert "tool_trace" in types
    # done 必须最后
    assert types[-1] == "done"


@pytest.mark.asyncio
async def test_run_agent_cancels_graph_on_llm_failure(monkeypatch):
    """LLM 流挂时 graph_task 必须被取消，不能 leak。

    场景：_stream_llm_text raise（httpx 错误、LLM 404 等）。
    若 graph_task 没 cancel：
    - 后台工具继续跑（rate-limiter 占用、可能写脏数据）
    - GC 时报 "Task was destroyed but it is pending!"
    """
    import asyncio

    graph_cancelled = []

    async def slow_graph(input_state):
        try:
            await asyncio.sleep(10)  # 模拟长跑
        except asyncio.CancelledError:
            graph_cancelled.append(True)
            raise

    async def failing_stream(*args, **kwargs):
        yield "首字"
        # 让事件循环调度一下——让 graph_task 真的进入 body
        # （否则 create_task 后从未被 await，cancel 时不会触发 CancelledError）
        await asyncio.sleep(0)
        raise RuntimeError("LLM 404")

    _patch_httpx_stream(monkeypatch, [])  # 不会真用 httpx
    monkeypatch.setattr("runner.agent_graph.ainvoke", slow_graph)
    monkeypatch.setattr("runner._stream_llm_text", failing_stream)

    req = runner.AgentChatReq(
        thread_id=None,
        messages=[{"role": "user", "content": "x"}],
        llm={"provider": "", "baseURL": "http://x", "apiKey": "k", "model": "m"},
    )
    events = []
    async for ev in runner.run_agent(req):
        events.append(ev)

    # graph 必须被取消（CancelledError 真的进入 body）
    assert graph_cancelled, "graph_task 应在 LLM 失败时被 cancel，不能 leak"
    # 顶层 error 事件仍要发（外层 try/except 兜底）
    assert any(e["type"] == "error" for e in events)


@pytest.mark.asyncio
async def test_run_agent_persists_decision_card(monkeypatch, tmp_path):
    """decision_artifact emit 后应写一条 decisions 记录。"""
    async def fake_graph_ainvoke(input_state):
        return {"intent": "decision", "decision_card": {
            "code": "600519", "name": "茅台", "current_price": 1685.0,
            "target_price": 1900.0, "entry_low": 1685.0, "entry_high": 1720.0,
            "stop_loss": 1550.0, "take_profit": 2080.0,
            "cadence": [{"batch": 1, "pct": 1.0, "trigger": "immediate"}],
            "basis_type": "model", "model_versions_json": {},
            "assumptions": [], "citations": [], "explanation": "测试"
        }, "tool_traces": []}

    _patch_httpx_stream(monkeypatch, [_sse_delta("ok"), "data: [DONE]"])
    monkeypatch.setattr("runner.agent_graph.ainvoke", fake_graph_ainvoke)

    db_path = tmp_path / "test.db"
    monkeypatch.setenv("VR_AGENT_DB", str(db_path))
    from persistence import db, threads
    await db.close_db()
    await db.init_db()
    # 评审 #2：先建 thread 行，避免 decisions 外键违反
    await threads.create_thread(tid="test-thread", title="t", model="m")

    req = runner.AgentChatReq(
        thread_id="test-thread",
        messages=[{"role": "user", "content": "分析 600519"}],
        llm={"provider": "", "baseURL": "http://x", "apiKey": "k", "model": "m"},
    )
    events = []
    async for ev in runner.run_agent(req):
        events.append(ev)

    from persistence import decisions
    items = await decisions.list_by_code("600519")
    assert len(items) >= 1
    assert items[0]["target_price"] == 1900.0
    assert items[0]["status"] == "active"
    assert items[0]["thread_id"] == "test-thread"
    # 评审 M2: 验证 raw_artifact_json 真的存了完整 card（不只是字段平铺）
    from persistence import decisions as d_dao
    row = await d_dao.get_decision(items[0]["id"])
    assert row is not None
    assert row["raw_artifact_json"]["code"] == "600519"
    assert row["raw_artifact_json"]["explanation"] == "测试"
    assert row["raw_artifact_json"]["target_price"] == 1900.0
    await db.close_db()


@pytest.mark.asyncio
async def test_run_agent_creates_thread_when_none_provided(monkeypatch, tmp_path):
    """评审 #2：req.thread_id 为 None 时 runner 应自动建 thread，不能 IntegrityError。"""
    async def fake_graph_ainvoke(input_state):
        return {"intent": "decision", "decision_card": {
            "code": "000001", "name": "平安银行", "current_price": 12.0,
            "target_price": 14.0, "entry_low": 12.0, "entry_high": 12.3,
            "stop_loss": 11.0, "take_profit": 16.0,
            "cadence": [], "basis_type": "model", "model_versions_json": {},
            "assumptions": [], "citations": [], "explanation": "测试"
        }, "tool_traces": []}

    _patch_httpx_stream(monkeypatch, [_sse_delta("ok"), "data: [DONE]"])
    monkeypatch.setattr("runner.agent_graph.ainvoke", fake_graph_ainvoke)

    db_path = tmp_path / "test.db"
    monkeypatch.setenv("VR_AGENT_DB", str(db_path))
    from persistence import db, decisions, threads as threads_dao
    await db.close_db()
    await db.init_db()

    req = runner.AgentChatReq(
        thread_id=None,  # ← 关键：不传 thread_id
        messages=[{"role": "user", "content": "分析 000001"}],
        llm={"provider": "", "baseURL": "http://x", "apiKey": "k", "model": "m"},
    )
    events = []
    async for ev in runner.run_agent(req):
        events.append(ev)

    # 不应崩；decisions 表应有一条记录，且 thread_id 指向新建的 thread
    items = await decisions.list_by_code("000001")
    assert len(items) == 1
    new_tid = items[0]["thread_id"]
    assert await threads_dao.get_thread(new_tid) is not None
    # 评审 M2: 验证 raw_artifact_json 真的存了完整 card（不只是字段平铺）
    from persistence import decisions as d_dao
    row = await d_dao.get_decision(items[0]["id"])
    assert row is not None
    assert row["raw_artifact_json"]["code"] == "000001"
    assert row["raw_artifact_json"]["explanation"] == "测试"
    assert row["raw_artifact_json"]["target_price"] == 14.0
    await db.close_db()
