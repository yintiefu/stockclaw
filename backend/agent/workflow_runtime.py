"""确定性工作流运行时与辅助函数。

负责：
- 确定性投研底稿抓取（并行/串行分区调度、空值判决、缺口标记、固定顺序还原）；
- 确定性底稿摘要生成；
- 上下文预算控制与阶段未产出哨兵替换；
- 模型流式调用与阶段增量事件发射；
- 敏感信息脱敏与稳定错误模型生成；
- 终态与确定性摘要归纳（<= 80 字符）。
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import re
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessageChunk, BaseMessage, HumanMessage, SystemMessage

from agent.policy import fixed_system_policy
from agent.tool_executor import execute_tool, tool_policy
from agent.workflow_events import WorkflowEventEmitter, utc_now
from agent.workflow_loader import DossierConfig, DossierSectionConfig
from agent.workflow_state import (
    DossierResult,
    DossierSection,
    StageResult,
    WorkflowError,
    WorkflowStatus,
    format_stage_context,
    stage_unproduced_sentinel,
)

_META_KEYS = frozenset({"period", "unit", "note", "code", "generated_at", "tracks", "total_cached"})
NO_RECORD_TEXT = "（未取到任何记录：可能确实没有此类事件，也可能是该数据源暂时不可用。两种情况都不得据此推断。）"


def is_payload_empty(value: Any) -> bool:
    """递归判断数据负载是否属于有壳无肉的空数据。"""
    if value is None or value == "" or value == [] or value == {}:
        return True
    if isinstance(value, list):
        return all(is_payload_empty(x) for x in value)
    if isinstance(value, dict):
        for k, v in value.items():
            if k in _META_KEYS:
                continue
            if isinstance(v, (list, dict)):
                if not is_payload_empty(v):
                    return False
            elif isinstance(v, bool):
                if v:
                    return False
            elif isinstance(v, (int, float)):
                if v:
                    return False
            elif v:
                return False
        return True
    return False


def sanitize_error_string(raw: str) -> str:
    """严格脱敏错误文本，消除 Bearer 凭据、API Key、内部 IP 及敏感查询参数。"""
    if not raw:
        return ""
    # 替换 Bearer token
    redacted = re.sub(r"(?i)bearer\s+[A-Za-z0-9_\-\.]+", "Bearer [REDACTED]", raw)
    # 替换各种常见 API Key 格式
    redacted = re.sub(r"(?i)(?:key|token|password|secret|apikey|api_key)[\"':= ]+([A-Za-z0-9_\-\.]{6,})", r"key=[REDACTED]", redacted)
    redacted = re.sub(r"sk-[A-Za-z0-9_\-]{8,}", "[REDACTED_KEY]", redacted)
    # 替换内部 IP 地址
    redacted = re.sub(r"\b(?:10|172\.(?:1[6-9]|2[0-9]|3[01])|192\.168)\.\d{1,3}\.\d{1,3}\b", "[INTERNAL_IP]", redacted)
    # 截短长度防超长 payload
    if len(redacted) > 200:
        redacted = redacted[:190] + "...[redacted]"
    return redacted


def redact_workflow_error(
    err: Exception | str,
    stage_id: str | None = None,
    code: str = "EXECUTION_ERROR",
) -> WorkflowError:
    """脱敏错误信息并生成严格白名单的 WorkflowError。"""
    redacted = sanitize_error_string(str(err))
    return WorkflowError(
        code=code,
        message=redacted or "执行异常",
        stage_id=stage_id,
    )


def _resolve_args(args: dict[str, Any], input_dict: dict[str, Any]) -> dict[str, Any]:
    """替换参数中的 ${input.<field>} 模板引用。"""
    resolved = {}
    for k, v in args.items():
        if isinstance(v, str) and v.startswith("${input.") and v.endswith("}"):
            field_name = v[8:-1]
            resolved[k] = input_dict.get(field_name, "")
        elif isinstance(v, list):
            resolved[k] = [
                input_dict.get(item[8:-1], "") if isinstance(item, str) and item.startswith("${input.") and item.endswith("}") else item
                for item in v
            ]
        else:
            resolved[k] = v
    return resolved


async def collect_dossier_sections(
    code: str,
    config: DossierConfig,
    emitter: WorkflowEventEmitter | None = None,
) -> tuple[list[DossierSection], list[str]]:
    """确定性执行底稿抓取并上报进度。"""
    input_dict = {"code": code}
    sections_by_id: dict[str, DossierSection] = {}
    total = len(config.sections)
    loaded_count = 0

    # 分离 parallel_safe 与 serial
    parallel_sections: list[DossierSectionConfig] = []
    serial_sections: list[DossierSectionConfig] = []
    for s in config.sections:
        if tool_policy(s.tool).value == "parallel_safe":
            parallel_sections.append(s)
        else:
            serial_sections.append(s)

    async def _fetch_one(sec_cfg: DossierSectionConfig) -> DossierSection:
        nonlocal loaded_count
        args = _resolve_args(sec_cfg.args, input_dict)
        title = sec_cfg.title or sec_cfg.id
        try:
            raw_result = await execute_tool(sec_cfg.tool, args)
        except Exception as e:
            raw_result = {"error": str(e)}

        is_err = isinstance(raw_result, dict) and bool(raw_result.get("error"))
        is_empty = is_payload_empty(raw_result)

        if is_err or is_empty:
            if sec_cfg.empty_policy == "allow_no_record" and not is_err:
                status = "no_record"
                summary = "无记录"
                body = NO_RECORD_TEXT
                err_msg = None
            else:
                status = "gap"
                summary = "数据缺失"
                body = NO_RECORD_TEXT
                raw_err = str(raw_result.get("error")) if is_err else "数据为空"
                err_msg = sanitize_error_string(raw_err)
        else:
            status = "ok"
            err_msg = None
            body_str = json.dumps(raw_result, ensure_ascii=False, default=str)
            if len(body_str) > config.section_chars:
                body = body_str[:config.section_chars - 15] + "...[truncated]"
            else:
                body = body_str
            summary = body[:100].replace("\n", " ")

        section = DossierSection(
            id=sec_cfg.id,
            tool=sec_cfg.tool,
            title=title,
            empty_policy=sec_cfg.empty_policy,
            status=status,
            summary=summary,
            body=body,
            error=err_msg,
        )
        loaded_count += 1
        if emitter:
            await emitter.emit(
                "dossier_progress",
                title=title,
                section_id=sec_cfg.id,
                tool=sec_cfg.tool,
                status=status,
                loaded=loaded_count,
                total=total,
            )
        return section

    # 并行执行 parallel_safe 项
    if parallel_sections:
        par_results = await asyncio.gather(*[_fetch_one(s) for s in parallel_sections])
        for s in par_results:
            sections_by_id[s.id] = s

    # 串行执行 serial 项
    for s_cfg in serial_sections:
        s = await _fetch_one(s_cfg)
        sections_by_id[s.id] = s

    # 按配置原始顺序还原
    ordered_sections: list[DossierSection] = []
    missing: list[str] = []
    for s_cfg in config.sections:
        sec = sections_by_id.get(s_cfg.id)
        if sec:
            ordered_sections.append(sec)
            if sec.status == "gap":
                missing.append(sec.title)

    if emitter:
        await emitter.emit(
            "dossier_completed",
            section_count=len(ordered_sections),
            missing_count=len(missing),
        )

    return ordered_sections, missing


def summarize_dossier(
    sections: list[DossierSection],
    missing: list[str],
    max_chars: int = 6000,
) -> str:
    """生成客观投研底稿结构化摘要。"""
    lines = ["【客观事实底稿摘要】"]
    for s in sections:
        lines.append(f"- **{s.title}** ({s.tool}): {s.summary}")
    if missing:
        lines.append(f"\n【数据缺口】：{ '、'.join(missing) }")
    full_text = "\n".join(lines)
    if len(full_text) > max_chars:
        return full_text[:max_chars - 15] + "...[truncated]"
    return full_text


def format_full_dossier_text(
    sections: list[DossierSection],
    missing: list[str],
    code: str = "",
) -> str:
    """生成给角色模型阅读的完整底稿文本。"""
    parts = [f"【客观事实底稿 · {code}】", "以下全部为接口实时拉取的客观数据，不含任何主观观点：", ""]
    for s in sections:
        parts.append(f"## {s.title}（来源工具 {s.tool}）\n{s.body}\n")
    if missing:
        parts.append(f"## 数据缺口\n以下数据本次未取到，立论时不得臆测：{('、'.join(missing))}")
    return "\n".join(parts)


def serialize_stage_context(
    stages: dict[str, StageResult],
    stage_ids: list[str],
    max_chars: int = 24000,
) -> tuple[str, bool]:
    """序列化前序各阶段的输出为上下文文本。"""
    blocks = []
    for sid in stage_ids:
        st = stages.get(sid)
        text = format_stage_context(sid, st)
        blocks.append(f"### 【阶段：{sid}】\n{text}")

    full = "\n\n".join(blocks)
    if len(full) > max_chars:
        return full[:max_chars - 15] + "...[truncated]", True
    return full, False


async def run_stage(
    model: BaseChatModel,
    messages: list[BaseMessage | dict[str, Any]],
    max_chars: int = 1200,
    emitter: WorkflowEventEmitter | None = None,
    stage_id: str = "",
) -> tuple[str, bool]:
    """流式执行单个阶段模型推理并向 emitter 发送增量事件（硬上限控制）。"""
    buf: list[str] = []
    current_len = 0
    truncated = False
    suffix = "...[truncated]"
    suffix_len = len(suffix)
    effective_max = max(0, max_chars - suffix_len) if max_chars > suffix_len else max_chars

    async for chunk in model.astream(messages):
        piece = chunk.content if isinstance(chunk, BaseMessage) else str(chunk)
        if isinstance(piece, list):
            piece = "".join(str(p) for p in piece)
        if not piece:
            continue

        if current_len + len(piece) > effective_max:
            allowed = max(0, effective_max - current_len)
            if allowed > 0:
                buf.append(piece[:allowed])
                current_len += allowed
                if emitter and stage_id:
                    await emitter.emit("stage_delta", stage_id=stage_id, delta=piece[:allowed])
            truncated = True
            break

        buf.append(piece)
        current_len += len(piece)
        if emitter and stage_id:
            await emitter.emit("stage_delta", stage_id=stage_id, delta=piece)

    full_output = "".join(buf).strip()
    if truncated:
        full_output = full_output + suffix

    return full_output, truncated


def finalize_workflow(
    status: WorkflowStatus,
    stage_results: dict[str, StageResult],
    missing: list[str],
    workflow_type: str = "workflow",
) -> tuple[WorkflowStatus, str]:
    """计算工作流终态与不超过 80 字符的确定性摘要。"""
    completed_count = sum(1 for s in stage_results.values() if s.status == "completed")
    total_stages = len(stage_results)
    missing_count = len(missing)

    if status == "completed":
        summary = f"{workflow_type} 完成：{completed_count}/{total_stages} 阶段完成，{missing_count} 项缺口"
    elif status == "failed":
        summary = f"{workflow_type} 失败：已完成 {completed_count}/{total_stages} 阶段"
    elif status == "cancelled":
        summary = f"{workflow_type} 已取消：停留在 {completed_count}/{total_stages} 阶段"
    else:
        summary = f"{workflow_type} 状态：{status} ({completed_count}/{total_stages})"

    if len(summary) > 80:
        summary = summary[:77] + "..."
    return status, summary
