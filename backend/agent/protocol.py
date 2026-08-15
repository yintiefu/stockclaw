from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Literal
from uuid import uuid4

from ag_ui.core.events import (
    CustomEvent,
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
        if isinstance(event, ToolCallStartEvent):
            self._tool_calls[event.tool_call_id] = {"name": event.tool_call_name, "args_text": ""}
            self._tool_call_order.append(event.tool_call_id)
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
            self._capture(event.value)
            return []
        if isinstance(event, RunFinishedEvent) and self.pending:
            interrupts = [{
                "id": item.bridge_interrupt_id,
                "reason": "tool_call",
                "message": item.value["action_requests"][0].get("description", "Tool approval required"),
                "toolCallId": item.tool_call_id,
                "responseSchema": {
                    "type": "object",
                    "properties": {
                        "decision": {"enum": ["approve", "reject"]},
                        "scope": {"enum": ["once", "thread_session"]},
                    },
                    "required": ["decision", "scope"],
                },
            } for item in self.pending]
            return [RunFinishedEvent(
                thread_id=self.thread_id,
                run_id=self.run_id,
                outcome={"type": "interrupt", "interrupts": interrupts},
            )]
        return [event]

    def cancelled(self) -> RunCancelledEvent:
        return RunCancelledEvent(thread_id=self.thread_id, run_id=self.run_id)

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
