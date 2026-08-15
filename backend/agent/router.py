from __future__ import annotations

import asyncio
import ipaddress
import os
from typing import Any, Callable

from ag_ui.core.events import RunErrorEvent, RunFinishedEvent
from ag_ui.core.types import RunAgentInput
from ag_ui.encoder import EventEncoder
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.tools import BaseTool

from agent.models import ModelRef, RuntimeForwardedProps, RunSecrets
from agent.protocol import AgentProtocolBridge, PendingInterrupt
from agent.runs import ResumeRejected, RunCoordinator, ThreadBusy
from agent.runtime import AgentFactory, RunConfigMismatch, build_chat_model
from agent.tool_registry import build_builtin_tools

router = APIRouter(prefix="/api/agent")

coordinator = RunCoordinator(factory=AgentFactory())


def build_middleware() -> tuple[Any, ...]:
    """1A 默认不挂额外中间件；审批流由测试注入 HITL 中间件。"""
    return ()


class EndpointError(Exception):
    def __init__(self, code: str, detail: str, status: int = 400):
        self.code = code
        self.detail = detail
        self.status = status


def require_secure_model_key_transport(request: Request) -> None:
    """明文 HTTP 只允许回环地址；否则要求 https。仅信任显式配置的可信代理头。"""
    host = request.client.host if request.client else ""
    try:
        is_loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        is_loopback = False
    if is_loopback:
        return
    if os.environ.get("VR_TRUST_PROXY_HEADERS") == "1":
        trusted = [item.strip() for item in os.environ.get("VR_TRUSTED_PROXY_IPS", "").split(",") if item.strip()]
        if host in trusted and request.headers.get("X-Forwarded-Proto") == "https":
            return
    if request.url.scheme != "https":
        raise EndpointError(
            "INSECURE_MODEL_KEY_TRANSPORT",
            "模型密钥只能通过 HTTPS 或本机回环传输",
        )


def _error_response(code: str, detail: str, status: int = 400) -> JSONResponse:
    return JSONResponse(status_code=status, content={"code": code, "detail": detail})


def _error_event(message: str) -> RunErrorEvent:
    return RunErrorEvent(message=message, code="AGENT_RUN_FAILED")


def _classify(input_data: RunAgentInput) -> str:
    """按 spec 的合法形状分类（start/resume/steer-away）；混合/畸形形状一律失败关闭。"""
    forwarded_props = input_data.forwarded_props or {}
    command = forwarded_props.get("command") or {}
    resume_entries = (command.get("resume") or []) if isinstance(command, dict) else []
    messages = input_data.messages or []

    has_cancelled = any(isinstance(e, dict) and e.get("status") == "cancelled" for e in resume_entries)
    user_messages = [m for m in messages if getattr(m, "role", None) == "user"]

    if resume_entries and has_cancelled:
        # steer-away：全部 cancelled + 恰好一条新 user message
        if not all(isinstance(e, dict) and e.get("status") == "cancelled" for e in resume_entries):
            raise ValueError("steer-away 不能混合 resolved 与 cancelled 条目")
        if len(user_messages) != 1:
            raise ValueError("steer-away 必须恰好携带一条新 user message")
        return "steer_away"
    if resume_entries:
        # 纯 resume：不得携带新消息
        if messages:
            raise ValueError("纯 resume 不得携带新消息（messages 必须为空）")
        return "resume"
    # start：恰好一条新 user message
    if len(user_messages) != 1 or len(messages) != 1:
        raise ValueError("start 必须恰好携带一条新 user message")
    return "start"


def _validate_steer_away(pending: list[PendingInterrupt], entries: list[dict]) -> None:
    probe = AgentProtocolBridge("", "", pending=pending)
    if not probe.is_steer_away(entries):
        raise ValueError("resume 条目不是全量 cancelled，不能作为 steer-away")


def _without_resume_command(input_data: RunAgentInput) -> RunAgentInput:
    props = {k: v for k, v in (input_data.forwarded_props or {}).items() if k != "command"}
    return input_data.model_copy(update={"forwarded_props": props})


@router.post("/run")
async def run(input_data: RunAgentInput, request: Request) -> StreamingResponse:
    model_key = (request.headers.get("X-VR-Agent-Model-Key") or "").strip()
    if not model_key:
        return _error_response("MODEL_KEY_REQUIRED", "缺少 X-VR-Agent-Model-Key 请求头")

    try:
        require_secure_model_key_transport(request)
    except EndpointError as exc:
        return _error_response(exc.code, exc.detail, exc.status)

    forwarded_props = input_data.forwarded_props or {}
    runtime_props_raw = forwarded_props.get("runtime") or {}
    if isinstance(runtime_props_raw, dict) and runtime_props_raw.get("retryOf"):
        return _error_response(
            "RETRY_REQUIRES_DURABLE_HISTORY",
            "重试需要 1B 的持久化运行历史，1A 暂不支持",
        )
    try:
        runtime_props = RuntimeForwardedProps.model_validate(runtime_props_raw or {})
    except Exception:
        return _error_response("INVALID_RUNTIME_PROPS", "forwardedProps.runtime 缺少有效的 model 配置")

    command = forwarded_props.get("command") or {}
    resume_entries = (command.get("resume") or []) if isinstance(command, dict) else []

    try:
        mode = _classify(input_data)
    except ValueError as exc:
        return _error_response("INVALID_REQUEST_SHAPE", str(exc))

    secrets = RunSecrets(model_api_key=model_key)
    model_ref: ModelRef = runtime_props.model
    thread_id = input_data.thread_id
    run_id = input_data.run_id

    model_builder: Callable[[ModelRef, RunSecrets], BaseChatModel] = build_chat_model
    tools: list[BaseTool] = build_builtin_tools()
    middleware = build_middleware()

    try:
        if mode == "steer_away":
            handle = await coordinator.acquire_steer_away(
                thread_id,
                entries=resume_entries,
                validate=_validate_steer_away,
                model_ref=model_ref,
                secrets=secrets,
                model_builder=model_builder,
                tools=tools,
                middleware=middleware,
            )
            adapter_input = _without_resume_command(input_data)
        elif mode == "resume":
            handle, resume_value = await coordinator.acquire_resume(
                thread_id,
                model_ref=model_ref,
                secrets=secrets,
                model_builder=model_builder,
                validate=lambda pending: AgentProtocolBridge("", "", pending=pending).resume_value(resume_entries),
            )
            adapter_input = handle.runtime.resume_input(run_id, resume_value)
        else:
            handle = await coordinator.acquire_start(
                model_ref=model_ref,
                secrets=secrets,
                model_builder=model_builder,
                tools=tools,
                thread_id=thread_id,
                middleware=middleware,
            )
            adapter_input = input_data
    except ThreadBusy as exc:
        return _error_response(exc.code, str(exc), 409)
    except ResumeRejected as exc:
        return _error_response(exc.code, str(exc), 409)
    except ValueError as exc:
        return _error_response("RESUME_REJECTED", str(exc))
    except RunConfigMismatch as exc:
        # spec：resume 的 ModelRef 与活动线程不一致 → 409
        return _error_response(exc.code, str(exc), 409)

    bridge = AgentProtocolBridge(thread_id, run_id, pending=handle.pending_interrupts)
    adapter = handle.runtime.new_adapter(run_id)
    encoder = EventEncoder(accept=request.headers.get("accept"))

    async def event_generator():
        terminal: str = "completed"
        try:
            async for event in adapter.run(adapter_input):
                if await request.is_disconnected():
                    await coordinator.cancel(thread_id)
                    return
                converted_events = bridge.convert(event)
                if isinstance(event, RunFinishedEvent) and bridge.pending:
                    handle.pending_interrupts = list(bridge.pending)
                    await coordinator.mark_awaiting_approval(thread_id)
                    terminal = "awaiting_approval"
                for converted in converted_events:
                    yield encoder.encode(converted)
        except asyncio.CancelledError:
            # anyio 取消作用域里不能 await，用同步路径清理
            coordinator.cancel_sync(thread_id)
            raise
        except Exception as exc:
            message = str(exc).replace(model_key, "[redacted]")[:1000]
            yield encoder.encode(_error_event(message))
            terminal = "failed"
        finally:
            if terminal != "awaiting_approval":
                await coordinator.finish_if_terminal(thread_id, terminal)

    return StreamingResponse(event_generator(), media_type=encoder.get_content_type())
