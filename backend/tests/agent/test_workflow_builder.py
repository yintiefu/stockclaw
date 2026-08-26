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
import uuid
import pytest
import yaml
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

import tools as legacy_tools
from agent.skill_backends import BUILTIN_SKILLS_DIR
from agent.workflow_builder import build_workflow_graph
from agent.workflow_loader import load_workflow_config_from_file, validate_workflow_config
from agent.workflow_state import StageResult, WorkflowState
from tests.agent.fakes import ScriptedChatModel

WORKFLOWS_DIR = Path(__file__).resolve().parents[2] / "agent" / "workflows"


def valid_debate_input() -> dict:
    return {"code": "600519"}


def run_config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id, "run_id": f"run-{thread_id}"}}


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

    config = run_config("checkpoint-test-start")
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
    res_std = await graph_std.ainvoke({"input": {"code": "600519"}, "variant": "standard"}, config=run_config("t-std"))

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
    res_cross = await graph_cross.ainvoke({"input": {"code": "600519"}, "variant": "cross_exam"}, config=run_config("t-cross"))

    assert res_cross["workflow_status"] == "completed"
    assert set(res_cross["stages"].keys()) == {"bull", "bear", "bull_rebut", "bear_rebut", "referee"}


@pytest.mark.asyncio
async def test_single_pass_reflection_graph():
    cfg = load_workflow_config_from_file(WORKFLOWS_DIR / "reflection.yaml", builtin_skills_root=BUILTIN_SKILLS_DIR)
    model = ScriptedChatModel([AIMessage(content="审计结果：发现 2 处逻辑跳跃与验证清单。")])
    graph = build_workflow_graph(cfg, model=model, checkpointer=InMemorySaver(), builtin_skills_root=BUILTIN_SKILLS_DIR)

    res = await graph.ainvoke(
        {"input": {"source": "这家公司非常好，明年必翻倍。"}},
        config=run_config("t-reflect"),
    )
    assert res["workflow_status"] == "completed"
    assert res["result"] == "审计结果：发现 2 处逻辑跳跃与验证清单。"
    assert "reflection" in res["stages"]
    assert res["stages"]["reflection"].status == "completed"
    assert res["started_at"]
    assert res["completed_at"]
    assert res["stages"]["reflection"].id == "reflection"
    assert res["stages"]["reflection"].started_at
    assert res["stages"]["reflection"].completed_at


@pytest.mark.asyncio
async def test_single_pass_stage_prompt_does_not_advertise_tools():
    # 阶段模型不绑定工具：系统提示词必须用无工具版策略（不宣传 query_* / 调用工具，
    # 保留中立红线），否则强 agentic 模型会输出「我要先调工具」而不做分析。
    cfg = load_workflow_config_from_file(WORKFLOWS_DIR / "reflection.yaml", builtin_skills_root=BUILTIN_SKILLS_DIR)
    model = ScriptedChatModel([AIMessage(content="审计结果")])
    graph = build_workflow_graph(cfg, model=model, checkpointer=InMemorySaver(), builtin_skills_root=BUILTIN_SKILLS_DIR)

    await graph.ainvoke(
        {"input": {"source": "某公司明年必翻倍。"}},
        config=run_config("t-no-tools-prompt"),
    )

    system = str(model.invocations[0][0].content)
    assert "query_" not in system
    assert "你可以调用工具" not in system
    assert "不调用任何工具" in system
    assert "不推荐买卖" in system


@pytest.mark.asyncio
async def test_single_pass_final_prompt_respects_max_chars():
    cfg = load_workflow_config_from_file(
        WORKFLOWS_DIR / "reflection.yaml",
        builtin_skills_root=BUILTIN_SKILLS_DIR,
    )
    cfg = cfg.model_copy(update={
        "input": cfg.input.model_copy(update={"max_chars": 1800}),
    })
    model = ScriptedChatModel([AIMessage(content="审计完成")])
    graph = build_workflow_graph(
        cfg,
        model=model,
        checkpointer=InMemorySaver(),
        builtin_skills_root=BUILTIN_SKILLS_DIR,
    )

    result = await graph.ainvoke(
        {"input": {"source": "用户原文" * 1000}},
        config=run_config("single-pass-budget"),
    )

    prompt_chars = sum(len(str(message.content)) for message in model.invocations[0])
    assert prompt_chars <= cfg.input.max_chars
    assert result["stages"]["reflection"].context_truncated is True


@pytest.mark.asyncio
async def test_single_pass_fixed_prompt_overflow_fails_before_model():
    cfg = load_workflow_config_from_file(
        WORKFLOWS_DIR / "reflection.yaml",
        builtin_skills_root=BUILTIN_SKILLS_DIR,
    )
    cfg = cfg.model_copy(update={
        "input": cfg.input.model_copy(update={"max_chars": 10}),
    })
    model = ScriptedChatModel([AIMessage(content="不应被调用")])
    graph = build_workflow_graph(
        cfg,
        model=model,
        checkpointer=InMemorySaver(),
        builtin_skills_root=BUILTIN_SKILLS_DIR,
    )

    result = await graph.ainvoke(
        {"input": {"source": "待审计文本"}},
        config=run_config("single-pass-overflow"),
    )

    assert model.invocations == []
    assert result["workflow_status"] == "failed"
    assert result["result"] is None
    assert result["errors"][-1].code == "CONTEXT_OVERFLOW"
    assert result["errors"][-1].retryable is False


@pytest.mark.asyncio
async def test_single_pass_input_error_does_not_echo_secret():
    cfg = load_workflow_config_from_file(
        WORKFLOWS_DIR / "reflection.yaml",
        builtin_skills_root=BUILTIN_SKILLS_DIR,
    )
    model = ScriptedChatModel([AIMessage(content="不应被调用")])
    graph = build_workflow_graph(
        cfg,
        model=model,
        checkpointer=InMemorySaver(),
        builtin_skills_root=BUILTIN_SKILLS_DIR,
    )
    secret = "Bearer sk-input-secret"

    with pytest.raises(ValueError, match="input.source") as exc_info:
        await graph.ainvoke(
            {"input": {"unexpected": secret}},
            config=run_config("single-pass-secret-input"),
        )

    assert secret not in str(exc_info.value)
    assert model.invocations == []


@pytest.mark.asyncio
async def test_single_pass_rejects_non_object_input_with_stable_field_path():
    cfg = load_workflow_config_from_file(
        WORKFLOWS_DIR / "reflection.yaml",
        builtin_skills_root=BUILTIN_SKILLS_DIR,
    )
    model = ScriptedChatModel([AIMessage(content="不应被调用")])
    graph = build_workflow_graph(
        cfg,
        model=model,
        checkpointer=InMemorySaver(),
        builtin_skills_root=BUILTIN_SKILLS_DIR,
    )

    with pytest.raises(ValueError, match="input.source"):
        await graph.ainvoke(
            {"input": None},  # type: ignore[typeddict-item]
            config=run_config("single-pass-invalid-shape"),
        )

    assert model.invocations == []


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
    res = await graph.ainvoke({"input": {"code": "600519"}, "variant": "standard"}, config=run_config("t-err-continue"))

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

    res = await graph.ainvoke({"input": {"code": "600519"}, "variant": "standard"}, config=run_config("t-all-fail"))

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

    res = await graph.ainvoke({"input": {"code": "600519"}, "variant": "standard"}, config=run_config("t-referee-fail"))

    assert res["workflow_status"] == "failed"
    assert res["stages"]["referee"].status == "failed"
    assert res["result"] is None


@pytest.mark.asyncio
async def test_on_error_fail_marks_remaining_stages_skipped(monkeypatch):
    monkeypatch.setattr(legacy_tools, "exec_tool", lambda name, args: {"price": 1800.0, "ok": True})
    raw = yaml.safe_load((WORKFLOWS_DIR / "debate.yaml").read_text(encoding="utf-8"))
    raw["id"] = "early_fail"
    raw["stages"][0]["on_error"] = "fail"
    cfg = validate_workflow_config(
        raw,
        workflow_id="early_fail",
        builtin_skills_root=BUILTIN_SKILLS_DIR,
    )

    class FailingFirstModel(ScriptedChatModel):
        async def astream(self, messages, config=None, **kwargs):
            self.invocations.append(messages)
            raise RuntimeError("model failed")
            yield  # pragma: no cover

    model = FailingFirstModel([])
    graph = build_workflow_graph(
        cfg,
        model=model,
        checkpointer=InMemorySaver(),
        builtin_skills_root=BUILTIN_SKILLS_DIR,
    )

    result = await graph.ainvoke(
        {"input": {"code": "600519"}, "variant": "standard"},
        config=run_config("early-fail"),
    )

    assert result["workflow_status"] == "failed"
    assert result["stages"]["bull"].status == "failed"
    assert result["stages"]["bear"].status == "skipped"
    assert result["stages"]["bear"].completed_at
    assert result["stages"]["referee"].status == "skipped"
    assert result["stages"]["referee"].completed_at
    assert len(model.invocations) == 1


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
    server_run_id = uuid.uuid4()
    async for mode, chunk in graph.astream(
        {"input": {"code": "600519"}, "variant": "standard"},
        config={
            "configurable": {
                "thread_id": "t-custom-stream",
                "run_id": "configurable-fallback-must-not-shadow-runtime",
            },
            "run_id": server_run_id,
        },
        stream_mode=["custom", "updates"],
    ):
        if mode == "custom":
            custom_events.append(chunk)

    assert len(custom_events) > 0
    event_types = [e.get("type") for e in custom_events]
    assert "workflow.status" in event_types
    assert "dossier.progress" in event_types
    assert "dossier.ready" in event_types
    assert "stage.started" in event_types
    assert "stage.delta" in event_types
    assert "stage.completed" in event_types
    assert "workflow.completed" in event_types
    assert custom_events[-1]["type"] == "workflow.completed"
    assert all("input" not in event for event in custom_events)
    assert {event["run_id"] for event in custom_events} == {str(server_run_id)}
    assert [event["seq"] for event in custom_events] == list(range(1, len(custom_events) + 1))
    assert len({(event["type"], event["seq"]) for event in custom_events}) == len(custom_events)
    started = next(event for event in custom_events if event["type"] == "stage.started")
    assert started["label"] == "多方研究员"
    assert custom_events[-1]["status"] == "completed"


@pytest.mark.asyncio
async def test_each_new_run_on_same_thread_restarts_event_sequence(monkeypatch):
    monkeypatch.setattr(legacy_tools, "exec_tool", lambda name, args: {"price": 1800.0, "ok": True})
    cfg = load_workflow_config_from_file(
        WORKFLOWS_DIR / "debate.yaml",
        builtin_skills_root=BUILTIN_SKILLS_DIR,
    )
    model = ScriptedChatModel([
        AIMessage(content="首次多方"),
        AIMessage(content="首次空方"),
        AIMessage(content="首次主持"),
        AIMessage(content="再次多方"),
        AIMessage(content="再次空方"),
        AIMessage(content="再次主持"),
    ])
    graph = build_workflow_graph(
        cfg,
        model=model,
        checkpointer=InMemorySaver(),
        builtin_skills_root=BUILTIN_SKILLS_DIR,
    )

    for index in range(2):
        run_id = uuid.uuid4()
        events = []
        async for mode, chunk in graph.astream(
            {"input": {"code": "600519"}, "variant": "standard"},
            config={
                "configurable": {"thread_id": "same-thread-new-run"},
                "run_id": run_id,
            },
            stream_mode=["custom", "updates"],
        ):
            if mode == "custom":
                events.append(chunk)

        assert events, f"第 {index + 1} 次 run 应发射事件"
        assert events[0]["seq"] == 1
        assert [event["seq"] for event in events] == list(range(1, len(events) + 1))
        assert {event["run_id"] for event in events} == {str(run_id)}


@pytest.mark.asyncio
async def test_resumed_new_run_restarts_event_sequence(monkeypatch):
    monkeypatch.setattr(legacy_tools, "exec_tool", lambda name, args: {"price": 1800.0, "ok": True})
    cfg = load_workflow_config_from_file(
        WORKFLOWS_DIR / "debate.yaml",
        builtin_skills_root=BUILTIN_SKILLS_DIR,
    )
    graph = build_workflow_graph(
        cfg,
        model=ScriptedChatModel([
            AIMessage(content="多方"),
            AIMessage(content="空方"),
            AIMessage(content="主持"),
        ]),
        checkpointer=InMemorySaver(),
        builtin_skills_root=BUILTIN_SKILLS_DIR,
    )
    thread_config = {"configurable": {"thread_id": "resume-new-run"}}
    await graph.ainvoke(
        {"input": {"code": "600519"}, "variant": "standard"},
        config={**thread_config, "run_id": uuid.uuid4()},
        interrupt_after=["start_bull"],
    )

    resumed_run_id = uuid.uuid4()
    events = []
    async for mode, chunk in graph.astream(
        None,
        config={**thread_config, "run_id": resumed_run_id},
        stream_mode=["custom", "updates"],
    ):
        if mode == "custom":
            events.append(chunk)

    assert events[0]["seq"] == 1
    assert [event["seq"] for event in events] == list(range(1, len(events) + 1))
    assert {event["run_id"] for event in events} == {str(resumed_run_id)}


@pytest.mark.asyncio
async def test_new_run_overwrites_reducer_backed_terminal_state(monkeypatch):
    monkeypatch.setattr(legacy_tools, "exec_tool", lambda name, args: {"price": 1800.0, "ok": True})
    raw = yaml.safe_load((WORKFLOWS_DIR / "debate.yaml").read_text(encoding="utf-8"))
    raw["id"] = "rerun_state"
    raw["stages"][0]["on_error"] = "fail"
    cfg = validate_workflow_config(
        raw,
        workflow_id="rerun_state",
        builtin_skills_root=BUILTIN_SKILLS_DIR,
    )

    class FailOnSecondRunModel(ScriptedChatModel):
        async def astream(self, messages, config=None, **kwargs):
            if len(self.invocations) == 3:
                self.invocations.append(messages)
                raise RuntimeError("model failed")
            async for chunk in super().astream(messages, config=config, **kwargs):
                yield chunk

    model = FailOnSecondRunModel([
        AIMessage(content="首次多方"),
        AIMessage(content="首次空方"),
        AIMessage(content="首次主持"),
    ])
    graph = build_workflow_graph(
        cfg,
        model=model,
        checkpointer=InMemorySaver(),
        builtin_skills_root=BUILTIN_SKILLS_DIR,
    )
    thread_config = {"configurable": {"thread_id": "rerun-state"}}
    first = await graph.ainvoke(
        {"input": {"code": "600519"}, "variant": "standard"},
        config={**thread_config, "run_id": uuid.uuid4()},
    )
    assert first["workflow_status"] == "completed"

    second = await graph.ainvoke(
        {"input": {"code": "600519"}, "variant": "standard"},
        config={**thread_config, "run_id": uuid.uuid4()},
    )

    assert second["workflow_status"] == "failed"
    assert second["stages"]["bull"].status == "failed"
    assert second["stages"]["bear"].status == "skipped"
    assert second["stages"]["referee"].status == "skipped"
    assert second["result"] is None
    assert len(second["errors"]) == 1


@pytest.mark.asyncio
async def test_input_pattern_rejects_before_tools_or_model(monkeypatch):
    tool_calls = 0

    def fake_tool(name, args):
        nonlocal tool_calls
        tool_calls += 1
        return {"price": 1800.0}

    monkeypatch.setattr(legacy_tools, "exec_tool", fake_tool)
    cfg = load_workflow_config_from_file(WORKFLOWS_DIR / "debate.yaml", builtin_skills_root=BUILTIN_SKILLS_DIR)
    model = ScriptedChatModel([AIMessage(content="不应被调用")])
    graph = build_workflow_graph(cfg, model=model, checkpointer=InMemorySaver(), builtin_skills_root=BUILTIN_SKILLS_DIR)

    with pytest.raises(ValueError, match="input.code"):
        await graph.ainvoke(
            {"input": {"code": "abc"}, "variant": "standard"},
            config=run_config("invalid-code"),
        )

    assert tool_calls == 0
    assert model.invocations == []


@pytest.mark.asyncio
async def test_result_stage_is_selected_by_configuration(monkeypatch):
    monkeypatch.setattr(legacy_tools, "exec_tool", lambda name, args: {"price": 1800.0, "ok": True})
    raw = yaml.safe_load((WORKFLOWS_DIR / "debate.yaml").read_text(encoding="utf-8"))
    raw["id"] = "configured_result"
    raw["result_stage"] = "bear"
    raw["stages"] = raw["stages"][:2]
    raw["stages"][1]["on_error"] = "fail"
    raw["variants"] = {"standard": ["bull", "bear"]}
    cfg = validate_workflow_config(raw, workflow_id="configured_result", builtin_skills_root=BUILTIN_SKILLS_DIR)
    model = ScriptedChatModel([AIMessage(content="多方文本"), AIMessage(content="配置结果阶段")])
    graph = build_workflow_graph(cfg, model=model, checkpointer=InMemorySaver(), builtin_skills_root=BUILTIN_SKILLS_DIR)

    result = await graph.ainvoke(
        {"input": {"code": "600519"}, "variant": "standard"},
        config=run_config("configured-result"),
    )

    assert result["workflow_status"] == "completed"
    assert result["result"] == "配置结果阶段"


@pytest.mark.asyncio
async def test_model_failure_terminal_state_and_events_do_not_leak_secret(monkeypatch):
    monkeypatch.setattr(legacy_tools, "exec_tool", lambda name, args: {"price": 1800.0, "ok": True})
    cfg = load_workflow_config_from_file(
        WORKFLOWS_DIR / "debate.yaml",
        builtin_skills_root=BUILTIN_SKILLS_DIR,
    )
    secret = "Bearer sk-private-upstream-secret"

    class SecretFailureModel(ScriptedChatModel):
        async def astream(self, messages, config=None, **kwargs):
            self.invocations.append(messages)
            raise RuntimeError(f"model failed: {secret}")
            yield  # pragma: no cover

    model = SecretFailureModel([])
    graph = build_workflow_graph(
        cfg,
        model=model,
        checkpointer=InMemorySaver(),
        builtin_skills_root=BUILTIN_SKILLS_DIR,
    )
    custom_events = []
    final_state = None
    async for mode, chunk in graph.astream(
        {"input": {"code": "600519"}, "variant": "standard"},
        config=run_config("secret-failure"),
        stream_mode=["custom", "values"],
    ):
        if mode == "custom":
            custom_events.append(chunk)
        elif mode == "values":
            final_state = chunk

    assert final_state is not None
    assert final_state["workflow_status"] == "failed"
    assert "result" in final_state and final_state["result"] is None
    assert final_state["started_at"]
    assert final_state["completed_at"]
    failed_stage = final_state["stages"]["referee"]
    assert failed_stage.status == "failed"
    assert failed_stage.started_at and failed_stage.completed_at
    serialized = repr(final_state) + repr(custom_events)
    assert secret not in serialized
    assert custom_events[-1]["type"] == "workflow.failed"
    assert custom_events[-1]["retryable"] is True


# ---------------------------------------------------------------------------
# 客户端取消/审计补丁写入 dict 形状阶段后的恢复（E2E 发现的真实回归）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_staged_resume_after_client_cancel_dict_patch(monkeypatch):
    """前端 cancel/interrupt 回写（updateState 不带 as_node）写入 JSON dict 阶段，
    恢复 run 时节点必须容忍 dict 并规整回 StageResult，而不是 AttributeError。"""
    monkeypatch.setattr(legacy_tools, "exec_tool", lambda name, args: {"price": 1800.0, "ok": True})
    cfg = load_workflow_config_from_file(WORKFLOWS_DIR / "debate.yaml", builtin_skills_root=BUILTIN_SKILLS_DIR)
    model = ScriptedChatModel([
        AIMessage(content="多方立论"),
        AIMessage(content="空方立论"),
        AIMessage(content="中立主持归纳"),
    ])
    graph = build_workflow_graph(cfg, model=model, checkpointer=InMemorySaver(), builtin_skills_root=BUILTIN_SKILLS_DIR)
    config = run_config("client-cancel-patch")

    await graph.ainvoke({"input": valid_debate_input(), "variant": "standard"},
                        config=config, interrupt_after=["run_bear"])
    state = await graph.aget_state(config)
    # 前端 terminalPatch 的形状：JSON dict、running 阶段改 cancelled
    patched = {
        sid: ({"id": sid, "status": "cancelled", "content": None, "completed_at": "2026-08-26T00:00:00Z"}
              if st.status == "running" else st.model_dump(mode="json"))
        for sid, st in state.values["stages"].items()
    }
    patched["referee"] = {"id": "referee", "status": "pending", "content": None}
    await graph.aupdate_state(config, {
        "workflow_status": "cancelled",
        "current_stage": None,
        "completed_at": "2026-08-26T00:00:00Z",
        "stages": patched,
    })

    result = await graph.ainvoke(None, config=config)

    assert result["workflow_status"] == "completed"
    assert all(isinstance(st, StageResult) and st.status == "completed"
               for st in result["stages"].values())
    assert result["result"] == "中立主持归纳"


@pytest.mark.asyncio
async def test_single_pass_resume_after_client_cancel_dict_patch():
    """single_pass 图同样要容忍客户端 dict 补丁后的恢复。"""
    cfg = load_workflow_config_from_file(WORKFLOWS_DIR / "reflection.yaml", builtin_skills_root=BUILTIN_SKILLS_DIR)
    model = ScriptedChatModel([AIMessage(content="审计完成结果")])
    graph = build_workflow_graph(cfg, model=model, checkpointer=InMemorySaver(), builtin_skills_root=BUILTIN_SKILLS_DIR)
    config = run_config("single-cancel-patch")

    await graph.ainvoke({"input": {"source": "已有推理文本"}}, config=config, interrupt_after=["start_stage"])
    await graph.aupdate_state(config, {
        "workflow_status": "cancelled",
        "current_stage": None,
        "stages": {"reflection": {"id": "reflection", "status": "cancelled", "content": None}},
    })

    result = await graph.ainvoke(None, config=config)

    assert result["workflow_status"] == "completed"
    assert isinstance(result["stages"]["reflection"], StageResult)
    assert result["result"] == "审计完成结果"
