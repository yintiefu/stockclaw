"""工作流自定义事件模式与单调自增发射器。

定义 9 类固定自定义事件的 Pydantic Discriminated Union，与前端 SDK 保持逐字段严格一致；
提供 WorkflowEventEmitter 保证单 run 内事件序号 seq 严格递增且不分叉。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any, Literal, Union
from langchain_core.callbacks.manager import adispatch_custom_event
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from agent.workflow_state import WorkflowError


def utc_now() -> str:
    """生成 UTC ISO-8601 时间戳。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class _EventBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    workflow_id: str
    run_id: str
    seq: int
    emitted_at: str = Field(default_factory=utc_now)


class WorkflowStartedEvent(_EventBase):
    type: Literal["workflow_started"] = "workflow_started"
    workflow_type: str
    input: dict[str, Any]
    variant: str | None = None


class DossierProgressEvent(_EventBase):
    type: Literal["dossier_progress"] = "dossier_progress"
    title: str
    section_id: str
    tool: str
    status: Literal["ok", "no_record", "gap"]
    loaded: int
    total: int


class DossierCompletedEvent(_EventBase):
    type: Literal["dossier_completed"] = "dossier_completed"
    section_count: int
    missing_count: int


class StageStartedEvent(_EventBase):
    type: Literal["stage_started"] = "stage_started"
    stage_id: str


class StageDeltaEvent(_EventBase):
    type: Literal["stage_delta"] = "stage_delta"
    stage_id: str
    delta: str


class StageCompletedEvent(_EventBase):
    type: Literal["stage_completed"] = "stage_completed"
    stage_id: str
    truncated: bool = False


class StageFailedEvent(_EventBase):
    type: Literal["stage_failed"] = "stage_failed"
    stage_id: str
    error: WorkflowError


class WorkflowCompletedEvent(_EventBase):
    type: Literal["workflow_completed"] = "workflow_completed"
    workflow_type: str
    result_summary: str


class WorkflowFailedEvent(_EventBase):
    type: Literal["workflow_failed"] = "workflow_failed"
    workflow_type: str
    error: WorkflowError


WorkflowEventUnion = Annotated[
    Union[
        WorkflowStartedEvent,
        DossierProgressEvent,
        DossierCompletedEvent,
        StageStartedEvent,
        StageDeltaEvent,
        StageCompletedEvent,
        StageFailedEvent,
        WorkflowCompletedEvent,
        WorkflowFailedEvent,
    ],
    Field(discriminator="type"),
]

from langgraph.config import get_stream_writer

_EVENT_ADAPTER = TypeAdapter(WorkflowEventUnion)


def validate_workflow_event(raw: dict[str, Any]) -> WorkflowEventUnion:
    """使用 Discriminated Union 校验并实例化工作流事件。"""
    return _EVENT_ADAPTER.validate_python(raw)


class WorkflowEventEmitter:
    """每个 Graph Node 内的单调自增事件发射器。"""

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
        run_id = None
        if config and hasattr(config, "get"):
            top_run_id = config.get("run_id")
            if top_run_id is not None:
                run_id = str(top_run_id)
            if not run_id:
                configurable = config.get("configurable", {})
                if isinstance(configurable, dict) or hasattr(configurable, "get"):
                    c_run_id = configurable.get("run_id")
                    if c_run_id is not None:
                        run_id = str(c_run_id)
            if not run_id:
                metadata = config.get("metadata", {})
                if isinstance(metadata, dict) or hasattr(metadata, "get"):
                    m_run_id = metadata.get("run_id")
                    if m_run_id is not None:
                        run_id = str(m_run_id)
        if not isinstance(run_id, str) or not run_id:
            run_id = "local-run"
        return cls(workflow_id, run_id, starting_seq, config)

    @property
    def last_seq(self) -> int:
        return self._seq

    async def emit(self, event_type: str, **payload: Any) -> None:
        self._seq += 1
        data = {
            "type": event_type,
            "workflow_id": self.workflow_id,
            "run_id": self.run_id,
            "seq": self._seq,
            "emitted_at": utc_now(),
            **payload,
        }
        event = validate_workflow_event(data)
        serialized = event.model_dump(mode="json")
        try:
            writer = get_stream_writer()
            if writer:
                writer(serialized)
        except Exception:
            pass

        if self._dispatch_fn:
            self._dispatch_fn("workflow", serialized, self._config)
        elif self._config:
            try:
                await adispatch_custom_event("workflow", serialized, config=self._config)
            except Exception:
                pass
