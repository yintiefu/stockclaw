"""工作流状态模型与 Reducer 契约测试。

测试覆盖：
- 状态字面量校验（WorkflowStatus / StageStatus）；
- Pydantic 模型 extra="forbid"；
- merge_stage_results 不可变合并（纯类型化，无 dict 规约）；
- append_workflow_errors 纯追加；
- 未完成阶段禁止持正文指针；
- 按指针解析阶段正文（messages 通道）与哨兵标记。
"""
from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage
from pydantic import ValidationError

from agent.workflow_state import (
    DossierResult,
    DossierSection,
    StageResult,
    WorkflowError,
    append_workflow_errors,
    format_stage_context,
    merge_stage_results,
    message_text,
    stage_content,
    stage_unproduced_sentinel,
)


def workflow_error(
    code: str,
    message: str,
    stage_id: str | None = None,
) -> WorkflowError:
    return WorkflowError(
        code=code,
        message=message,
        stage_id=stage_id,
    )


def test_stage_result_rejects_pointer_when_not_completed() -> None:
    with pytest.raises(ValidationError):
        StageResult(id="bull", status="failed", message_id="m1")


def test_merge_stage_results_is_plain_typed_merge() -> None:
    old = {"bull": StageResult(id="bull", status="completed", message_id="m1")}
    new = {"bear": StageResult(id="bear", status="running")}
    merged = merge_stage_results(old, new)
    assert set(merged) == {"bull", "bear"}
    assert merged["bear"].status == "running"


def test_stage_content_resolves_pointer_over_objects_and_dicts() -> None:
    messages = [
        AIMessage(id="m1", content="多方观点正文"),
        {"id": "m2", "content": [{"type": "reasoning", "reasoning": "思考"}, {"type": "text", "text": "裁判"}]},
    ]
    assert stage_content(messages, StageResult(id="bull", status="completed", message_id="m1")) == "多方观点正文"
    assert stage_content(messages, StageResult(id="referee", status="completed", message_id="m2")) == "裁判"
    assert stage_content(messages, StageResult(id="x", status="completed")) is None


def test_message_text_joins_text_blocks_only() -> None:
    assert message_text({"content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}) == "ab"
    assert message_text({"content": "裸字符串"}) == "裸字符串"


def test_format_stage_context_uses_pointer_and_sentinel() -> None:
    messages = [AIMessage(id="m1", content="正文")]
    done = StageResult(id="bull", status="completed", message_id="m1")
    pending = StageResult(id="bear", status="running")
    assert format_stage_context("bull", done, messages) == "正文"
    assert format_stage_context("bear", pending, messages) == stage_unproduced_sentinel("bear")


def test_append_workflow_errors_appends_immutably() -> None:
    err1 = workflow_error("CODE_1", "错误1")
    err2 = workflow_error("CODE_2", "错误2")
    old = [err1]
    new = [err2]
    res = append_workflow_errors(old, new)
    assert res == [err1, err2]
    assert res is not old


def test_extra_fields_are_forbidden() -> None:
    with pytest.raises(ValidationError) as workflow_exc:
        WorkflowError(code="ERR", message="msg", extra_field="forbidden")  # type: ignore
    assert workflow_exc.value.errors()[0]["type"] == "extra_forbidden"

    with pytest.raises(ValidationError) as stage_exc:
        StageResult(stage_id="s1", status="completed", extra_field="forbidden")  # type: ignore
    assert stage_exc.value.errors()[0]["type"] == "extra_forbidden"

    with pytest.raises(ValidationError) as dossier_exc:
        DossierSection(
            id="quote", tool="query_quote", title="行情",
            empty_policy="gap_if_empty", status="completed", summary="ok", body="{}",
            extra="forbidden",  # type: ignore
        )
    assert dossier_exc.value.errors()[0]["type"] == "extra_forbidden"


def test_dossier_section_and_result_model() -> None:
    sec = DossierSection(
        id="quote",
        tool="query_quote",
        title="行情",
        empty_policy="gap_if_empty",
        status="completed",
        summary="最新价 100",
        body="{\"price\": 100}",
    )
    res = DossierResult(sections=[sec], summary="底稿摘要", missing=[], has_substantive_data=True)
    assert len(res.sections) == 1
    assert res.summary == "底稿摘要"
    assert res.missing == []


def test_state_models_expose_the_stable_design_contract() -> None:
    error = WorkflowError(
        code="MODEL_ERROR",
        message="模型不可用",
        retryable=True,
        stage_id="bull",
    )
    result = StageResult(
        id="bull",
        status="failed",
        truncated=False,
        context_truncated=True,
        started_at="2026-08-25T12:00:00Z",
        completed_at="2026-08-25T12:00:01Z",
        error=error,
    )

    assert result.id == "bull"
    assert result.stage_id == "bull"
    assert result.context_truncated is True
    assert result.started_at == "2026-08-25T12:00:00Z"
    assert result.error is not None and result.error.retryable is True


@pytest.mark.parametrize("status", ["completed", "no_record", "gap", "failed"])
def test_dossier_sections_distinguish_all_design_statuses(status: str) -> None:
    section = DossierSection(
        id="quote",
        tool="query_quote",
        title="行情",
        empty_policy="gap_if_empty",
        status=status,
        summary="摘要",
        body="{}",
    )
    dossier = DossierResult(
        sections=[section],
        summary="底稿摘要",
        missing=[] if status == "completed" else ["行情"],
        has_substantive_data=status == "completed",
    )

    assert dossier.sections[0].status == status
    assert dossier.has_substantive_data is (status == "completed")
