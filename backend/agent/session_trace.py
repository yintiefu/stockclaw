"""Agent 会话调用链路追踪：LangChain middleware 写入按线程组织的 JSONL。

每个 run 的模型调用 / 工具调用 / HITL 拒绝 / 耗时 / token 用量实时追加到
``~/.vibe-research/agent/traces/<thread_id>.jsonl``（可用 ``trace.dir`` 覆盖），
可 ``tail -f`` / ``jq`` 消费。全部本地，零云端依赖。

硬性约束：
- 追踪的任何失败都不得影响 agent 运行——所有写入 try/except 包住、仅打 stderr，
  且首错熔断（写入器自禁用，整个进程只告警一次）；
- 中间件实例是进程级单例（graph.py 模块加载时一次性装配），一切可变状态按
  ``(thread_id, run_id)`` 键控隔离，FIFO 上限 1024 防中断 run 泄漏；
- 密钥绝不进入追踪文件：事件只含消息内容与工具入参/结果，绝不 dump settings 对象。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sys
import time
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Any

from langchain.agents.middleware import (
    AgentMiddleware,
    ExtendedModelResponse,
    ModelRequest,
    ModelResponse,
    ToolCallRequest,
)
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage

from agent.settings import TraceSettings

PREVIEW_LIMIT = 2000
_THREAD_ID_SAFE = re.compile(r"^[a-zA-Z0-9_-]+$")
_MAX_TRACKED_RUNS = 1024


def _now_iso() -> str:
    """钩子执行时的墙钟，本地时区、毫秒精度（秒级会丢快速调用的先后序）。"""
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def _text_preview(content: Any, limit: int = PREVIEW_LIMIT) -> str:
    if isinstance(content, list):
        text = "".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content
        )
    else:
        text = str(content)
    return text[:limit]


# ---------------------------------------------------------------------------
# 事件构造（纯函数，便于单测）
# ---------------------------------------------------------------------------

def build_run_start(thread_id: str, run_id: str) -> dict[str, Any]:
    return {"ts": _now_iso(), "event": "run_start", "thread_id": thread_id, "run_id": run_id}


def build_model_call(
    run_id: str,
    seq: int,
    duration_ms: int,
    model: str | None,
    input_tokens: int | None,
    output_tokens: int | None,
    tool_calls: list[dict[str, Any]],
    content_preview: str,
) -> dict[str, Any]:
    return {
        "ts": _now_iso(),
        "event": "model_call",
        "run_id": run_id,
        "seq": seq,
        "duration_ms": duration_ms,
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "tool_calls": tool_calls,
        "content_preview": content_preview,
    }


def build_tool_call(
    run_id: str,
    seq: int,
    name: str,
    args: Any,
    duration_ms: int,
    status: str,
    result_preview: str | None = None,
    result_chars: int | None = None,
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "ts": _now_iso(),
        "event": "tool_call",
        "run_id": run_id,
        "seq": seq,
        "name": name,
        "args": args,
        "duration_ms": duration_ms,
        "status": status,
    }
    if result_preview is not None:
        event["result_preview"] = result_preview
    if result_chars is not None:
        event["result_chars"] = result_chars
    return event


def build_hitl_reject(run_id: str, seq: int, name: str, tool_call_id: str, content: str) -> dict[str, Any]:
    return {
        "ts": _now_iso(),
        "event": "hitl_reject",
        "run_id": run_id,
        "seq": seq,
        "name": name,
        "tool_call_id": tool_call_id,
        "status": "error",
        "content": _text_preview(content),
    }


def build_run_end(run_id: str, status: str, total_ms: int) -> dict[str, Any]:
    return {"ts": _now_iso(), "event": "run_end", "run_id": run_id, "status": status, "total_ms": total_ms}


# ---------------------------------------------------------------------------
# 追加写入器：异常隔离 + 首错熔断 + thread_id 清洗 + parent 断言
# ---------------------------------------------------------------------------

def _safe_thread_filename(thread_id: str) -> str:
    """白名单之外的 thread_id（路径穿越、斜杠等）用 sha1 摘要替代。"""
    if _THREAD_ID_SAFE.match(thread_id or ""):
        return f"{thread_id}.jsonl"
    digest = hashlib.sha1((thread_id or "empty").encode("utf-8", "replace")).hexdigest()
    return f"{digest}.jsonl"


class _TraceWriter:
    """每事件 open-append-close 的单写者追加写入器；首错自禁用（熔断）。"""

    def __init__(self, traces_dir: Path) -> None:
        self._dir = traces_dir
        self._dir_ready = False
        self._disabled = False

    def emit(self, thread_id: str, payload: dict[str, Any]) -> None:
        if self._disabled:
            return
        try:
            if not self._dir_ready:
                self._dir.mkdir(parents=True, exist_ok=True)
                self._dir = self._dir.resolve()
                self._dir_ready = True
            path = self._dir / _safe_thread_filename(thread_id)
            if path.resolve().parent != self._dir:
                raise AssertionError(f"追踪文件越出 traces 目录：{path}")
            line = json.dumps(payload, ensure_ascii=False, default=str)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except Exception as exc:  # noqa: BLE001 —— 追踪绝不能拖垮 agent 运行
            self._disabled = True
            print(f"警告：Agent 追踪写入失败，本进程已停用追踪：{exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# 中间件
# ---------------------------------------------------------------------------

def _extract_model_info(result: Any) -> dict[str, Any]:
    """从 wrap_model_call 返回值防御式提取 usage / model_name / tool_calls / 预览。

    返回形状可能是 ``ModelResponse`` 或 ``ExtendedModelResponse``（含 Command 包装）；
    流式上游常缺 usage chunk——契约：token 记 ``None``，不报错、不硬填 0。
    """
    if isinstance(result, ExtendedModelResponse):
        result = result.model_response
    if isinstance(result, ModelResponse):
        messages: list[BaseMessage] = list(result.result)
    else:
        messages = [result] if isinstance(result, BaseMessage) else []
    ai = next((m for m in reversed(messages) if isinstance(m, AIMessage)), None)
    if ai is None:
        return {"model": None, "input_tokens": None, "output_tokens": None,
                "tool_calls": [], "content_preview": ""}
    usage = getattr(ai, "usage_metadata", None) or {}
    return {
        "model": (getattr(ai, "response_metadata", None) or {}).get("model_name"),
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "tool_calls": [
            {"name": call.get("name"), "args": call.get("args")}
            for call in (ai.tool_calls or [])
        ],
        "content_preview": _text_preview(ai.content),
    }


def _extract_tool_result(result: Any) -> tuple[str | None, int | None]:
    if isinstance(result, ToolMessage):
        text = str(result.content)
        return _text_preview(text), len(text)
    if isinstance(result, BaseMessage):
        text = str(result.content)
        return _text_preview(text), len(text)
    if result is None:
        return None, None
    text = str(result)
    return _text_preview(text), len(text)


class SessionTraceMiddleware(AgentMiddleware):
    """置于 middleware 列表第一位（wrap 链最外层，计时最接近真实耗时）。"""

    def __init__(self, trace: TraceSettings) -> None:
        super().__init__()
        self._writer = _TraceWriter(trace.dir)
        # (thread_id, run_id) -> 状态；FIFO 有界，防中断 run 泄漏
        self._seq: OrderedDict[tuple[str, str], int] = OrderedDict()
        self._start: OrderedDict[tuple[str, str], float] = OrderedDict()
        self._emitted_rejects: OrderedDict[tuple[str, str], set[str]] = OrderedDict()
        # run 内经 wrap_tool_call 真正分发的 tool_call_id——HITL 人造拒绝消息的 id 不在其中
        self._executed: OrderedDict[tuple[str, str], set[str]] = OrderedDict()

    # -- run 级状态 -----------------------------------------------------------

    def _touch(self, key: tuple[str, str]) -> None:
        for table in (self._seq, self._start, self._emitted_rejects, self._executed):
            table.pop(key, None)
            table[key] = 0 if table is self._seq else (0.0 if table is self._start else set())
            if len(table) > _MAX_TRACKED_RUNS:
                table.popitem(last=False)

    def _next_seq(self, key: tuple[str, str]) -> int:
        self._seq[key] = self._seq.get(key, 0) + 1
        return self._seq[key]

    @staticmethod
    def _ids(runtime: Any) -> tuple[str, str]:
        info = getattr(runtime, "execution_info", None)
        thread_id = getattr(info, "thread_id", None)
        run_id = getattr(info, "run_id", None)
        if not thread_id:
            try:
                from langgraph.config import get_config
                thread_id = get_config().get("configurable", {}).get("thread_id")
            except Exception:
                thread_id = None
        return str(thread_id or "unknown"), str(run_id or "unknown")

    # -- 钩子：run 生命周期 ----------------------------------------------------

    def before_agent(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        key = self._ids(runtime)
        self._touch(key)
        self._seq[key] = 0
        self._start[key] = time.perf_counter()
        self._emitted_rejects[key] = set()
        thread_id, run_id = key
        self._writer.emit(thread_id, build_run_start(thread_id, run_id))
        return None

    async def abefore_agent(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        # LangGraph Server 会拦截事件循环线程内的阻塞文件 IO，写入必须放到工作线程
        return await asyncio.to_thread(self.before_agent, state, runtime)

    def after_agent(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        key = self._ids(runtime)
        thread_id, run_id = key
        start = self._start.get(key)
        total_ms = int((time.perf_counter() - start) * 1000) if start is not None else 0
        self._writer.emit(thread_id, build_run_end(run_id, "success", total_ms))
        for table in (self._seq, self._start, self._emitted_rejects, self._executed):
            table.pop(key, None)
        return None

    async def aafter_agent(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        return await asyncio.to_thread(self.after_agent, state, runtime)

    # -- 钩子：模型调用计时 ----------------------------------------------------

    def _record_model_call(self, key: tuple[str, str], started: float, result: Any) -> Any:
        info = _extract_model_info(result)
        thread_id, run_id = key
        self._writer.emit(thread_id, build_model_call(
            run_id=run_id,
            seq=self._next_seq(key),
            duration_ms=int((time.perf_counter() - started) * 1000),
            model=info["model"],
            input_tokens=info["input_tokens"],
            output_tokens=info["output_tokens"],
            tool_calls=info["tool_calls"],
            content_preview=info["content_preview"],
        ))
        return result

    def wrap_model_call(self, request: ModelRequest, handler: Any) -> Any:
        key = self._ids(request.runtime)
        started = time.perf_counter()
        result = handler(request)
        return self._record_model_call(key, started, result)

    async def awrap_model_call(self, request: ModelRequest, handler: Any) -> Any:
        key = self._ids(request.runtime)
        started = time.perf_counter()
        result = await handler(request)
        return await asyncio.to_thread(self._record_model_call, key, started, result)

    # -- 钩子：工具调用计时 ----------------------------------------------------

    def _mark_executed(self, key: tuple[str, str], request: ToolCallRequest) -> None:
        call_id = request.tool_call.get("id")
        if call_id:
            self._executed.setdefault(key, set()).add(str(call_id))

    def _record_tool_error(self, key: tuple[str, str], started: float, request: ToolCallRequest, exc_text: str) -> None:
        thread_id, run_id = key
        self._writer.emit(thread_id, build_tool_call(
            run_id=run_id, seq=self._next_seq(key),
            name=str(request.tool_call.get("name")),
            args=request.tool_call.get("args"),
            duration_ms=int((time.perf_counter() - started) * 1000),
            status="error", result_preview=_text_preview(exc_text), result_chars=len(exc_text),
        ))

    def _record_tool_call(
        self, key: tuple[str, str], started: float, request: ToolCallRequest,
        result: Any, status: str,
    ) -> Any:
        thread_id, run_id = key
        preview, chars = _extract_tool_result(result)
        self._writer.emit(thread_id, build_tool_call(
            run_id=run_id,
            seq=self._next_seq(key),
            name=str(request.tool_call.get("name")),
            args=request.tool_call.get("args"),
            duration_ms=int((time.perf_counter() - started) * 1000),
            status=status,
            result_preview=preview,
            result_chars=chars,
        ))
        return result

    def wrap_tool_call(self, request: ToolCallRequest, handler: Any) -> Any:
        key = self._ids(getattr(request, "runtime", None))
        self._mark_executed(key, request)
        started = time.perf_counter()
        try:
            result = handler(request)
        except Exception as exc:
            self._record_tool_error(key, started, request, str(exc))
            raise
        return self._record_tool_call(key, started, request, result, "ok")

    async def awrap_tool_call(self, request: ToolCallRequest, handler: Any) -> Any:
        key = self._ids(getattr(request, "runtime", None))
        self._mark_executed(key, request)
        started = time.perf_counter()
        try:
            result = await handler(request)
        except Exception as exc:
            await asyncio.to_thread(
                self._record_tool_error, key, started, request, str(exc))
            raise
        return await asyncio.to_thread(self._record_tool_call, key, started, request, result, "ok")

    # -- 钩子：HITL 拒绝识别 ---------------------------------------------------

    def after_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        key = self._ids(runtime)
        thread_id, run_id = key
        messages = list(state.get("messages", [])) if isinstance(state, dict) else []
        if not messages:
            return None
        # HITL 拒绝不经过 ToolNode：人造 ToolMessage(status=error) 的 tool_call_id
        # 从未被 wrap_tool_call 分发（真正的工具报错会先经 wrap_tool_call 记录）。
        executed = self._executed.setdefault(key, set())
        seen = self._emitted_rejects.setdefault(key, set())
        for message in messages:
            if not isinstance(message, ToolMessage):
                continue
            if message.tool_call_id in executed or message.tool_call_id in seen:
                continue
            seen.add(message.tool_call_id)
            self._writer.emit(thread_id, build_hitl_reject(
                run_id=run_id, seq=self._next_seq(key),
                name=str(message.name or "unknown"),
                tool_call_id=str(message.tool_call_id),
                content=str(message.content),
            ))
        return None

    async def aafter_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        return await asyncio.to_thread(self.after_model, state, runtime)


# ---------------------------------------------------------------------------
# CLI 查看器：python -m agent.session_trace [list|show <thread_id>] [--traces-dir DIR]
# ---------------------------------------------------------------------------

def _default_traces_dir() -> Path:
    try:
        from agent.settings import load_agent_settings
        return load_agent_settings().trace.dir
    except Exception:
        return (Path.home() / ".vibe-research" / "agent" / "traces").resolve()


def _fmt_tokens(value: Any) -> str:
    return "—" if value is None else str(value)


def _cmd_list(traces_dir: Path) -> int:
    if not traces_dir.is_dir():
        print(f"追踪目录不存在：{traces_dir}")
        return 1
    files = sorted(traces_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        print(f"（{traces_dir} 下暂无追踪文件）")
        return 0
    print(f"{'线程':38} {'行数':>6}  最后时间")
    for path in files:
        lines = path.read_text(encoding="utf-8").splitlines()
        last_ts = ""
        for line in reversed(lines):
            try:
                last_ts = json.loads(line).get("ts", "")
                if last_ts:
                    break
            except json.JSONDecodeError:
                continue
        print(f"{path.stem:38} {len(lines):>6}  {last_ts}")
    return 0


def _cmd_show(traces_dir: Path, thread_id: str, raw: bool) -> int:
    path = traces_dir / _safe_thread_filename(thread_id)
    if not path.is_file():
        print(f"追踪文件不存在：{path}")
        return 1
    lines = path.read_text(encoding="utf-8").splitlines()
    if raw:
        for line in lines:
            print(line)
        return 0
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            print(f"（无法解析的行：{line[:60]}…）")
            continue
        kind = event.get("event")
        ts = str(event.get("ts", ""))[11:23]
        seq = event.get("seq")
        if kind == "run_start":
            print(f"\n=== run {event.get('run_id', '')} 开始（{event.get('ts', '')}）===")
        elif kind == "model_call":
            calls = ", ".join(c.get("name", "?") for c in event.get("tool_calls", []))
            suffix = f" → 调用工具[{calls}]" if calls else ""
            print(f"  [{ts}] #{seq} 模型 {event.get('model') or '—'} "
                  f"{event.get('duration_ms')}ms "
                  f"tokens {_fmt_tokens(event.get('input_tokens'))}/{_fmt_tokens(event.get('output_tokens'))}"
                  f"（缺失记 —）{suffix}")
        elif kind == "tool_call":
            preview = str(event.get("result_preview", ""))[:120].replace("\n", " ")
            print(f"  [{ts}] #{seq} 工具 {event.get('name')} "
                  f"{event.get('duration_ms')}ms [{event.get('status')}] {preview}")
        elif kind == "hitl_reject":
            print(f"  [{ts}] #{seq} ✗ HITL 拒绝 {event.get('name')} "
                  f"(id={event.get('tool_call_id')})：{str(event.get('content', ''))[:80]}")
        elif kind == "run_end":
            print(f"=== run 结束 status={event.get('status')} 共 {event.get('total_ms')}ms ===")
    return 0


def main(argv: list[str] | None = None) -> int:
    import argparse

    dir_parent = argparse.ArgumentParser(add_help=False)
    # SUPPRESS：子命令解析时不得用默认值覆盖主解析器已收到的 --traces-dir
    dir_parent.add_argument("--traces-dir", type=Path, default=argparse.SUPPRESS,
                            help="追踪目录（默认读 agent settings 的 trace.dir）")
    parser = argparse.ArgumentParser(
        prog="python -m agent.session_trace",
        description="查看 Agent 工作台调用链路追踪（JSONL）",
        parents=[dir_parent],
    )
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("list", parents=[dir_parent], help="列出追踪目录下的线程（默认行为）")
    show = sub.add_parser("show", parents=[dir_parent], help="按 run 分组渲染某线程的时间线")
    show.add_argument("thread_id")
    show.add_argument("--raw", action="store_true", help="输出 jq-friendly 原文")
    args = parser.parse_args(argv)

    traces_dir = (getattr(args, "traces_dir", None) or _default_traces_dir()).expanduser().resolve()
    if args.command == "show":
        return _cmd_show(traces_dir, args.thread_id, args.raw)
    return _cmd_list(traces_dir)


if __name__ == "__main__":
    raise SystemExit(main())
