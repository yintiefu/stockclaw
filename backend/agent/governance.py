"""1D 治理核心：RunControl（reservation 事务、活跃段计时、Provider usage 聚合）。

RunControl 只持有 Policy 快照、计数、计时与遥测，不持有模型 key、MCP secret、
session、真实 Skill 路径或无限工具原文。构造函数只接受 PolicySnapshot 与单调时钟。
"""

from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Literal

from agent.models import ContextTruncation, PolicySnapshot, RunUsage
from agent.policy import POLICY_DEFAULTS

PersistCallback = Callable[["GovernanceView"], Awaitable[None]]

# 未接 PolicyStore 的构造路径（占位句柄/纯内存测试）使用的默认快照
DEFAULT_POLICY_SNAPSHOT = PolicySnapshot(policy_revision=0, **POLICY_DEFAULTS)


class GovernanceError(RuntimeError):
    code = "GOVERNANCE_ERROR"


class ModelCallLimitExceeded(GovernanceError):
    code = "MODEL_CALL_LIMIT_EXCEEDED"


class ToolCallLimitExceeded(GovernanceError):
    code = "TOOL_CALL_LIMIT_EXCEEDED"


class RunActiveTimeout(GovernanceError):
    code = "RUN_ACTIVE_TIMEOUT"


class GovernancePersistenceFailed(GovernanceError):
    code = "PERSISTENCE_FAILED"


class GovernanceTerminalError(GovernanceError):
    """控制已终结后到达的预留/段开启请求；code 取自首次终结原因。"""

    def __init__(self, code: str, message: str):
        super().__init__(message or code)
        self.code = code


@dataclass(frozen=True)
class TerminalFact:
    code: str
    message: str


@dataclass(frozen=True)
class GovernanceView:
    """跨锁传递的不可变治理视图（RunDocument 落盘字段的唯一来源）。"""

    control_revision: int
    usage: RunUsage
    active_elapsed_ms: int
    context_truncation: ContextTruncation


@dataclass
class _UsageAggregation:
    """Provider usage 聚合计数：状态推导只看这些计数，从不在本地分词。"""

    reserved_model_calls: int = 0
    completed_calls: int = 0
    completed_with_usage: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    any_input_reported: bool = False
    any_output_reported: bool = False
    any_total_reported: bool = False


@dataclass
class _ReservationRollback:
    """预留事务的回滚快照。"""

    usage: RunUsage
    aggregation: _UsageAggregation
    control_revision: int


class RunControl:
    """产品 run 的治理控制：无密钥、跨 resume 复用、终态后拒绝新预留。

    锁约定：reservation_lock 只包住 校验/自增 → 完整 run 持久化 → 回滚；
    execution_lock / artifact_mutation_lock 是产品 run 级的执行与 Artifact 锁；
    同步计数与时钟遥测使用 threading.RLock。
    """

    reservation_lock: asyncio.Lock
    execution_lock: asyncio.Lock
    artifact_mutation_lock: asyncio.Lock

    def __init__(self, snapshot: PolicySnapshot, clock: Callable[[], float] = time.monotonic):
        self.snapshot = snapshot
        self._clock = clock
        self._counter_lock = threading.RLock()
        self.reservation_lock = asyncio.Lock()
        self.execution_lock = asyncio.Lock()
        self.artifact_mutation_lock = asyncio.Lock()
        self._control_revision = 0
        self._usage = RunUsage()
        self._aggregation = _UsageAggregation()
        self._context_truncation = ContextTruncation()
        self._closed_segments_ms: list[int] = []
        self._open_since: float | None = None
        self.terminal_error: TerminalFact | None = None

    # ---- 预留事务 ----

    async def reserve_model(self, persist: PersistCallback) -> GovernanceView:
        return await self._reserve("model", persist)

    async def reserve_tool(self, persist: PersistCallback) -> GovernanceView:
        return await self._reserve("tool", persist)

    async def _reserve(self, kind: Literal["model", "tool"], persist: PersistCallback) -> GovernanceView:
        async with self.reservation_lock:
            self._reject_terminal()
            rollback = self._increment_reservation(kind)  # 超限在自增前抛出
            view = self._view_locked()
            try:
                await persist(view)
            except BaseException as exc:
                self._rollback(rollback)
                self.mark_terminal(
                    "PERSISTENCE_FAILED", f"治理预留持久化失败: {exc}")
                raise GovernancePersistenceFailed(
                    f"{kind} 预留持久化失败，计数已回滚") from exc
            return view

    def _increment_reservation(self, kind: Literal["model", "tool"]) -> _ReservationRollback:
        with self._counter_lock:
            rollback = _ReservationRollback(
                usage=self._usage,
                aggregation=self._aggregation,
                control_revision=self._control_revision,
            )
            if kind == "model":
                if self._usage.model_calls >= self.snapshot.max_model_calls:
                    raise ModelCallLimitExceeded(
                        f"模型调用已达上限 {self.snapshot.max_model_calls}")
                self._usage = self._usage.model_copy(
                    update={"model_calls": self._usage.model_calls + 1})
                self._aggregation.reserved_model_calls += 1
            else:
                if self._usage.tool_calls >= self.snapshot.max_tool_calls:
                    raise ToolCallLimitExceeded(
                        f"工具调用已达上限 {self.snapshot.max_tool_calls}")
                self._usage = self._usage.model_copy(
                    update={"tool_calls": self._usage.tool_calls + 1})
            self._control_revision += 1
            return rollback

    def _rollback(self, rollback: _ReservationRollback) -> None:
        with self._counter_lock:
            self._usage = rollback.usage
            self._aggregation = rollback.aggregation
            self._control_revision = rollback.control_revision

    # ---- 活跃段 ----

    def begin_active_segment(self) -> GovernanceView:
        with self._counter_lock:
            self._reject_terminal()
            if self._open_since is None:
                self._open_since = self._clock()
                self._control_revision += 1
            return self._view_locked()

    def close_active_segment(self) -> GovernanceView:
        with self._counter_lock:
            if self._open_since is not None:
                elapsed_ms = int((self._clock() - self._open_since) * 1000)
                self._closed_segments_ms.append(max(0, elapsed_ms))
                self._open_since = None
                self._control_revision += 1
            return self._view_locked()

    def remaining_active_seconds(self) -> float:
        with self._counter_lock:
            return max(0.0, self.snapshot.max_active_seconds - self._active_elapsed_seconds())

    # ---- Provider usage ----

    def record_model_usage(
        self,
        usage: tuple[int | None, int | None, int | None] | None = None,
        *,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        total_tokens: int | None = None,
    ) -> GovernanceView:
        """记录一次完成的 Provider 调用；未回报任何 token 数值即视为缺失 usage。

        终态后仍可调用（终局持久化需要补齐最后的 usage 视图）。
        """
        if usage is not None:
            input_tokens, output_tokens, total_tokens = usage
        with self._counter_lock:
            agg = self._aggregation
            agg.completed_calls += 1
            if any(value is not None for value in (input_tokens, output_tokens, total_tokens)):
                agg.completed_with_usage += 1
                if input_tokens is not None:
                    agg.input_tokens += input_tokens
                    agg.any_input_reported = True
                if output_tokens is not None:
                    agg.output_tokens += output_tokens
                    agg.any_output_reported = True
                if total_tokens is not None:
                    agg.total_tokens += total_tokens
                    agg.any_total_reported = True
            self._usage = self._usage.model_copy(update={
                "input_tokens": agg.input_tokens if agg.any_input_reported else None,
                "output_tokens": agg.output_tokens if agg.any_output_reported else None,
                "total_tokens": self._derived_total(),
                "token_status": self._token_status(),
            })
            self._control_revision += 1
            return self._view_locked()

    def _derived_total(self) -> int | None:
        agg = self._aggregation
        if agg.any_total_reported:
            return agg.total_tokens
        if agg.any_input_reported and agg.any_output_reported:
            return agg.input_tokens + agg.output_tokens
        return None

    def _token_status(self) -> Literal["available", "partial", "unavailable"]:
        agg = self._aggregation
        if agg.completed_with_usage == 0:
            return "unavailable"
        # 已预留但未完成的调用按缺失 usage 计
        effective_total = max(agg.completed_calls, agg.reserved_model_calls)
        if agg.completed_with_usage >= effective_total:
            return "available"
        return "partial"

    # ---- 终态 ----

    def mark_terminal(self, code: str, message: str = "") -> None:
        with self._counter_lock:
            if self.terminal_error is None:
                self.terminal_error = TerminalFact(code=code, message=message or code)

    def _reject_terminal(self) -> None:
        if self.terminal_error is not None:
            raise GovernanceTerminalError(
                self.terminal_error.code,
                f"运行控制已终结（{self.terminal_error.code}），拒绝新的治理操作",
            )

    # ---- 视图 ----

    def set_context_truncation(self, truncation: ContextTruncation) -> GovernanceView:
        with self._counter_lock:
            if self._context_truncation != truncation:
                self._context_truncation = truncation
                self._control_revision += 1
            return self._view_locked()

    def view(self) -> GovernanceView:
        with self._counter_lock:
            return self._view_locked()

    def _view_locked(self) -> GovernanceView:
        return GovernanceView(
            control_revision=self._control_revision,
            usage=self._usage,
            active_elapsed_ms=self._active_elapsed_ms_locked(),
            context_truncation=self._context_truncation,
        )

    def _active_elapsed_seconds(self) -> float:
        with self._counter_lock:
            return self._active_elapsed_ms_locked() / 1000.0

    def _active_elapsed_ms_locked(self) -> int:
        total = sum(self._closed_segments_ms)
        if self._open_since is not None:
            total += max(0, int((self._clock() - self._open_since) * 1000))
        return total
