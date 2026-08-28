"""工作流运行时确定性辅助函数契约测试。

测试覆盖：
- 底稿抓取顺序与展示顺序一致；
- allow_no_record 产生 no_record，gap_if_empty 产生 gap 并计入 missing；
- 确定性摘要与 6000 字符限制；
- 失败/未完成阶段序列化为【阶段 <id> 未产出】且不泄露错误细节；
- 裁判阶段上下文使用摘要与关键阶段，不填入完整底稿正文；
- 阶段模型输出按上限截断并标记 truncated=True；
- 错误信息脱敏（去除 API Key / 内部 IP / 堆栈）。
"""
from __future__ import annotations

import asyncio
import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langchain_core.runnables import RunnableConfig

import tools as legacy_tools
from agent.workflow_loader import DossierConfig, DossierSectionConfig, StageConfig
from agent.workflow_runtime import (
    WorkflowContextOverflow,
    build_stage_messages,
    collect_dossier_sections,
    is_payload_empty,
    redact_workflow_error,
    run_stage,
    serialize_stage_context,
    summarize_dossier,
)
from agent.workflow_state import DossierResult, DossierSection, StageResult, WorkflowError
from tests.agent.fakes import ScriptedChatModel, install_fake_exec_tool


@pytest.fixture(autouse=True)
def _fake_tools(monkeypatch):
    """底稿工具离线化（本文件仅 collect_dossier 系测试真正触达，autouse 防遗漏）。"""
    install_fake_exec_tool(monkeypatch)


def _stage(sid, status="completed", message_id=None):
    return StageResult(id=sid, status=status, message_id=message_id)


def test_is_payload_empty_logic():
    assert is_payload_empty(None) is True
    assert is_payload_empty("") is True
    assert is_payload_empty([]) is True
    assert is_payload_empty({}) is True
    assert is_payload_empty({"period": "近5年", "metrics": {}}) is True
    assert is_payload_empty({"price": 100.0}) is False
    assert is_payload_empty([{"k": "v"}]) is False


def test_redact_workflow_error_removes_sensitive_data():
    raw_error = "Request failed to https://api.openai.com/v1/chat/completions with Bearer sk-secret12345678 at 192.168.1.30: Internal Server Error"
    err = redact_workflow_error(raw_error, stage_id="bull", code="UPSTREAM_ERROR")
    assert isinstance(err, WorkflowError)
    assert err.code == "UPSTREAM_ERROR"
    assert err.stage_id == "bull"
    assert err.retryable is False
    assert "sk-secret" not in err.message
    assert "192.168.1.30" not in err.message


@pytest.mark.asyncio
async def test_collect_dossier_preserves_order_and_handles_policies(monkeypatch):
    def fake_tool(name, args):
        if name == "query_quote":
            return {"price": 1800}
        if name == "query_margin":
            return {}  # empty for allow_no_record
        if name == "query_financials":
            return {"error": "service unavailable"}  # failed for gap_if_empty
        return {"data": "ok"}

    monkeypatch.setattr(legacy_tools, "exec_tool", fake_tool)

    dossier_cfg = DossierConfig(
        section_chars=1800,
        dossier_summary_chars=6000,
        sections=[
            DossierSectionConfig(id="quote", tool="query_quote", title="行情", empty_policy="gap_if_empty"),
            DossierSectionConfig(id="margin", tool="query_margin", title="两融", empty_policy="allow_no_record"),
            DossierSectionConfig(id="fin", tool="query_financials", title="财务", empty_policy="gap_if_empty"),
        ],
    )

    sections, missing = await collect_dossier_sections("600519", dossier_cfg)

    # Check preserved order
    assert [s.id for s in sections] == ["quote", "margin", "fin"]

    assert sections[0].status == "completed"
    assert sections[1].status == "no_record"
    assert "未取到任何记录" in sections[1].body
    assert sections[2].status == "failed"
    assert "财务" in missing
    assert len(missing) == 1


def test_summarize_dossier_deterministic_and_capped():
    sections = [
        DossierSection(id="s1", tool="query_quote", title="行情", empty_policy="gap_if_empty", status="completed", summary="现价 1800", body="..."),
        DossierSection(id="s2", tool="query_margin", title="两融", empty_policy="allow_no_record", status="no_record", summary="无记录", body="..."),
    ]
    summary = summarize_dossier(sections, missing=["财务"], max_chars=1000)
    assert "行情" in summary
    assert "两融" in summary
    assert "财务" in summary
    assert len(summary) <= 1000


def test_summarize_dossier_respects_tiny_limit():
    sections = [
        DossierSection(
            id="s1",
            tool="query_quote",
            title="行情",
            empty_policy="gap_if_empty",
            status="completed",
            summary="现价 1800",
            body="正文",
        ),
    ]

    assert len(summarize_dossier(sections, missing=[], max_chars=1)) <= 1


@pytest.mark.asyncio
async def test_collect_dossier_section_respects_tiny_limit(monkeypatch):
    monkeypatch.setattr(legacy_tools, "exec_tool", lambda name, args: {"price": 1800.0})
    dossier_cfg = DossierConfig(
        section_chars=1,
        dossier_summary_chars=1,
        sections=[DossierSectionConfig(
            id="quote",
            tool="query_quote",
            title="行情",
            empty_policy="gap_if_empty",
        )],
    )

    sections, _ = await collect_dossier_sections("600519", dossier_cfg)

    assert len(sections[0].body) <= 1


def test_serialize_stage_context_reads_pointer_content():
    messages = [AIMessage(id="m1", content="多方正文")]
    stages = {"bull": _stage("bull", message_id="m1"), "bear": _stage("bear", status="failed")}
    text, _ = serialize_stage_context(stages, ["bull", "bear"], messages, max_chars=1000)
    assert "多方正文" in text
    assert "【阶段 bear 未产出】" in text


@pytest.mark.asyncio
async def test_run_stage_streaming_and_truncation():
    # Long output stream
    chunks = [AIMessageChunk(content="chunk1 "), AIMessageChunk(content="chunk2 " * 200)]
    model = ScriptedChatModel([AIMessage(content="".join(c.content for c in chunks))])

    content, truncated, message_id = await run_stage(
        model=model,
        messages=[{"role": "user", "content": "hello"}],
        max_chars=50,
        stage_id="bull",
    )
    assert len(content) <= 50
    assert truncated is True
    assert message_id  # BaseChatModel 常规路径必有自动 lc-run id


@pytest.mark.asyncio
async def test_run_stage_does_not_truncate_output_within_limit():
    expected = "客观分析" * 10
    model = ScriptedChatModel([AIMessage(content=expected)])

    content, truncated, message_id = await run_stage(
        model=model,
        messages=[{"role": "user", "content": "hello"}],
        max_chars=len(expected),
        stage_id="bull",
    )

    assert content == expected
    assert truncated is False
    assert message_id


@pytest.mark.asyncio
async def test_run_stage_small_limit_still_respects_hard_cap():
    model = ScriptedChatModel([AIMessage(content="0123456789")])

    content, truncated, _ = await run_stage(
        model=model,
        messages=[{"role": "user", "content": "hello"}],
        max_chars=5,
        stage_id="bull",
    )

    assert len(content) <= 5
    assert truncated is True


@pytest.mark.asyncio
async def test_run_stage_closes_stream_after_truncation():
    class ClosingModel:
        closed = False

        def astream(self, messages):
            async def chunks():
                try:
                    yield AIMessageChunk(content="超长输出" * 100)
                finally:
                    self.closed = True

            return chunks()

    model = ClosingModel()
    await run_stage(
        model=model,  # type: ignore[arg-type]
        messages=[{"role": "user", "content": "hello"}],
        max_chars=20,
        stage_id="bull",
    )

    assert model.closed is True


@pytest.mark.asyncio
async def test_run_stage_extracts_text_blocks_and_skips_reasoning():
    # 开启 thinking 的 ReasoningChatOpenAI 会把流式增量规范成 content blocks
    # （reasoning / text）；阶段产出只应包含 text 块正文，reasoning 属于思考过程
    # 必须跳过，绝不能把块 dict 原样拼接进 stage content。
    model = ScriptedChatModel([AIMessage(content=[
        {"type": "reasoning", "reasoning": "先思考一下", "index": 0},
        {"type": "text", "text": "客观结论", "index": 1},
    ])])

    content, truncated, _ = await run_stage(
        model=ScriptedChatModel([AIMessage(content=[
            {"type": "reasoning", "reasoning": "先思考一下", "index": 0},
            {"type": "text", "text": "客观结论", "index": 1},
        ])]),
        messages=[{"role": "user", "content": "hello"}],
        max_chars=100,
        stage_id="bull",
    )

    assert content == "客观结论"
    assert truncated is False


@pytest.mark.asyncio
async def test_run_stage_joins_text_blocks_across_chunks():
    class BlockModel:
        def astream(self, messages):
            async def chunks():
                yield AIMessageChunk(content=[{"type": "reasoning", "reasoning": "隐", "index": 0}])
                yield AIMessageChunk(content=[{"type": "text", "text": "第一", "index": 1}])
                yield AIMessageChunk(content=[{"type": "text", "text": "第二", "index": 1}])
                yield AIMessageChunk(content="裸字符串")

            return chunks()

    content, truncated, _ = await run_stage(
        model=BlockModel(),  # type: ignore[arg-type]
        messages=[{"role": "user", "content": "hello"}],
        max_chars=100,
        stage_id="bear",
    )

    assert content == "第一第二裸字符串"
    assert truncated is False


@pytest.mark.asyncio
async def test_collect_dossier_emits_progress_directly(monkeypatch):
    emitted: list[tuple] = []
    monkeypatch.setattr("agent.workflow_runtime.emit_dossier_progress",
                        lambda *a: emitted.append(a))
    config = DossierConfig(
        section_chars=100, dossier_summary_chars=1000,
        sections=[DossierSectionConfig(id="q", tool="query_quote",
                                       args={"codes": ["${input.code}"]}, empty_policy="gap_if_empty")],
    )
    sections, missing = await collect_dossier_sections("600519", config)
    assert emitted and emitted[0][0] == "q"
    assert sections[0].status == "completed"


def test_build_stage_messages_budgets_the_final_serialized_prompt() -> None:
    dossier = DossierResult(
        sections=[DossierSection(
            id="quote",
            tool="query_quote",
            title="行情",
            empty_policy="gap_if_empty",
            status="completed",
            summary="现价 1800",
            body="底稿正文" * 1000,
        )],
        summary="底稿摘要" * 500,
        missing=["财务"],
        has_substantive_data=True,
    )
    stages = {"bull": _stage("bull", message_id="m1")}
    stage_messages = [AIMessage(id="m1", content="多方阶段内容")]
    base_stage = StageConfig(
        id="bear",
        label="空方研究员",
        skill="builtin/debate",
        instruction="references/bear.md",
        context=[],
        output_chars=1200,
        context_chars=60000,
    )
    base_messages, _ = build_stage_messages(
        workflow_id="debate",
        stage=base_stage,
        instruction_text="角色指引",
        user_text="请分析 600519",
        dossier=dossier,
        stages=stages,
        preceding_stage_ids=["bull"],
        messages=stage_messages,
    )
    fixed_chars = sum(len(str(message.content)) for message in base_messages)
    budgeted_stage = base_stage.model_copy(update={
        "context": ["dossier", "stage.bull"],
        "context_chars": fixed_chars + 80,
    })

    messages, context_truncated = build_stage_messages(
        workflow_id="debate",
        stage=budgeted_stage,
        instruction_text="角色指引",
        user_text="请分析 600519",
        dossier=dossier,
        stages=stages,
        preceding_stage_ids=["bull"],
        messages=stage_messages,
    )

    serialized = "".join(str(message.content) for message in messages)
    assert len(serialized) <= budgeted_stage.context_chars
    assert "多方阶段内容" in serialized
    assert context_truncated is True


def test_build_stage_messages_fails_when_fixed_prompt_exceeds_budget() -> None:
    stage = StageConfig(
        id="bull",
        label="多方研究员",
        skill="builtin/debate",
        instruction="references/bull.md",
        context=[],
        output_chars=1200,
        context_chars=10,
    )
    with pytest.raises(WorkflowContextOverflow, match="CONTEXT_OVERFLOW"):
        build_stage_messages(
            workflow_id="debate",
            stage=stage,
            instruction_text="不可裁剪的角色指引",
            user_text="不可裁剪的用户输入",
            dossier=None,
            stages={},
            preceding_stage_ids=[],
        )


def test_build_stage_messages_only_loads_declared_context() -> None:
    dossier = DossierResult(
        sections=[DossierSection(
            id="quote",
            tool="query_quote",
            title="行情",
            empty_policy="gap_if_empty",
            status="completed",
            summary="不应出现的摘要",
            body="不应出现的完整底稿",
        )],
        summary="不应出现的底稿摘要",
        missing=["不应出现的数据缺口"],
        has_substantive_data=True,
    )
    stage = StageConfig(
        id="bear",
        label="空方研究员",
        skill="builtin/debate",
        instruction="references/bear.md",
        context=["stage.bull"],
        output_chars=1200,
        context_chars=60000,
    )

    messages, context_truncated = build_stage_messages(
        workflow_id="debate",
        stage=stage,
        instruction_text="角色指引",
        user_text="请分析 600519",
        dossier=dossier,
        stages={
            "bull": _stage("bull", message_id="m1"),
            "other": _stage("other", message_id="m2"),
        },
        preceding_stage_ids=["bull", "other"],
        messages=[
            AIMessage(id="m1", content="允许的多方阶段"),
            AIMessage(id="m2", content="不应出现的其他阶段"),
        ],
    )

    serialized = "".join(str(message.content) for message in messages)
    assert "允许的多方阶段" in serialized
    assert "不应出现" not in serialized
    assert context_truncated is False


def test_build_stage_messages_uses_bounded_omission_marker() -> None:
    dossier = DossierResult(
        sections=[DossierSection(
            id="quote",
            tool="query_quote",
            title="行情",
            empty_policy="gap_if_empty",
            status="completed",
            summary="摘要",
            body="完整底稿" * 100,
        )],
        summary="摘要",
        missing=[],
        has_substantive_data=True,
    )
    stages = {"bull": _stage("bull", message_id="m1")}
    stage_messages = [AIMessage(id="m1", content="多方阶段内容")]
    base_stage = StageConfig(
        id="bear",
        label="空方研究员",
        skill="builtin/debate",
        instruction="references/bear.md",
        context=[],
        output_chars=1200,
        context_chars=60000,
    )
    base_messages, _ = build_stage_messages(
        workflow_id="debate",
        stage=base_stage,
        instruction_text="角色指引",
        user_text="请分析 600519",
        dossier=dossier,
        stages=stages,
        preceding_stage_ids=["bull"],
        messages=stage_messages,
    )
    fixed_chars = sum(len(str(message.content)) for message in base_messages)
    stage_block = "\n\n【前序阶段 bull】\n多方阶段内容"
    stage = base_stage.model_copy(update={
        "context": ["stage.bull", "dossier"],
        "context_chars": fixed_chars + len(stage_block) + len("dossier.quote【省略】"),
    })

    messages, context_truncated = build_stage_messages(
        workflow_id="debate",
        stage=stage,
        instruction_text="角色指引",
        user_text="请分析 600519",
        dossier=dossier,
        stages=stages,
        preceding_stage_ids=["bull"],
        messages=stage_messages,
    )

    serialized = "".join(str(message.content) for message in messages)
    assert len(serialized) <= stage.context_chars
    assert "多方阶段内容" in serialized
    assert "dossier.quote【省略】" in serialized
    assert context_truncated is True


def test_build_stage_messages_fails_if_full_omission_id_cannot_fit() -> None:
    dossier = DossierResult(
        sections=[DossierSection(
            id="unique-context-id",
            tool="query_quote",
            title="行情",
            empty_policy="gap_if_empty",
            status="completed",
            summary="摘要",
            body="完整底稿" * 100,
        )],
        summary="摘要",
        missing=[],
        has_substantive_data=True,
    )
    base_stage = StageConfig(
        id="bull",
        label="多方研究员",
        skill="builtin/debate",
        instruction="references/bull.md",
        context=[],
        output_chars=1200,
        context_chars=60000,
    )
    base_messages, _ = build_stage_messages(
        workflow_id="debate",
        stage=base_stage,
        instruction_text="角色指引",
        user_text="请分析 600519",
        dossier=dossier,
        stages={},
        preceding_stage_ids=[],
    )
    fixed_chars = sum(len(str(message.content)) for message in base_messages)
    stage = base_stage.model_copy(update={
        "context": ["dossier"],
        "context_chars": fixed_chars + len("dossier.unique-context-id【省略】") - 1,
    })

    with pytest.raises(WorkflowContextOverflow, match="CONTEXT_OVERFLOW"):
        build_stage_messages(
            workflow_id="debate",
            stage=stage,
            instruction_text="角色指引",
            user_text="请分析 600519",
            dossier=dossier,
            stages={},
            preceding_stage_ids=[],
        )


class RawStreamModel(BaseChatModel):
    """直覆 astream：绕过 BaseChatModel 的流式包装（chunk 不带自动 id）。

    不继承 ScriptedChatModel（其 replies: deque[AIMessage] 会校验元素类型——传
    字符串 reply 构造即抛 AttributeError）；自定义状态必须声明为字段，给
    Pydantic 模型实例赋未声明属性同样会抛错（均已探针核实）。
    """
    chunks: list
    captured_config: RunnableConfig | None = None

    @property
    def _llm_type(self) -> str:
        return "raw-stream-test"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        from langchain_core.outputs import ChatGeneration, ChatResult
        last = self.chunks[-1] if self.chunks else AIMessageChunk(content="")
        return ChatResult(generations=[ChatGeneration(message=last)])

    async def astream(self, messages, config=None, **kwargs):
        self.captured_config = config
        for chunk in self.chunks:
            yield chunk


@pytest.mark.asyncio
async def test_run_stage_propagates_config_and_captures_stream_id():
    model = RawStreamModel(chunks=[
        AIMessageChunk(content="多", id="stream-1"),
        AIMessageChunk(content="方", id="stream-1"),
    ])
    cfg = RunnableConfig(tags=["node:run_bull"])
    content, truncated, message_id = await run_stage(
        model, [HumanMessage(content="hi")], max_chars=100, stage_id="bull", config=cfg,
    )
    assert content == "多方"
    assert truncated is False
    assert message_id == "stream-1"
    assert model.captured_config is cfg


@pytest.mark.asyncio
async def test_run_stage_reports_none_id_when_stream_has_none():
    """无 id 流（防御路径）：返回 None，由 builder 用 fallback id。"""
    model = RawStreamModel(chunks=[AIMessageChunk(content="正文")])
    content, _, message_id = await run_stage(model, [], max_chars=100)
    assert content == "正文"
    assert message_id is None
