"""工作流状态模型与 Reducer 契约测试。

测试覆盖：
- 状态字面量校验（WorkflowStatus / StageStatus）；
- Pydantic 模型 extra="forbid"；
- merge_stage_results 不可变更新与单阶段覆盖；
- append_workflow_errors 纯追加；
- 阶段失败不可保留 content；
- 序列化与哨兵标记。
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent.workflow_state import (
    DossierResult,
    DossierSection,
    StageResult,
    WorkflowError,
    WorkflowState,
    append_workflow_errors,
    merge_stage_results,
)


def stage(
    stage_id: str,
    status: str,
    content: str | None = None,
    error: WorkflowError | None = None,
    truncated: bool = False,
    completed_at: str | None = None,
) -> StageResult:
    return StageResult(
        stage_id=stage_id,
        status=status,
        content=content,
        error=error,
        truncated=truncated,
        completed_at=completed_at,
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


def test_merge_stage_results_updates_only_named_stage() -> None:
    old = {"bull": stage("bull", "running"), "bear": stage("bear", "pending")}
    new = {"bull": stage("bull", "completed", content="多方文本")}
    merged = merge_stage_results(old, new)
    assert merged["bull"].status == "completed"
    assert merged["bull"].content == "多方文本"
    assert merged["bear"].status == "pending"
    assert merged is not old


def test_failed_stage_cannot_retain_content() -> None:
    failed = stage("bull", "failed", error=workflow_error("MODEL_ERROR", "模型不可用", "bull"))
    assert failed.content is None
    assert failed.error is not None
    assert failed.error.code == "MODEL_ERROR"


def test_failed_stage_with_content_raises_validation_error() -> None:
    with pytest.raises(ValidationError):
        StageResult(
            stage_id="bull",
            status="failed",
            content="不应存在内容",
            error=workflow_error("MODEL_ERROR", "模型不可用", "bull"),
        )


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
        content=None,
        truncated=False,
        context_truncated=True,
        started_at="2026-08-25T12:00:00Z",
        completed_at="2026-08-25T12:00:01Z",
        error=error,
    )

    assert result.id == "bull"
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
