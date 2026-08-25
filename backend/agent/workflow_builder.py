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
)
from agent.workflow_runtime import (
    collect_dossier_sections,
    format_full_dossier_text,
    redact_workflow_error,
    run_stage,
    serialize_stage_context,
    summarize_dossier,
)
from agent.workflow_state import (
    DossierResult,
    StageResult,
    WorkflowError,
    WorkflowState,
    WorkflowStatus,
)


def _load_instruction_text(skill_name: str, instruction_path: str, root: Path) -> str:
    """读取指定技能的指令文本。"""
    prefix = skill_name.removeprefix("builtin/")
    file_path = root / prefix / instruction_path
    if not file_path.is_file():
        # 尝试直接在技能根目录下寻找
        skill_file = root / prefix / "SKILL.md"
        if skill_file.is_file():
            return skill_file.read_text(encoding="utf-8")
        return ""
    return file_path.read_text(encoding="utf-8")


def _build_staged_graph(
    cfg: StagedResearchConfig,
    model: BaseChatModel,
    checkpointer: BaseCheckpointSaver | None,
    builtin_skills_root: Path,
) -> CompiledStateGraph:
    builder = StateGraph(WorkflowState)

    # 1. 校验与初始化节点
    async def validate_input(state: WorkflowState, config: RunnableConfig) -> dict[str, Any]:
        variant = state.get("variant") or list(cfg.variants.keys())[0]
        if variant not in cfg.variants:
            raise ValueError(f"未知的变体：'{variant}'，有效变体：{list(cfg.variants.keys())}")

        code = state.get("input", {}).get("code", "")
        if not code or not isinstance(code, str):
            raise ValueError(f"缺少必要输入字段 'code': {state.get('input')}")

        emitter = WorkflowEventEmitter.from_config(cfg.id, state.get("event_seq", 0), config)
        await emitter.emit(
            "workflow_started",
            workflow_type=cfg.id,
            input=state.get("input", {}),
            variant=variant,
        )
        return {
            "workflow_id": cfg.id,
            "workflow_type": cfg.id,
            "workflow_status": "running",
            "config_version": cfg.config_version,
            "variant": variant,
            "stages": {},
            "errors": [],
            "event_seq": emitter.last_seq,
        }

    builder.add_node("validate_input", validate_input)

    # 2. 收集底稿节点
    async def collect_dossier(state: WorkflowState, config: RunnableConfig) -> dict[str, Any]:
        code = state["input"]["code"]
        emitter = WorkflowEventEmitter.from_config(cfg.id, state.get("event_seq", 0), config)
        sections, missing = await collect_dossier_sections(code, cfg.dossier, emitter)
        summary = summarize_dossier(sections, missing, cfg.dossier.dossier_summary_chars)
        dossier_res = DossierResult(sections=sections, summary=summary, missing=missing)
        return {
            "dossier": dossier_res,
            "event_seq": emitter.last_seq,
        }

    builder.add_node("collect_dossier", collect_dossier)

    # 3. 动态注册所有 stage 节点
    for s_cfg in cfg.stages:
        stage_id = s_cfg.id
        instruction_text = _load_instruction_text(s_cfg.skill, s_cfg.instruction, builtin_skills_root)

        def make_start_fn(sid: str):
            async def start_fn(state: WorkflowState, config: RunnableConfig) -> dict[str, Any]:
                emitter = WorkflowEventEmitter.from_config(cfg.id, state.get("event_seq", 0), config)
                await emitter.emit("stage_started", stage_id=sid)
                return {
                    "current_stage": sid,
                    "stages": {sid: StageResult(stage_id=sid, status="running")},
                    "event_seq": emitter.last_seq,
                }
            return start_fn

        def make_run_fn(stage_config, instr: str):
            sid = stage_config.id

            async def run_fn(state: WorkflowState, config: RunnableConfig) -> dict[str, Any]:
                current_st = state.get("stages", {}).get(sid)
                if current_st and current_st.status == "completed":
                    return {}

                code = state.get("input", {}).get("code", "")
                dossier = state.get("dossier")
                dossier_sections = dossier.sections if dossier else []
                dossier_missing = dossier.missing if dossier else []

                if sid == "referee":
                    facts_text = summarize_dossier(dossier_sections, dossier_missing, stage_config.context_chars)
                else:
                    facts_text = format_full_dossier_text(dossier_sections, dossier_missing, code)

                # 计算前序阶段上下文
                variant = state.get("variant") or list(cfg.variants.keys())[0]
                variant_stages = cfg.variants.get(variant, [])
                preceding_ids: list[str] = []
                for vid in variant_stages:
                    if vid == sid:
                        break
                    preceding_ids.append(vid)

                context_text, _ctx_truncated = serialize_stage_context(
                    state.get("stages", {}),
                    preceding_ids,
                    stage_config.context_chars,
                )

                policy_text = fixed_system_policy(f"工作流：{cfg.id} · 阶段：{sid}")
                messages = [
                    SystemMessage(
                        content=f"{policy_text}\n\n【角色任务与指引】\n{instr}\n\n{facts_text}\n\n【前序讨论上下文】\n{context_text}"
                    ),
                    HumanMessage(content=f"请基于客观底稿与前序上下文，针对标的 {code} 进行分析并输出。"),
                ]

                emitter = WorkflowEventEmitter.from_config(cfg.id, state.get("event_seq", 0), config)
                try:
                    content, truncated = await run_stage(
                        model=model,
                        messages=messages,
                        max_chars=stage_config.output_chars,
                        emitter=emitter,
                        stage_id=sid,
                    )
                    await emitter.emit("stage_completed", stage_id=sid, truncated=truncated)
                    return {
                        "stages": {
                            sid: StageResult(
                                stage_id=sid,
                                status="completed",
                                content=content,
                                truncated=truncated,
                                completed_at=utc_now(),
                            )
                        },
                        "event_seq": emitter.last_seq,
                    }
                except Exception as e:
                    err = redact_workflow_error(e, stage_id=sid, code="MODEL_ERROR")
                    await emitter.emit("stage_failed", stage_id=sid, error=err)
                    return {
                        "stages": {sid: StageResult(stage_id=sid, status="failed", error=err)},
                        "errors": [err],
                        "event_seq": emitter.last_seq,
                    }

            return run_fn

        builder.add_node(f"start_{stage_id}", make_start_fn(stage_id))
        builder.add_node(f"run_{stage_id}", make_run_fn(s_cfg, instruction_text))

    # 4. 汇总与终态节点
    async def finalize(state: WorkflowState, config: RunnableConfig) -> dict[str, Any]:
        stages = state.get("stages", {})
        errors = state.get("errors", [])
        variant = state.get("variant") or list(cfg.variants.keys())[0]
        expected_stages = cfg.variants.get(variant, [])

        completed_count = sum(1 for sid in expected_stages if stages.get(sid, StageResult(stage_id=sid, status="pending")).status == "completed")
        failed_count = sum(1 for sid in expected_stages if stages.get(sid, StageResult(stage_id=sid, status="pending")).status == "failed")

        if failed_count > 0 and completed_count == 0:
            final_status: WorkflowStatus = "failed"
        elif failed_count > 0:
            final_status = "partial"
        else:
            final_status = "completed"

        dossier = state.get("dossier")
        missing_count = len(dossier.missing) if dossier else 0

        summary = f"多空辩论完成：{completed_count}/{len(expected_stages)} 阶段完成，{missing_count} 项缺口"
        if final_status == "failed":
            summary = f"多空辩论失败：{failed_count} 个阶段报错中断"
        elif final_status == "partial":
            summary = f"多空辩论部分完成：{completed_count}/{len(expected_stages)} 阶段完成（含错误）"

        if len(summary) > 80:
            summary = summary[:77] + "..."

        emitter = WorkflowEventEmitter.from_config(cfg.id, state.get("event_seq", 0), config)
        if final_status in ("completed", "partial"):
            await emitter.emit("workflow_completed", workflow_type=cfg.id, result_summary=summary)
        else:
            err = errors[-1] if errors else WorkflowError(code="EXECUTION_FAILED", message="工作流执行失败")
            await emitter.emit("workflow_failed", workflow_type=cfg.id, error=err)

        referee_st = stages.get("referee")
        result_text = referee_st.content if (referee_st and referee_st.status == "completed") else None

        return {
            "workflow_status": final_status,
            "result_summary": summary,
            "result": result_text,
            "event_seq": emitter.last_seq,
        }

    builder.add_node("finalize", finalize)

    # 5. 边与路由设置
    builder.set_entry_point("validate_input")
    builder.add_edge("validate_input", "collect_dossier")

    def route_after_dossier(state: WorkflowState) -> str:
        variant = state.get("variant") or list(cfg.variants.keys())[0]
        v_stages = cfg.variants.get(variant, [])
        return f"start_{v_stages[0]}"

    start_targets = {f"start_{s.id}": f"start_{s.id}" for s in cfg.stages}
    builder.add_conditional_edges("collect_dossier", route_after_dossier, start_targets)

    for i, s_cfg in enumerate(cfg.stages):
        sid = s_cfg.id
        builder.add_edge(f"start_{sid}", f"run_{sid}")

        def make_stage_router(current_stage_cfg):
            curr_id = current_stage_cfg.id
            on_err = current_stage_cfg.on_error

            def stage_router(state: WorkflowState) -> str:
                variant = state.get("variant") or list(cfg.variants.keys())[0]
                v_stages = cfg.variants.get(variant, [])
                st_res = state.get("stages", {}).get(curr_id)

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
    builder = StateGraph(WorkflowState)
    instruction_text = _load_instruction_text(cfg.skill, cfg.instruction, builtin_skills_root)

    # 1. 校验与初始化节点
    async def validate_input(state: WorkflowState, config: RunnableConfig) -> dict[str, Any]:
        text_field = cfg.input.text_field
        inp_text = state.get("input", {}).get(text_field, "")
        if not inp_text or not isinstance(inp_text, str):
            raise ValueError(f"缺少必要输入文本字段 '{text_field}': {state.get('input')}")

        emitter = WorkflowEventEmitter.from_config(cfg.id, state.get("event_seq", 0), config)
        await emitter.emit(
            "workflow_started",
            workflow_type=cfg.id,
            input=state.get("input", {}),
            variant=None,
        )
        return {
            "workflow_id": cfg.id,
            "workflow_type": cfg.id,
            "workflow_status": "running",
            "config_version": cfg.config_version,
            "variant": None,
            "stages": {},
            "errors": [],
            "event_seq": emitter.last_seq,
        }

    builder.add_node("validate_input", validate_input)

    # 2. 启动阶段节点
    async def start_stage(state: WorkflowState, config: RunnableConfig) -> dict[str, Any]:
        emitter = WorkflowEventEmitter.from_config(cfg.id, state.get("event_seq", 0), config)
        await emitter.emit("stage_started", stage_id=cfg.id)
        return {
            "current_stage": cfg.id,
            "stages": {cfg.id: StageResult(stage_id=cfg.id, status="running")},
            "event_seq": emitter.last_seq,
        }

    builder.add_node("start_stage", start_stage)

    # 3. 运行阶段节点
    async def run_stage_fn(state: WorkflowState, config: RunnableConfig) -> dict[str, Any]:
        inp_text = state["input"].get(cfg.input.text_field, "")
        if len(inp_text) > cfg.input.max_chars:
            inp_text = inp_text[:cfg.input.max_chars]

        policy_text = fixed_system_policy(f"工作流：{cfg.id}")
        messages = [
            SystemMessage(content=f"{policy_text}\n\n【分析任务与指引】\n{instruction_text}"),
            HumanMessage(content=f"【待分析输入内容】\n{inp_text}\n\n请按指引进行结构化分析与审计。"),
        ]

        emitter = WorkflowEventEmitter.from_config(cfg.id, state.get("event_seq", 0), config)
        try:
            content, truncated = await run_stage(
                model=model,
                messages=messages,
                max_chars=HARD_LIMITS["stage_output_chars"],
                emitter=emitter,
                stage_id=cfg.id,
            )
            await emitter.emit("stage_completed", stage_id=cfg.id, truncated=truncated)
            return {
                "result": content,
                "stages": {
                    cfg.id: StageResult(
                        stage_id=cfg.id,
                        status="completed",
                        content=content,
                        truncated=truncated,
                        completed_at=utc_now(),
                    )
                },
                "event_seq": emitter.last_seq,
            }
        except Exception as e:
            err = redact_workflow_error(e, stage_id=cfg.id, code="MODEL_ERROR")
            await emitter.emit("stage_failed", stage_id=cfg.id, error=err)
            return {
                "stages": {cfg.id: StageResult(stage_id=cfg.id, status="failed", error=err)},
                "errors": [err],
                "event_seq": emitter.last_seq,
            }

    builder.add_node("run_stage", run_stage_fn)

    # 4. 汇总节点
    async def finalize(state: WorkflowState, config: RunnableConfig) -> dict[str, Any]:
        st = state.get("stages", {}).get(cfg.id)
        errors = state.get("errors", [])
        if st and st.status == "completed":
            final_status: WorkflowStatus = "completed"
            summary = f"{cfg.id} 分析完成"
        else:
            final_status = "failed"
            summary = f"{cfg.id} 分析失败"

        emitter = WorkflowEventEmitter.from_config(cfg.id, state.get("event_seq", 0), config)
        if final_status == "completed":
            await emitter.emit("workflow_completed", workflow_type=cfg.id, result_summary=summary)
        else:
            err = errors[-1] if errors else WorkflowError(code="EXECUTION_FAILED", message="工作流执行失败")
            await emitter.emit("workflow_failed", workflow_type=cfg.id, error=err)

        return {
            "workflow_status": final_status,
            "result_summary": summary,
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
