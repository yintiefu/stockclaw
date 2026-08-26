"""工作流 StateGraph 编译器。

将严格校验的 StagedResearchConfig 或 SinglePassConfig 静态编译为强类型 LangGraph 图：
- StagedResearchGraph：validate_input -> collect_dossier -> [start_<id> -> run_<id>] -> finalize
- SinglePassGraph：validate_input -> start -> run -> finalize
- 状态强类型 WorkflowState；
- 全阶段发射单调自增自定义事件；
- 模型调用前通过 start_<id> 节点将 stage.status=running 写入 Checkpoint。
"""
from __future__ import annotations

import functools
from pathlib import Path
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Overwrite

from agent.model_factory import build_model
from agent.policy import fixed_system_policy
from agent.settings import load_agent_settings
from agent.skill_backends import BUILTIN_SKILLS_DIR
from agent.workflow_events import WorkflowEventEmitter, utc_now
from agent.workflow_loader import (
    HARD_LIMITS,
    SinglePassConfig,
    StagedResearchConfig,
    WorkflowConfig,
    WorkflowConfigError,
    validate_staged_input,
)
from agent.workflow_runtime import (
    WorkflowContextOverflow,
    build_stage_messages,
    collect_dossier_sections,
    redact_workflow_error,
    run_stage,
    summarize_dossier,
)
from agent.workflow_state import (
    DossierResult,
    StageResult,
    WorkflowError,
    WorkflowState,
    WorkflowStatus,
    coerce_stage_map,
    coerce_stage_result,
)


class WorkflowGraphState(WorkflowState, total=False):
    """增加仅供事件续号使用的内部 checkpoint 字段。"""

    event_run_id: str


def _event_emitter(
    workflow_id: str,
    state: WorkflowGraphState,
    config: RunnableConfig,
) -> WorkflowEventEmitter:
    """同一 run 续号；新 run 即使从中间 checkpoint 恢复也从 1 开始。"""
    emitter = WorkflowEventEmitter.from_config(
        workflow_id,
        state.get("event_seq", 0),
        config,
    )
    if state.get("event_run_id") != emitter.run_id:
        return WorkflowEventEmitter.from_config(workflow_id, 0, config)
    return emitter


def _load_instruction_text(skill_name: str, instruction_path: str, root: Path) -> str:
    """读取指定技能的指令文本，缺失时快速失败。"""
    prefix = skill_name.removeprefix("builtin/").removeprefix("/builtin/")
    file_path = root / prefix / instruction_path
    if file_path.is_file():
        return file_path.read_text(encoding="utf-8")
    raise WorkflowConfigError(f"未找到技能指令文件：{file_path} (skill={skill_name}, instruction={instruction_path})")


def _build_staged_graph(
    cfg: StagedResearchConfig,
    model: BaseChatModel,
    checkpointer: BaseCheckpointSaver | None,
    builtin_skills_root: Path,
) -> CompiledStateGraph:
    builder = StateGraph(WorkflowGraphState)

    # 1. 校验与初始化节点
    async def validate_input(state: WorkflowGraphState, config: RunnableConfig) -> dict[str, Any]:
        variant = state.get("variant") or list(cfg.variants.keys())[0]
        if variant not in cfg.variants:
            raise ValueError(f"未知的变体：'{variant}'，有效变体：{list(cfg.variants.keys())}")

        validate_staged_input(cfg, state.get("input", {}))

        emitter = _event_emitter(cfg.id, state, config)
        await emitter.emit(
            "workflow.status",
            status="running",
            message="工作流已启动",
        )
        return {
            "workflow_id": cfg.id,
            "workflow_status": "running",
            "config_version": cfg.config_version,
            "variant": variant,
            "started_at": utc_now(),
            "dossier": None,
            "stages": Overwrite(value={}),
            "current_stage": None,
            "result": None,
            "result_summary": None,
            "completed_at": None,
            "errors": Overwrite(value=[]),
            "event_run_id": emitter.run_id,
            "event_seq": emitter.last_seq,
        }

    builder.add_node("validate_input", validate_input)

    # 2. 收集底稿节点
    async def collect_dossier(state: WorkflowGraphState, config: RunnableConfig) -> dict[str, Any]:
        code = state["input"]["code"]
        emitter = _event_emitter(cfg.id, state, config)
        sections, missing = await collect_dossier_sections(code, cfg.dossier, emitter)
        summary = summarize_dossier(sections, missing, cfg.dossier.dossier_summary_chars)
        dossier_res = DossierResult(
            sections=sections,
            summary=summary,
            missing=missing,
            has_substantive_data=any(section.status == "completed" for section in sections),
        )
        return {
            "dossier": dossier_res,
            "event_run_id": emitter.run_id,
            "event_seq": emitter.last_seq,
        }

    builder.add_node("collect_dossier", collect_dossier)

    # 2.5 底稿全空熔断节点
    async def abort_no_data(state: WorkflowGraphState, config: RunnableConfig) -> dict[str, Any]:
        err = WorkflowError(
            code="NO_SUBSTANTIVE_DATA",
            message="全部客观数据源抓取失败或为空，分析必须有客观底稿支撑，辩论中止。",
            stage_id=None,
        )
        emitter = _event_emitter(cfg.id, state, config)
        await emitter.emit(
            "workflow.failed",
            error_code=err.code,
            message=err.message,
            retryable=err.retryable,
        )
        return {
            "workflow_status": "failed",
            "completed_at": utc_now(),
            "result_summary": "多空辩论中止：无客观底稿数据",
            "result": None,
            "errors": [err],
            "event_run_id": emitter.run_id,
            "event_seq": emitter.last_seq,
        }

    builder.add_node("abort_no_data", abort_no_data)
    builder.add_edge("abort_no_data", END)

    # 3. 动态注册所有 stage 节点
    for s_cfg in cfg.stages:
        stage_id = s_cfg.id
        instruction_text = _load_instruction_text(s_cfg.skill, s_cfg.instruction, builtin_skills_root)

        def make_start_fn(sid: str, label: str):
            async def start_fn(state: WorkflowGraphState, config: RunnableConfig) -> dict[str, Any]:
                emitter = _event_emitter(cfg.id, state, config)
                await emitter.emit("stage.started", stage_id=sid, label=label)
                return {
                    "current_stage": sid,
                    "stages": {sid: StageResult(id=sid, status="running", started_at=utc_now())},
                    "event_run_id": emitter.run_id,
                    "event_seq": emitter.last_seq,
                }
            return start_fn

        def make_run_fn(stage_config, instr: str):
            sid = stage_config.id

            async def run_fn(state: WorkflowGraphState, config: RunnableConfig) -> dict[str, Any]:
                current_st = coerce_stage_result(state.get("stages", {}).get(sid), sid)
                if current_st and current_st.status == "completed":
                    return {}

                dossier = state.get("dossier")
                variant = state.get("variant") or list(cfg.variants.keys())[0]
                variant_stages = cfg.variants.get(variant, [])
                preceding_ids: list[str] = []
                for vid in variant_stages:
                    if vid == sid:
                        break
                    preceding_ids.append(vid)

                emitter = _event_emitter(cfg.id, state, config)
                try:
                    code = state.get("input", {}).get("code", "")
                    messages, context_truncated = build_stage_messages(
                        workflow_id=cfg.id,
                        stage=stage_config,
                        instruction_text=instr,
                        user_text=f"请基于允许的客观底稿与前序上下文，针对标的 {code} 进行分析并输出。",
                        dossier=dossier,
                        stages=coerce_stage_map(state.get("stages")),
                        preceding_stage_ids=preceding_ids,
                    )
                    content, truncated = await run_stage(
                        model=model,
                        messages=messages,
                        max_chars=stage_config.output_chars,
                        emitter=emitter,
                        stage_id=sid,
                    )
                    await emitter.emit("stage.completed", stage_id=sid, truncated=truncated)
                    return {
                        "stages": {
                            sid: StageResult(
                                id=sid,
                                status="completed",
                                content=content,
                                truncated=truncated,
                                context_truncated=context_truncated,
                                started_at=current_st.started_at if current_st else None,
                                completed_at=utc_now(),
                            )
                        },
                        "event_run_id": emitter.run_id,
                        "event_seq": emitter.last_seq,
                    }
                except Exception as e:
                    error_code = "CONTEXT_OVERFLOW" if isinstance(e, WorkflowContextOverflow) else "MODEL_ERROR"
                    err = redact_workflow_error(e, stage_id=sid, code=error_code)
                    await emitter.emit(
                        "stage.failed",
                        stage_id=sid,
                        error_code=err.code,
                        message=err.message,
                        retryable=err.retryable,
                    )
                    return {
                        "stages": {sid: StageResult(
                            id=sid,
                            status="failed",
                            started_at=current_st.started_at if current_st else None,
                            completed_at=utc_now(),
                            error=err,
                        )},
                        "errors": [err],
                        "event_run_id": emitter.run_id,
                        "event_seq": emitter.last_seq,
                    }

            return run_fn

        builder.add_node(f"start_{stage_id}", make_start_fn(stage_id, s_cfg.label))
        builder.add_node(f"run_{stage_id}", make_run_fn(s_cfg, instruction_text))

    # 4. 汇总与终态节点
    async def finalize(state: WorkflowGraphState, config: RunnableConfig) -> dict[str, Any]:
        stages = coerce_stage_map(state.get("stages"))
        errors = state.get("errors", [])
        variant = state.get("variant") or list(cfg.variants.keys())[0]
        expected_stages = cfg.variants.get(variant, [])

        completed_count = sum(1 for sid in expected_stages if stages.get(sid, StageResult(id=sid, status="pending")).status == "completed")
        failed_count = sum(1 for sid in expected_stages if stages.get(sid, StageResult(id=sid, status="pending")).status == "failed")

        result_stage = stages.get(cfg.result_stage)
        result_stage_failed = not result_stage or result_stage.status != "completed"

        if result_stage_failed or (failed_count > 0 and completed_count == 0):
            final_status: WorkflowStatus = "failed"
        elif failed_count > 0:
            final_status = "partial"
        else:
            final_status = "completed"

        dossier = state.get("dossier")
        missing_count = len(dossier.missing) if dossier else 0

        if final_status == "failed":
            if result_stage_failed:
                summary = f"{cfg.id} 失败：结果阶段未能正常产出"
            else:
                summary = f"{cfg.id} 失败：{failed_count} 个阶段报错中断"
        elif final_status == "partial":
            summary = f"{cfg.id} 部分完成：{completed_count}/{len(expected_stages)} 阶段完成（含错误）"
        else:
            summary = f"{cfg.id} 完成：{completed_count}/{len(expected_stages)} 阶段完成，{missing_count} 项缺口"

        if len(summary) > 80:
            summary = summary[:77] + "..."

        completed_at = utc_now()
        skipped_stages = {
            sid: StageResult(id=sid, status="skipped", completed_at=completed_at)
            for sid in expected_stages
            if sid not in stages
        }

        emitter = _event_emitter(cfg.id, state, config)
        if final_status in ("completed", "partial"):
            await emitter.emit("workflow.completed", status=final_status)
        else:
            err = errors[-1] if errors else WorkflowError(code="EXECUTION_FAILED", message="工作流执行失败")
            await emitter.emit(
                "workflow.failed",
                error_code=err.code,
                message=err.message,
                retryable=err.retryable,
            )

        result_text = result_stage.content if (result_stage and result_stage.status == "completed") else None

        return {
            "workflow_status": final_status,
            "completed_at": completed_at,
            "stages": skipped_stages,
            "result_summary": summary,
            "result": result_text,
            "event_run_id": emitter.run_id,
            "event_seq": emitter.last_seq,
        }

    builder.add_node("finalize", finalize)

    # 5. 边与路由设置
    builder.set_entry_point("validate_input")
    builder.add_edge("validate_input", "collect_dossier")

    def route_after_dossier(state: WorkflowGraphState) -> str:
        dossier = state.get("dossier")
        if not dossier or not dossier.has_substantive_data:
            return "abort_no_data"
        variant = state.get("variant") or list(cfg.variants.keys())[0]
        v_stages = cfg.variants.get(variant, [])
        return f"start_{v_stages[0]}"

    start_targets = {f"start_{s.id}": f"start_{s.id}" for s in cfg.stages}
    start_targets["abort_no_data"] = "abort_no_data"
    builder.add_conditional_edges("collect_dossier", route_after_dossier, start_targets)

    for i, s_cfg in enumerate(cfg.stages):
        sid = s_cfg.id
        builder.add_edge(f"start_{sid}", f"run_{sid}")

        def make_stage_router(current_stage_cfg):
            curr_id = current_stage_cfg.id
            on_err = current_stage_cfg.on_error

            def stage_router(state: WorkflowGraphState) -> str:
                variant = state.get("variant") or list(cfg.variants.keys())[0]
                v_stages = cfg.variants.get(variant, [])
                st_res = coerce_stage_result(state.get("stages", {}).get(curr_id), curr_id)

                if st_res and st_res.status == "failed" and on_err == "fail":
                    return "finalize"

                if curr_id in v_stages:
                    curr_idx = v_stages.index(curr_id)
                    if curr_idx + 1 < len(v_stages):
                        return f"start_{v_stages[curr_idx + 1]}"
                return "finalize"

            return stage_router

        targets = {**start_targets, "finalize": "finalize"}
        builder.add_conditional_edges(f"run_{sid}", make_stage_router(s_cfg), targets)

    builder.add_edge("finalize", END)
    return builder.compile(checkpointer=checkpointer)


def _build_single_pass_graph(
    cfg: SinglePassConfig,
    model: BaseChatModel,
    checkpointer: BaseCheckpointSaver | None,
    builtin_skills_root: Path,
) -> CompiledStateGraph:
    builder = StateGraph(WorkflowGraphState)
    instruction_text = _load_instruction_text(cfg.skill, cfg.instruction, builtin_skills_root)

    # 1. 校验与初始化节点
    async def validate_input(state: WorkflowGraphState, config: RunnableConfig) -> dict[str, Any]:
        text_field = cfg.input.text_field
        input_values = state.get("input")
        inp_text = input_values.get(text_field) if isinstance(input_values, dict) else None
        if not inp_text or not isinstance(inp_text, str):
            raise ValueError(f"输入字段 input.{text_field} 缺失或类型错误")

        emitter = _event_emitter(cfg.id, state, config)
        await emitter.emit(
            "workflow.status",
            status="running",
            message="工作流已启动",
        )
        return {
            "workflow_id": cfg.id,
            "workflow_status": "running",
            "config_version": cfg.config_version,
            "variant": None,
            "started_at": utc_now(),
            "dossier": None,
            "stages": Overwrite(value={}),
            "current_stage": None,
            "result": None,
            "result_summary": None,
            "completed_at": None,
            "errors": Overwrite(value=[]),
            "event_run_id": emitter.run_id,
            "event_seq": emitter.last_seq,
        }

    builder.add_node("validate_input", validate_input)

    # 2. 启动阶段节点
    async def start_stage(state: WorkflowGraphState, config: RunnableConfig) -> dict[str, Any]:
        emitter = _event_emitter(cfg.id, state, config)
        await emitter.emit("stage.started", stage_id=cfg.id, label=cfg.id)
        return {
            "current_stage": cfg.id,
            "stages": {cfg.id: StageResult(id=cfg.id, status="running", started_at=utc_now())},
            "event_run_id": emitter.run_id,
            "event_seq": emitter.last_seq,
        }

    builder.add_node("start_stage", start_stage)

    # 3. 运行阶段节点
    async def run_stage_fn(state: WorkflowGraphState, config: RunnableConfig) -> dict[str, Any]:
        raw_inp = state["input"].get(cfg.input.text_field, "")
        # 单阶段模型不绑定工具：用无工具版策略，避免模型输出「要先调工具」的叙述。
        policy_text = fixed_system_policy(f"工作流：{cfg.id}", tools=False)
        system_text = f"{policy_text}\n\n【分析任务与指引】\n{instruction_text}"
        human_prefix = "【待分析输入内容】\n"
        human_suffix = "\n\n请按指引进行结构化分析与审计。"

        emitter = _event_emitter(cfg.id, state, config)
        try:
            fixed_chars = len(system_text) + len(human_prefix) + len(human_suffix)
            if fixed_chars > cfg.input.max_chars:
                raise WorkflowContextOverflow()
            input_chars = cfg.input.max_chars - fixed_chars
            inp_truncated = len(raw_inp) > input_chars
            inp_text = raw_inp[:input_chars]
            messages = [
                SystemMessage(content=system_text),
                HumanMessage(content=f"{human_prefix}{inp_text}{human_suffix}"),
            ]
            content, truncated = await run_stage(
                model=model,
                messages=messages,
                max_chars=HARD_LIMITS["stage_output_chars"],
                emitter=emitter,
                stage_id=cfg.id,
            )
            await emitter.emit("stage.completed", stage_id=cfg.id, truncated=truncated)
            running_stage = coerce_stage_result(state.get("stages", {}).get(cfg.id), cfg.id)
            return {
                "result": content,
                "stages": {
                    cfg.id: StageResult(
                        id=cfg.id,
                        status="completed",
                        content=content,
                        truncated=truncated,
                        context_truncated=inp_truncated,
                        started_at=running_stage.started_at if running_stage else None,
                        completed_at=utc_now(),
                    )
                },
                "event_run_id": emitter.run_id,
                "event_seq": emitter.last_seq,
            }
        except Exception as e:
            error_code = "CONTEXT_OVERFLOW" if isinstance(e, WorkflowContextOverflow) else "MODEL_ERROR"
            err = redact_workflow_error(e, stage_id=cfg.id, code=error_code)
            await emitter.emit(
                "stage.failed",
                stage_id=cfg.id,
                error_code=err.code,
                message=err.message,
                retryable=err.retryable,
            )
            running_stage = coerce_stage_result(state.get("stages", {}).get(cfg.id), cfg.id)
            return {
                "stages": {cfg.id: StageResult(
                    id=cfg.id,
                    status="failed",
                    started_at=running_stage.started_at if running_stage else None,
                    completed_at=utc_now(),
                    error=err,
                )},
                "errors": [err],
                "event_run_id": emitter.run_id,
                "event_seq": emitter.last_seq,
            }

    builder.add_node("run_stage", run_stage_fn)

    # 4. 汇总节点
    async def finalize(state: WorkflowGraphState, config: RunnableConfig) -> dict[str, Any]:
        st = coerce_stage_result(state.get("stages", {}).get(cfg.id), cfg.id)
        errors = state.get("errors", [])
        if st and st.status == "completed":
            final_status: WorkflowStatus = "completed"
            summary = f"{cfg.id} 分析完成"
        else:
            final_status = "failed"
            summary = f"{cfg.id} 分析失败"

        emitter = _event_emitter(cfg.id, state, config)
        if final_status == "completed":
            await emitter.emit("workflow.completed", status="completed")
        else:
            err = errors[-1] if errors else WorkflowError(code="EXECUTION_FAILED", message="工作流执行失败")
            await emitter.emit(
                "workflow.failed",
                error_code=err.code,
                message=err.message,
                retryable=err.retryable,
            )

        return {
            "workflow_status": final_status,
            "completed_at": utc_now(),
            "result": st.content if (st and st.status == "completed") else None,
            "result_summary": summary,
            "event_run_id": emitter.run_id,
            "event_seq": emitter.last_seq,
        }

    builder.add_node("finalize", finalize)

    # 连接流水线
    builder.set_entry_point("validate_input")
    builder.add_edge("validate_input", "start_stage")
    builder.add_edge("start_stage", "run_stage")
    builder.add_edge("run_stage", "finalize")
    builder.add_edge("finalize", END)

    return builder.compile(checkpointer=checkpointer)


def build_workflow_graph(
    config: WorkflowConfig,
    *,
    model: BaseChatModel | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
    builtin_skills_root: Path | None = None,
) -> CompiledStateGraph:
    """基于工作流配置编译对应的 StateGraph 实例。"""
    active_model = model or build_model(load_agent_settings())
    skills_root = builtin_skills_root or BUILTIN_SKILLS_DIR

    if isinstance(config, StagedResearchConfig):
        return _build_staged_graph(config, active_model, checkpointer, skills_root)
    elif isinstance(config, SinglePassConfig):
        return _build_single_pass_graph(config, active_model, checkpointer, skills_root)
    else:
        raise TypeError(f"不支持的工作流配置类型：{type(config)}")
