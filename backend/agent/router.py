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
from agent.protocol import AgentProtocolBridge
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

    secrets = RunSecrets(model_api_key=model_key)
    model_ref: ModelRef = runtime_props.model
    thread_id = input_data.thread_id
    run_id = input_data.run_id

    command = forwarded_props.get("command") or {}
    resume_entries = (command.get("resume") or []) if isinstance(command, dict) else []

    # —— 分类：start / resume / steer-away ——
    if resume_entries and any(isinstance(entry, dict) and entry.get("status") == "cancelled" for entry in resume_entries):
        mode = "steer_away"
    elif resume_entries:
        mode = "resume"
    else:
        mode = "start"

    model_builder: Callable[[ModelRef, RunSecrets], BaseChatModel] = build_chat_model
    tools: list[BaseTool] = build_builtin_tools()
    middleware = build_middleware()

    if mode == "steer_away":
        # 校验全部 pending bridge ID，然后关闭旧句柄、按新消息全新起跑
        active = coordinator.active(thread_id)
        if active is None or not active.pending_interrupts:
            return _error_response("RESUME_REJECTED", "没有待处理的审批中断", 409)
        try:
            probe = AgentProtocolBridge(thread_id, run_id, pending=active.pending_interrupts)
            if not probe.is_steer_away(resume_entries):
                return _error_response("RESUME_REJECTED", "混合/部分取消的 resume 不被支持")
        except ValueError as exc:
            return _error_response("RESUME_REJECTED", str(exc))
        await coordinator.steer_away(thread_id)
        try:
            handle = await coordinator.acquire_start(
                model_ref=model_ref,
                secrets=secrets,
                model_builder=model_builder,
                tools=tools,
                thread_id=thread_id,
                middleware=middleware,
            )
        except ThreadBusy as exc:
            return _error_response(exc.code, str(exc), 409)
        adapter_input = _without_resume_command(input_data)
    elif mode == "resume":
        try:
            active = await coordinator.acquire_resume(thread_id)
        except ResumeRejected as exc:
            return _error_response(exc.code, str(exc), 409)
        bridge_probe = AgentProtocolBridge(thread_id, run_id, pending=active.pending_interrupts)
        try:
            resume_value = bridge_probe.resume_value(resume_entries)
        except ValueError as exc:
            return _error_response("RESUME_REJECTED", str(exc))
        try:
            await coordinator.rebuild_graph(active, model_ref=model_ref, secrets=secrets, model_builder=model_builder)
        except RunConfigMismatch as exc:
            return _error_response(exc.code, str(exc))
        # 先转 running 再清 pending —— 校验失败时列表保持原状
        active.pending_interrupts = []
        handle = active
        adapter_input = handle.runtime.resume_input(run_id, resume_value)
    else:
        try:
            handle = await coordinator.acquire_start(
                model_ref=model_ref,
                secrets=secrets,
                model_builder=model_builder,
                tools=tools,
                thread_id=thread_id,
                middleware=middleware,
            )
        except ThreadBusy as exc:
            return _error_response(exc.code, str(exc), 409)
        adapter_input = input_data

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
