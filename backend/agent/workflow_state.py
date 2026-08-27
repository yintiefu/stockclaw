"""工作流图状态定义、数据模型与不可变 Reducers。

定义工作流图（Debate / Reflection / DailyReview / NewsDigest）的强类型状态、
Pydantic 数据契约、错误哨兵以及不可变归约函数。

v2 契约：阶段权威正文只住在 messages 通道（add_messages 按 id 归并），
StageResult 退化为状态机 + message_id 指针；客户端不再回写工作流状态。
"""
from __future__ import annotations

from typing import Annotated, Any, Literal
from langchain_core.messages import AnyMessage
from langgraph.graph import add_messages
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
    """单个阶段执行结果与终态（状态机）。权威正文在 WorkflowState.messages，
    阶段只持 message_id 指针；未完成阶段禁止持指针。"""
    model_config = ConfigDict(extra="forbid")

    id: str = Field(validation_alias=AliasChoices("id", "stage_id"))
    status: StageStatus
    message_id: str | None = None
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
    def validate_message_integrity(self) -> "StageResult":
        if self.status != "completed" and self.message_id is not None:
            raise ValueError("未完成阶段不可持有正文指针")
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
    old: dict[str, StageResult] | None,
    new: dict[str, StageResult] | None,
) -> dict[str, StageResult]:
    """不可变合并阶段结果字典。客户端不再回写状态，无需 dict 规约。"""
    result: dict[str, StageResult] = {}
    for sid, value in {**(old or {}), **(new or {})}.items():
        if isinstance(value, StageResult):
            result[sid] = value
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


def message_text(message: AnyMessage | dict[str, Any]) -> str:
    """消息正文 → 纯文本：content 为字符串直取，块数组只拼 text（reasoning 是思考过程）。"""
    content = message.get("content") if isinstance(message, dict) else getattr(message, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.get("text", "") if isinstance(block, dict) else ""
            for block in content
        )
    return ""


def stage_content(
    messages: list[AnyMessage | dict[str, Any]] | None,
    stage: StageResult | None,
) -> str | None:
    """按指针取阶段权威正文；找不到或无指针返回 None。"""
    if stage is None or stage.message_id is None:
        return None
    for message in messages or []:
        mid = message.get("id") if isinstance(message, dict) else getattr(message, "id", None)
        if mid == stage.message_id:
            return message_text(message)
    return None


def format_stage_context(
    stage_id: str,
    stage_result: StageResult | None,
    messages: list[AnyMessage | dict[str, Any]] | None = None,
) -> str:
    """序列化阶段输出供后续阶段作为上下文；未产出/失败阶段返回占位哨兵。"""
    text = stage_content(messages, stage_result)
    if text is None or not text:
        return stage_unproduced_sentinel(stage_id)
    return text


class WorkflowState(TypedDict, total=False):
    """LangGraph 工作流强类型状态契约。"""
    workflow_id: str
    workflow_status: WorkflowStatus
    started_at: str | None
    completed_at: str | None
    config_version: int
    input: dict[str, Any]
    # 重试控制位：独立顶层通道（LastValue）。绝不复用 input —— 否则 resume 会
    # 覆盖掉原始 code/source 快照，二次运行只剩 resume 标记。
    resume: bool
    variant: str | None
    dossier: DossierResult | None
    stages: Annotated[dict[str, StageResult], merge_stage_results]
    messages: Annotated[list[AnyMessage], add_messages]
    current_stage: str | None
    result_summary: str | None
    errors: Annotated[list[WorkflowError], append_workflow_errors]
