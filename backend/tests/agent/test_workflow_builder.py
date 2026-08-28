"""工作流图编译器与执行契约测试（v2：resume 通道路由、版本门控、消息指针写入、input 保持）。

测试覆盖：
- debate 工作流 standard 与 cross_exam 变体执行路径；
- 阶段正文写入 messages 通道、StageResult 只持 message_id 指针、无 result 键；
- start_node 在调用模型前将 stage.status=running 写入 checkpoint；
- on_error=continue 容错跳过与未产出占位符；on_error=fail 直接路由至 finalize；
- resume（{resume: true}）：input 原样保留、只补跑非终态阶段、已完成后续阶段不重放；
- 配置版本门控：config_version 不匹配 / 空线程的 resume 被写入式拒绝（failed 终态
  + errors 消息，run 正常收尾）；
- reflection 单步图编译与执行。
"""
from __future__ import annotations

from pathlib import Path
import pytest
import yaml
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langgraph.checkpoint.memory import InMemorySaver

from agent.skill_backends import BUILTIN_SKILLS_DIR
from agent.workflow_builder import build_workflow_graph
from agent.workflow_loader import load_workflow_config_from_file, validate_workflow_config
from agent.workflow_state import StageResult, message_text
from tests.agent.fakes import ScriptedChatModel, install_fake_exec_tool

WORKFLOWS_DIR = Path(__file__).resolve().parents[2] / "agent" / "workflows"
DEBATE_YAML = WORKFLOWS_DIR / "debate.yaml"


@pytest.fixture(autouse=True)
def _fake_tools(monkeypatch):
    """底稿 13 节离线化（助手与 FAKE_TOOL_RESULTS 见 tests/agent/fakes.py）。
    不 fake 会触真实数据源 + 全空走 abort_no_data。"""
    install_fake_exec_tool(monkeypatch)


def valid_debate_input() -> dict:
    return {"code": "600519"}


def run_config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id, "run_id": f"run-{thread_id}"}}


def _debate_cfg():
    return load_workflow_config_from_file(DEBATE_YAML, builtin_skills_root=BUILTIN_SKILLS_DIR)


def _system_text(messages) -> str:
    return "".join(str(m.content) for m in messages if getattr(m, "type", "") == "system")


class StageAwareModel(ScriptedChatModel):
    """按系统提示角色输出脚本文本（判别串与 e2e fixture StageAwareDebateModel 一致：
    「中立主持人」「空方研究员」——substring 必须与 debate.yaml 实际系统提示吻合，
    fixture 已在 e2e 里验证过这些判别串）。"""

    def _pick_reply(self, messages) -> AIMessage:
        text = _system_text(messages)
        if "中立主持人" in text:
            return AIMessage(content="中立主持脚本归纳")
        if "空方研究员" in text:
            return AIMessage(content="空方脚本观点")
        return AIMessage(content="多方脚本观点")

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.invocations.append(list(messages))
        return ChatResult(generations=[ChatGeneration(message=self._pick_reply(messages))])

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        self.invocations.append(list(messages))
        message = self._pick_reply(messages)
        yield ChatGenerationChunk(message=AIMessageChunk(content=message.content))


class FlakyBearModel(StageAwareModel):
    """首次空方调用抛错，制造「bear 失败 → resume 恢复」场景。

    计数用公开字段 invocations（_stream/_generate 先 append 再 _pick_reply，
    故计数已含本次调用，bear_visits<=1 即首次）。不碰 PrivateAttr：BaseChatModel
    是 Pydantic 模型，类级读 `_bear_calls` 拿到的是 ModelPrivateAttr 描述符对象，
    对其做算术直接 TypeError（已探针核实）。
    """

    def _pick_reply(self, messages) -> AIMessage:
        if "空方研究员" in _system_text(messages):
            bear_visits = sum(
                1 for inv in self.invocations if "空方研究员" in _system_text(inv)
            )
            if bear_visits <= 1:
                raise RuntimeError("MODEL_ERROR 模拟一次失败")
        return super()._pick_reply(messages)


def _thread(tid):
    return {"configurable": {"thread_id": tid}}


@pytest.mark.asyncio
async def test_fresh_run_writes_pointer_messages_and_no_result_key():
    graph = build_workflow_graph(_debate_cfg(), model=StageAwareModel(replies=[]),
                                 checkpointer=InMemorySaver(), builtin_skills_root=BUILTIN_SKILLS_DIR)
    result = await graph.ainvoke({"input": {"code": "600519"}, "variant": "standard"}, config=_thread("t1"))
    bull = result["stages"]["bull"]
    assert bull.status == "completed"
    assert bull.message_id  # BaseChatModel 自动 lc-run id
    assert any(m.id == bull.message_id for m in result["messages"])
    assert message_text(next(m for m in result["messages"] if m.id == bull.message_id)) == "多方脚本观点"
    assert "result" not in result


@pytest.mark.asyncio
async def test_resume_preserves_input_and_continues_failed_stage():
    cfg = _debate_cfg()
    graph = build_workflow_graph(cfg, model=FlakyBearModel(replies=[]),
                                 checkpointer=InMemorySaver(), builtin_skills_root=BUILTIN_SKILLS_DIR)
    first = await graph.ainvoke({"input": {"code": "600519"}, "variant": "standard"}, config=_thread("t2"))
    assert first["stages"]["bear"].status == "failed"
    assert first["input"] == {"code": "600519"}

    # 第二次运行（同 thread）：resume 通道触发，input 必须原样保留
    second = await graph.ainvoke({"resume": True}, config=_thread("t2"))
    assert second["input"] == {"code": "600519"}
    assert second["stages"]["bear"].status == "completed"
    assert second["stages"]["referee"].status == "completed"
    # 已完成的 bull 不重放：多方正文只出现一次
    bull_texts = [message_text(m) for m in second["messages"]]
    assert bull_texts.count("多方脚本观点") == 1
    assert bull_texts.count("空方脚本观点") == 1


@pytest.mark.asyncio
async def test_resume_skips_later_completed_stages():
    """bear 失败但 referee 已完成（真实部分完成历史：失败路径不写消息）时，
    resume 只补 bear——后续 completed 阶段不得重跑（重复模型费用 + 重复正文）。

    现行 start_* 会无条件把阶段覆盖回 running（workflow_builder.py:186），
    把 run_* 里的 completed 守卫（:197）中和掉；v2 必须靠路由持续跳过。
    """
    cfg = _debate_cfg()
    model = FlakyBearModel(replies=[])
    graph = build_workflow_graph(cfg, model=model,
                                 checkpointer=InMemorySaver(), builtin_skills_root=BUILTIN_SKILLS_DIR)
    # 首跑：bear 失败（on_error=continue）→ referee 照常完成 → partial
    first = await graph.ainvoke({"input": {"code": "600519"}, "variant": "standard"}, config=_thread("t4"))
    assert first["stages"]["bear"].status == "failed"
    assert first["stages"]["referee"].status == "completed"
    assert first["workflow_status"] == "partial"
    referee_calls = sum(1 for inv in model.invocations if "中立主持人" in _system_text(inv))
    assert referee_calls == 1  # 首跑恰好一次
    bear_calls = sum(1 for inv in model.invocations if "空方研究员" in _system_text(inv))
    assert bear_calls == 1  # 失败一次

    await graph.ainvoke({"resume": True}, config=_thread("t4"))
    final = (await graph.aget_state(_thread("t4"))).values
    assert final["stages"]["bear"].status == "completed"
    assert final["workflow_status"] == "completed"
    # referee 未重跑：调用次数与正文条数都还是 1
    assert sum(1 for inv in model.invocations if "中立主持人" in _system_text(inv)) == 1
    # bear 恰好补跑一次（失败不写消息，重跑新增一条）
    assert sum(1 for inv in model.invocations if "空方研究员" in _system_text(inv)) == 2
    texts = [message_text(m) for m in final["messages"]]
    assert texts.count("中立主持脚本归纳") == 1
    assert texts.count("空方脚本观点") == 1


@pytest.mark.asyncio
async def test_resume_rejected_on_version_mismatch_writes_failed_state():
    """版本不匹配的 resume 是「写入式拒绝」：不抛异常，拒绝原因落 checkpoint 终态。

    实测（inmem + v2 协议）：entry 抛 ValueError 只留下 status=error 的 run 记录——
    无 lifecycle failed 事件、run.error 无文本，前端无从感知原因；而页面失败判定的
    唯一可靠来源是 checkpoint（useWorkflowRun.readTerminalState）。所以拒绝必须与
    abort_no_data 同构：写 failed + errors，run 正常收尾。
    """
    cfg = _debate_cfg()
    graph = build_workflow_graph(cfg, model=StageAwareModel(replies=[]),
                                 checkpointer=InMemorySaver(), builtin_skills_root=BUILTIN_SKILLS_DIR)
    first = await graph.ainvoke({"input": {"code": "600519"}, "variant": "standard"}, config=_thread("t3"))
    assert first["workflow_status"] == "completed"
    # 公开 API 直写值（config_version 是 LastValue 通道）制造版本不匹配；
    # checkpointer.put 需要完整 Checkpoint 对象，不是测试接口。
    await graph.aupdate_state(_thread("t3"), {"config_version": 999})

    result = await graph.ainvoke({"resume": True}, config=_thread("t3"))
    assert result["workflow_status"] == "failed"
    assert result["errors"][0].message == "配置版本不兼容：请查看已有状态或重新发起工作流"
    assert result["errors"][0].code == "RESUME_CONFIG_VERSION"
    assert result["completed_at"] is not None
    # 已完成阶段不被触碰、正文不重放
    assert result["stages"]["bull"].status == "completed"
    texts = [message_text(m) for m in result["messages"]]
    assert texts.count("多方脚本观点") == 1


@pytest.mark.asyncio
async def test_resume_recollects_dossier_when_collection_was_interrupted():
    """底稿收集期被中断（state 无 dossier）：resume 必须回到 collect_dossier。

    否则 _resume_target 直接路由到首个阶段，而 _context_candidates 对
    dossier=None 静默跳过全部底稿块——模型在无数据状态下空跑辩论，
    违反「分析必须有客观底稿支撑」的产品红线。
    """
    cfg = _debate_cfg()
    model = StageAwareModel(replies=[])
    graph = build_workflow_graph(cfg, model=model,
                                 checkpointer=InMemorySaver(), builtin_skills_root=BUILTIN_SKILLS_DIR)
    config = run_config("t-dossier-resume")
    # 只跑到 validate_input 即断：此刻 state 有 input/running 但无 dossier
    await graph.ainvoke(
        {"input": {"code": "600519"}, "variant": "standard"},
        config=config,
        interrupt_after=["validate_input"],
    )
    snapshot = await graph.aget_state(config)
    assert snapshot.values.get("dossier") is None

    result = await graph.ainvoke({"resume": True}, config=config)
    assert result["dossier"] is not None and len(result["dossier"].sections) > 0
    assert result["stages"]["bull"].status == "completed"
    assert result["workflow_status"] == "completed"
    # 底稿确实进入了模型上下文（系统提示含底稿摘要块）
    assert any("底稿" in _system_text(inv) for inv in model.invocations)


@pytest.mark.asyncio
async def test_start_node_commits_running_before_model_node():
    cfg = _debate_cfg()
    checkpointer = InMemorySaver()
    model = StageAwareModel(replies=[])
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
async def test_debate_standard_and_cross_exam_variants():
    cfg = _debate_cfg()

    # Standard variant (3 stages: bull, bear, referee)
    graph_std = build_workflow_graph(cfg, model=StageAwareModel(replies=[]),
                                     checkpointer=InMemorySaver(), builtin_skills_root=BUILTIN_SKILLS_DIR)
    res_std = await graph_std.ainvoke({"input": {"code": "600519"}, "variant": "standard"}, config=run_config("t-std"))

    assert res_std["workflow_status"] == "completed"
    assert set(res_std["stages"].keys()) == {"bull", "bear", "referee"}
    assert res_std["stages"]["bull"].status == "completed"
    assert res_std["stages"]["bear"].status == "completed"
    assert res_std["stages"]["referee"].status == "completed"
    referee = res_std["stages"]["referee"]
    assert referee.message_id
    assert "中立主持脚本归纳" == message_text(
        next(m for m in res_std["messages"] if m.id == referee.message_id))
    assert "600519" in res_std["result_summary"] or "debate" in res_std["result_summary"] or "多空辩论" in res_std["result_summary"]

    # Cross-exam variant (5 stages)
    graph_cross = build_workflow_graph(cfg, model=StageAwareModel(replies=[]),
                                       checkpointer=InMemorySaver(), builtin_skills_root=BUILTIN_SKILLS_DIR)
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
    assert "result" not in res
    assert "reflection" in res["stages"]
    assert res["stages"]["reflection"].status == "completed"
    stage = res["stages"]["reflection"]
    assert stage.message_id
    assert message_text(next(m for m in res["messages"] if m.id == stage.message_id)) == "审计结果：发现 2 处逻辑跳跃与验证清单。"
    assert res["started_at"]
    assert res["completed_at"]
    assert stage.id == "reflection"
    assert stage.started_at
    assert stage.completed_at


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
    assert "result" not in result
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
async def test_stage_continue_on_error_routes_to_next_stage():
    cfg = _debate_cfg()

    class FailingBullModel(StageAwareModel):
        async def astream(self, messages, config=None, **kwargs):
            # Fail on first call (bull), succeed on bear and referee
            if len(self.invocations) == 0:
                self.invocations.append(list(messages))
                raise RuntimeError("Bull model failed")
            async for chunk in super().astream(messages, config=config, **kwargs):
                yield chunk

    model = FailingBullModel(replies=[])
    graph = build_workflow_graph(cfg, model=model, checkpointer=InMemorySaver(), builtin_skills_root=BUILTIN_SKILLS_DIR)
    res = await graph.ainvoke({"input": {"code": "600519"}, "variant": "standard"}, config=run_config("t-err-continue"))

    assert res["stages"]["bull"].status == "failed"
    assert res["stages"]["bull"].message_id is None
    assert res["stages"]["bear"].status == "completed"
    assert res["stages"]["referee"].status == "completed"
    assert len(res["errors"]) >= 1
    assert res["workflow_status"] == "partial"


@pytest.mark.asyncio
async def test_dossier_all_failed_aborts_without_model_calls(monkeypatch):
    # Simulate all tools failing / empty（覆盖 autouse fixture，制造全空底稿）
    import tools as legacy_tools
    monkeypatch.setattr(legacy_tools, "exec_tool", lambda name, args: {"error": "接口限流或无数据"})
    cfg = _debate_cfg()
    model = ScriptedChatModel([AIMessage(content="不应被调用")])
    graph = build_workflow_graph(cfg, model=model, checkpointer=InMemorySaver(), builtin_skills_root=BUILTIN_SKILLS_DIR)

    res = await graph.ainvoke({"input": {"code": "600519"}, "variant": "standard"}, config=run_config("t-all-fail"))

    assert res["workflow_status"] == "failed"
    assert len(model.invocations) == 0, "全部底稿失败时模型不应被调用"
    assert any(err.code == "NO_SUBSTANTIVE_DATA" for err in res["errors"])


@pytest.mark.asyncio
async def test_referee_failure_fails_entire_workflow():
    cfg = _debate_cfg()

    class FailingRefereeModel(StageAwareModel):
        async def astream(self, messages, config=None, **kwargs):
            if len(self.invocations) == 2:  # 3rd invocation (referee) fails
                self.invocations.append(list(messages))
                raise RuntimeError("Referee failed")
            async for chunk in super().astream(messages, config=config, **kwargs):
                yield chunk

    model = FailingRefereeModel(replies=[])
    graph = build_workflow_graph(cfg, model=model, checkpointer=InMemorySaver(), builtin_skills_root=BUILTIN_SKILLS_DIR)

    res = await graph.ainvoke({"input": {"code": "600519"}, "variant": "standard"}, config=run_config("t-referee-fail"))

    assert res["workflow_status"] == "failed"
    assert res["stages"]["referee"].status == "failed"
    assert res["stages"]["referee"].message_id is None


@pytest.mark.asyncio
async def test_on_error_fail_marks_remaining_stages_skipped():
    raw = yaml.safe_load(DEBATE_YAML.read_text(encoding="utf-8"))
    raw["id"] = "early_fail"
    raw["stages"][0]["on_error"] = "fail"
    cfg = validate_workflow_config(
        raw,
        workflow_id="early_fail",
        builtin_skills_root=BUILTIN_SKILLS_DIR,
    )

    class FailingFirstModel(StageAwareModel):
        async def astream(self, messages, config=None, **kwargs):
            self.invocations.append(list(messages))
            raise RuntimeError("model failed")
            yield  # pragma: no cover

    model = FailingFirstModel(replies=[])
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
async def test_new_run_overwrites_reducer_backed_terminal_state():
    raw = yaml.safe_load(DEBATE_YAML.read_text(encoding="utf-8"))
    raw["id"] = "rerun_state"
    raw["stages"][0]["on_error"] = "fail"
    cfg = validate_workflow_config(
        raw,
        workflow_id="rerun_state",
        builtin_skills_root=BUILTIN_SKILLS_DIR,
    )

    class FailOnSecondRunModel(StageAwareModel):
        async def astream(self, messages, config=None, **kwargs):
            if len(self.invocations) == 3:
                self.invocations.append(list(messages))
                raise RuntimeError("model failed")
            async for chunk in super().astream(messages, config=config, **kwargs):
                yield chunk

    model = FailOnSecondRunModel(replies=[])
    graph = build_workflow_graph(
        cfg,
        model=model,
        checkpointer=InMemorySaver(),
        builtin_skills_root=BUILTIN_SKILLS_DIR,
    )
    thread_config = {"configurable": {"thread_id": "rerun-state"}}
    first = await graph.ainvoke(
        {"input": {"code": "600519"}, "variant": "standard"},
        config=thread_config,
    )
    assert first["workflow_status"] == "completed"

    second = await graph.ainvoke(
        {"input": {"code": "600519"}, "variant": "standard"},
        config=thread_config,
    )

    assert second["workflow_status"] == "failed"
    assert second["stages"]["bull"].status == "failed"
    assert second["stages"]["bear"].status == "skipped"
    assert second["stages"]["referee"].status == "skipped"
    assert len(second["errors"]) == 1


@pytest.mark.asyncio
async def test_input_pattern_rejects_before_tools_or_model():
    cfg = _debate_cfg()
    model = ScriptedChatModel([AIMessage(content="不应被调用")])
    graph = build_workflow_graph(cfg, model=model, checkpointer=InMemorySaver(), builtin_skills_root=BUILTIN_SKILLS_DIR)

    with pytest.raises(ValueError, match="input.code"):
        await graph.ainvoke(
            {"input": {"code": "abc"}, "variant": "standard"},
            config=run_config("invalid-code"),
        )

    assert model.invocations == []


@pytest.mark.asyncio
async def test_result_stage_is_selected_by_configuration():
    raw = yaml.safe_load(DEBATE_YAML.read_text(encoding="utf-8"))
    raw["id"] = "configured_result"
    raw["result_stage"] = "bear"
    raw["stages"] = raw["stages"][:2]
    raw["stages"][1]["on_error"] = "fail"
    raw["variants"] = {"standard": ["bull", "bear"]}
    cfg = validate_workflow_config(raw, workflow_id="configured_result", builtin_skills_root=BUILTIN_SKILLS_DIR)
    model = StageAwareModel(replies=[])
    graph = build_workflow_graph(cfg, model=model, checkpointer=InMemorySaver(), builtin_skills_root=BUILTIN_SKILLS_DIR)

    result = await graph.ainvoke(
        {"input": {"code": "600519"}, "variant": "standard"},
        config=run_config("configured-result"),
    )

    assert result["workflow_status"] == "completed"
    bear = result["stages"]["bear"]
    assert bear.message_id
    assert message_text(next(m for m in result["messages"] if m.id == bear.message_id)) == "空方脚本观点"


@pytest.mark.asyncio
async def test_model_failure_terminal_state_does_not_leak_secret():
    cfg = _debate_cfg()
    secret = "Bearer sk-private-upstream-secret"

    class SecretFailureModel(StageAwareModel):
        async def astream(self, messages, config=None, **kwargs):
            self.invocations.append(list(messages))
            raise RuntimeError(f"model failed: {secret}")
            yield  # pragma: no cover

    model = SecretFailureModel(replies=[])
    graph = build_workflow_graph(
        cfg,
        model=model,
        checkpointer=InMemorySaver(),
        builtin_skills_root=BUILTIN_SKILLS_DIR,
    )
    final_state = None
    async for mode, chunk in graph.astream(
        {"input": {"code": "600519"}, "variant": "standard"},
        config=run_config("secret-failure"),
        stream_mode=["values"],
    ):
        final_state = chunk

    assert final_state is not None
    assert final_state["workflow_status"] == "failed"
    assert final_state["started_at"]
    assert final_state["completed_at"]
    failed_stage = final_state["stages"]["referee"]
    assert failed_stage.status == "failed"
    assert failed_stage.started_at and failed_stage.completed_at
    assert secret not in repr(final_state)
    assert final_state["errors"][-1].retryable is True


@pytest.mark.asyncio
async def test_resume_rejected_on_empty_checkpoint():
    """run 在首个 checkpoint 落盘前被取消（空线程）：resume 被写入式拒绝（终态 + 文案）。"""
    cfg = _debate_cfg()
    graph = build_workflow_graph(cfg, model=StageAwareModel(replies=[]),
                                 checkpointer=InMemorySaver(), builtin_skills_root=BUILTIN_SKILLS_DIR)
    # 只建线程不运行：state 为空（config_version 与 input 均缺失）
    result = await graph.ainvoke({"resume": True}, config=_thread("t-empty"))
    assert result["workflow_status"] == "failed"
    assert result["errors"][0].message == "该工作流没有可恢复的状态：请重新发起工作流"
    assert result["errors"][0].code == "RESUME_NO_STATE"
