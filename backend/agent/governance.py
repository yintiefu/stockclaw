"""1D 治理核心：RunControl（reservation 事务、活跃段计时、Provider usage 聚合）
与模型上下文治理（canonical 渲染、完整 turn 裁剪、模型 reservation、budget 事件）。

RunControl 只持有 Policy 快照、计数、计时与遥测，不持有模型 key、MCP secret、
session、真实 Skill 路径或无限工具原文。构造函数只接受 PolicySnapshot 与单调时钟。
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Literal

from agent.models import ContextTruncation, PolicySnapshot, RunUsage
from agent.policy import POLICY_DEFAULTS
from langchain.agents.middleware import AgentMiddleware

PersistCallback = Callable[["GovernanceView"], Awaitable[None]]
EmitCallback = Callable[[dict], Awaitable[None]]

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


class ContextLimitExceeded(GovernanceError):
    code = "CONTEXT_LIMIT_EXCEEDED"


class ToolTimedOut(GovernanceError):
    """min(tool, active) 截止耗尽；code 按绑定来源映射 TOOL_TIMEOUT / RUN_ACTIVE_TIMEOUT。"""

    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


class ToolArgsInvalid(ValueError):
    """schema 预校验失败的可纠正错误：LangGraph 会转成 error ToolMessage。"""


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

    def note_sources_added(self, count: int) -> GovernanceView:
        """短协调锁的 Source 提交路径：只递增 revision，不取 reservation_lock。"""
        with self._counter_lock:
            if count > 0:
                self._control_revision += count
            return self._view_locked()

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


# ---- 上下文治理：canonical 渲染与完整 turn 裁剪（spec §11） ----

_SKILL_TOOL_NAME = "load_skill"


def _canonical_json(value: Any) -> str:
    """排序 + 紧凑 + 非 ASCII 保留；allow_nan 兜底防止历史 NaN 打断计量。"""
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False)
    except ValueError:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"))


def render_policy_explanation(snapshot: PolicySnapshot) -> str:
    """由 run 快照推导的确定性、无密钥治理说明。"""
    return (
        "\n\n## 本次运行的资源边界（Policy 快照，运行期间不变）\n"
        f"- 模型调用上限：{snapshot.max_model_calls} 次\n"
        f"- 工具调用上限：{snapshot.max_tool_calls} 次\n"
        f"- 单个工具最长执行：{snapshot.tool_timeout_seconds} 秒\n"
        f"- 本次运行最长活跃时长：{snapshot.max_active_seconds} 秒\n"
        f"- 上下文字符上限：{snapshot.max_context_chars}\n"
        "以上是客观运行约束说明，不构成任何投资建议。"
    )


def _render_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if content is None:
        return ""
    if isinstance(content, list):
        parts = []
        for piece in content:
            if isinstance(piece, str):
                parts.append(piece)
            else:
                parts.append(_canonical_json(piece))
        return "".join(parts)
    return _canonical_json(content)


def _render_message(message: Any) -> str:
    role = getattr(message, "type", None) or getattr(message, "role", "") or ""
    name = getattr(message, "name", None)
    label = f"{role}:{name}" if name else str(role)
    pieces = [f"[{label}]"]
    content = getattr(message, "content", "")
    rendered_content = _render_content(content)
    if rendered_content:
        pieces.append(rendered_content)
    for call in getattr(message, "tool_calls", None) or []:
        pieces.append(
            f"<tool_call id={call.get('id') or ''} name={call.get('name') or ''} "
            f"args={_canonical_json(call.get('args') or {})}>")
    tool_call_id = getattr(message, "tool_call_id", None)
    if tool_call_id:
        pieces.append(f"<tool_result call_id={tool_call_id}>")
    return "\n".join(pieces)


def render_model_context(system_message: Any, messages: list[Any]) -> str:
    """System 与 message history 共用的 canonical renderer（计量与裁剪同源）。"""
    blocks: list[str] = []
    if system_message is not None:
        blocks.append("[system]\n" + _render_content(getattr(system_message, "content", "")))
    blocks.extend(_render_message(message) for message in messages)
    return "\n".join(blocks)


def _is_human(message: Any) -> bool:
    return (getattr(message, "type", None) or getattr(message, "role", "")) in ("human", "user")


def _message_tool_call_names(message: Any) -> dict[str, str]:
    return {
        call.get("id") or "": call.get("name") or ""
        for call in (getattr(message, "tool_calls", None) or [])
    }


def _loaded_skill(message: Any, call_names: dict[str, str]) -> str | None:
    """ToolMessage 若对应 load_skill 调用，返回其结果里声明的 Skill 名。"""
    tool_call_id = getattr(message, "tool_call_id", None)
    if not tool_call_id or call_names.get(tool_call_id) != _SKILL_TOOL_NAME:
        return None
    content = getattr(message, "content", "")
    try:
        payload = json.loads(content) if isinstance(content, str) else content
        name = payload.get("name") if isinstance(payload, dict) else None
        return name or None
    except (json.JSONDecodeError, TypeError):
        return None


def _group_turns(messages: list[Any]) -> list[list[Any]]:
    """从每个 user message 到下一个 user message 之前为一组完整 turn。"""
    turns: list[list[Any]] = []
    current: list[Any] = []
    for message in messages:
        if _is_human(message) and current:
            turns.append(current)
            current = []
        current.append(message)
    if current:
        turns.append(current)
    return turns


def _latest_skill_turns(messages: list[Any]) -> set[int]:
    """每个已加载 Skill 的最新完整 load_skill turn（按消息索引返回）。"""
    call_names: dict[str, str] = {}
    for message in messages:
        call_names.update(_message_tool_call_names(message))
    latest: dict[str, int] = {}
    for index, message in enumerate(messages):
        skill = _loaded_skill(message, call_names)
        if skill is not None:
            latest[skill] = index
    return set(latest.values())


def trim_model_request(request: Any, limit: int) -> tuple[Any, ContextTruncation]:
    """在 Provider 格式化与 reservation 之前裁剪 ModelRequest。

    强制保留：system（中立提示 + Policy 说明 + Skill 目录）、当前 user turn、
    每个已加载 Skill 的最新完整 load_skill turn。其余历史从新到旧加入完整 turn，
    恢复时间顺序；强制内容超限时抛 ContextLimitExceeded（绝不切分单位）。
    """
    system = getattr(request, "system_message", None)
    messages = list(getattr(request, "messages", []) or [])
    original_chars = len(render_model_context(system, messages))
    if original_chars <= limit:
        return request, ContextTruncation(
            occurred=False, original_chars=original_chars,
            retained_chars=original_chars, removed_turns=0)

    turns = _group_turns(messages)
    if not turns:
        raise ContextLimitExceeded(
            f"强制上下文 {original_chars} 字符超过上限 {limit}")

    # 每个 turn 的起止消息索引（turn 内部不可拆分）
    starts: list[int] = []
    index = 0
    for turn in turns:
        starts.append(index)
        index += len(turn)

    current_turn_index = len(turns) - 1
    forced_turns = {current_turn_index}
    for message_index in _latest_skill_turns(messages):
        for position, start in enumerate(starts):
            end = starts[position + 1] if position + 1 < len(starts) else len(messages)
            if start <= message_index < end:
                forced_turns.add(position)
                break

    def _turn_chars(turn_positions: set[int]) -> int:
        selected = sorted(turn_positions)
        kept = [message for position in selected for message in turns[position]]
        return len(render_model_context(system, kept))

    forced_chars = _turn_chars(forced_turns)
    if forced_chars > limit:
        raise ContextLimitExceeded(
            f"强制保留内容 {forced_chars} 字符超过上限 {limit}，"
            "不允许裁剪 System、Skill 指令或当前 turn")

    kept_turns = set(forced_turns)
    # 从新到旧加入完整 turn，直到下一个 turn 会超限
    for position in range(len(turns) - 1, -1, -1):
        if position in kept_turns:
            continue
        if _turn_chars(kept_turns | {position}) > limit:
            break
        kept_turns.add(position)

    if len(kept_turns) == len(turns):
        retained_chars = len(render_model_context(system, messages))
        return request, ContextTruncation(
            occurred=False, original_chars=original_chars,
            retained_chars=retained_chars, removed_turns=0)

    kept_messages = [
        message for position in sorted(kept_turns) for message in turns[position]
    ]
    retained_chars = len(render_model_context(system, kept_messages))
    trimmed = request.override(messages=kept_messages)
    return trimmed, ContextTruncation(
        occurred=True,
        original_chars=original_chars,
        retained_chars=retained_chars,
        removed_turns=len(turns) - len(kept_turns),
    )


def extract_provider_usage(response: Any) -> tuple[int | None, int | None, int | None] | None:
    """从 Provider 响应中的 AIMessage.usage_metadata 提取 token 用量。"""
    if isinstance(response, list):
        candidates = response
    else:
        candidates = []
        for attr in ("messages", "result", "generations"):
            inner = getattr(response, attr, None)
            if isinstance(inner, list) and inner:
                candidates = [
                    item.message if hasattr(item, "message") else item for item in inner
                ]
                break
        if not candidates:
            result = getattr(response, "result", None)
            candidates = [result] if result is not None else [response]
    for message in candidates:
        metadata = getattr(message, "usage_metadata", None)
        if metadata:
            return (
                metadata.get("input_tokens"),
                metadata.get("output_tokens"),
                metadata.get("total_tokens"),
            )
    return None


async def _default_emit(payload: dict) -> None:
    """生产路径：经 LangGraph 自定义事件机制发布（无回调上下文时静默跳过）。"""
    try:
        from langchain_core.callbacks import adispatch_custom_event

        await adispatch_custom_event("budget.updated", payload)
    except Exception as exc:  # 事件失败绝不影响已提交状态
        print(f"governance budget emit failed: {exc!r}", file=sys.stderr)


def build_budget_payload(*, thread_id: str, run_id: str, control: RunControl,
                         view: GovernanceView) -> dict:
    """§19.1 budget.updated 载荷：恰好七个 camelCase 字段。"""
    return {
        "threadId": thread_id,
        "runId": run_id,
        "controlRevision": view.control_revision,
        "budgetSnapshot": control.snapshot.model_dump(mode="json"),
        "usage": view.usage.model_dump(mode="json"),
        "activeElapsedMs": view.active_elapsed_ms,
        "contextTruncation": view.context_truncation.model_dump(mode="json"),
    }


class ContextAndModelGovernance(AgentMiddleware):
    """模型调用治理中间件（元组第一位、最外层）。

    顺序契约：上下文计量/裁剪 → active deadline 检查 → reservation（持久化成功
    才继续）→ budget.updated → Provider 调用（剩余 active deadline 内）→
    记录 usage（错误/取消也记录缺失 usage，不覆盖原始异常）。
    """

    def __init__(
        self,
        control: RunControl,
        *,
        persist: PersistCallback,
        emit: EmitCallback | None = None,
        thread_id: str = "",
        run_id: str = "",
    ):
        super().__init__()
        self._control = control
        self._persist = persist
        self._emit = emit or _default_emit
        self._thread_id = thread_id
        self._run_id = run_id

    @property
    def control(self) -> RunControl:
        return self._control

    async def _persist_telemetry(self, view: GovernanceView) -> None:
        try:
            await self._persist(view)
        except BaseException as exc:
            self._control.mark_terminal(
                "PERSISTENCE_FAILED", f"治理遥测持久化失败: {exc}")
            raise GovernancePersistenceFailed("治理遥测持久化失败") from exc
        await self._emit_budget(view)

    async def _emit_budget(self, view: GovernanceView) -> None:
        try:
            await self._emit(build_budget_payload(
                thread_id=self._thread_id, run_id=self._run_id,
                control=self._control, view=view))
        except Exception as exc:  # 事件失败绝不影响已提交状态
            print(f"governance budget emit failed: {exc!r}", file=sys.stderr)

    async def awrap_model_call(self, request, call_llm, **kwargs):
        # 1) 上下文治理：同一 renderer 负责计量与裁剪
        trimmed, truncation = trim_model_request(
            request, self._control.snapshot.max_context_chars)
        if truncation.occurred:
            await self._persist_telemetry(self._control.set_context_truncation(truncation))
        elif truncation.original_chars is not None:
            self._control.set_context_truncation(truncation)  # 只更新内存计量

        # 2) active deadline：过期即拒绝 Provider 与 reservation
        if self._control.remaining_active_seconds() <= 0:
            raise RunActiveTimeout("本次运行的活跃时长已耗尽")

        # 3) 模型 reservation：持久化成功才允许调用 Provider
        view = await self._control.reserve_model(self._persist)
        await self._emit_budget(view)

        # 4) Provider 调用：剩余 active deadline 内
        remaining = self._control.remaining_active_seconds()
        try:
            response = await asyncio.wait_for(
                call_llm(trimmed), timeout=remaining if remaining > 0 else 0.0)
        except asyncio.TimeoutError as exc:
            raise RunActiveTimeout("Provider 调用超出本次运行的活跃时长") from exc
        except BaseException:
            # 错误/取消：按缺失 usage 记录一次完成；不覆盖原始异常
            try:
                await self._persist_telemetry(self._control.record_model_usage(None))
            except GovernancePersistenceFailed:
                pass
            raise
        usage = extract_provider_usage(response)
        await self._persist_telemetry(self._control.record_model_usage(usage))
        return response


# ---- 工具准入治理（spec §10：锁序、容量、reservation） ----

import contextlib  # noqa: E402
import time as _time_module  # noqa: E402

from langchain.agents.middleware import AgentMiddleware as _AgentMiddleware  # noqa: E402

from agent.tool_executor import (  # noqa: E402
    BoundedToolExecutor,
    CapacityLease,
    ToolDeadlineExceeded,
    ToolExecutionContext,
    install_tool_execution_context,
    reset_tool_execution_context,
)

CAPACITY_WAIT_SECONDS = 1.0
_WAIT_POLL_SECONDS = 0.02

_JSON_TYPE_FAMILY = {
    "string": (str,),
    "array": (list,),
    "object": (dict,),
    "boolean": (bool,),
    "integer": (int,),
    "number": (int, float),
}


def classify_tool(tool: Any) -> tuple[str, bool, bool, bool]:
    """(origin, execution_lock, builtin_serial, capacity)；按不可变元数据分类。

    无元数据的工具按本地工具处理（execution lock、无进程串行、无容量）。
    """
    metadata = getattr(tool, "metadata", None) or {}
    origin = metadata.get("vr_origin") or "local"
    if origin == "mcp":
        return ("mcp", False, False, False)
    execution_lock = bool(metadata.get("vr_execution_lock", origin != "mcp"))
    builtin_serial = bool(metadata.get("vr_builtin_serial", False))
    capacity = bool(metadata.get("vr_capacity", False))
    return (origin, execution_lock, builtin_serial, capacity)


def _json_schema_type_ok(expected: Any, value: Any) -> bool:
    if expected is None:
        return True
    if isinstance(expected, list):
        return all(_json_schema_type_ok(item, value) for item in expected)
    family = _JSON_TYPE_FAMILY.get(str(expected))
    if family is None:
        return True
    if isinstance(value, bool) and str(expected) not in ("boolean",):
        return False
    return isinstance(value, family)


def _validate_json_schema_args(schema: dict, args: dict) -> None:
    """legacy 内置 dict(JSON) schema 的最小预校验：required + 类型族。"""
    for name in schema.get("required") or []:
        if name not in args:
            raise ToolArgsInvalid(f"缺少必填参数 {name}")
    properties = schema.get("properties") or {}
    extra_allowed = schema.get("additionalProperties", True)
    for name, value in args.items():
        if name not in properties:
            if extra_allowed is False:
                raise ToolArgsInvalid(f"未知参数 {name}")
            continue
        rule = properties[name] or {}
        if not _json_schema_type_ok(rule.get("type"), value):
            raise ToolArgsInvalid(f"参数 {name} 类型不合法")
        enum = rule.get("enum")
        if enum is not None and value not in enum:
            raise ToolArgsInvalid(f"参数 {name} 不在允许取值内")
        if isinstance(value, list) and rule.get("items"):
            item_type = (rule["items"] or {}).get("type")
            for item in value:
                if not _json_schema_type_ok(item_type, item):
                    raise ToolArgsInvalid(f"参数 {name} 的元素类型不合法")


def prevalidate_tool_args(tool: Any, args: Any) -> str | None:
    """返回 None 表示通过；'native' 表示交给原生校验路径；'invalid' 表示明确失败。"""
    if tool is None:
        return None
    schema = getattr(tool, "args_schema", None)
    if isinstance(schema, type) and hasattr(schema, "model_validate"):
        if isinstance(args, dict):
            try:
                schema.model_validate(args)
            except Exception:
                return "invalid"
        return None
    if isinstance(schema, dict):
        if isinstance(args, dict):
            try:
                _validate_json_schema_args(schema, args)
            except ToolArgsInvalid:
                return "invalid"
        return None
    return None


class ToolExecutionGovernance(_AgentMiddleware):
    """真实 handler 边界的准入治理（元组最后一位、最内层工具包装）。

    顺序契约（锁序不变量）：schema 预校验 → execution lock → 进程级 builtin
    serial lock → executor capacity → reservation（持久化成功才继续）→ 安装
    ToolExecutionContext → handler（min(tool, active) 截止内）→ 复位并逆序释放。
    MCP 跳过本地锁与容量，仅在真实调用前 reservation。
    """

    def __init__(
        self,
        control: RunControl,
        *,
        persist: PersistCallback,
        emit: EmitCallback | None = None,
        executor: BoundedToolExecutor | None = None,
        builtin_serial_lock: asyncio.Lock | None = None,
        thread_id: str = "",
        run_id: str = "",
        clock: Callable[[], float] | None = None,
    ):
        super().__init__()
        self._control = control
        self._persist = persist
        self._emit_cb = emit
        self._executor = executor
        self._builtin_serial_lock = builtin_serial_lock
        self._thread_id = thread_id
        self._run_id = run_id
        # 截止时钟可注入（测试用 FakeClock 快进）；生产为 time.monotonic
        self._clock = clock or _time_module.monotonic

    # ---- 截止与错误映射 ----

    def _deadline_error(self) -> ToolTimedOut:
        if self._control.remaining_active_seconds() <= 0:
            return ToolTimedOut("本次运行的活跃时长已耗尽", "RUN_ACTIVE_TIMEOUT")
        return ToolTimedOut("工具执行超过 Policy 截止时间", "TOOL_TIMEOUT")

    def _remaining_seconds(self, tool_deadline: float) -> float:
        """实际剩余 = min(tool 剩余, active 剩余)；时钟可注入故每次重算。"""
        return min(
            tool_deadline - self._clock(),
            self._control.remaining_active_seconds(),
        )

    async def _acquire_lock_bounded(self, lock: asyncio.Lock, tool_deadline: float) -> None:
        while True:
            remaining = self._remaining_seconds(tool_deadline)
            if remaining <= 0:
                raise self._deadline_error()
            acquire_task = asyncio.ensure_future(lock.acquire())
            try:
                await asyncio.wait_for(acquire_task, timeout=min(_WAIT_POLL_SECONDS, remaining))
                return
            except asyncio.TimeoutError:
                if acquire_task.cancelled() is False:
                    acquire_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await acquire_task
                continue

    async def _await_bounded(self, awaitable, tool_deadline: float):
        """轮询式有界等待：每 20ms 重算剩余时间，配合可注入时钟可测。"""
        task = asyncio.ensure_future(awaitable)
        while True:
            remaining = self._remaining_seconds(tool_deadline)
            if remaining <= 0:
                task.cancel()
                with contextlib.suppress(BaseException):
                    await task
                raise self._deadline_error()
            try:
                return await asyncio.wait_for(asyncio.shield(task),
                                              timeout=min(_WAIT_POLL_SECONDS, remaining))
            except asyncio.TimeoutError:
                continue

    async def _emit_budget(self, view: GovernanceView) -> None:
        emit = self._emit_cb or _default_emit
        try:
            await emit(build_budget_payload(
                thread_id=self._thread_id, run_id=self._run_id,
                control=self._control, view=view))
        except Exception as exc:  # 事件失败绝不影响已提交状态
            print(f"governance budget emit failed: {exc!r}", file=sys.stderr)

    # ---- 中间件入口 ----

    async def awrap_tool_call(self, request, call_tool, **kwargs):
        tool = getattr(request, "tool", None)
        tool_call = getattr(request, "tool_call", {}) or {}
        args = tool_call.get("args")

        # 1) schema 预校验：在任何锁 / reservation 之前（失败零消耗）
        check = prevalidate_tool_args(tool, args)
        if check == "invalid":
            raise ToolArgsInvalid(f"工具 {tool_call.get('name')} 参数未通过 schema 校验")

        origin, needs_execution_lock, needs_serial, needs_capacity = classify_tool(tool)

        # 2) 截止：tool deadline 从预校验成功后开始；实际截止取 min(tool, active)
        tool_deadline = self._clock() + self._control.snapshot.tool_timeout_seconds
        if self._remaining_seconds(tool_deadline) <= 0:
            raise self._deadline_error()

        lease: CapacityLease | None = None
        held_locks: list[asyncio.Lock] = []
        try:
            # 3) 前置条件（锁序：execution → serial → capacity）
            if needs_execution_lock:
                await self._acquire_lock_bounded(self._control.execution_lock, tool_deadline)
                held_locks.append(self._control.execution_lock)
            if needs_serial:
                if self._builtin_serial_lock is None:
                    raise GovernanceError("进程级 builtin serial lock 未注入")
                await self._acquire_lock_bounded(self._builtin_serial_lock, tool_deadline)
                held_locks.append(self._builtin_serial_lock)
            if needs_capacity:
                if self._executor is None:
                    raise GovernanceError("有界同步执行器未注入")
                # executor 内部使用真实单调时钟：剩余秒数从当前真实时刻起算
                executor_deadline = _time_module.monotonic() + max(
                    0.0, self._remaining_seconds(tool_deadline))
                lease = await self._executor.acquire(
                    capacity_wait_seconds=CAPACITY_WAIT_SECONDS, deadline=executor_deadline)

            # 4) reservation：持久化成功才允许触达真实 handler
            view = await self._control.reserve_tool(self._persist)
            await self._emit_budget(view)

            # 5) 安装请求级上下文（无密钥）并调用内层
            context = ToolExecutionContext(
                thread_id=self._thread_id,
                product_run_id=self._run_id,
                execution_lock=self._control.execution_lock,
                builtin_serial_lock=self._builtin_serial_lock or asyncio.Lock(),
                executor=self._executor or BoundedToolExecutor(),
                tool_deadline=tool_deadline,
                capacity_lease=lease,
                control=self._control,
            )
            token = install_tool_execution_context(context)
            try:
                return await self._await_bounded(call_tool(request), tool_deadline)
            finally:
                reset_tool_execution_context(token)
        except ToolDeadlineExceeded as exc:
            raise self._deadline_error() from exc
        finally:
            # 逆序释放本层取得的锁；未提交的租约归还（已提交的归 future 回调）
            if lease is not None and not lease.submitted and not lease.released:
                lease.release_unsubmitted()
            for lock in reversed(held_locks):
                if lock.locked():
                    lock.release()
