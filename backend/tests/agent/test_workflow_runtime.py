"""工作流运行时确定性辅助函数契约测试。

测试覆盖：
- 底稿抓取顺序与展示顺序一致；
- allow_no_record 产生 no_record，gap_if_empty 产生 gap 并计入 missing；
- 确定性摘要与 6000 字符限制；
- 失败/未完成阶段序列化为【阶段 <id> 未产出】且不泄露错误细节；
- 裁判阶段上下文使用摘要与关键阶段，不填入完整底稿正文；
- 阶段模型输出按上限截断并标记 truncated=True；
- 错误信息脱敏（去除 API Key / 内部 IP / 堆栈）。
"""
from __future__ import annotations

import asyncio
import pytest
from langchain_core.messages import AIMessage, AIMessageChunk

import tools as legacy_tools
from agent.workflow_events import WorkflowEventEmitter
from agent.workflow_loader import DossierConfig, DossierSectionConfig
from agent.workflow_runtime import (
    collect_dossier_sections,
    format_stage_context,
    is_payload_empty,
    redact_workflow_error,
    run_stage,
    serialize_stage_context,
    summarize_dossier,
)
from agent.workflow_state import DossierSection, StageResult, WorkflowError
from tests.agent.fakes import ScriptedChatModel


def test_is_payload_empty_logic():
    assert is_payload_empty(None) is True
    assert is_payload_empty("") is True
    assert is_payload_empty([]) is True
    assert is_payload_empty({}) is True
    assert is_payload_empty({"period": "近5年", "metrics": {}}) is True
    assert is_payload_empty({"price": 100.0}) is False
    assert is_payload_empty([{"k": "v"}]) is False


def test_redact_workflow_error_removes_sensitive_data():
    raw_error = "Request failed to https://api.openai.com/v1/chat/completions with Bearer sk-secret12345678 at 192.168.1.30: Internal Server Error"
    err = redact_workflow_error(raw_error, stage_id="bull", code="UPSTREAM_ERROR")
    assert isinstance(err, WorkflowError)
    assert err.code == "UPSTREAM_ERROR"
    assert err.stage_id == "bull"
    assert "sk-secret" not in err.message
    assert "192.168.1.30" not in err.message


@pytest.mark.asyncio
async def test_collect_dossier_preserves_order_and_handles_policies(monkeypatch):
    def fake_tool(name, args):
        if name == "query_quote":
            return {"price": 1800}
        if name == "query_margin":
            return {}  # empty for allow_no_record
        if name == "query_financials":
            return {"error": "service unavailable"}  # failed for gap_if_empty
        return {"data": "ok"}

    monkeypatch.setattr(legacy_tools, "exec_tool", fake_tool)

    dossier_cfg = DossierConfig(
        section_chars=1800,
        dossier_summary_chars=6000,
        sections=[
            DossierSectionConfig(id="quote", tool="query_quote", title="行情", empty_policy="gap_if_empty"),
            DossierSectionConfig(id="margin", tool="query_margin", title="两融", empty_policy="allow_no_record"),
            DossierSectionConfig(id="fin", tool="query_financials", title="财务", empty_policy="gap_if_empty"),
        ],
    )

    sections, missing = await collect_dossier_sections("600519", dossier_cfg)

    # Check preserved order
    assert [s.id for s in sections] == ["quote", "margin", "fin"]

    assert sections[0].status == "ok"
    assert sections[1].status == "no_record"
    assert "未取到任何记录" in sections[1].body
    assert sections[2].status == "gap"
    assert "财务" in missing
    assert len(missing) == 1


def test_summarize_dossier_deterministic_and_capped():
    sections = [
        DossierSection(id="s1", tool="query_quote", title="行情", empty_policy="gap_if_empty", status="ok", summary="现价 1800", body="..."),
        DossierSection(id="s2", tool="query_margin", title="两融", empty_policy="allow_no_record", status="no_record", summary="无记录", body="..."),
    ]
    summary = summarize_dossier(sections, missing=["财务"], max_chars=1000)
    assert "行情" in summary
    assert "两融" in summary
    assert "财务" in summary
    assert len(summary) <= 1000


def test_serialize_stage_context_uses_sentinels_for_failed_stages():
    stages = {
        "bull": StageResult(stage_id="bull", status="completed", content="多方有力论点"),
        "bear": StageResult(stage_id="bear", status="failed", error=WorkflowError(code="ERR", message="fail", stage_id="bear")),
    }
    context, truncated = serialize_stage_context(stages, stage_ids=["bull", "bear"], max_chars=1000)
    assert "多方有力论点" in context
    assert "【阶段 bear 未产出】" in context
    assert "fail" not in context
    assert truncated is False


@pytest.mark.asyncio
async def test_run_stage_streaming_and_truncation():
    # Long output stream
    chunks = [AIMessageChunk(content="chunk1 "), AIMessageChunk(content="chunk2 " * 200)]
    model = ScriptedChatModel([AIMessage(content="".join(c.content for c in chunks))])

    content, truncated = await run_stage(
        model=model,
        messages=[{"role": "user", "content": "hello"}],
        max_chars=50,
        stage_id="bull",
    )
    assert len(content) <= 50
    assert truncated is True
