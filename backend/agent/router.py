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
from fastapi import APIRouter, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field, model_validator

from agent.models import ModelRef, RunSecrets, RunSummary, RuntimeForwardedProps, ThreadDocument
from agent.policy import (
    PolicyCorrupt,
    PolicyInvalid,
    PolicyPatch,
    PolicyReset,
    PolicyRevisionConflict,
    PolicyStore,
)
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
from agent.capabilities import (
    AllowanceRegistry,
    CapabilityResolver,
    McpUnavailable,
    enrich_pending_interrupts,
)
from agent.mcp import (
    McpError,
    McpRegistry,
    McpRevisionConflict,
    McpSecretMissing,
    McpServerNotFound,
    StdioTrustRequired,
)
from agent.skills import SkillError, SkillImporter, SkillRegistry, SkillResourceForbidden, SkillUnavailable
from agent.tool_executor import BoundedToolExecutor
from agent.tool_registry import build_builtin_tools

router = APIRouter(prefix="/api/agent")


@dataclass
class AgentServices:
    paths: AgentPaths
    threads: ThreadStore
    runs: RunStore
    coordinator: RunCoordinator
    skills: SkillRegistry
    importer: SkillImporter
    registry: McpRegistry
    policy: PolicyStore
    executor: BoundedToolExecutor
    builtin_serial_lock: asyncio.Lock


def build_services(root: Path | None = None) -> AgentServices:
    paths = AgentPaths(Path(root) if root is not None else default_agent_root())
    threads = ThreadStore(paths)
    runs = RunStore(paths)
    coordinator = RunCoordinator(factory=AgentFactory(), threads=threads, runs=runs)
    skills = SkillRegistry(paths.skills)
    importer = SkillImporter(paths.skills, skills)
    allowances = AllowanceRegistry()
    registry = McpRegistry.for_root(paths.root, allowances)
    policy = PolicyStore(paths.policy)
    executor = BoundedToolExecutor()
    builtin_serial_lock = asyncio.Lock()

    def _run_tools(skill_tools=()):
        # 测试接缝：1A/1B 通过 monkeypatch agent.router.build_builtin_tools 注入
        return [*build_builtin_tools(), *skill_tools]

    coordinator = RunCoordinator(
        factory=coordinator._factory, threads=threads, runs=runs,
        resolver=CapabilityResolver(skills, tools_provider=_run_tools,
                                    registry=registry, allowances=allowances),
        middleware_provider=lambda: build_middleware(),
        allowances=allowances,
        executor=executor,
        builtin_serial_lock=builtin_serial_lock,
    )
    return AgentServices(paths, threads, runs, coordinator, skills, importer, registry,
                         policy, executor, builtin_serial_lock)


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
    if hasattr(value, "model_copy") and hasattr(value, "__dict__"):
        # 嵌套的 pydantic 事件载荷（如 RunFinishedEvent.outcome）递归脱敏
        return _redact_model(value, secret)
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


# 后台持久化任务的强引用集合（asyncio 文档要求持有 create_task 结果，防 GC）
_BACKGROUND_PERSISTENCE: set[asyncio.Task] = set()

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

    secrets = RunSecrets(model_api_key=model_key)
    model_ref: ModelRef = runtime_props.model
    thread_id = input_data.thread_id
    run_id = input_data.run_id

    model_builder: Callable[[ModelRef, RunSecrets], BaseChatModel] = build_chat_model

    try:
        if mode == "retry":
            admission = await services.coordinator.acquire_retry(
                model_ref=model_ref,
                secrets=secrets,
                model_builder=model_builder,
                thread_id=thread_id,
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
                # thread_id 必须真实：approve+thread_session 的许可以其为键登记
                validate=lambda pending: AgentProtocolBridge(thread_id, "", pending=pending).resume_value_with_allowances(resume_entries),
                protocol_run_id=run_id,
                client_revision=runtime_props.thread_revision,
            )
            adapter_input = handle.runtime.resume_input(run_id, resume_value)
        else:
            admission = await services.coordinator.acquire_start(
                model_ref=model_ref,
                secrets=secrets,
                model_builder=model_builder,
                thread_id=thread_id,
                protocol_run_id=run_id,
                messages=input_data.messages,
                client_revision=runtime_props.thread_revision,
            )
            handle = admission.handle
            adapter_input = admission.input
    except SkillError as exc:
        # 能力错误发生在任何 user/run 写入之前（400/404/409）
        return _skill_error_response(exc)
    except McpUnavailable as exc:
        # fail-closed：相关 MCP server 不可用（流开始前 503，不触发 409 reload）
        return JSONResponse(status_code=503, content={
            "code": "MCP_UNAVAILABLE", "detail": str(exc)})
    except McpError as exc:
        # mcp.json 损坏等结构化 MCP 配置错误（含隔离文件名，无绝对路径）
        return _mcp_error_response(exc)
    except ThreadBusy as exc:
        return _conflict_response(exc.code, str(exc), thread_id=thread_id)
    except ResumeRejected as exc:
        return _conflict_response(exc.code, str(exc), thread_id=thread_id)
    except RetryNotAllowed as exc:
        status = None
        product_run_id = runtime_props.retry_of
        if product_run_id:
            try:
                status = (await asyncio.to_thread(services.runs.get, product_run_id)).status
            except Exception:
                status = None
        return _conflict_response(exc.code, str(exc), thread_id=thread_id,
                                  product_run_id=product_run_id, run_status=status)
    except DuplicateRunActive as exc:
        persisted = await asyncio.to_thread(services.runs.find_by_protocol_run_id, run_id)
        return _conflict_response(exc.code, str(exc), thread_id=thread_id,
                                  product_run_id=persisted.id if persisted else None,
                                  run_status=persisted.status if persisted else "running")
    except DuplicateRunTerminal as exc:
        persisted = await asyncio.to_thread(services.runs.find_by_protocol_run_id, run_id)
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
        # 准入阶段（接受用户消息）的 revision 事件：提交已成功；但 @ag-ui/client
        # 要求流的首个事件必须是 RUN_STARTED，故推迟到其后补发
        pending_initial = [
            encoder.encode(thread_revision_updated(thread_id, revision, utc_now()))
            for revision in initial_revisions
        ]
        try:
            async for event in adapter.run(adapter_input):
                if await request.is_disconnected():
                    await services.coordinator.cancel_run(thread_id, handle.product_run_id)
                    return
                if handle.phase == "cancelled" or (handle.journal is not None and handle.journal.closed):
                    # REST 取消/其他路径已终结本 run：丢弃所有迟到事件
                    return
                converted_events = bridge.convert(event)
                if isinstance(event, RunFinishedEvent):
                    if bridge.pending:
                        # 中断：先富集 MCP 元数据（camelCase + 脱敏参数）再持久化
                        bridge.pending = enrich_pending_interrupts(
                            bridge.pending, handle.capability_lease, model_key)
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
                        # 中断 outcome 携带的工具描述/元数据同样可能夹带密钥：出口统一脱敏
                        safe = redact_event(converted, model_key)
                        yield _redact_frame(encoder.encode(safe), model_key)
                    for frame in pending_initial:
                        yield frame
                    pending_initial = []
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
                    for frame in pending_initial:
                        yield frame
                    pending_initial = []
        except asyncio.CancelledError:
            # anyio 取消作用域里不能普通 await：独立的持久化任务 + 限时 shield 汇合
            persistence = asyncio.create_task(services.coordinator.cancel_run(thread_id, handle.product_run_id))
            # coordinator 级强引用：2 秒汇合超时后任务也不会被回收，直到完成回调观察结果
            _BACKGROUND_PERSISTENCE.add(persistence)

            def _observe_result(done_task):
                if not done_task.cancelled() and done_task.exception() is not None:
                    import sys
                    print(f"agent cancel persistence failed: {done_task.exception()!r}", file=sys.stderr)
            persistence.add_done_callback(lambda done_task: (
                _BACKGROUND_PERSISTENCE.discard(done_task),
                None if done_task.cancelled() or done_task.exception() is None
                else print(f"agent cancel persistence failed: {done_task.exception()!r}", file=__import__("sys").stderr),
            ))

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
    title: str | None = Field(default=None, min_length=1)
    selected_skills: list[str] | None = None

    @model_validator(mode="after")
    def require_change(self):
        if self.title is None and self.selected_skills is None:
            raise ValueError("至少提交 title 或 selected_skills")
        return self


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
    # resume_available 是计算字段，不进持久化 schema
    payload = doc.model_dump(mode="json")
    payload["resume_available"] = services.coordinator.resume_available(doc.id)
    return payload


@router.patch("/threads/{thread_id}")
async def patch_thread(thread_id: str, payload: ThreadPatch):
    try:
        selected = payload.selected_skills
        if selected is not None:
            try:
                # 与一次当前 generation 对照校验 + 去重（写前在 coordinator 线程锁内复检）
                selected = await _validate_selected_skills(selected)
            except SkillError as exc:
                return _skill_error_response(exc)
        updated = await services.coordinator.patch_thread(
            thread_id, payload.revision, payload.title, selected,
        )
    except InvalidDocumentId as exc:
        return _store_error_response(exc, 400)
    except DocumentNotFound as exc:
        return _store_error_response(exc, 404)
    except RevisionConflict as exc:
        return _store_error_response(exc, 409)
    except DocumentCorrupt as exc:
        return _store_error_response(exc, 500)
    return updated.model_dump(mode="json")


class ThreadDelete(BaseModel):
    revision: int = Field(ge=0)


@router.delete("/threads/{thread_id}", status_code=204)
async def delete_thread(thread_id: str, payload: ThreadDelete):
    try:
        await services.coordinator.delete_thread(thread_id, expected_revision=payload.revision)
    except InvalidDocumentId as exc:
        return _store_error_response(exc, 400)
    except ThreadBusy as exc:
        return _store_error_response(exc, 409)
    except DocumentNotFound as exc:
        return _store_error_response(exc, 404)
    except RevisionConflict as exc:
        return _store_error_response(exc, 409)
    except DocumentCorrupt as exc:
        return _store_error_response(exc, 500)
    return Response(status_code=204)


@router.delete("/threads/{thread_id}/allowances")
async def clear_thread_allowances(thread_id: str):
    cleared = services.coordinator.clear_allowances(thread_id)
    return {"cleared": cleared}


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
    await services.coordinator.cancel_run(run.thread_id, run.id)
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


# ---- 1C：Skill 管理 REST ----


def _skill_error_response(exc: Exception, *, scripts_403: bool = True) -> JSONResponse:
    code = getattr(exc, "code", "SKILL_INVALID")
    status = 400
    if code == "SKILL_UNAVAILABLE":
        status = 404
    elif code == "SKILL_CONFLICT":
        status = 409
    elif code == "SKILL_IN_USE":
        status = 409
    elif code == "SKILL_RESOURCE_FORBIDDEN":
        status = 403
    return JSONResponse(status_code=status, content={"code": code, "detail": str(exc)})


async def _validate_selected_skills(names: list[str]) -> list[str]:
    """对照同一个当前 generation 校验并去重；名称缺失返回 400。"""
    generation = await asyncio.to_thread(services.skills.current)
    if generation is None:
        generation = await asyncio.to_thread(services.skills.refresh)
    valid_names = {r.name for r in generation.skills if r.valid}
    deduped: list[str] = []
    for name in names:
        if name not in valid_names:
            raise SkillUnavailable(f"Skill 不存在或不可用: {name}")
        if name not in deduped:
            deduped.append(name)
    return deduped


class SkillFileResponse(BaseModel):
    relative_path: str
    category: str
    size: int
    mtime_ns: int
    sha256: str
    mime: str | None
    downloadable: bool


class SkillRecordResponse(BaseModel):
    directory: str
    name: str | None
    description: str | None
    digest: str | None
    valid: bool
    error_code: str | None = None
    error_detail: str | None = None
    files: list[SkillFileResponse] = []
    instructions: str | None = None  # 仅 detail 返回


def _skill_response(record, *, with_instructions: bool) -> SkillRecordResponse:
    return SkillRecordResponse(
        directory=record.directory, name=record.name, description=record.description,
        digest=record.digest, valid=record.valid, error_code=record.error_code,
        error_detail=record.error_detail,
        files=[SkillFileResponse(**{
            "relative_path": f.relative_path, "category": f.category, "size": f.size,
            "mtime_ns": f.mtime_ns, "sha256": f.sha256, "mime": f.mime,
            "downloadable": f.downloadable}) for f in record.files],
        instructions=record.instructions if with_instructions else None,
    )


@router.get("/skills")
async def list_skills():
    generation = await asyncio.to_thread(services.skills.refresh)
    return {
        "generation": generation.number,
        "skills": [_skill_response(r, with_instructions=False).model_dump() for r in generation.skills],
    }


@router.get("/skills/{skill_name}")
async def get_skill(skill_name: str):
    try:
        record = await asyncio.to_thread(services.skills.require, skill_name)
    except SkillError as exc:
        return _skill_error_response(exc)
    return _skill_response(record, with_instructions=True).model_dump()


@router.post("/skills/import")
async def import_skill(
    archive: UploadFile = File(...),
    overwrite: bool = Form(False),
    expected_digest: str | None = Form(None),
):
    try:
        staged = await services.importer.receive(archive)
        result = await asyncio.to_thread(
            lambda: services.importer.install(
                staged, overwrite=overwrite, expected_digest=expected_digest,
                in_use_check=services.coordinator.skill_in_use,
            )
        )
    except SkillError as exc:
        return _skill_error_response(exc)
    return {"record": _skill_response(result.record, with_instructions=True).model_dump(),
            "created": result.created}


@router.post("/skills/refresh")
async def refresh_skills():
    generation = await asyncio.to_thread(services.skills.refresh)
    return {"generation": generation.number}


@router.delete("/skills/{skill_name}")
async def delete_skill(skill_name: str, expected_digest: str = Query(...)):
    if await asyncio.to_thread(services.coordinator.skill_in_use, skill_name):
        return JSONResponse(status_code=409, content={
            "code": "SKILL_IN_USE", "detail": f"Skill {skill_name} 正在被活跃运行引用"})
    try:
        record = await asyncio.to_thread(services.importer.delete, skill_name, expected_digest)
    except SkillError as exc:
        return _skill_error_response(exc)
    return {"deleted": record.name}


_SAFE_INLINE_MIMES = {
    "image/png", "image/jpeg", "image/webp", "image/gif",
    "application/pdf", "text/plain", "text/markdown", "application/json",
}


@router.get("/skills/{skill_name}/files/{relative_path:path}")
async def get_skill_file(skill_name: str, relative_path: str):
    try:
        record, entry, path = await asyncio.to_thread(
            services.skills.resolve_file, skill_name, relative_path)
    except SkillError as exc:
        return _skill_error_response(exc)
    if not entry.downloadable or entry.mime not in _SAFE_INLINE_MIMES:
        return _skill_error_response(
            SkillResourceForbidden("该资源不允许下载或预览"))
    disposition = "inline" if entry.mime.startswith(("image/", "text/")) or entry.mime in (
        "application/json", "application/pdf") else "attachment"
    filename = f"{record.directory}--{entry.relative_path.replace('/', '--')}"
    return FileResponse(
        path, media_type=entry.mime,
        headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox; default-src 'none'",
            "Content-Disposition": f'{disposition}; filename="{filename}"',
        },
    )



# ---- 1C 切片 2：MCP 管理 REST ----


def _mcp_error_response(exc: Exception) -> JSONResponse:
    code = getattr(exc, "code", "MCP_CONFIG_ERROR")
    if "STDIO_FINGERPRINT_MISMATCH" in str(exc):
        code = "STDIO_FINGERPRINT_MISMATCH"
    status = 400
    if code == "MCP_SERVER_NOT_FOUND":
        status = 404
    elif code in ("MCP_REVISION_CONFLICT", "STDIO_TRUST_REQUIRED",
                  "STDIO_FINGERPRINT_MISMATCH", "MCP_CONFIG_BUSY"):
        status = 409
    elif code == "MCP_CONFIG_CORRUPT":
        status = 500
    payload: dict = {"code": code, "detail": str(exc)}
    if isinstance(exc, StdioTrustRequired):
        payload["preview"] = {
            "executable": exc.preview.executable,
            "resolved_executable": exc.preview.resolved_executable,
            "args": exc.preview.args,
            "fingerprint": exc.preview.fingerprint,
        }
    return JSONResponse(status_code=status, content=payload)


class McpAction(BaseModel):
    revision: int = Field(ge=0)


class McpAdd(BaseModel):
    revision: int = Field(ge=0)
    server: dict


class McpPatch(BaseModel):
    revision: int = Field(ge=0)
    server: dict | None = None
    tool_enabled: dict[str, bool] | None = None


class McpTrust(BaseModel):
    revision: int = Field(ge=0)
    fingerprint: str


@router.get("/mcp")
async def get_mcp_document():
    try:
        doc = await asyncio.to_thread(services.registry.store.load)
    except McpError as exc:
        return _mcp_error_response(exc)
    return doc.model_dump(mode="json")


@router.post("/mcp")
async def add_mcp_server(payload: McpAdd):
    try:
        server = await asyncio.to_thread(services.registry.store.validate_server, payload.server)
        doc = await services.registry.add(server)
    except McpError as exc:
        return _mcp_error_response(exc)
    except Exception as exc:  # pydantic 校验失败 → 400
        return JSONResponse(status_code=400, content={"code": "MCP_CONFIG_ERROR", "detail": str(exc)})
    return doc.model_dump(mode="json")



async def _reject_mcp_busy(server_id: str) -> JSONResponse | None:
    if await asyncio.to_thread(services.coordinator.mcp_server_in_use, server_id):
        return JSONResponse(status_code=409, content={
            "code": "MCP_CONFIG_BUSY",
            "detail": f"MCP server {server_id} 正被活跃运行引用，不能修改配置或重连"})
    return None


@router.patch("/mcp/{server_id}")
async def patch_mcp_server(server_id: str, payload: McpPatch):
    busy = await _reject_mcp_busy(server_id)
    if busy is not None:
        return busy
    from agent.mcp import StdioTransport, StreamableHttpTransport

    def mutate(server):
        updated = server
        if payload.server is not None:
            fields = payload.server
            changes = {}
            for key in ("display_name", "enabled"):
                if key in fields:
                    changes[key] = fields[key]
            if "transport" in fields:
                changes["transport"] = (
                    StdioTransport.model_validate(fields["transport"])
                    if fields["transport"].get("type") == "stdio"
                    else StreamableHttpTransport.model_validate(fields["transport"]))
            updated = server.model_copy(update=changes)
        if payload.tool_enabled is not None:
            tools = []
            for tool in updated.tools:
                enabled = payload.tool_enabled.get(tool.original_name, tool.enabled)
                tools.append(tool.model_copy(update={"enabled": bool(enabled)}))
            updated = updated.model_copy(update={"tools": tools})
        return updated

    try:
        doc = await services.registry.patch_server(server_id, payload.revision, mutate)
    except McpError as exc:
        return _mcp_error_response(exc)
    except Exception as exc:
        return JSONResponse(status_code=400, content={"code": "MCP_CONFIG_ERROR", "detail": str(exc)})
    return doc.model_dump(mode="json")


@router.delete("/mcp/{server_id}")
async def delete_mcp_server(server_id: str, revision: int = Query(...)):
    busy = await _reject_mcp_busy(server_id)
    if busy is not None:
        return busy
    try:
        warnings = await services.registry.delete(server_id, revision)
    except McpError as exc:
        return _mcp_error_response(exc)
    doc = await asyncio.to_thread(services.registry.store.load)
    payload = doc.model_dump(mode="json")
    payload["recovery_warnings"] = warnings
    return payload


@router.post("/mcp/{server_id}/trust")
async def trust_mcp_server(server_id: str, payload: McpTrust):
    try:
        doc = await services.registry.trust(server_id, payload.fingerprint, payload.revision)
    except McpError as exc:
        if "STDIO_FINGERPRINT_MISMATCH" in str(exc):
            preview = await services.registry.trust_preview(server_id)
            return JSONResponse(status_code=409, content={
                "code": "STDIO_FINGERPRINT_MISMATCH",
                "detail": "指纹不匹配",
                "preview": {
                    "executable": preview.executable,
                    "resolved_executable": preview.resolved_executable,
                    "args": preview.args,
                    "fingerprint": preview.fingerprint,
                },
            })
        return _mcp_error_response(exc)
    return doc.model_dump(mode="json")


@router.post("/mcp/{server_id}/test")
async def test_mcp_server(server_id: str, payload: McpAction):
    busy = await _reject_mcp_busy(server_id)
    if busy is not None:
        return busy
    try:
        health = await services.registry.test(server_id)
    except McpError as exc:
        return _mcp_error_response(exc)
    doc = await asyncio.to_thread(services.registry.store.load)
    payload_out = doc.model_dump(mode="json")
    payload_out["health"] = health.model_dump(mode="json")
    return payload_out


@router.post("/mcp/{server_id}/refresh")
async def refresh_mcp_server(server_id: str, payload: McpAction):
    busy = await _reject_mcp_busy(server_id)
    if busy is not None:
        return busy
    try:
        await services.registry.refresh(server_id)
    except McpError as exc:
        return _mcp_error_response(exc)
    doc = await asyncio.to_thread(services.registry.store.load)
    return doc.model_dump(mode="json")



# ---- 1D：Policy REST ----


def _policy_corrupt_response(exc: PolicyCorrupt) -> JSONResponse:
    return JSONResponse(status_code=503, content={"code": "POLICY_CORRUPT", "detail": str(exc)})


@router.get("/policy")
async def get_policy():
    try:
        view = await asyncio.to_thread(services.policy.get)
    except PolicyCorrupt as exc:
        return _policy_corrupt_response(exc)
    return view.model_dump(mode="json")


@router.patch("/policy")
async def patch_policy(payload: dict):
    try:
        patch = PolicyPatch.model_validate(payload)
    except Exception as exc:
        return JSONResponse(status_code=400, content={"code": "POLICY_INVALID", "detail": str(exc)[:500]})
    try:
        view = await asyncio.to_thread(services.policy.patch, patch)
    except PolicyCorrupt as exc:
        return _policy_corrupt_response(exc)
    except PolicyRevisionConflict as exc:
        return JSONResponse(status_code=409, content={
            "code": "POLICY_REVISION_CONFLICT",
            "detail": str(exc),
            "current_revision": exc.current_revision,
        })
    return view.model_dump(mode="json")


@router.post("/policy/reset")
async def reset_policy(payload: dict):
    try:
        reset = PolicyReset.model_validate(payload)
    except Exception as exc:
        return JSONResponse(status_code=400, content={"code": "POLICY_INVALID", "detail": str(exc)[:500]})
    try:
        view = await asyncio.to_thread(services.policy.reset, reset)
    except PolicyCorrupt as exc:
        return _policy_corrupt_response(exc)
    except PolicyRevisionConflict as exc:
        return JSONResponse(status_code=409, content={
            "code": "POLICY_REVISION_CONFLICT",
            "detail": str(exc),
            "current_revision": exc.current_revision,
        })
    except PolicyInvalid as exc:
        return JSONResponse(status_code=400, content={"code": exc.code, "detail": str(exc)})
    return view.model_dump(mode="json")


def _new_thread_id() -> str:
    from uuid import uuid4

    return f"th-{uuid4().hex}"


# ---- 1B：FastAPI 生命周期（启动对账 / 退出清理） ----


async def startup_agent_services() -> None:
    # 调用时再解引用模块级 services，测试可先 monkeypatch 再进入 lifespan
    current = services
    await asyncio.to_thread(reconcile_agent_data, current.paths, current.threads, current.runs)
    # 1C：Skill 导入恢复 + 首次扫描（不连接网络，也不触碰默认数据根以外的目录）
    await asyncio.to_thread(current.importer.recover)
    await asyncio.to_thread(current.skills.refresh)


async def shutdown_agent_services() -> None:
    current = services
    # 1D 停机顺序：先原子拒绝新的同步工具准入，再取消/持久化运行与关闭 MCP，
    # 最后关闭执行器本身（不等待仍在运行的第三方代码）。
    current.executor.begin_shutdown()
    await current.coordinator.shutdown()
    # coordinator lease 释放后排空 MCP 会话
    await current.registry.shutdown()
    current.executor.shutdown()
