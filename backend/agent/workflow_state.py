"""工作流图状态定义、数据模型与不可变 Reducers。

定义工作流图（Debate / Reflection / DailyReview / NewsDigest）的强类型状态、
Pydantic 数据契约、错误哨兵以及不可变归约函数。
"""
from __future__ import annotations

from typing import Annotated, Any, Literal
from pydantic import BaseModel, ConfigDict, model_validator
from typing_extensions import TypedDict

WorkflowStatus = Literal[
    "pending", "running", "completed", "partial", "failed", "cancelled", "interrupted",
]

StageStatus = Literal[
    "pending", "running", "completed", "failed", "skipped", "cancelled", "interrupted",
]


class WorkflowError(BaseModel):
    """工作流执行错误数据结构（严格白名单字段，禁止透传任意未脱敏 details）。"""
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    stage_id: str | None = None


class StageResult(BaseModel):
    """单个阶段执行结果与终态。"""
    model_config = ConfigDict(extra="forbid")

    stage_id: str
    status: StageStatus
    content: str | None = None
    error: WorkflowError | None = None
    truncated: bool = False
    completed_at: str | None = None

    @model_validator(mode="after")
    def validate_content_integrity(self) -> "StageResult":
        if self.status == "failed" and self.content is not None:
            raise ValueError(f"阶段 {self.stage_id} 处于 failed 状态，不可保留 content")
        return self


class DossierSection(BaseModel):
    """客观投研底稿单个小节。"""
    model_config = ConfigDict(extra="forbid")

    id: str
    tool: str
    title: str
    empty_policy: Literal["gap_if_empty", "allow_no_record"]
    status: Literal["ok", "no_record", "gap"]
    summary: str
    body: str
    error: str | None = None


class DossierResult(BaseModel):
    """完整投研底稿结果。"""
    model_config = ConfigDict(extra="forbid")

    sections: list[DossierSection]
    summary: str
    missing: list[str] = []


def merge_stage_results(
    old: dict[str, StageResult] | None,
    new: dict[str, StageResult] | None,
) -> dict[str, StageResult]:
    """不可变合并阶段结果字典。"""
    result = dict(old or {})
    if new:
        result.update(new)
    return result


def append_workflow_errors(
    old: list[WorkflowError] | None,
    new: list[WorkflowError] | WorkflowError | None,
) -> list[WorkflowError]:
    """纯追加工作流错误列表。"""
    result = list(old or [])
    if new is None:
        return result
    if isinstance(new, list):
        result.extend(new)
    else:
        result.append(new)
    return result


def stage_unproduced_sentinel(stage_id: str) -> str:
    """阶段未成功产出时的上下文占位符。"""
    return f"【阶段 {stage_id} 未产出】"


def format_stage_context(stage_id: str, stage_result: StageResult | None) -> str:
    """序列化阶段输出供后续阶段作为上下文，失败/未产出阶段返回占位符。"""
    if stage_result is None or stage_result.status != "completed" or not stage_result.content:
        return stage_unproduced_sentinel(stage_id)
    return stage_result.content


class WorkflowState(TypedDict, total=False):
    """LangGraph 工作流强类型状态契约。"""
    workflow_id: str
    workflow_type: str
    workflow_status: WorkflowStatus
    config_version: int
    input: dict[str, Any]
    variant: str | None
    dossier: DossierResult | None
    stages: Annotated[dict[str, StageResult], merge_stage_results]
    current_stage: str | None
    result: str | None
    result_summary: str | None
    errors: Annotated[list[WorkflowError], append_workflow_errors]
    context_truncated: bool
    event_seq: int
