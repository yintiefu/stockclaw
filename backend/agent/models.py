from __future__ import annotations

from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, SecretStr, model_validator


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
    """forwardedProps.runtime 的服务端视图（1A 不支持 retry）。"""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    model: ModelRef
    command: dict | None = None
    retry_of: str | None = Field(default=None, validation_alias="retryOf")

    @model_validator(mode="after")
    def reject_later_milestone_retry(self) -> "RuntimeForwardedProps":
        if self.retry_of is not None:
            raise ValueError("retry is introduced with durable run history in milestone 1B")
        return self


class RunPhase(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: Literal["running", "awaiting_approval", "completed", "failed", "cancelled"]
