"""工作流图状态定义、数据模型与不可变 Reducers。

定义工作流图（Debate / Reflection / DailyReview / NewsDigest）的强类型状态、
Pydantic 数据契约、错误哨兵以及不可变归约函数。
"""
from __future__ import annotations

from typing import Annotated, Any, Literal
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator
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
    retryable: bool = False
    stage_id: str | None = None


class StageResult(BaseModel):
    """单个阶段执行结果与终态。"""
    model_config = ConfigDict(extra="forbid")

    id: str = Field(validation_alias=AliasChoices("id", "stage_id"))
    status: StageStatus
    content: str | None = None
    truncated: bool = False
    context_truncated: bool = False
    started_at: str | None = None
    completed_at: str | None = None
    error: WorkflowError | None = None

    @property
    def stage_id(self) -> str:
        """兼容旧的 Python 读取方式；序列化契约始终使用 id。"""
        return self.id

    @model_validator(mode="after")
    def validate_content_integrity(self) -> "StageResult":
        if self.status == "failed" and self.content is not None:
            raise ValueError("失败阶段不可保留 content")
        return self


class DossierSection(BaseModel):
    """客观投研底稿单个小节。"""
    model_config = ConfigDict(extra="forbid")

    id: str
    tool: str
    title: str
    empty_policy: Literal["gap_if_empty", "allow_no_record"]
    status: Literal["completed", "no_record", "gap", "failed"]
    summary: str
    body: str
    error: str | None = None


class DossierResult(BaseModel):
    """完整投研底稿结果。"""
    model_config = ConfigDict(extra="forbid")

    sections: list[DossierSection]
    summary: str
    missing: list[str] = Field(default_factory=list)
    has_substantive_data: bool


def merge_stage_results(
    old: dict[str, StageResult | dict[str, Any]] | None,
    new: dict[str, StageResult | dict[str, Any]] | None,
) -> dict[str, StageResult]:
    """不可变合并阶段结果字典。

    客户端 updateState 取消/审计补丁会以 JSON dict 形状写入 stages 通道；
    在 reducer 内统一规整回 StageResult，保证通道里只剩强类型对象，
    恢复 run 的节点按属性读取不会遇到 AttributeError。
    """
    result: dict[str, StageResult] = {}
    for sid, value in {**(old or {}), **(new or {})}.items():
        stage = coerce_stage_result(value, sid)
        if stage is not None:
            result[sid] = stage
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


def coerce_stage_result(
    value: "StageResult | dict[str, Any] | None",
    stage_id: str = "",
) -> "StageResult | None":
    """把客户端 updateState 补丁写入的 JSON dict 阶段规整回 StageResult。

    前端取消/中断回写走 HTTP updateState，stages 通道里会混入 dict 形状；
    恢复 run 的节点按属性读取阶段，必须在入口统一规整，而不是 AttributeError。
    """
    if value is None or isinstance(value, StageResult):
        return value
    if isinstance(value, dict):
        allowed = set(StageResult.model_fields)
        payload = {k: v for k, v in value.items() if k in allowed}
        if stage_id and not payload.get("id"):
            payload["id"] = stage_id
        try:
            return StageResult.model_validate(payload)
        except Exception:
            return None
    return None


def coerce_stage_map(stages: "dict[str, Any] | None") -> "dict[str, StageResult]":
    """整个 stages 通道的规整视图；无法解析的阶段按缺失处理（上下文给未产出占位符）。"""
    coerced: dict[str, StageResult] = {}
    for sid, value in (stages or {}).items():
        stage = coerce_stage_result(value, sid)
        if stage is not None:
            coerced[sid] = stage
    return coerced


def format_stage_context(stage_id: str, stage_result: StageResult | dict[str, Any] | None) -> str:
    """序列化阶段输出供后续阶段作为上下文，失败/未产出阶段返回占位符。"""
    stage_result = coerce_stage_result(stage_result, stage_id)
    if stage_result is None or stage_result.status != "completed" or not stage_result.content:
        return stage_unproduced_sentinel(stage_id)
    return stage_result.content


class WorkflowState(TypedDict, total=False):
    """LangGraph 工作流强类型状态契约。"""
    workflow_id: str
    workflow_status: WorkflowStatus
    started_at: str | None
    completed_at: str | None
    config_version: int
    input: dict[str, Any]
    variant: str | None
    dossier: DossierResult | None
    stages: Annotated[dict[str, StageResult], merge_stage_results]
    current_stage: str | None
    result: str | None
    result_summary: str | None
    errors: Annotated[list[WorkflowError], append_workflow_errors]
    event_seq: int
