"""Task 2：会话追踪中间件契约——事件格式、usage 缺失记 null、异常隔离、
熔断只告警一次、preview 截断、hitl_reject orphan 识别、seq 按 run 隔离、
thread_id 清洗。全部离线、零真实目录 IO。"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest
from langchain.agents.middleware import ExtendedModelResponse, ModelResponse
from langchain_core.messages import AIMessage, ToolMessage

from agent.session_trace import (
    PREVIEW_LIMIT,
    SessionTraceMiddleware,
    _extract_model_info,
    _safe_thread_filename,
)
from agent.settings import TraceSettings


@dataclass
class _FakeExecutionInfo:
    thread_id: str
    run_id: str


@dataclass
class _FakeRuntime:
    execution_info: _FakeExecutionInfo


def make_middleware(traces_dir: Path) -> SessionTraceMiddleware:
    return SessionTraceMiddleware(TraceSettings(enabled=True, dir=traces_dir))


def read_events(traces_dir: Path, thread_id: str) -> list[dict]:
    path = traces_dir / f"{thread_id}.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


# ---------------------------------------------------------------------------
# 事件格式与 usage 契约
# ---------------------------------------------------------------------------

def test_model_call_usage_missing_records_null(tmp_path):
    mw = make_middleware(tmp_path)
    runtime = _FakeRuntime(_FakeExecutionInfo("t1", "r1"))
    mw.before_agent({"messages": []}, runtime)
    ai = AIMessage(content="回复")  # 无 usage_metadata
    response = ModelResponse(result=[ai])
    mw._record_model_call(("t1", "r1"), 0.0, response)
    events = read_events(tmp_path, "t1")
    model_call = events[-1]
    assert model_call["event"] == "model_call"
    assert model_call["input_tokens"] is None and model_call["output_tokens"] is None
    assert model_call["content_preview"] == "回复"


def test_model_call_usage_and_tool_calls_extracted(tmp_path):
    ai = AIMessage(
        content="分析",
        tool_calls=[{"id": "c1", "name": "query_quote", "args": {"query": "德明利"}}],
    )
    ai.usage_metadata = {"input_tokens": 3210, "output_tokens": 1105}
    ai.response_metadata = {"model_name": "glm-5.2"}
    info = _extract_model_info(ModelResponse(result=[ai]))
    assert info["model"] == "glm-5.2"
    assert info["input_tokens"] == 3210 and info["output_tokens"] == 1105
    assert info["tool_calls"] == [{"name": "query_quote", "args": {"query": "德明利"}}]


def test_extended_model_response_shape_extracted():
    ai = AIMessage(content="分块回复")
    ai.usage_metadata = {"input_tokens": 10, "output_tokens": 5}
    wrapped = ExtendedModelResponse(model_response=ModelResponse(result=[ai]))
    info = _extract_model_info(wrapped)
    assert info["input_tokens"] == 10
    assert info["content_preview"] == "分块回复"


# ---------------------------------------------------------------------------
# 异常隔离与熔断
# ---------------------------------------------------------------------------

def test_writer_failure_never_breaks_hooks_and_warns_once(tmp_path, monkeypatch, capsys):
    mw = make_middleware(tmp_path / "blocked")
    monkeypatch.setattr(
        Path, "mkdir", lambda *a, **k: (_ for _ in ()).throw(OSError("disk locked")),
    )
    runtime = _FakeRuntime(_FakeExecutionInfo("t2", "r2"))
    assert mw.before_agent({"messages": []}, runtime) is None
    assert mw.after_model({"messages": []}, runtime) is None
    assert mw.after_agent({"messages": []}, runtime) is None
    err = capsys.readouterr().err
    assert err.count("追踪写入失败") == 1  # 熔断：只告警一次
    assert not (tmp_path / "blocked").exists() or not any((tmp_path / "blocked").iterdir())


def test_tool_result_passes_through_and_error_reraised(tmp_path):
    import asyncio
    mw = make_middleware(tmp_path)
    request = type("R", (), {})()
    request.tool_call = {"name": "fixture_echo", "args": {"value": "x"}, "id": "c9"}
    request.runtime = _FakeRuntime(_FakeExecutionInfo("t3", "r3"))
    mw.before_agent({"messages": []}, request.runtime)
    mw.wrap_tool_call(request, lambda req: ToolMessage(content="透传结果", tool_call_id="c9"))
    events = read_events(tmp_path, "t3")
    assert events[-1]["event"] == "tool_call" and events[-1]["status"] == "ok"
    assert events[-1]["result_preview"] == "透传结果"

    def boom(req):
        raise RuntimeError("boom")
    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(mw.awrap_tool_call(request, boom))
    events = read_events(tmp_path, "t3")
    assert events[-1]["status"] == "error"


# ---------------------------------------------------------------------------
# preview 截断与 seq 隔离
# ---------------------------------------------------------------------------

def test_preview_truncated_with_result_chars(tmp_path):
    mw = make_middleware(tmp_path)
    request = type("R", (), {})()
    request.tool_call = {"name": "fixture_echo", "args": {}, "id": "c1"}
    request.runtime = _FakeRuntime(_FakeExecutionInfo("t4", "r4"))
    mw.before_agent({"messages": []}, request.runtime)
    long_text = "x" * (PREVIEW_LIMIT + 100)
    mw.wrap_tool_call(request, lambda req: ToolMessage(content=long_text, tool_call_id="c1"))
    event = read_events(tmp_path, "t4")[-1]
    assert len(event["result_preview"]) == PREVIEW_LIMIT
    assert event["result_chars"] == PREVIEW_LIMIT + 100


def test_seq_isolated_per_run(tmp_path):
    mw = make_middleware(tmp_path)
    run_a = _FakeRuntime(_FakeExecutionInfo("t5", "run-a"))
    run_b = _FakeRuntime(_FakeExecutionInfo("t5", "run-b"))
    mw.before_agent({"messages": []}, run_a)
    mw.before_agent({"messages": []}, run_b)
    assert mw._next_seq(("t5", "run-a")) == 1
    assert mw._next_seq(("t5", "run-b")) == 1
    assert mw._next_seq(("t5", "run-a")) == 2
    mw.after_agent({"messages": []}, run_a)
    assert ("t5", "run-a") not in mw._seq


def test_run_state_bounded_fifo(tmp_path):
    mw = make_middleware(tmp_path)
    for i in range(1100):
        mw.before_agent({"messages": []}, _FakeRuntime(_FakeExecutionInfo("t6", f"run-{i}")))
    assert len(mw._seq) <= 1024


# ---------------------------------------------------------------------------
# hitl_reject orphan 识别
# ---------------------------------------------------------------------------

def test_hitl_reject_orphan_detected(tmp_path):
    mw = make_middleware(tmp_path)
    runtime = _FakeRuntime(_FakeExecutionInfo("t7", "r7"))
    mw.before_agent({"messages": []}, runtime)
    ai = AIMessage(content="", tool_calls=[
        {"id": "kept", "name": "fixture_echo", "args": {}},
        {"id": "reject-1", "name": "fixture_echo", "args": {}},
    ])
    # 已批准的调用经 ToolNode 真正分发（wrap_tool_call 标记 executed）
    mw._mark_executed(("t7", "r7"), type("R", (), {"tool_call": {"id": "kept"}})())
    orphan = ToolMessage(content="用户拒绝该工具调用。", tool_call_id="reject-1", name="fixture_echo", status="error")
    kept = ToolMessage(content="approved", tool_call_id="kept", name="fixture_echo")
    mw.after_model({"messages": [ai, orphan, kept]}, runtime)
    events = read_events(tmp_path, "t7")
    rejects = [e for e in events if e["event"] == "hitl_reject"]
    assert len(rejects) == 1
    assert rejects[0]["tool_call_id"] == "reject-1"
    assert rejects[0]["name"] == "fixture_echo"
    assert rejects[0]["status"] == "error"
    assert rejects[0]["content"] == "用户拒绝该工具调用。"
    # 同一消息重复扫描不重复发事件
    mw.after_model({"messages": [ai, orphan, kept]}, runtime)
    assert len([e for e in read_events(tmp_path, "t7") if e["event"] == "hitl_reject"]) == 1


def test_approved_tool_message_not_flagged(tmp_path):
    mw = make_middleware(tmp_path)
    runtime = _FakeRuntime(_FakeExecutionInfo("t8", "r8"))
    mw.before_agent({"messages": []}, runtime)
    ai = AIMessage(content="", tool_calls=[{"id": "ok-1", "name": "fixture_echo", "args": {"value": "v"}}])
    result = ToolMessage(content="v", tool_call_id="ok-1", name="fixture_echo")
    mw._mark_executed(("t8", "r8"), type("R", (), {"tool_call": {"id": "ok-1"}})())
    mw.after_model({"messages": [ai, result]}, runtime)
    events = read_events(tmp_path, "t8")
    assert not [e for e in events if e["event"] == "hitl_reject"]


# ---------------------------------------------------------------------------
# thread_id 清洗
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad", ["../../etc/pwn", "a/b", "has space"])
def test_unsafe_thread_id_sanitized(tmp_path, bad):
    filename = _safe_thread_filename(bad)
    assert "/" not in filename and filename.endswith(".jsonl")
    runtime = _FakeRuntime(_FakeExecutionInfo(bad, "r9"))
    mw = make_middleware(tmp_path)
    assert mw.before_agent({"messages": []}, runtime) is None
    written = list(tmp_path.iterdir())
    assert len(written) == 1
    assert written[0].name == filename
    first = json.loads(written[0].read_text(encoding="utf-8").splitlines()[0])
    assert first["event"] == "run_start"
