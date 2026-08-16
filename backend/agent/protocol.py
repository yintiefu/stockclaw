from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Literal
from uuid import uuid4

from ag_ui.core.events import (
    CustomEvent,
    RawEvent,
    RunErrorEvent,
    RunFinishedEvent,
    ToolCallArgsEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
)
from ag_ui.core.types import ConfiguredBaseModel


class RunCancelledEvent(ConfiguredBaseModel):
    """标准客户端事件名的取消事件（AG-UI 协议）。"""

    type: Literal["RUN_CANCELLED"] = "RUN_CANCELLED"
    thread_id: str
    run_id: str


INTERRUPT_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {"enum": ["approve", "reject"]},
        "scope": {"enum": ["once", "thread_session"]},
    },
    "required": ["decision", "scope"],
}


def interrupt_payloads(pending: list[PendingInterrupt]) -> list[dict[str, Any]]:
    """待审批中断的标准载荷；SSE 与持久化元数据共用同一形状，
    前端经 metadata.custom["ag-ui"].interrupts 水合后可直接 resume。"""
    return [{
        "id": item.bridge_interrupt_id,
        "reason": "tool_call",
        "message": item.value["action_requests"][0].get("description", "Tool approval required"),
        "toolCallId": item.tool_call_id,
        "responseSchema": INTERRUPT_RESPONSE_SCHEMA,
    } for item in pending]


def thread_revision_updated(thread_id: str, revision: int, persisted_at: str) -> CustomEvent:
    """提交成功后才能发出的 revision 事件（项目自有，绝不进 bridge.convert）。"""
    return CustomEvent(
        name="thread.revision.updated",
        value=json.dumps({
            "threadId": thread_id,
            "revision": revision,
            "persistedAt": persisted_at,
        }, ensure_ascii=False),
    )


@dataclass(frozen=True)
class PendingInterrupt:
    """桥接层自有 ID 的待审批中断；顺序独立于 LangGraph 内部 ID。"""

    bridge_interrupt_id: str
    order: int
    tool_call_id: str
    value: dict[str, Any]


class AgentProtocolBridge:
    """把 legacy CUSTOM/on_interrupt 转成标准 RUN_FINISHED.outcome，并校验 resume。"""

    def __init__(
        self,
        thread_id: str,
        run_id: str,
        pending: list[PendingInterrupt] | None = None,
    ):
        self.thread_id = thread_id
        self.run_id = run_id
        self.pending = pending if pending is not None else []
        self._tool_calls: dict[str, dict[str, Any]] = {}
        self._tool_call_order: list[str] = []

    def convert(self, event: Any) -> list[Any]:
        if isinstance(event, RawEvent):
            self._observe_raw_tool_calls(event)
            return [event]
        if isinstance(event, ToolCallStartEvent):
            # 锁定版本不发独立的 ARGS 增量；参数就在 START 的 raw_event chunk 里。
            chunk_args = ""
            chunk = (event.raw_event or {}).get("data", {}).get("chunk", {}) if isinstance(event.raw_event, dict) else {}
            for piece in chunk.get("tool_call_chunks") or []:
                if piece.get("id") == event.tool_call_id and piece.get("args"):
                    chunk_args = piece["args"]
                    break
            self._tool_calls[event.tool_call_id] = {
                "name": event.tool_call_name,
                "args_text": chunk_args,
            }
            self._tool_call_order.append(event.tool_call_id)
            if chunk_args:
                # 合成标准 TOOL_CALL_ARGS，下游无需理解 raw_event 结构
                return [event, ToolCallArgsEvent(tool_call_id=event.tool_call_id, delta=chunk_args)]
            return [event]
        if isinstance(event, ToolCallArgsEvent):
            if event.tool_call_id not in self._tool_calls:
                raise ValueError("tool args arrived before tool start")
            self._tool_calls[event.tool_call_id]["args_text"] += event.delta
            return [event]
        if isinstance(event, ToolCallEndEvent):
            if event.tool_call_id not in self._tool_calls:
                raise ValueError("tool end arrived before tool start")
            return [event]
        if isinstance(event, CustomEvent):
            if event.name != "on_interrupt":
                return [RunErrorEvent(message=f"Unsupported custom event: {event.name}", code="UNSUPPORTED_CUSTOM_EVENT")]
            # 本锁定版本里 CustomEvent.value 是 JSON 字符串而非 dict，统一解析。
            raw = event.value
            value = json.loads(raw) if isinstance(raw, str) else raw
            self._capture(value)
            return []
        if isinstance(event, RunFinishedEvent) and self.pending:
            interrupts = interrupt_payloads(self.pending)
            return [RunFinishedEvent(
                thread_id=self.thread_id,
                run_id=self.run_id,
                outcome={"type": "interrupt", "interrupts": interrupts},
            )]
        return [event]

    def cancelled(self) -> RunCancelledEvent:
        return RunCancelledEvent(thread_id=self.thread_id, run_id=self.run_id)

    def _observe_raw_tool_calls(self, event: RawEvent) -> None:
        """锁定版本里中断前不会发 TOOL_CALL_* 事件；从 RawEvent 的模型输出里补观察。"""
        payload = event.event if isinstance(event.event, dict) else {}
        if payload.get("event") != "on_chat_model_end":
            return
        output = (payload.get("data") or {}).get("output") or {}
        for call in output.get("tool_calls") or []:
            call_id = call.get("id")
            if not call_id or call_id in self._tool_calls:
                continue
            self._tool_calls[call_id] = {
                "name": call.get("name"),
                "args_text": json.dumps(call.get("args") or {}),
            }
            self._tool_call_order.append(call_id)

    def _ordered_entries(self, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        by_id: dict[str, dict[str, Any]] = {}
        for entry in entries:
            interrupt_id = entry.get("interruptId")
            if not interrupt_id or interrupt_id in by_id:
                raise ValueError("resume contains a missing or duplicate interrupt ID")
            by_id[interrupt_id] = entry
        expected = {item.bridge_interrupt_id for item in self.pending}
        if set(by_id) != expected:
            raise ValueError("resume must answer every pending bridge interrupt exactly once")
        return [by_id[item.bridge_interrupt_id] for item in sorted(self.pending, key=lambda item: item.order)]

    def is_steer_away(self, entries: list[dict[str, Any]]) -> bool:
        ordered = self._ordered_entries(entries)
        return bool(ordered) and all(entry.get("status") == "cancelled" for entry in ordered)

    def resume_value(self, entries: list[dict[str, Any]]) -> dict[str, Any]:
        ordered = self._ordered_entries(entries)
        decisions = []
        for entry in ordered:
            if entry.get("status") != "resolved":
                raise ValueError("cancelled interrupts are transport-level steer-away, not HITL decisions")
            payload = entry.get("payload") or {}
            if payload == {"decision": "approve", "scope": "once"}:
                decisions.append({"type": "approve"})
            elif payload == {"decision": "reject", "scope": "once"}:
                decisions.append({"type": "reject", "message": "User rejected the tool call"})
            else:
                # thread_session 需要 1C 的会话级许可注册表；1A 只展示、不承诺。
                raise ValueError("unsupported approval payload")
        return {"decisions": decisions}

    def _capture(self, value: dict[str, Any]) -> None:
        actions = value.get("action_requests") or []
        reviews = value.get("review_configs") or []
        if not actions or len(actions) != len(reviews):
            raise ValueError("legacy interrupt has an invalid HITL request")
        candidates = [
            (tool_call_id, self._tool_calls[tool_call_id])
            for tool_call_id in self._tool_call_order
            if not any(item.tool_call_id == tool_call_id for item in self.pending)
        ]
        if len(candidates) < len(actions):
            # A repeated observation reuses the already captured mapping.
            if len(self.pending) == len(actions):
                for item, action, review in zip(
                    sorted(self.pending, key=lambda pending: pending.order),
                    actions,
                    reviews,
                    strict=True,
                ):
                    if item.value != {"action_requests": [action], "review_configs": [review]}:
                        raise ValueError("reloaded interrupt differs from the pending mapping")
                return
            raise ValueError("interrupt cannot be matched to observed tool calls")
        for action, review, (tool_call_id, observed) in zip(actions, reviews, candidates, strict=True):
            observed_args = json.loads(observed["args_text"] or "{}")
            if action.get("name") != observed["name"] or action.get("args") != observed_args:
                raise ValueError("interrupt action does not match the streamed tool call")
            self.pending.append(PendingInterrupt(
                str(uuid4()), len(self.pending), tool_call_id,
                {"action_requests": [action], "review_configs": [review]},
            ))
