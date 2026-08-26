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
from agent.workflow_loader import DossierConfig, DossierSectionConfig, StageConfig
from agent.workflow_state import (
    DossierResult,
    DossierSection,
    StageResult,
    WorkflowError,
    WorkflowStatus,
    coerce_stage_map,
    format_stage_context,
    stage_unproduced_sentinel,
)

_META_KEYS = frozenset({"period", "unit", "note", "code", "generated_at", "tracks", "total_cached"})
NO_RECORD_TEXT = "（未取到任何记录：可能确实没有此类事件，也可能是该数据源暂时不可用。两种情况都不得据此推断。）"
_TRUNCATION_MARKER = "...[truncated]"


def _truncate_text(text: str, max_chars: int) -> tuple[str, bool]:
    """按硬字符上限截断；极小上限也绝不因标记而突破预算。"""
    limit = max(0, max_chars)
    if len(text) <= limit:
        return text, False
    marker = _TRUNCATION_MARKER[:limit]
    body_chars = limit - len(marker)
    return text[:body_chars] + marker, True


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


ERROR_CODE_MESSAGES: dict[str, str] = {
    "AUTH_ERROR": "模型服务鉴权失败，请检查 API Key 或访问权限",
    "RATE_LIMITED": "上游接口请求被限流，请稍后重试",
    "NETWORK_TIMEOUT": "网络连接超时或上游服务无响应",
    "MODEL_UNAVAILABLE": "模型服务不可用或返回格式异常",
    "DATA_SOURCE_ERROR": "客观数据源查询失败或无有效数据",
    "NO_SUBSTANTIVE_DATA": "全部客观数据源抓取失败或为空，分析必须有客观底稿支撑",
    "CONTEXT_OVERFLOW": "上下文或提示词长度超出最大允许预算",
    "EXECUTION_ERROR": "工作流执行阶段发生内部异常",
    "MODEL_ERROR": "模型推理执行异常",
    "CANCELLED": "工作流已由用户取消",
}

_RETRYABLE_ERROR_CODES = frozenset({
    "RATE_LIMITED", "NETWORK_TIMEOUT", "MODEL_UNAVAILABLE", "DATA_SOURCE_ERROR", "MODEL_ERROR",
})


class WorkflowContextOverflow(RuntimeError):
    """固定 prompt 自身已超出配置预算。"""

    def __init__(self) -> None:
        super().__init__("CONTEXT_OVERFLOW：固定提示词与用户输入超出上下文预算")


def classify_error(err: Exception | str, default_code: str = "EXECUTION_ERROR") -> tuple[str, str]:
    """将任意异常安全归类为稳定错误码与固定安全中文文案，严禁泄露原始报错与上游正文。"""
    err_str = str(err).lower()

    if "auth" in err_str or "unauthorized" in err_str or "401" in err_str or "403" in err_str or "api_key" in err_str:
        return "AUTH_ERROR", ERROR_CODE_MESSAGES["AUTH_ERROR"]
    elif "rate" in err_str or "429" in err_str or "quota" in err_str or "throttle" in err_str or "限流" in err_str:
        return "RATE_LIMITED", ERROR_CODE_MESSAGES["RATE_LIMITED"]
    elif "timeout" in err_str or "timed out" in err_str or "connect" in err_str or "econn" in err_str or "超时" in err_str:
        return "NETWORK_TIMEOUT", ERROR_CODE_MESSAGES["NETWORK_TIMEOUT"]
    elif "model" in err_str or "bad gateway" in err_str or "502" in err_str or "503" in err_str:
        return "MODEL_UNAVAILABLE", ERROR_CODE_MESSAGES["MODEL_UNAVAILABLE"]
    elif "dossier" in err_str or "tool" in err_str:
        return "DATA_SOURCE_ERROR", ERROR_CODE_MESSAGES["DATA_SOURCE_ERROR"]

    code = default_code or "EXECUTION_ERROR"
    return code, ERROR_CODE_MESSAGES.get(code, "执行异常")


def redact_workflow_error(
    err: Exception | str,
    stage_id: str | None = None,
    code: str = "EXECUTION_ERROR",
) -> WorkflowError:
    """脱敏错误信息并生成严格白名单的 WorkflowError（纯稳定错误码与固定中文文案）。"""
    resolved_code, message = classify_error(err, default_code=code)
    return WorkflowError(
        code=resolved_code,
        message=message,
        retryable=resolved_code in _RETRYABLE_ERROR_CODES,
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
    all_sections: list[DossierSection] = []
    total_count = len(config.sections)
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

        if is_err:
            status = "failed"
            summary = "数据源查询失败"
            body = NO_RECORD_TEXT
            err_msg = "客观数据源查询异常"
        elif is_empty:
            if sec_cfg.empty_policy == "allow_no_record":
                status = "no_record"
                summary = "无记录"
                body = NO_RECORD_TEXT
                err_msg = None
            else:
                status = "gap"
                summary = "数据缺失"
                body = NO_RECORD_TEXT
                err_msg = "客观数据源查询异常或无记录" if is_err else "数据为空"
        else:
            status = "completed"
            err_msg = None
            body_str = json.dumps(raw_result, ensure_ascii=False, default=str)
            body, _ = _truncate_text(body_str, config.section_chars)
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
                "dossier.progress",
                section_id=sec_cfg.id,
                section_status=status,
                completed=loaded_count,
                total=total_count,
            )
        return section

    # 并行执行 non-throttled sections
    if parallel_sections:
        results = await asyncio.gather(*[_fetch_one(s) for s in parallel_sections])
        all_sections.extend(results)

    # 串行执行 throttled sections (严守东财串行限流纪律)
    for s in serial_sections:
        res = await _fetch_one(s)
        all_sections.extend([res])

    # 按照原始 sections 声明顺序重排
    order_map = {s.id: i for i, s in enumerate(config.sections)}
    all_sections.sort(key=lambda x: order_map.get(x.id, 999))

    missing = [s.title for s in all_sections if s.status == "gap"]
    missing.extend(s.title for s in all_sections if s.status == "failed")
    has_substantive_data = any(section.status == "completed" for section in all_sections)
    if emitter:
        await emitter.emit(
            "dossier.ready",
            completed=len(all_sections),
            missing=missing,
            has_substantive_data=has_substantive_data,
        )

    return all_sections, missing


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
    return _truncate_text(full_text, max_chars)[0]


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
    stages = coerce_stage_map(stages)
    blocks = []
    for sid in stage_ids:
        st = stages.get(sid)
        text = format_stage_context(sid, st)
        blocks.append(f"### 【阶段：{sid}】\n{text}")

    full = "\n\n".join(blocks)
    return _truncate_text(full, max_chars)


def _context_candidates(
    stage: StageConfig,
    dossier: DossierResult | None,
    stages: dict[str, StageResult],
    preceding_stage_ids: list[str],
) -> list[tuple[str, str]]:
    """按固定优先级生成可裁剪上下文块。"""
    candidates: list[tuple[str, str]] = []
    explicit_stage_ids = [
        ref.removeprefix("stage.") for ref in stage.context if ref.startswith("stage.")
    ]
    if "stages" in stage.context:
        explicit_stage_ids.extend(preceding_stage_ids)
    selected_stage_ids = [
        stage_id for stage_id in preceding_stage_ids if stage_id in set(explicit_stage_ids)
    ]
    for stage_id in selected_stage_ids:
        candidates.append((
            f"stage.{stage_id}",
            f"【前序阶段 {stage_id}】\n{format_stage_context(stage_id, stages.get(stage_id))}",
        ))

    if dossier is not None and "dossier.summary" in stage.context:
        candidates.append(("dossier.summary", f"【底稿摘要】\n{dossier.summary}"))
    if dossier is not None and "dossier.missing" in stage.context:
        missing_text = "、".join(dossier.missing) if dossier.missing else "无"
        candidates.append(("dossier.missing", f"【数据缺口】\n{missing_text}"))
    if dossier is not None and "dossier" in stage.context:
        for section in dossier.sections:
            candidates.append((
                f"dossier.{section.id}",
                f"【底稿 {section.id} · {section.title}】\n{section.body}",
            ))
        if dossier.missing:
            candidates.append(("dossier.missing", f"【数据缺口】\n{'、'.join(dossier.missing)}"))
    return candidates


def _omission_marker(context_ids: list[str]) -> str:
    """生成包含全部被省略上下文 ID 的紧凑标记。"""
    if not context_ids:
        return ""
    joined = "、".join(context_ids)
    return f"{joined}【省略】"


def build_stage_messages(
    *,
    workflow_id: str,
    stage: StageConfig,
    instruction_text: str,
    user_text: str,
    dossier: DossierResult | None,
    stages: dict[str, StageResult],
    preceding_stage_ids: list[str],
) -> tuple[list[BaseMessage], bool]:
    """在最终 System + Human 序列化文本上执行硬预算。"""
    # 阶段模型不绑定工具：用无工具版策略，避免模型输出「要先调工具」的叙述。
    policy_text = fixed_system_policy(f"工作流：{workflow_id} · 阶段：{stage.id}", tools=False)
    base_system = f"{policy_text}\n\n【角色任务与指引】\n{instruction_text}"
    max_chars = stage.context_chars
    if len(base_system) + len(user_text) > max_chars:
        raise WorkflowContextOverflow()

    system_text = base_system
    candidates = _context_candidates(stage, dossier, stages, preceding_stage_ids)
    additions = [(context_id, f"\n\n{context_text}") for context_id, context_text in candidates]
    available = max_chars - len(base_system) - len(user_text)
    total_context_chars = sum(len(addition) for _, addition in additions)
    context_truncated = total_context_chars > available

    if not context_truncated:
        system_text += "".join(addition for _, addition in additions)
    else:
        prefix_lengths = [0]
        for _, addition in additions:
            prefix_lengths.append(prefix_lengths[-1] + len(addition))

        for first_omitted in range(len(additions) - 1, -1, -1):
            omitted_ids = [context_id for context_id, _ in additions[first_omitted:]]
            marker = _omission_marker(omitted_ids)
            if prefix_lengths[first_omitted] + len(marker) <= available:
                system_text += "".join(
                    addition for _, addition in additions[:first_omitted]
                )
                system_text += marker
                break
        else:
            raise WorkflowContextOverflow()

    messages: list[BaseMessage] = [
        SystemMessage(content=system_text),
        HumanMessage(content=user_text),
    ]
    if sum(len(str(message.content)) for message in messages) > max_chars:
        raise WorkflowContextOverflow()
    return messages, context_truncated


def _content_text(content: Any) -> str:
    """从模型增量内容中提取阶段产出正文。

    开启 thinking 的 ReasoningChatOpenAI 会把流式增量规范成 content blocks
    （reasoning / text，见 agent/reasoning_model.py）；reasoning 是思考过程，
    不属于阶段产出，跳过；只拼接 text 块与裸字符串，避免把块 dict 原样写进正文。
    """
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content) if content else ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "".join(parts)


async def run_stage(
    model: BaseChatModel,
    messages: list[BaseMessage | dict[str, Any]],
    max_chars: int = 1200,
    emitter: WorkflowEventEmitter | None = None,
    stage_id: str = "",
) -> tuple[str, bool]:
    """流式执行单个阶段模型推理并向 emitter 发送增量事件（硬上限控制与连接生命周期管理）。"""
    max_chars = max(0, max_chars)
    captured = ""
    emitted_len = 0
    truncated = False
    suffix = _TRUNCATION_MARKER

    stream = model.astream(messages)
    try:
        async for chunk in stream:
            piece = (
                _content_text(chunk.content)
                if isinstance(chunk, BaseMessage)
                else _content_text(str(chunk))
            )
            if not piece:
                continue

            remaining_probe = max_chars + 1 - len(captured)
            if remaining_probe > 0:
                captured += piece[:remaining_probe]
            if len(captured) > max_chars:
                truncated = True
                break

            # 暂留截断标记等长的尾部；若后续越界，已发 delta 仍与终态前缀一致。
            safe_end = max(0, len(captured) - len(suffix))
            if emitter and stage_id and safe_end > emitted_len:
                await emitter.emit(
                    "stage.delta",
                    stage_id=stage_id,
                    delta=captured[emitted_len:safe_end],
                )
                emitted_len = safe_end
    finally:
        if hasattr(stream, "aclose"):
            try:
                await stream.aclose()
            except Exception:
                pass

    if truncated:
        marker = suffix[:max_chars]
        body_chars = max_chars - len(marker)
        full_output = captured[:body_chars] + marker
    else:
        full_output = captured

    if emitter and stage_id and len(full_output) > emitted_len:
        await emitter.emit(
            "stage.delta",
            stage_id=stage_id,
            delta=full_output[emitted_len:],
        )

    return full_output, truncated


def finalize_workflow(
    status: WorkflowStatus,
    stage_results: dict[str, StageResult],
    missing: list[str],
    workflow_type: str = "workflow",
) -> tuple[WorkflowStatus, str]:
    """计算工作流终态与不超过 80 字符的确定性摘要。"""
    stage_results = coerce_stage_map(stage_results)
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
