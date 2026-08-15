from __future__ import annotations

import asyncio
import ipaddress
import os
from typing import TypeVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal

from ag_ui.core.events import BaseEvent, RunErrorEvent, RunFinishedEvent
from ag_ui.core.types import RunAgentInput
from ag_ui.encoder import EventEncoder
import anyio
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from agent.models import ModelRef, RunSecrets, RunSummary, RuntimeForwardedProps, ThreadDocument
from agent.protocol import AgentProtocolBridge, PendingInterrupt, thread_revision_updated
from agent.runs import (
    DuplicateRunActive,
    DuplicateRunTerminal,
    MessageConflict,
    ResumeRejected,
    RetryNotAllowed,
    RunCoordinator,
    ThreadBusy,
)
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


def _conflict_response(code: str, detail: str, *, thread_id: str | None = None,
                       product_run_id: str | None = None, run_status: str | None = None) -> JSONResponse:
    """结构化 409：snake_case 线格式（code/detail/thread_id/product_run_id/status）。"""
    return JSONResponse(status_code=409, content={
        "code": code,
        "detail": detail,
        "thread_id": thread_id,
        "product_run_id": product_run_id,
        "status": run_status,
    })


def _error_event(message: str) -> RunErrorEvent:
    return RunErrorEvent(message=message, code="AGENT_RUN_FAILED")


def _redact_value(value, secret: str):
    if isinstance(value, str):
        return value.replace(secret, "[redacted]")
    if isinstance(value, list):
        return [_redact_value(item, secret) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_value(item, secret) for item in value)
    if isinstance(value, set):
        return {_redact_value(item, secret) for item in value}
    if isinstance(value, dict):
        return {key: _redact_value(item, secret) for key, item in value.items()}
    return value


def _redact_model(model, secret: str):
    updates = {key: _redact_value(value, secret) for key, value in model.__dict__.items()}
    return model.model_copy(update=updates)


def redact_event(event, model_key: str):
    """对事件的字符串字段做密钥替换；不改变事件结构。"""
    if not model_key or not hasattr(event, "model_copy"):
        return event
    return _redact_model(event, model_key)


def _redact_frame(frame: str, model_key: str) -> str:
    """字符串级兜底：任何序列化路径漏掉的密钥都在出口替换。"""
    return frame.replace(model_key, "[redacted]") if model_key else frame


def _event_kind(event) -> str:
    return str(getattr(getattr(event, "type", None), "value", getattr(event, "type", "")))


# 只进内存的事件（无 JSON 写，内联观察即可）
_MEMORY_ONLY_KINDS = {
    "TEXT_MESSAGE_START", "TEXT_MESSAGE_CONTENT",
    "TOOL_CALL_START", "TOOL_CALL_ARGS", "TOOL_CALL_END",
}


class _PersistenceFailure(RuntimeError):
    """JSON 提交失败：必须显式告知客户端状态未持久化。"""


def _classify(input_data: RunAgentInput) -> str:
    """按 spec 的合法形状分类（start/resume/steer-away）；混合/畸形形状一律失败关闭。"""
    forwarded_props = input_data.forwarded_props or {}
    command = forwarded_props.get("command") or {}
    resume_entries = (command.get("resume") or []) if isinstance(command, dict) else []
    messages = input_data.messages or []

    has_cancelled = any(isinstance(e, dict) and e.get("status") == "cancelled" for e in resume_entries)
    runtime_raw = forwarded_props.get("runtime") or {}
    retry_of = runtime_raw.get("retryOf") if isinstance(runtime_raw, dict) else None
    if retry_of is not None:
        if resume_entries or messages:
            raise ValueError("retry 不得携带 resume 或新消息")
        return "retry"

    if resume_entries and has_cancelled:
        # steer-away：全部 cancelled + 最后一条是新 user message（前面是客户端历史前缀）
        if not all(isinstance(e, dict) and e.get("status") == "cancelled" for e in resume_entries):
            raise ValueError("steer-away 不能混合 resolved 与 cancelled 条目")
        if not messages or getattr(messages[-1], "role", None) != "user":
            raise ValueError("steer-away 必须携带一条新 user message")
        return "steer_away"
    if resume_entries:
        # 纯 resume：不得携带新消息
        if messages:
            raise ValueError("纯 resume 不得携带新消息（messages 必须为空）")
        return "resume"
    # start：客户端历史前缀 + 最后一条新 user message（前缀由协调器对照服务端校验）
    if not messages or getattr(messages[-1], "role", None) != "user":
        raise ValueError("start 必须以一条新 user message 结尾")
    return "start"


def _validate_steer_away(pending: list[PendingInterrupt], entries: list[dict]) -> None:
    probe = AgentProtocolBridge("", "", pending=pending)
    if not probe.is_steer_away(entries):
        raise ValueError("resume 条目不是全量 cancelled，不能作为 steer-away")


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
    if mode == "retry" and runtime_props.thread_revision is None:
        # 1A 兼容缺省 revision 只覆盖 start/resume/steer-away；retry 必须带权威 revision
        return _error_response("INVALID_RUNTIME_PROPS", "retry 请求必须携带 runtime.threadRevision")

    secrets = RunSecrets(model_api_key=model_key)
    model_ref: ModelRef = runtime_props.model
    thread_id = input_data.thread_id
    run_id = input_data.run_id

    model_builder: Callable[[ModelRef, RunSecrets], BaseChatModel] = build_chat_model
    tools: list[BaseTool] = build_builtin_tools()
    middleware = build_middleware()

    try:
        if mode == "retry":
            admission = await services.coordinator.acquire_retry(
                model_ref=model_ref,
                secrets=secrets,
                model_builder=model_builder,
                tools=tools,
                thread_id=thread_id,
                middleware=middleware,
                protocol_run_id=run_id,
                retry_of=runtime_props.retry_of or "",
                client_revision=runtime_props.thread_revision or 0,
            )
            handle = admission.handle
            adapter_input = admission.input
        elif mode == "steer_away":
            admission = await services.coordinator.acquire_steer_away(
                thread_id,
                entries=resume_entries,
                validate=_validate_steer_away,
                model_ref=model_ref,
                secrets=secrets,
                model_builder=model_builder,
                tools=tools,
                middleware=middleware,
                messages=input_data.messages,
                client_revision=runtime_props.thread_revision,
                protocol_run_id=run_id,
            )
            handle = admission.handle
            adapter_input = admission.input
        elif mode == "resume":
            handle, resume_value = await services.coordinator.acquire_resume(
                thread_id,
                model_ref=model_ref,
                secrets=secrets,
                model_builder=model_builder,
                validate=lambda pending: AgentProtocolBridge("", "", pending=pending).resume_value(resume_entries),
                protocol_run_id=run_id,
                client_revision=runtime_props.thread_revision,
            )
            adapter_input = handle.runtime.resume_input(run_id, resume_value)
        else:
            admission = await services.coordinator.acquire_start(
                model_ref=model_ref,
                secrets=secrets,
                model_builder=model_builder,
                tools=tools,
                thread_id=thread_id,
                middleware=middleware,
                protocol_run_id=run_id,
                messages=input_data.messages,
                client_revision=runtime_props.thread_revision,
            )
            handle = admission.handle
            adapter_input = admission.input
    except ThreadBusy as exc:
        return _conflict_response(exc.code, str(exc), thread_id=thread_id)
    except ResumeRejected as exc:
        return _conflict_response(exc.code, str(exc), thread_id=thread_id)
    except RetryNotAllowed as exc:
        status = None
        product_run_id = runtime_props.retry_of
        if product_run_id:
            try:
                status = services.runs.get(product_run_id).status
            except Exception:
                status = None
        return _conflict_response(exc.code, str(exc), thread_id=thread_id,
                                  product_run_id=product_run_id, run_status=status)
    except DuplicateRunActive as exc:
        return _conflict_response(exc.code, str(exc), thread_id=thread_id,
                                  product_run_id=run_id, run_status="running")
    except DuplicateRunTerminal as exc:
        persisted = services.runs.find_by_protocol_run_id(run_id)
        return _conflict_response(exc.code, str(exc), thread_id=thread_id,
                                  product_run_id=persisted.id if persisted else None,
                                  run_status=persisted.status if persisted else "completed")
    except MessageConflict as exc:
        return _conflict_response(exc.code, str(exc), thread_id=thread_id)
    except RevisionConflict as exc:
        return _conflict_response(exc.code, str(exc), thread_id=thread_id)
    except ValueError as exc:
        return _error_response("RESUME_REJECTED", str(exc))
    except RunConfigMismatch as exc:
        # spec：resume 的 ModelRef 与活动线程不一致 → 409（前端统一走权威重载）
        return _conflict_response(exc.code, str(exc), thread_id=thread_id)

    bridge = AgentProtocolBridge(thread_id, run_id, pending=handle.pending_interrupts)
    adapter = handle.runtime.new_adapter(run_id)
    encoder = EventEncoder(accept=request.headers.get("accept"))

    initial_revisions = list(getattr(locals().get("admission", None), "revisions", []) or [])

    async def _commit_or_fail(commit_fn):
        """执行持久化迁移；失败时抛 _PersistenceFailure（消息已脱敏）。"""
        try:
            return await commit_fn()
        except _PersistenceFailure:
            raise
        except Exception as exc:
            raise _PersistenceFailure(str(exc).replace(model_key, "[redacted]")[:500]) from exc

    async def event_generator():
        terminal: str = "completed"
        try:
            # 准入阶段（接受用户消息）的 revision 事件：提交已成功，先行发出
            for revision in initial_revisions:
                yield encoder.encode(thread_revision_updated(thread_id, revision, utc_now()))

            async for event in adapter.run(adapter_input):
                if await request.is_disconnected():
                    await services.coordinator.cancel_run(thread_id)
                    return
                converted_events = bridge.convert(event)
                if isinstance(event, RunFinishedEvent):
                    if bridge.pending:
                        # 中断：先持久化 pending 元数据 → revision 事件 → 释放 Graph → 再发标准 interrupt
                        handle.pending_interrupts = list(bridge.pending)
                        commits = await _commit_or_fail(
                            lambda: services.coordinator.journal_interrupt(thread_id, bridge.pending)
                        )
                        for commit in commits:
                            yield encoder.encode(thread_revision_updated(commit.thread_id, commit.revision, commit.persisted_at))
                        await services.coordinator.mark_awaiting_approval(thread_id)
                        terminal = "awaiting_approval"
                    else:
                        # 终局：最终 revision 事件必须先于 RUN_FINISHED
                        commits = await _commit_or_fail(
                            lambda: services.coordinator.journal_terminal(thread_id, "completed")
                        )
                        for commit in commits:
                            yield encoder.encode(thread_revision_updated(commit.thread_id, commit.revision, commit.persisted_at))
                        terminal = "completed"
                    for converted in converted_events:
                        yield encoder.encode(converted)
                    continue
                for converted in converted_events:
                    safe_event = redact_event(converted, model_key)
                    if _event_kind(safe_event) in _MEMORY_ONLY_KINDS:
                        # 纯内存累积：不经过工作线程，也不持锁
                        if handle.journal is not None and not handle.journal.closed:
                            handle.journal.observe(safe_event)
                    else:
                        commits = await _commit_or_fail(
                            lambda e=safe_event: services.coordinator.journal_observe(thread_id, e)
                        )
                        for commit in commits:
                            yield encoder.encode(thread_revision_updated(commit.thread_id, commit.revision, commit.persisted_at))
                    yield _redact_frame(encoder.encode(safe_event), model_key)
        except asyncio.CancelledError:
            # anyio 取消作用域里不能普通 await：独立的持久化任务 + 限时 shield 汇合
            persistence = asyncio.create_task(services.coordinator.cancel_run(thread_id))

            def _observe_result(done_task):
                if not done_task.cancelled() and done_task.exception() is not None:
                    import sys
                    print(f"agent cancel persistence failed: {done_task.exception()!r}", file=sys.stderr)
            persistence.add_done_callback(_observe_result)

            with anyio.move_on_after(2, shield=True):
                await asyncio.shield(persistence)
            # 超时也不取消持久化任务：它持有线程锁并在后台完成
            raise
        except _PersistenceFailure as exc:
            yield encoder.encode(RunErrorEvent(
                message=f"运行状态未能持久化，本地历史可能不完整：{exc}",
                code="PERSISTENCE_FAILED",
            ))
            terminal = "failed"
            services.coordinator.cancel_sync(thread_id)
        except Exception as exc:
            message = str(exc).replace(model_key, "[redacted]")[:1000]
            try:
                commits = await _commit_or_fail(
                    lambda: services.coordinator.journal_terminal(thread_id, "failed", "AGENT_RUN_FAILED", message)
                )
                for commit in commits:
                    yield encoder.encode(thread_revision_updated(commit.thread_id, commit.revision, commit.persisted_at))
            except _PersistenceFailure:
                yield encoder.encode(RunErrorEvent(
                    message=f"运行状态未能持久化，本地历史可能不完整：{message}",
                    code="PERSISTENCE_FAILED",
                ))
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


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str):
    try:
        run = await asyncio.to_thread(services.runs.get, run_id)
    except InvalidDocumentId as exc:
        return _store_error_response(exc, 400)
    except DocumentNotFound as exc:
        return _store_error_response(exc, 404)
    except DocumentCorrupt as exc:
        return _store_error_response(exc, 500)
    # 幂等：断连 / REST 取消 / CancelledError 共用同一条持久化迁移
    await services.coordinator.cancel_run(run.thread_id)
    refreshed = await asyncio.to_thread(services.runs.get, run_id)
    return refreshed.model_dump(mode="json")


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
