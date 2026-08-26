"""工作流固定自定义事件契约与单调发射器。"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any, Literal, Union

from langchain_core.callbacks.manager import adispatch_custom_event
from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer
from langgraph.runtime import get_runtime
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter


def utc_now() -> str:
    """生成 UTC ISO-8601 时间戳。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


NonEmptyString = Annotated[str, StringConstraints(min_length=1)]
EmittedAt = Annotated[str, StringConstraints(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")]
DossierSectionStatus = Literal["completed", "no_record", "gap", "failed"]


class _EventBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    workflow_id: NonEmptyString
    run_id: NonEmptyString
    seq: int = Field(ge=1)
    emitted_at: EmittedAt


class WorkflowStatusEvent(_EventBase):
    type: Literal["workflow.status"] = "workflow.status"
    status: Literal["pending", "running", "completed", "partial", "failed", "cancelled", "interrupted"]
    message: str


class DossierProgressEvent(_EventBase):
    type: Literal["dossier.progress"] = "dossier.progress"
    section_id: NonEmptyString
    section_status: DossierSectionStatus
    completed: int = Field(ge=0)
    total: int = Field(ge=0)


class DossierReadyEvent(_EventBase):
    type: Literal["dossier.ready"] = "dossier.ready"
    completed: int = Field(ge=0)
    missing: list[str]
    has_substantive_data: bool


class StageStartedEvent(_EventBase):
    type: Literal["stage.started"] = "stage.started"
    stage_id: NonEmptyString
    label: NonEmptyString


class StageDeltaEvent(_EventBase):
    type: Literal["stage.delta"] = "stage.delta"
    stage_id: NonEmptyString
    delta: str


class StageCompletedEvent(_EventBase):
    type: Literal["stage.completed"] = "stage.completed"
    stage_id: NonEmptyString
    truncated: bool


class StageFailedEvent(_EventBase):
    type: Literal["stage.failed"] = "stage.failed"
    stage_id: NonEmptyString
    error_code: NonEmptyString
    message: NonEmptyString
    retryable: bool


class WorkflowCompletedEvent(_EventBase):
    type: Literal["workflow.completed"] = "workflow.completed"
    status: Literal["completed", "partial"]


class WorkflowFailedEvent(_EventBase):
    type: Literal["workflow.failed"] = "workflow.failed"
    error_code: NonEmptyString
    message: NonEmptyString
    retryable: bool


WorkflowEventUnion = Annotated[
    Union[
        WorkflowStatusEvent,
        DossierProgressEvent,
        DossierReadyEvent,
        StageStartedEvent,
        StageDeltaEvent,
        StageCompletedEvent,
        StageFailedEvent,
        WorkflowCompletedEvent,
        WorkflowFailedEvent,
    ],
    Field(discriminator="type"),
]

_EVENT_ADAPTER = TypeAdapter(WorkflowEventUnion)


def validate_workflow_event(raw: dict[str, Any]) -> WorkflowEventUnion:
    """使用固定 discriminated union 校验工作流事件。"""
    return _EVENT_ADAPTER.validate_python(raw)


def _read_run_id(config: RunnableConfig | None) -> str | None:
    try:
        execution_info = get_runtime().execution_info
    except RuntimeError:
        execution_info = None
    if execution_info is not None and execution_info.run_id:
        return str(execution_info.run_id)

    if not config or not hasattr(config, "get"):
        return None
    configurable = config.get("configurable", {})
    if hasattr(configurable, "get"):
        value = configurable.get("run_id")
        if value is not None and str(value).strip():
            return str(value)
    for container in (config, config.get("metadata", {})):
        if not hasattr(container, "get"):
            continue
        value = container.get("run_id")
        if value is not None and str(value).strip():
            return str(value)
    return None


class WorkflowEventEmitter:
    """为一个 run 分配连续序号并发射类型化事件。"""

    def __init__(
        self,
        workflow_id: str,
        run_id: str,
        starting_seq: int,
        config: RunnableConfig | None = None,
    ) -> None:
        self.workflow_id = workflow_id
        self.run_id = run_id
        self._seq = starting_seq
        self._config = config
        self._dispatch_fn = None

    @classmethod
    def from_config(
        cls,
        workflow_id: str,
        starting_seq: int,
        config: RunnableConfig | None = None,
    ) -> "WorkflowEventEmitter":
        run_id = _read_run_id(config)
        if run_id is None:
            raise RuntimeError("LangGraph run_id 缺失")
        return cls(workflow_id, run_id, starting_seq, config)

    @property
    def last_seq(self) -> int:
        return self._seq

    async def emit(self, event_type: str, **payload: Any) -> None:
        next_seq = self._seq + 1
        event = validate_workflow_event({
            "type": event_type,
            "workflow_id": self.workflow_id,
            "run_id": self.run_id,
            "seq": next_seq,
            "emitted_at": utc_now(),
            **payload,
        })
        self._seq = next_seq
        serialized = event.model_dump(mode="json")

        if self._dispatch_fn is not None:
            self._dispatch_fn("workflow", serialized, self._config)
            return
        try:
            writer = get_stream_writer()
        except RuntimeError:
            writer = None
        if writer is not None:
            writer(serialized)
            return
        if self._config is not None:
            await adispatch_custom_event("workflow", serialized, config=self._config)
