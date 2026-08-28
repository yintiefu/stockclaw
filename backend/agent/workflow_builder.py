"""工作流 StateGraph 编译器。

将严格校验的 StagedResearchConfig 或 SinglePassConfig 静态编译为强类型 LangGraph 图：
- StagedResearchGraph：entry -> [auto_resume | validate_input -> collect_dossier] -> [start_<id> -> run_<id>] -> finalize
- SinglePassGraph：entry -> [auto_resume | validate_input] -> start_stage -> run_stage -> finalize
- 状态强类型 WorkflowState；阶段权威正文写入 messages 通道（StageResult 只持 message_id 指针）；
- resume 走独立顶层控制通道（绝不覆写 input）：entry 持有版本门控，auto_resume 按
  「变体顺序第一个非终态阶段」路由；阶段间为状态驱动条件路由（持续跳过 completed/skipped），
  避免重进已完成阶段（start_* 会把阶段覆盖回 running，静态边会中和 run_* 的守卫）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Overwrite

from agent.model_factory import build_model
from agent.policy import fixed_system_policy
from agent.settings import load_agent_settings
from agent.skill_backends import BUILTIN_SKILLS_DIR
from agent.workflow_events import utc_now
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
)


def _load_instruction_text(skill_name: str, instruction_path: str, root: Path) -> str:
    """读取指定技能的指令文本，缺失时快速失败。"""
    prefix = skill_name.removeprefix("builtin/").removeprefix("/builtin/")
    file_path = root / prefix / instruction_path
    if file_path.is_file():
        return file_path.read_text(encoding="utf-8")
    raise WorkflowConfigError(f"未找到技能指令文件：{file_path} (skill={skill_name}, instruction={instruction_path})")


def _is_resume(state: WorkflowState) -> bool:
    """resume 走独立顶层通道，绝不动 input（原始 code/source 必须跨重试保留）。"""
    return state.get("resume") is True


def _resume_rejection(state: WorkflowState, expected_version: int) -> WorkflowError | None:
    """resume 门控：无可恢复状态 / 版本不兼容时返回拒绝错误（写入式拒绝）。

    拒绝必须写进 checkpoint 而不是在节点里 raise：实测 inmem + v2 协议下节点抛错
    只留下 status=error 的 run 记录——没有 lifecycle failed 事件、run.error 也没有
    文本，前端（唯一可靠来源是 checkpoint）无从感知拒绝原因。
    """
    stored = state.get("config_version")
    if stored is None and "input" not in state:
        # run 在首个 checkpoint 落盘前被取消：没有任何可恢复状态
        return WorkflowError(
            code="RESUME_NO_STATE",
            message="该工作流没有可恢复的状态：请重新发起工作流",
            stage_id=None,
        )
    if stored != expected_version:
        return WorkflowError(
            code="RESUME_CONFIG_VERSION",
            message="配置版本不兼容：请查看已有状态或重新发起工作流",
            stage_id=None,
        )
    return None


def _build_staged_graph(
    cfg: StagedResearchConfig,
    model: BaseChatModel,
    checkpointer: BaseCheckpointSaver | None,
    builtin_skills_root: Path,
) -> CompiledStateGraph:
    builder = StateGraph(WorkflowState)

    # 0. 统一入口与 resume 路由（v2 run.start 无 goto，重试 = 新 run + 控制位）
    async def entry(state: WorkflowState, config: RunnableConfig) -> dict[str, Any]:
        """入口直通；resume 门控在 auto_resume 里做（写入式拒绝，见 _resume_rejection）。"""
        return {}

    def _resume_target(state: WorkflowState) -> str:
        """被门控拒绝（failed 终态已写入）直接收尾；否则按变体顺序找第一个
        非终态阶段；全部完成则收尾（幂等）。"""
        if state.get("workflow_status") == "failed":
            return END
        variant = state.get("variant") or list(cfg.variants.keys())[0]
        stages = state.get("stages", {})
        for sid in cfg.variants.get(variant, []):
            st = stages.get(sid)
            if st is None or st.status in ("running", "failed", "interrupted", "cancelled"):
                return f"start_{sid}"
        return "finalize"

    async def auto_resume(state: WorkflowState, config: RunnableConfig) -> dict[str, Any]:
        """resume 模式：先过门控；通过则只清控制位并置运行态（普通值直写，不需要 Overwrite）。"""
        rejection = _resume_rejection(state, cfg.config_version)
        if rejection is not None:
            return {
                "workflow_status": "failed",
                "completed_at": utc_now(),
                "result_summary": rejection.message,
                "errors": [rejection],
            }
        return {"workflow_status": "running", "resume": False}

    start_targets_with_finalize = {f"start_{s.id}": f"start_{s.id}" for s in cfg.stages}
    start_targets_with_finalize["finalize"] = "finalize"
    start_targets_with_finalize[END] = END

    builder.add_node("entry", entry)
    builder.set_entry_point("entry")
    builder.add_conditional_edges(
        "entry",
        lambda s: "auto_resume" if _is_resume(s) else "validate_input",
        {"auto_resume": "auto_resume", "validate_input": "validate_input"},
    )
    builder.add_node("auto_resume", auto_resume)
    builder.add_conditional_edges("auto_resume", _resume_target, start_targets_with_finalize)

    # 1. 校验与初始化节点
    async def validate_input(state: WorkflowState, config: RunnableConfig) -> dict[str, Any]:
        variant = state.get("variant") or list(cfg.variants.keys())[0]
        if variant not in cfg.variants:
            raise ValueError(f"未知的变体：'{variant}'，有效变体：{list(cfg.variants.keys())}")

        validate_staged_input(cfg, state.get("input", {}))

        return {
            "workflow_id": cfg.id,
            "workflow_status": "running",
            "config_version": cfg.config_version,
            "variant": variant,
            "started_at": utc_now(),
            "dossier": None,
            "stages": Overwrite(value={}),
            "current_stage": None,
            "result_summary": None,
            "completed_at": None,
            "errors": Overwrite(value=[]),
        }

    builder.add_node("validate_input", validate_input)

    # 2. 收集底稿节点
    async def collect_dossier(state: WorkflowState, config: RunnableConfig) -> dict[str, Any]:
        code = state["input"]["code"]
        sections, missing = await collect_dossier_sections(code, cfg.dossier)
        summary = summarize_dossier(sections, missing, cfg.dossier.dossier_summary_chars)
        dossier_res = DossierResult(
            sections=sections,
            summary=summary,
            missing=missing,
            has_substantive_data=any(section.status == "completed" for section in sections),
        )
        return {"dossier": dossier_res}

    builder.add_node("collect_dossier", collect_dossier)

    # 2.5 底稿全空熔断节点
    async def abort_no_data(state: WorkflowState, config: RunnableConfig) -> dict[str, Any]:
        err = WorkflowError(
            code="NO_SUBSTANTIVE_DATA",
            message="全部客观数据源抓取失败或为空，分析必须有客观底稿支撑，辩论中止。",
            stage_id=None,
        )
        return {
            "workflow_status": "failed",
            "completed_at": utc_now(),
            "result_summary": "多空辩论中止：无客观底稿数据",
            "errors": [err],
        }

    builder.add_node("abort_no_data", abort_no_data)
    builder.add_edge("abort_no_data", END)

    # 3. 动态注册所有 stage 节点
    for s_cfg in cfg.stages:
        stage_id = s_cfg.id
        instruction_text = _load_instruction_text(s_cfg.skill, s_cfg.instruction, builtin_skills_root)

        def make_start_fn(sid: str):
            async def start_fn(state: WorkflowState, config: RunnableConfig) -> dict[str, Any]:
                return {
                    "current_stage": sid,
                    "stages": {sid: StageResult(id=sid, status="running", started_at=utc_now())},
                }
            return start_fn

        def make_run_fn(stage_config, instr: str):
            sid = stage_config.id

            async def run_fn(state: WorkflowState, config: RunnableConfig) -> dict[str, Any]:
                current_st = state.get("stages", {}).get(sid)
                if current_st is not None and current_st.status == "completed":
                    return {}

                dossier = state.get("dossier")
                variant = state.get("variant") or list(cfg.variants.keys())[0]
                variant_stages = cfg.variants.get(variant, [])
                preceding_ids: list[str] = []
                for vid in variant_stages:
                    if vid == sid:
                        break
                    preceding_ids.append(vid)

                try:
                    code = state.get("input", {}).get("code", "")
                    messages, context_truncated = build_stage_messages(
                        workflow_id=cfg.id,
                        stage=stage_config,
                        instruction_text=instr,
                        user_text=f"请基于允许的客观底稿与前序上下文，针对标的 {code} 进行分析并输出。",
                        dossier=dossier,
                        stages=state.get("stages", {}),
                        preceding_stage_ids=preceding_ids,
                        messages=state.get("messages", []),
                    )
                    content, truncated, message_id = await run_stage(
                        model=model,
                        messages=messages,
                        max_chars=stage_config.output_chars,
                        stage_id=sid,
                        config=config,
                    )
                    message_id = message_id or f"stage-{sid}"  # 防御性 fallback：无流式 id 的模型
                    return {
                        "stages": {
                            sid: StageResult(
                                id=sid,
                                status="completed",
                                message_id=message_id,
                                truncated=truncated,
                                context_truncated=context_truncated,
                                started_at=current_st.started_at if current_st else None,
                                completed_at=utc_now(),
                            )
                        },
                        "messages": [AIMessage(id=message_id, content=content)],
                    }
                except Exception as e:
                    error_code = "CONTEXT_OVERFLOW" if isinstance(e, WorkflowContextOverflow) else "MODEL_ERROR"
                    err = redact_workflow_error(e, stage_id=sid, code=error_code)
                    return {
                        "stages": {sid: StageResult(
                            id=sid,
                            status="failed",
                            started_at=current_st.started_at if current_st else None,
                            completed_at=utc_now(),
                            error=err,
                        )},
                        "errors": [err],
                    }

            return run_fn

        builder.add_node(f"start_{stage_id}", make_start_fn(stage_id))
        builder.add_node(f"run_{stage_id}", make_run_fn(s_cfg, instruction_text))

    # 4. 汇总与终态节点
    async def finalize(state: WorkflowState, config: RunnableConfig) -> dict[str, Any]:
        stages = state.get("stages", {})
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

        return {
            "workflow_status": final_status,
            "completed_at": completed_at,
            "stages": skipped_stages,
            "result_summary": summary,
        }

    builder.add_node("finalize", finalize)

    # 5. 边与路由设置
    builder.add_edge("validate_input", "collect_dossier")

    def route_after_dossier(state: WorkflowState) -> str:
        dossier = state.get("dossier")
        if not dossier or not dossier.has_substantive_data:
            return "abort_no_data"
        variant = state.get("variant") or list(cfg.variants.keys())[0]
        v_stages = cfg.variants.get(variant, [])
        return f"start_{v_stages[0]}"

    start_targets = {f"start_{s.id}": f"start_{s.id}" for s in cfg.stages}
    start_targets["abort_no_data"] = "abort_no_data"
    builder.add_conditional_edges("collect_dossier", route_after_dossier, start_targets)

    def _make_next_target(sid: str, on_err: str):
        """run_<sid> 的后继路由：失败且 on_error=fail 直接收尾；否则变体顺序中 sid
        之后第一个非 completed/skipped 阶段，全部终态则 finalize。工厂捕获 sid，
        避免循环内 lambda 迟绑定。"""
        def next_target(state: WorkflowState) -> str:
            variant = state.get("variant") or list(cfg.variants.keys())[0]
            order = cfg.variants.get(variant, [])
            stages = state.get("stages", {})
            st = stages.get(sid)
            if st is not None and st.status == "failed" and on_err == "fail":
                return "finalize"
            for nxt in (order[order.index(sid) + 1:] if sid in order else []):
                st_next = stages.get(nxt)
                if st_next is None or st_next.status not in ("completed", "skipped"):
                    return f"start_{nxt}"
            return "finalize"
        return next_target

    for s_cfg in cfg.stages:
        sid = s_cfg.id
        builder.add_edge(f"start_{sid}", f"run_{sid}")
        builder.add_conditional_edges(
            f"run_{sid}", _make_next_target(sid, s_cfg.on_error), start_targets_with_finalize,
        )

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

    # 0. 统一入口与 resume 路由（单阶段：目标恒 start_stage，已完成则幂等收尾；
    # 门控与 staged 同构——写入式拒绝，见 _resume_rejection）
    async def entry(state: WorkflowState, config: RunnableConfig) -> dict[str, Any]:
        return {}

    async def auto_resume(state: WorkflowState, config: RunnableConfig) -> dict[str, Any]:
        rejection = _resume_rejection(state, cfg.config_version)
        if rejection is not None:
            return {
                "workflow_status": "failed",
                "completed_at": utc_now(),
                "result_summary": rejection.message,
                "errors": [rejection],
            }
        return {"workflow_status": "running", "resume": False}

    def _resume_target(state: WorkflowState) -> str:
        if state.get("workflow_status") == "failed":
            return END
        st = state.get("stages", {}).get(cfg.id)
        if st is not None and st.status in ("completed", "skipped"):
            return "finalize"
        return "start_stage"

    builder.add_node("entry", entry)
    builder.set_entry_point("entry")
    builder.add_conditional_edges(
        "entry",
        lambda s: "auto_resume" if _is_resume(s) else "validate_input",
        {"auto_resume": "auto_resume", "validate_input": "validate_input"},
    )
    builder.add_node("auto_resume", auto_resume)
    builder.add_conditional_edges(
        "auto_resume", _resume_target, {"start_stage": "start_stage", "finalize": "finalize", END: END},
    )

    # 1. 校验与初始化节点
    async def validate_input(state: WorkflowState, config: RunnableConfig) -> dict[str, Any]:
        text_field = cfg.input.text_field
        input_values = state.get("input")
        inp_text = input_values.get(text_field) if isinstance(input_values, dict) else None
        if not inp_text or not isinstance(inp_text, str):
            raise ValueError(f"输入字段 input.{text_field} 缺失或类型错误")

        return {
            "workflow_id": cfg.id,
            "workflow_status": "running",
            "config_version": cfg.config_version,
            "variant": None,
            "started_at": utc_now(),
            "dossier": None,
            "stages": Overwrite(value={}),
            "current_stage": None,
            "result_summary": None,
            "completed_at": None,
            "errors": Overwrite(value=[]),
        }

    builder.add_node("validate_input", validate_input)

    # 2. 启动阶段节点
    async def start_stage(state: WorkflowState, config: RunnableConfig) -> dict[str, Any]:
        return {
            "current_stage": cfg.id,
            "stages": {cfg.id: StageResult(id=cfg.id, status="running", started_at=utc_now())},
        }

    builder.add_node("start_stage", start_stage)

    # 3. 运行阶段节点
    async def run_stage_fn(state: WorkflowState, config: RunnableConfig) -> dict[str, Any]:
        current_st = state.get("stages", {}).get(cfg.id)
        if current_st is not None and current_st.status == "completed":
            return {}

        raw_inp = state["input"].get(cfg.input.text_field, "")
        # 单阶段模型不绑定工具：用无工具版策略，避免模型输出「要先调工具」的叙述。
        policy_text = fixed_system_policy(f"工作流：{cfg.id}", tools=False)
        system_text = f"{policy_text}\n\n【分析任务与指引】\n{instruction_text}"
        human_prefix = "【待分析输入内容】\n"
        human_suffix = "\n\n请按指引进行结构化分析与审计。"

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
            content, truncated, message_id = await run_stage(
                model=model,
                messages=messages,
                max_chars=HARD_LIMITS["stage_output_chars"],
                stage_id=cfg.id,
                config=config,
            )
            message_id = message_id or f"stage-{cfg.id}"
            return {
                "stages": {
                    cfg.id: StageResult(
                        id=cfg.id,
                        status="completed",
                        message_id=message_id,
                        truncated=truncated,
                        context_truncated=inp_truncated,
                        started_at=current_st.started_at if current_st else None,
                        completed_at=utc_now(),
                    )
                },
                "messages": [AIMessage(id=message_id, content=content)],
            }
        except Exception as e:
            error_code = "CONTEXT_OVERFLOW" if isinstance(e, WorkflowContextOverflow) else "MODEL_ERROR"
            err = redact_workflow_error(e, stage_id=cfg.id, code=error_code)
            return {
                "stages": {cfg.id: StageResult(
                    id=cfg.id,
                    status="failed",
                    started_at=current_st.started_at if current_st else None,
                    completed_at=utc_now(),
                    error=err,
                )},
                "errors": [err],
            }

    builder.add_node("run_stage", run_stage_fn)

    # 4. 汇总节点
    async def finalize(state: WorkflowState, config: RunnableConfig) -> dict[str, Any]:
        st = state.get("stages", {}).get(cfg.id)
        if st is not None and st.status == "completed":
            final_status: WorkflowStatus = "completed"
            summary = f"{cfg.id} 分析完成"
        else:
            final_status = "failed"
            summary = f"{cfg.id} 分析失败"

        return {
            "workflow_status": final_status,
            "completed_at": utc_now(),
            "result_summary": summary,
        }

    builder.add_node("finalize", finalize)

    # 连接流水线
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
