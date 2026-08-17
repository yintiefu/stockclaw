from __future__ import annotations

from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, SecretStr, field_validator


class ModelRef(BaseModel):
    """脱敏的模型引用 —— 只含 provider / baseURL / model，绝不含 API key。"""

    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")

    provider: str = Field(min_length=1)
    base_url: str = Field(
        min_length=1,
        validation_alias=AliasChoices("baseURL", "base_url"),
        serialization_alias="baseURL",
    )
    model: str = Field(min_length=1)


class RunSecrets(BaseModel):
    """请求级密钥容器 —— 只存在于请求任务内，绝不写入事件/检查点/配置。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model_api_key: SecretStr


class RuntimeForwardedProps(BaseModel):
    """forwardedProps.runtime 的服务端视图。

    `command` 是 AG-UI 顶层转发属性，不会出现在 runtime 内。
    `thread_revision` 必填：前端迁移后所有 run 形状都携带权威 revision。
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    model: ModelRef
    thread_revision: int = Field(ge=0, validation_alias="threadRevision")
    retry_of: str | None = Field(default=None, validation_alias="retryOf")


SCHEMA_VERSION = 1

RunStatus = Literal[
    "running", "awaiting_approval", "completed", "failed", "cancelled", "interrupted"
]


class RunPhase(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: RunStatus


class AgentMessage(BaseModel):
    """线程内的持久化消息；partial/pending_interrupt 的内容不会进入后续模型输入。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    role: Literal["user", "assistant", "tool"]
    content: Any
    partial: bool = False
    pending_interrupt: bool = False
    interrupts: list[dict[str, Any]] = Field(default_factory=list)
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    tool_call_id: str | None = None
    created_at: str | None = None


class RunSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    status: RunStatus
    updated_at: str
    retry_of: str | None = None


class ThreadDocument(BaseModel):
    """权威线程文档 —— 唯一可信的消息历史来源。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = SCHEMA_VERSION
    id: str
    title: str
    created_at: str
    updated_at: str
    revision: int = Field(ge=0)
    selected_skills: list[str] = Field(default_factory=list)
    messages: list[AgentMessage] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    last_run: RunSummary | None = None

    @classmethod
    def new(cls, thread_id: str, title: str, *, now: str) -> "ThreadDocument":
        return cls(
            id=thread_id,
            title=title,
            created_at=now,
            updated_at=now,
            revision=0,
        )

    def model_history(self) -> list[AgentMessage]:
        """进入后续模型输入的完整消息：排除 partial 与 pending_interrupt。"""
        return [m for m in self.messages if not m.partial and not m.pending_interrupt]


TokenStatus = Literal["available", "partial", "unavailable"]


class PolicySnapshot(BaseModel):
    """run 级不可变治理快照 —— 落盘进 RunDocument，绝不含密钥。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    policy_revision: int = Field(ge=0)
    max_model_calls: int = Field(ge=1, le=32)
    max_tool_calls: int = Field(ge=1, le=64)
    tool_timeout_seconds: int = Field(ge=5, le=120)
    max_active_seconds: int = Field(ge=30, le=1800)
    max_context_chars: int = Field(ge=16000, le=500000)


class ContextTruncation(BaseModel):
    """上下文裁剪遥测 —— 只记录事实，不保留被裁内容。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    occurred: bool = False
    original_chars: int | None = Field(default=None, ge=0)
    retained_chars: int | None = Field(default=None, ge=0)
    removed_turns: int | None = Field(default=None, ge=0)


class RunUsage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    model_calls: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    token_status: TokenStatus = "unavailable"


class RunDocument(BaseModel):
    """产品级运行文档；密钥只存在于请求内，绝不进入本结构。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = SCHEMA_VERSION
    id: str
    thread_id: str
    protocol_run_ids: list[str]
    trigger_message_id: str
    retry_of: str | None = None
    status: RunStatus
    started_at: str
    updated_at: str
    ended_at: str | None = None
    elapsed_ms: int = 0
    active_elapsed_ms: int = 0
    approval_wait_ms: int = 0
    budget_snapshot: PolicySnapshot | dict[str, Any] = Field(default_factory=dict)
    control_revision: int = Field(default=0, ge=0)
    context_truncation: ContextTruncation = Field(default_factory=ContextTruncation)
    model_ref: ModelRef
    history_head_id: str | None = None
    usage: RunUsage = Field(default_factory=RunUsage)
    tool_summaries: list[dict[str, Any]] = Field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None

    @field_validator("budget_snapshot")
    @classmethod
    def _budget_snapshot_dict_only_when_legacy_empty(cls, value: Any) -> Any:
        # dict 形态只保留给 1C 及更早的历史文档（恰好为空）；新数据一律是 PolicySnapshot。
        if isinstance(value, dict) and value:
            raise ValueError("budget_snapshot 只能是空 dict（历史文档）或 PolicySnapshot")
        return value

    @classmethod
    def start(
        cls,
        *,
        run_id: str,
        thread_id: str,
        protocol_run_id: str,
        model_ref: ModelRef,
        trigger_message_id: str,
        history_head_id: str | None,
        now: str,
        retry_of: str | None = None,
    ) -> "RunDocument":
        return cls(
            id=run_id,
            thread_id=thread_id,
            protocol_run_ids=[protocol_run_id],
            trigger_message_id=trigger_message_id,
            retry_of=retry_of,
            status="running",
            started_at=now,
            updated_at=now,
            model_ref=model_ref,
            history_head_id=history_head_id,
        )
