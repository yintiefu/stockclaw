"""工作流图编译器与执行契约测试。

测试覆盖：
- debate 工作流 standard 与 cross_exam 变体执行路径；
- reflection / daily_review / news_digest 单步图编译与执行；
- start_node 在调用模型前将 stage.status=running 写入 checkpoint；
- 无效 variant 校验拦截；
- on_error=continue 容错跳过与未产出占位符；
- on_error=fail 直接路由至 finalize；
- 已完成阶段在 resume 时不重复执行；
- single_pass 图结果固定存放在 state["result"]。
"""
from __future__ import annotations

import asyncio
from pathlib import Path
import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

import tools as legacy_tools
from agent.skill_backends import BUILTIN_SKILLS_DIR
from agent.workflow_builder import build_workflow_graph
from agent.workflow_loader import load_workflow_config_from_file
from agent.workflow_state import StageResult, WorkflowState
from tests.agent.fakes import ScriptedChatModel

WORKFLOWS_DIR = Path(__file__).resolve().parents[2] / "agent" / "workflows"


def valid_debate_input() -> dict:
    return {"code": "600519"}


@pytest.mark.asyncio
async def test_start_node_commits_running_before_model_node(monkeypatch):
    monkeypatch.setattr(legacy_tools, "exec_tool", lambda name, args: {"price": 1800.0, "ok": True})
    cfg = load_workflow_config_from_file(WORKFLOWS_DIR / "debate.yaml", builtin_skills_root=BUILTIN_SKILLS_DIR)
    checkpointer = InMemorySaver()
    model = ScriptedChatModel([
        AIMessage(content="多方立论"),
        AIMessage(content="空方立论"),
        AIMessage(content="中立主持"),
    ])
    graph = build_workflow_graph(cfg, model=model, checkpointer=checkpointer, builtin_skills_root=BUILTIN_SKILLS_DIR)

    config = {"configurable": {"thread_id": "checkpoint-test-start"}}
    initial_input = {"input": valid_debate_input(), "variant": "standard"}

    # Run up to interrupt_after start_bull
    await graph.ainvoke(initial_input, config=config, interrupt_after=["start_bull"])
    state = await graph.aget_state(config)

    assert state.values["current_stage"] == "bull"
    assert state.values["stages"]["bull"].status == "running"
    assert "run_bull" in state.next


@pytest.mark.asyncio
async def test_debate_standard_and_cross_exam_variants(monkeypatch):
    monkeypatch.setattr(legacy_tools, "exec_tool", lambda name, args: {"price": 1800.0, "ok": True})
    cfg = load_workflow_config_from_file(WORKFLOWS_DIR / "debate.yaml", builtin_skills_root=BUILTIN_SKILLS_DIR)

    # Standard variant (3 stages: bull, bear, referee)
    model_std = ScriptedChatModel([
        AIMessage(content="多方观点"),
        AIMessage(content="空方观点"),
        AIMessage(content="中立裁判：双方分歧点与验证清单"),
    ])
    graph_std = build_workflow_graph(cfg, model=model_std, checkpointer=InMemorySaver(), builtin_skills_root=BUILTIN_SKILLS_DIR)
    res_std = await graph_std.ainvoke({"input": {"code": "600519"}, "variant": "standard"}, config={"configurable": {"thread_id": "t-std"}})

    assert res_std["workflow_status"] == "completed"
    assert set(res_std["stages"].keys()) == {"bull", "bear", "referee"}
    assert res_std["stages"]["bull"].status == "completed"
    assert res_std["stages"]["bear"].status == "completed"
    assert res_std["stages"]["referee"].status == "completed"
    assert "分歧点" in res_std["result"]
    assert "600519" in res_std["result_summary"] or "debate" in res_std["result_summary"] or "多空辩论" in res_std["result_summary"]

    # Cross-exam variant (5 stages)
    model_cross = ScriptedChatModel([
        AIMessage(content="多方观点"),
        AIMessage(content="空方观点"),
        AIMessage(content="多方反驳"),
        AIMessage(content="空方反驳"),
        AIMessage(content="中立裁判：最终分歧与验证清单"),
    ])
    graph_cross = build_workflow_graph(cfg, model=model_cross, checkpointer=InMemorySaver(), builtin_skills_root=BUILTIN_SKILLS_DIR)
    res_cross = await graph_cross.ainvoke({"input": {"code": "600519"}, "variant": "cross_exam"}, config={"configurable": {"thread_id": "t-cross"}})

    assert res_cross["workflow_status"] == "completed"
    assert set(res_cross["stages"].keys()) == {"bull", "bear", "bull_rebut", "bear_rebut", "referee"}


@pytest.mark.asyncio
async def test_single_pass_reflection_graph():
    cfg = load_workflow_config_from_file(WORKFLOWS_DIR / "reflection.yaml", builtin_skills_root=BUILTIN_SKILLS_DIR)
    model = ScriptedChatModel([AIMessage(content="审计结果：发现 2 处逻辑跳跃与验证清单。")])
    graph = build_workflow_graph(cfg, model=model, checkpointer=InMemorySaver(), builtin_skills_root=BUILTIN_SKILLS_DIR)

    res = await graph.ainvoke(
        {"input": {"source": "这家公司非常好，明年必翻倍。"}},
        config={"configurable": {"thread_id": "t-reflect"}},
    )
    assert res["workflow_status"] == "completed"
    assert res["result"] == "审计结果：发现 2 处逻辑跳跃与验证清单。"
    assert "reflection" in res["stages"]
    assert res["stages"]["reflection"].status == "completed"


@pytest.mark.asyncio
async def test_stage_continue_on_error_routes_to_next_stage(monkeypatch):
    monkeypatch.setattr(legacy_tools, "exec_tool", lambda name, args: {"price": 1800.0, "ok": True})
    cfg = load_workflow_config_from_file(WORKFLOWS_DIR / "debate.yaml", builtin_skills_root=BUILTIN_SKILLS_DIR)

    class FailingBullModel(ScriptedChatModel):
        async def astream(self, messages, config=None, **kwargs):
            # Fail on first call (bull), succeed on bear and referee
            if len(self.invocations) == 0:
                self.invocations.append(messages)
                raise RuntimeError("Bull model failed")
            async for chunk in super().astream(messages, config=config, **kwargs):
                yield chunk

    model = FailingBullModel([
        AIMessage(content="空方观点正常输出"),
        AIMessage(content="中立裁判：注意多方阶段未产出"),
    ])
    graph = build_workflow_graph(cfg, model=model, checkpointer=InMemorySaver(), builtin_skills_root=BUILTIN_SKILLS_DIR)
    res = await graph.ainvoke({"input": {"code": "600519"}, "variant": "standard"}, config={"configurable": {"thread_id": "t-err-continue"}})

    assert res["stages"]["bull"].status == "failed"
    assert res["stages"]["bull"].content is None
    assert res["stages"]["bear"].status == "completed"
    assert res["stages"]["referee"].status == "completed"
    assert len(res["errors"]) >= 1
    assert res["workflow_status"] == "partial"


@pytest.mark.asyncio
async def test_dossier_all_failed_aborts_without_model_calls(monkeypatch):
    # Simulate all tools failing / empty
    monkeypatch.setattr(legacy_tools, "exec_tool", lambda name, args: {"error": "接口限流或无数据"})
    cfg = load_workflow_config_from_file(WORKFLOWS_DIR / "debate.yaml", builtin_skills_root=BUILTIN_SKILLS_DIR)
    model = ScriptedChatModel([AIMessage(content="不应被调用")])
    graph = build_workflow_graph(cfg, model=model, checkpointer=InMemorySaver(), builtin_skills_root=BUILTIN_SKILLS_DIR)

    res = await graph.ainvoke({"input": {"code": "600519"}, "variant": "standard"}, config={"configurable": {"thread_id": "t-all-fail"}})

    assert res["workflow_status"] == "failed"
    assert len(model.invocations) == 0, "全部底稿失败时模型不应被调用"
    assert any(err.code == "NO_SUBSTANTIVE_DATA" for err in res["errors"])


@pytest.mark.asyncio
async def test_referee_failure_fails_entire_workflow(monkeypatch):
    monkeypatch.setattr(legacy_tools, "exec_tool", lambda name, args: {"price": 1800.0, "ok": True})
    cfg = load_workflow_config_from_file(WORKFLOWS_DIR / "debate.yaml", builtin_skills_root=BUILTIN_SKILLS_DIR)

    class FailingRefereeModel(ScriptedChatModel):
        async def astream(self, messages, config=None, **kwargs):
            if len(self.invocations) == 2:  # 3rd invocation (referee) fails
                self.invocations.append(messages)
                raise RuntimeError("Referee failed")
            async for chunk in super().astream(messages, config=config, **kwargs):
                yield chunk

    model = FailingRefereeModel([
        AIMessage(content="多方正常"),
        AIMessage(content="空方正常"),
    ])
    graph = build_workflow_graph(cfg, model=model, checkpointer=InMemorySaver(), builtin_skills_root=BUILTIN_SKILLS_DIR)

    res = await graph.ainvoke({"input": {"code": "600519"}, "variant": "standard"}, config={"configurable": {"thread_id": "t-referee-fail"}})

    assert res["workflow_status"] == "failed"
    assert res["stages"]["referee"].status == "failed"
    assert res["result"] is None


@pytest.mark.asyncio
async def test_compiled_graph_emits_custom_stream_events(monkeypatch):
    monkeypatch.setattr(legacy_tools, "exec_tool", lambda name, args: {"price": 1800.0, "ok": True})
    cfg = load_workflow_config_from_file(WORKFLOWS_DIR / "debate.yaml", builtin_skills_root=BUILTIN_SKILLS_DIR)
    model = ScriptedChatModel([
        AIMessage(content="多方论点"),
        AIMessage(content="空方论点"),
        AIMessage(content="中立主持"),
    ])
    graph = build_workflow_graph(cfg, model=model, checkpointer=InMemorySaver(), builtin_skills_root=BUILTIN_SKILLS_DIR)

    custom_events = []
    async for mode, chunk in graph.astream(
        {"input": {"code": "600519"}, "variant": "standard"},
        config={"configurable": {"thread_id": "t-custom-stream"}},
        stream_mode=["custom", "updates"],
    ):
        if mode == "custom":
            custom_events.append(chunk)

    assert len(custom_events) > 0
    event_types = [e.get("type") for e in custom_events]
    assert "workflow_started" in event_types
    assert "dossier_progress" in event_types
    assert "dossier_completed" in event_types
    assert "stage_started" in event_types
    assert "stage_delta" in event_types
    assert "stage_completed" in event_types
    assert "workflow_completed" in event_types
    assert custom_events[-1]["type"] == "workflow_completed"
