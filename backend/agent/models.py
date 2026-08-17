from __future__ import annotations

from typing import Any, Literal

import math

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator


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


# ---- 1D Artifact 线模型（spec §14） ----


class MarkdownContent(BaseModel):
    """纯 Markdown 字符串；渲染层禁用 raw HTML。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    markdown: str


class TableColumn(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
    label: str = Field(min_length=1, max_length=100)


class TableContent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    columns: list[TableColumn] = Field(min_length=1, max_length=50)
    rows: list[dict[str, Any]] = Field(default_factory=list, max_length=5000)

    @model_validator(mode="after")
    def validate_rows(self):
        expected = [column.key for column in self.columns]
        if len({column.key for column in self.columns}) != len(expected):
            raise ValueError("table column key 必须唯一")
        for row in self.rows:
            if set(row) != set(expected):
                raise ValueError("table row 必须恰好包含全部已声明 key（空单元格用 null）")
            for value in row.values():
                if not _is_json_scalar(value):
                    raise ValueError("table cell 只能是 string/number/boolean/null")
        return self


def _is_json_scalar(value: Any) -> bool:
    if value is None or isinstance(value, (str, bool)):
        return True
    if isinstance(value, int):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    return False


def _json_node_stats(value: Any, depth: int) -> tuple[int, int]:
    """返回 (节点总数, 最大深度)；根节点深度为 1。"""
    count = 1
    max_depth = depth
    if isinstance(value, dict):
        for item in value.values():
            child_count, child_depth = _json_node_stats(item, depth + 1)
            count += child_count
            max_depth = max(max_depth, child_depth)
    elif isinstance(value, list):
        for item in value:
            child_count, child_depth = _json_node_stats(item, depth + 1)
            count += child_count
            max_depth = max(max_depth, child_depth)
    else:
        if not _is_json_scalar(value):
            raise ValueError("JSON 内容包含非 JSON 类型")
    return count, max_depth


class JsonContent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    value: Any

    @model_validator(mode="after")
    def validate_value(self):
        count, depth = _json_node_stats(self.value, 1)
        if depth > 32:
            raise ValueError("JSON 内容最大嵌套深度为 32")
        if count > 50_000:
            raise ValueError("JSON 内容最多 50,000 个节点")
        return self


class SourcesContentItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str = Field(min_length=1)
    note: str | None = Field(default=None, max_length=2000)


class SourcesContent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    items: list[SourcesContentItem] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def validate_unique(self):
        if len({item.source_id for item in self.items}) != len(self.items):
            raise ValueError("sources content 的 source_id 必须唯一")
        return self


ArtifactContent = MarkdownContent | TableContent | JsonContent | SourcesContent


class ArtifactDocument(BaseModel):
    """不可变 Artifact：创建后不可修改，版本链经 parent_artifact_id 线性延伸。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    id: str = Field(min_length=1, max_length=128)
    thread_id: str = Field(min_length=1, max_length=128)
    run_id: str = Field(min_length=1, max_length=128)
    type: Literal["markdown", "table", "json", "sources"]
    title: str = Field(min_length=1, max_length=200)
    created_at: str = Field(min_length=1)
    parent_artifact_id: str | None = None
    content: ArtifactContent
    source_ids: list[str] = Field(default_factory=list, max_length=200)

    @field_validator("title")
    @classmethod
    def _trim_title(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("title 不能为空白")
        return trimmed

    @model_validator(mode="after")
    def _content_matches_type(self):
        expected = {
            "markdown": MarkdownContent, "table": TableContent,
            "json": JsonContent, "sources": SourcesContent,
        }
        if not isinstance(self.content, expected[self.type]):
            raise ValueError(f"content 形状与 type={self.type} 不匹配")
        if len(set(self.source_ids)) != len(self.source_ids):
            raise ValueError("source_ids 必须有序去重")
        if isinstance(self.content, SourcesContent):
            declared = set(self.source_ids)
            for item in self.content.items:
                if item.source_id not in declared:
                    raise ValueError("sources content 引用了未声明的 source_id")
        return self


class ArtifactMetadata(BaseModel):
    """事件/列表用的轻量 Artifact 元数据（不含 content）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    thread_id: str
    run_id: str
    type: Literal["markdown", "table", "json", "sources"]
    title: str
    created_at: str
    parent_artifact_id: str | None = None
    source_count: int = Field(default=0, ge=0)

    @classmethod
    def from_artifact(cls, artifact: ArtifactDocument) -> "ArtifactMetadata":
        return cls(
            id=artifact.id, thread_id=artifact.thread_id, run_id=artifact.run_id,
            type=artifact.type, title=artifact.title, created_at=artifact.created_at,
            parent_artifact_id=artifact.parent_artifact_id,
            source_count=len(artifact.source_ids),
        )

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


ARTIFACT_MAX_BYTES = 1_048_576

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


class ToolExecutionSource(BaseModel):
    """已执行工具的来源记录（执行记录摘要，不是真实性认证）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    kind: Literal["tool_execution"]
    tool_call_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    origin: Literal["builtin", "skill", "mcp", "artifact"]
    completed_at: str = Field(min_length=1)
    arguments_summary: str = Field(default="", max_length=1000)
    result_summary: str = Field(default="", max_length=1000)
    verification: Literal["executed_record"] = "executed_record"


class ModelUrlSource(BaseModel):
    """模型提供的 URL：仅记录，绝不验证、抓取或评分。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    kind: Literal["model_url"]
    url: str = Field(min_length=1, max_length=2048)
    label: str | None = Field(default=None, max_length=200)
    created_at: str = Field(min_length=1)
    verification: Literal["model_provided_unverified"] = "model_provided_unverified"


SourceRecord = ToolExecutionSource | ModelUrlSource


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
    sources: list[SourceRecord] = Field(default_factory=list)
    sources_truncated: bool = False
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
