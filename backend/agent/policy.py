"""1D Policy 文档：默认值、范围校验、CAS store、非破坏性损坏与显式恢复。

契约（spec §7）：缺失文件 GET 返回默认值且不落盘；PATCH 按 revision CAS 合并；
损坏 fail-closed（503 POLICY_CORRUPT）且绝不复用"读取即隔离"的通用路径；
只有 `{"confirm_corrupt": true}` 的显式 reset 才能恢复（保留 .corrupt-* 副本）。
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from agent.models import PolicySnapshot
from agent.stores import atomic_write_json, utc_now, utc_stamp

POLICY_DEFAULTS = {
    "max_model_calls": 8,
    "max_tool_calls": 16,
    "tool_timeout_seconds": 30,
    "max_active_seconds": 300,
    "max_context_chars": 120_000,
}

_POLICY_FIELDS = ("max_model_calls", "max_tool_calls", "tool_timeout_seconds",
                  "max_active_seconds", "max_context_chars")


class PolicyError(RuntimeError):
    code = "POLICY_ERROR"


class PolicyInvalid(PolicyError):
    code = "POLICY_INVALID"


class PolicyRevisionConflict(PolicyError):
    code = "POLICY_REVISION_CONFLICT"

    def __init__(self, current_revision: int):
        super().__init__(f"Policy revision 过期，当前 revision 为 {current_revision}")
        self.current_revision = current_revision


class PolicyCorrupt(PolicyError):
    """损坏状态：非破坏性 fail-closed，直到显式 confirm_corrupt reset。"""

    code = "POLICY_CORRUPT"


class PolicyDocument(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = Field(default=1, frozen=True)
    revision: int = Field(ge=1)
    updated_at: str
    max_model_calls: int = Field(ge=1, le=32)
    max_tool_calls: int = Field(ge=1, le=64)
    tool_timeout_seconds: int = Field(ge=5, le=120)
    max_active_seconds: int = Field(ge=30, le=1800)
    max_context_chars: int = Field(ge=16000, le=500000)


class PolicyView(PolicyDocument):
    """传输层视图：`persisted` 只在响应中出现，不写入文件。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    revision: int = Field(ge=0)  # 缺失文件视图允许 revision=0
    updated_at: str | None = None
    persisted: bool = False


class PolicyPatch(BaseModel):
    """CAS PATCH 请求：当前 revision + 至少一个待更新字段，未知字段拒绝。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    revision: int = Field(ge=0)
    max_model_calls: int | None = Field(default=None, ge=1, le=32)
    max_tool_calls: int | None = Field(default=None, ge=1, le=64)
    tool_timeout_seconds: int | None = Field(default=None, ge=5, le=120)
    max_active_seconds: int | None = Field(default=None, ge=30, le=1800)
    max_context_chars: int | None = Field(default=None, ge=16000, le=500000)

    @model_validator(mode="after")
    def require_change(self):
        if all(getattr(self, name) is None for name in _POLICY_FIELDS):
            raise ValueError("至少提交一个待更新的 Policy 字段")
        return self


class PolicyReset(BaseModel):
    """两种互斥形状：正常 reset 携带 revision；损坏 reset 只携带 confirm_corrupt。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    revision: int | None = Field(default=None, ge=0)
    confirm_corrupt: bool = False

    @model_validator(mode="after")
    def require_exclusive_shape(self):
        if self.confirm_corrupt == (self.revision is not None):
            raise ValueError("reset 必须二选一：revision（正常）或 confirm_corrupt（损坏恢复）")
        return self


def _validation_summary(exc: ValidationError) -> str:
    """只输出字段与规则，不回显文件内容或绝对路径。"""
    parts = []
    for error in exc.errors():
        location = ".".join(str(piece) for piece in error.get("loc", ()))
        parts.append(f"{location}: {error.get('msg', '不合法')}")
    return "；".join(parts) or "schema 不合法"


class PolicyStore:
    def __init__(self, path: Path):
        self._path = path
        self._lock = threading.RLock()

    # ---- 读取 ----

    def _read_document(self) -> PolicyDocument | None:
        """直接读当前文件；缺失返回 None；损坏抛 PolicyCorrupt（非破坏性）。"""
        if not self._path.exists():
            return None
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
            raise PolicyCorrupt(f"{self._path.name} 损坏：JSON 解析失败") from exc
        try:
            return PolicyDocument.model_validate(payload)
        except ValidationError as exc:
            raise PolicyCorrupt(
                f"{self._path.name} 损坏：{_validation_summary(exc)}") from exc

    def _view(self, document: PolicyDocument | None) -> PolicyView:
        if document is None:
            return PolicyView(revision=0, persisted=False, **POLICY_DEFAULTS)
        return PolicyView(
            revision=document.revision,
            updated_at=document.updated_at,
            persisted=True,
            **{name: getattr(document, name) for name in _POLICY_FIELDS},
        )

    def get(self) -> PolicyView:
        with self._lock:
            return self._view(self._read_document())

    def snapshot(self) -> PolicySnapshot:
        """run 准入用的不可变快照；policy_revision 即当前文档 revision。"""
        with self._lock:
            document = self._read_document()
            if document is None:
                return PolicySnapshot(policy_revision=0, **POLICY_DEFAULTS)
            return PolicySnapshot(
                policy_revision=document.revision,
                **{name: getattr(document, name) for name in _POLICY_FIELDS},
            )

    # ---- 写入 ----

    def patch(self, payload: PolicyPatch) -> PolicyView:
        # 请求体已由 PolicyPatch 严格校验（未知字段/范围/至少一个字段）后再进入锁
        with self._lock:
            current = self._read_document()
            current_revision = current.revision if current is not None else 0
            if payload.revision != current_revision:
                raise PolicyRevisionConflict(current_revision)
            merged = {name: POLICY_DEFAULTS[name] for name in _POLICY_FIELDS}
            if current is not None:
                merged.update({name: getattr(current, name) for name in _POLICY_FIELDS})
            merged.update({name: value for name, value in payload.model_dump(
                exclude={"revision"}).items() if value is not None})
            document = PolicyDocument(
                revision=current_revision + 1, updated_at=utc_now(), **merged)
            atomic_write_json(self._path, document.model_dump(mode="json"))
            return self._view(document)

    def reset(self, payload: PolicyReset) -> PolicyView:
        if payload.confirm_corrupt:
            return self._reset_corrupt()
        return self._reset_normal(payload.revision or 0)

    def _reset_normal(self, expected_revision: int) -> PolicyView:
        with self._lock:
            current = self._read_document()
            current_revision = current.revision if current is not None else 0
            if expected_revision != current_revision:
                raise PolicyRevisionConflict(current_revision)
            document = PolicyDocument(
                revision=current_revision + 1, updated_at=utc_now(), **POLICY_DEFAULTS)
            atomic_write_json(self._path, document.model_dump(mode="json"))
            return self._view(document)

    def _reset_corrupt(self) -> PolicyView:
        """显式损坏恢复：保留 .corrupt-<timestamp> 副本后写 revision 1 默认值。"""
        with self._lock:
            try:
                healthy = self._path.exists() and self._read_document() is not None
            except PolicyCorrupt:
                healthy = False
            if healthy:
                # 健康文件不允许绕过 revision CAS 直接重置
                raise PolicyInvalid("Policy 未处于损坏状态，请使用携带 revision 的正常 reset")
            if self._path.exists():
                quarantined = self._path.with_name(f"{self._path.name}.corrupt-{utc_stamp()}")
                os.replace(self._path, quarantined)
                try:
                    directory_fd = os.open(self._path.parent, os.O_RDONLY)
                    try:
                        os.fsync(directory_fd)
                    finally:
                        os.close(directory_fd)
                except OSError:
                    pass
            document = PolicyDocument(revision=1, updated_at=utc_now(), **POLICY_DEFAULTS)
            atomic_write_json(self._path, document.model_dump(mode="json"))
            return self._view(document)
