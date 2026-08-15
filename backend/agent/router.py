from __future__ import annotations

import asyncio
import ipaddress
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal

from ag_ui.core.events import RunErrorEvent, RunFinishedEvent
from ag_ui.core.types import RunAgentInput
from ag_ui.encoder import EventEncoder
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from agent.models import ModelRef, RunSecrets, RunSummary, RuntimeForwardedProps, ThreadDocument
from agent.protocol import AgentProtocolBridge, PendingInterrupt
from agent.runs import ResumeRejected, RunCoordinator, ThreadBusy
from agent.runtime import AgentFactory, RunConfigMismatch, build_chat_model
from agent.stores import (
    AgentPaths,
    DocumentCorrupt,
    DocumentNotFound,
    InvalidDocumentId,
    RecoveryWarning,
    RevisionConflict,
    RunStore,
    ThreadStore,
    default_agent_root,
    reconcile_agent_data,
    utc_now,
)
from agent.tool_registry import build_builtin_tools

router = APIRouter(prefix="/api/agent")


@dataclass
class AgentServices:
    paths: AgentPaths
    threads: ThreadStore
    runs: RunStore
    coordinator: RunCoordinator


def build_services(root: Path | None = None) -> AgentServices:
    paths = AgentPaths(Path(root) if root is not None else default_agent_root())
    threads = ThreadStore(paths)
    runs = RunStore(paths)
    coordinator = RunCoordinator(factory=AgentFactory(), threads=threads, runs=runs)
    return AgentServices(paths, threads, runs, coordinator)


services = build_services()


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
            handle = await services.coordinator.acquire_steer_away(
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
            handle, resume_value = await services.coordinator.acquire_resume(
                thread_id,
                model_ref=model_ref,
                secrets=secrets,
                model_builder=model_builder,
                validate=lambda pending: AgentProtocolBridge("", "", pending=pending).resume_value(resume_entries),
            )
            adapter_input = handle.runtime.resume_input(run_id, resume_value)
        else:
            handle = await services.coordinator.acquire_start(
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
                    await services.coordinator.cancel(thread_id)
                    return
                converted_events = bridge.convert(event)
                if isinstance(event, RunFinishedEvent) and bridge.pending:
                    handle.pending_interrupts = list(bridge.pending)
                    await services.coordinator.mark_awaiting_approval(thread_id)
                    terminal = "awaiting_approval"
                for converted in converted_events:
                    yield encoder.encode(converted)
        except asyncio.CancelledError:
            # anyio 取消作用域里不能 await，用同步路径清理
            services.coordinator.cancel_sync(thread_id)
            raise
        except Exception as exc:
            message = str(exc).replace(model_key, "[redacted]")[:1000]
            yield encoder.encode(_error_event(message))
            terminal = "failed"
        finally:
            if terminal != "awaiting_approval":
                await services.coordinator.finish_if_terminal(thread_id, terminal)

    return StreamingResponse(event_generator(), media_type=encoder.get_content_type())


# ---- 1B：线程 / 运行 REST ----


class ThreadCreate(BaseModel):
    title: str = "新会话"


class ThreadPatch(BaseModel):
    revision: int = Field(ge=0)
    title: str = Field(min_length=1)


class ThreadSummaryResponse(BaseModel):
    id: str
    title: str
    updated_at: str
    revision: int
    last_run: RunSummary | None = None


class RecoveryWarningResponse(BaseModel):
    code: Literal["DOCUMENT_CORRUPT"]
    document_type: Literal["thread", "run"]
    filename: str


class ThreadListResponse(BaseModel):
    threads: list[ThreadSummaryResponse]
    warnings: list[RecoveryWarningResponse]


def _store_error_response(exc: Exception, status: int) -> JSONResponse:
    code = getattr(exc, "code", "AGENT_STORE_ERROR")
    return JSONResponse(status_code=status, content={"code": code, "detail": str(exc)})


def _summary_of(thread: ThreadDocument) -> ThreadSummaryResponse:
    return ThreadSummaryResponse(
        id=thread.id,
        title=thread.title,
        updated_at=thread.updated_at,
        revision=thread.revision,
        last_run=thread.last_run,
    )


@router.get("/threads", response_model=ThreadListResponse)
async def list_threads():
    threads, thread_warnings = await asyncio.to_thread(services.threads.list_documents)
    run_scan = await asyncio.to_thread(services.runs.scan)
    # run 的 last_run 已经写在线程文档里；这里只把 run 侧隔离文件一并向 UI 报告
    summaries = [_summary_of(t) for t in threads]
    warnings = [
        RecoveryWarningResponse(code=w.code, document_type=w.document_type, filename=w.filename)
        for w in list(thread_warnings) + list(run_scan.warnings)
    ]
    return ThreadListResponse(threads=summaries, warnings=warnings)


@router.post("/threads", status_code=201)
async def create_thread(payload: ThreadCreate):
    thread_id = _new_thread_id()
    doc = ThreadDocument.new(thread_id, payload.title, now=utc_now())
    await asyncio.to_thread(services.threads.create, doc)
    return doc.model_dump(mode="json")


@router.get("/threads/{thread_id}")
async def get_thread(thread_id: str):
    try:
        doc = await asyncio.to_thread(services.threads.get, thread_id)
    except InvalidDocumentId as exc:
        return _store_error_response(exc, 400)
    except DocumentNotFound as exc:
        return _store_error_response(exc, 404)
    except DocumentCorrupt as exc:
        return _store_error_response(exc, 500)
    return doc.model_dump(mode="json")


@router.patch("/threads/{thread_id}")
async def patch_thread(thread_id: str, payload: ThreadPatch):
    try:
        updated = await services.coordinator.patch_thread(thread_id, payload.revision, payload.title)
    except InvalidDocumentId as exc:
        return _store_error_response(exc, 400)
    except DocumentNotFound as exc:
        return _store_error_response(exc, 404)
    except RevisionConflict as exc:
        return _store_error_response(exc, 409)
    except DocumentCorrupt as exc:
        return _store_error_response(exc, 500)
    return updated.model_dump(mode="json")


@router.delete("/threads/{thread_id}", status_code=204)
async def delete_thread(thread_id: str):
    try:
        await services.coordinator.delete_thread(thread_id)
    except InvalidDocumentId as exc:
        return _store_error_response(exc, 400)
    except ThreadBusy as exc:
        return _store_error_response(exc, 409)
    except DocumentNotFound as exc:
        return _store_error_response(exc, 404)
    except DocumentCorrupt as exc:
        return _store_error_response(exc, 500)
    return Response(status_code=204)


@router.get("/runs/{run_id}")
async def get_run(run_id: str):
    try:
        run = await asyncio.to_thread(services.runs.get, run_id)
    except InvalidDocumentId as exc:
        return _store_error_response(exc, 400)
    except DocumentNotFound as exc:
        return _store_error_response(exc, 404)
    except DocumentCorrupt as exc:
        return _store_error_response(exc, 500)
    return run.model_dump(mode="json")


def _new_thread_id() -> str:
    from uuid import uuid4

    return f"th-{uuid4().hex}"


# ---- 1B：FastAPI 生命周期（启动对账 / 退出清理） ----


async def startup_agent_services() -> None:
    # 调用时再解引用模块级 services，测试可先 monkeypatch 再进入 lifespan
    current = services
    await asyncio.to_thread(reconcile_agent_data, current.paths, current.threads, current.runs)


async def shutdown_agent_services() -> None:
    await services.coordinator.shutdown()
