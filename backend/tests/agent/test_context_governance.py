"""1D 上下文治理：canonical 渲染、完整 turn 裁剪、模型 reservation 与 budget 事件。"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from agent.governance import (
    ContextAndModelGovernance,
    ContextLimitExceeded,
    RunActiveTimeout,
    RunControl,
    render_model_context,
    render_policy_explanation,
    trim_model_request,
)
from agent.models import PolicySnapshot
from agent.runtime import compose_system_prompt

pytestmark = pytest.mark.asyncio


class FakeClock:
    def __init__(self, start: float = 1000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def snapshot(**overrides) -> PolicySnapshot:
    values = {
        "policy_revision": 1,
        "max_model_calls": 8,
        "max_tool_calls": 16,
        "tool_timeout_seconds": 30,
        "max_active_seconds": 300,
        "max_context_chars": 120_000,
    }
    values.update(overrides)
    return PolicySnapshot(**values)


def model_request(system: str | None = None, messages=None):
    from langchain.agents.middleware.types import ModelRequest
    from tests.agent.fakes import ScriptedChatModel

    return ModelRequest(
        model=ScriptedChatModel([]),
        messages=list(messages or []),
        system_message=SystemMessage(content=system) if system else None,
    )


def load_skill_turn(marker: str, skill: str = "s1", call_id: str = "ls-1"):
    """一个包含完整 load_skill 调用/结果的用户 turn。"""
    return [
        HumanMessage(content=f"user-{marker}"),
        AIMessage(content="", tool_calls=[{
            "id": call_id, "name": "load_skill", "args": {"name": skill}}]),
        ToolMessage(
            content=json.dumps({"name": skill, "digest": "d1", "instructions": f"指令-{marker}"}, ensure_ascii=False),
            tool_call_id=call_id),
    ]


def plain_turn(marker: str, filler: int = 0):
    return [
        HumanMessage(content=f"user-{marker}" + "u" * filler),
        AIMessage(content=f"assistant-{marker}" + "a" * filler),
    ]


async def record_view(view):
    await asyncio.sleep(0)


def make_middleware(control, *, events=None, persist=None, thread_id="thread-1", run_id="run-1"):
    async def emit(payload):
        if events is not None:
            events.append(payload)

    return ContextAndModelGovernance(
        control, persist=persist or record_view, emit=emit,
        thread_id=thread_id, run_id=run_id)


# ---- 渲染 ----


def test_render_is_deterministic_and_covers_roles_names_call_ids():
    messages = [
        HumanMessage(content="分析现金流"),
        AIMessage(content="", tool_calls=[{
            "id": "call-9", "name": "query_quote", "args": {"codes": ["600519"]}}]),
        ToolMessage(content={"price": 10}, tool_call_id="call-9"),
        AIMessage(content="结论", name="clara"),
    ]
    first = render_model_context(SystemMessage(content="中立提示"), messages)
    second = render_model_context(SystemMessage(content="中立提示"), messages)
    assert first == second
    for expected in ("human", "ai", "tool", "clara", "call-9", "query_quote",
                     "分析现金流", "中立提示"):
        assert expected in first
    assert "600519" in first


def test_structured_content_renders_sorted_compact_json():
    rendered = render_model_context(None, [
        HumanMessage(content=[{"z": 1, "a": {"c": 3, "b": 2}}]),
    ])
    assert '"a":{"b":2,"c":3}' in rendered
    assert '"z":1' in rendered
    assert ", " not in rendered.split('"a"', 1)[1]  # 紧凑分隔符


def test_render_includes_system_and_message_separators():
    rendered = render_model_context(
        SystemMessage(content="SYS"),
        [HumanMessage(content="U1"), AIMessage(content="A1")],
    )
    assert rendered.index("SYS") < rendered.index("U1") < rendered.index("A1")


def test_policy_explanation_is_deterministic_and_secret_free():
    explanation = render_policy_explanation(snapshot())
    assert explanation == render_policy_explanation(snapshot())
    for token in ("8", "16", "30", "300", "120000"):
        assert token in explanation.replace(",", "")
    assert "key" not in explanation.lower()


def test_compose_system_prompt_orders_neutrality_policy_catalog():
    prompt = compose_system_prompt("POLICY-EXPLANATION", "\n\n## 用户已启用的 Skill")
    assert prompt.index("Agent 工作台") < prompt.index("POLICY-EXPLANATION") < prompt.index("用户已启用的 Skill")


# ---- 裁剪 ----


def test_small_context_returns_request_unchanged_with_zero_metrics():
    messages = [*plain_turn("t1"), *plain_turn("t2")]
    request = model_request(system="SYS", messages=messages)
    trimmed, truncation = trim_model_request(request, 120_000)
    assert [m.content for m in trimmed.messages] == [m.content for m in messages]
    assert truncation.occurred is False
    assert truncation.removed_turns == 0
    assert truncation.original_chars == truncation.retained_chars
    assert truncation.original_chars > 0


def test_trim_drops_oldest_turns_and_restores_chronological_order():
    history = []
    for marker in ("old", "mid", "new"):
        history.extend(plain_turn(marker, filler=200))
    request = model_request(system="SYS", messages=history)
    limit = len(render_model_context(SystemMessage(content="SYS"), history)) - 420
    trimmed, truncation = trim_model_request(request, limit)
    contents = [m.content for m in trimmed.messages]
    assert truncation.occurred is True
    assert truncation.removed_turns >= 1
    assert contents[0].startswith("user-mid") or contents[0].startswith("user-new")
    assert contents[-2].startswith("user-new")  # 当前 turn 完整保留
    assert "assistant-old" not in contents
    # 时间顺序保持：user-mid 在 user-new 前（若两者都保留）
    if any("user-mid" in c for c in contents) and any("user-new" in c for c in contents):
        assert contents[0].startswith("user-mid")
    assert truncation.retained_chars == len(render_model_context(
        SystemMessage(content="SYS"), trimmed.messages))


def test_tool_call_and_result_never_split():
    history = [
        HumanMessage(content="u-1"),
        AIMessage(content="", tool_calls=[{"id": "c-1", "name": "t", "args": {}}]),
        ToolMessage(content="result-1", tool_call_id="c-1"),
        HumanMessage(content="u-2"),
    ]
    request = model_request(system="SYS", messages=history)
    trimmed, _ = trim_model_request(request, 60)  # 极小上限：只会保留强制内容
    kept_ids = {getattr(m, "tool_call_id", None) for m in trimmed.messages}
    kept_call_ids = {
        call.get("id")
        for m in trimmed.messages
        for call in (getattr(m, "tool_calls", None) or [])
    }
    # 留下的 tool result 必有配对的 tool call 声明
    for value in kept_ids:
        if value:
            assert value in kept_call_ids


def test_current_turn_and_latest_load_skill_turns_are_forced():
    history = [
        *load_skill_turn("early", skill="s1", call_id="ls-1"),
        *plain_turn("middle", filler=400),
        *load_skill_turn("late", skill="s2", call_id="ls-2"),
        *plain_turn("current", filler=50),
    ]
    request = model_request(system="SYS", messages=history)
    full = len(render_model_context(SystemMessage(content="SYS"), history))
    # 上限只够 system + 两个 load_skill turn + 当前 turn：middle 必须被裁掉
    forced = len(render_model_context(SystemMessage(content="SYS"), [
        *load_skill_turn("early", skill="s1", call_id="ls-1"),
        *load_skill_turn("late", skill="s2", call_id="ls-2"),
        *plain_turn("current", filler=50),
    ]))
    limit = forced + 100
    assert limit < full
    trimmed, truncation = trim_model_request(request, limit)
    contents = [m.content for m in trimmed.messages]
    assert truncation.occurred is True
    assert not any("user-middle" in c for c in contents)
    assert any("指令-early" in c for c in contents)
    assert any("指令-late" in c for c in contents)
    assert any("user-current" in c for c in contents)


def test_only_latest_load_skill_turn_of_same_skill_is_forced():
    history = [
        *load_skill_turn("first", skill="s1", call_id="ls-1"),
        *load_skill_turn("second", skill="s1", call_id="ls-2"),
        *plain_turn("current", filler=50),
    ]
    request = model_request(system="SYS", messages=history)
    forced = len(render_model_context(SystemMessage(content="SYS"), [
        *load_skill_turn("second", skill="s1", call_id="ls-2"),
        *plain_turn("current", filler=50),
    ]))
    full = len(render_model_context(SystemMessage(content="SYS"), history))
    limit = forced + 50
    assert limit < full
    trimmed, truncation = trim_model_request(request, limit)
    contents = [m.content for m in trimmed.messages]
    assert any("指令-second" in c for c in contents)
    assert not any("指令-first" in c for c in contents)
    assert truncation.removed_turns == 1


def test_trim_never_mutates_original_request():
    history = [*plain_turn("old", filler=300), *plain_turn("new")]
    request = model_request(system="SYS", messages=history)
    original_ids = [id(m) for m in request.messages]
    trim_model_request(request, 300)
    assert [id(m) for m in request.messages] == original_ids
    assert len(request.messages) == len(history)


# ---- 中间件 ----


async def test_forced_context_overflow_prevents_provider_and_reservation():
    control = RunControl(snapshot(max_context_chars=16_000), clock=FakeClock())
    provider = AsyncMock()
    middleware = make_middleware(control)
    request = model_request(system="x" * 15_000, messages=[HumanMessage(content="y" * 2_000)])
    with pytest.raises(ContextLimitExceeded):
        await middleware.awrap_model_call(request, provider)
    provider.assert_not_awaited()
    assert control.view().usage.model_calls == 0


async def test_reservation_persisted_and_budget_emitted_before_provider():
    control = RunControl(snapshot(), clock=FakeClock())
    events: list[dict] = []
    order: list[str] = []

    async def persist(view):
        order.append(f"persist:{view.usage.model_calls}:{view.control_revision}")

    middleware = make_middleware(control, events=events, persist=persist)

    async def tracking_provider(request):
        order.append("provider")
        return AIMessage(content="ok", usage_metadata={
            "input_tokens": 10, "output_tokens": 5, "total_tokens": 15})

    request = model_request(system="SYS", messages=[HumanMessage(content="问")])
    await middleware.awrap_model_call(request, tracking_provider)

    assert order[0].startswith("persist:1:")  # 持久化先于 Provider
    assert order[1] == "provider"
    assert control.view().usage.model_calls == 1
    assert control.view().usage.token_status == "available"
    assert control.view().usage.total_tokens == 15
    assert len(events) == 2  # reservation + usage
    first = events[0]
    assert set(first) == {"threadId", "runId", "controlRevision", "budgetSnapshot",
                          "usage", "activeElapsedMs", "contextTruncation"}
    assert first["threadId"] == "thread-1" and first["runId"] == "run-1"
    assert first["budgetSnapshot"]["max_model_calls"] == 8
    assert first["usage"]["model_calls"] == 1


async def test_trim_telemetry_persisted_before_provider():
    control = RunControl(snapshot(), clock=FakeClock())  # 默认 120_000 上限
    views: list = []

    async def persist(view):
        views.append(view)

    middleware = make_middleware(control, persist=persist)
    order: list[str] = []

    async def provider(request):
        order.append("provider")
        return AIMessage(content="ok")

    history = [*plain_turn("old", filler=70_000), *plain_turn("new", filler=10)]
    request = model_request(system="SYS", messages=history)
    await middleware.awrap_model_call(request, provider)
    assert order == ["provider"]
    telemetry = [v for v in views if v.context_truncation.occurred]
    assert telemetry and telemetry[0].context_truncation.removed_turns >= 1
    assert control.view().context_truncation.occurred is True


async def test_provider_error_records_missing_usage_and_keeps_exception():
    control = RunControl(snapshot(), clock=FakeClock())
    views: list = []

    async def persist(view):
        views.append(view)

    middleware = make_middleware(control, persist=persist)

    async def provider(request):
        raise RuntimeError("provider boom")

    request = model_request(system="SYS", messages=[HumanMessage(content="问")])
    with pytest.raises(RuntimeError, match="provider boom"):
        await middleware.awrap_model_call(request, provider)
    assert control.view().usage.model_calls == 1  # 预留已持久化
    assert control.view().usage.token_status == "unavailable"


async def test_active_deadline_blocks_provider_and_reservation():
    clock = FakeClock()
    control = RunControl(snapshot(max_active_seconds=300), clock=clock)
    control.begin_active_segment()
    clock.advance(301)
    provider = AsyncMock()
    middleware = make_middleware(control)
    request = model_request(system="SYS", messages=[HumanMessage(content="问")])
    with pytest.raises(RunActiveTimeout):
        await middleware.awrap_model_call(request, provider)
    provider.assert_not_awaited()
    assert control.view().usage.model_calls == 0


async def test_provider_call_bounded_by_remaining_active_deadline():
    clock = FakeClock()
    control = RunControl(snapshot(max_active_seconds=300), clock=clock)
    control.begin_active_segment()
    clock.advance(299.5)

    started = asyncio.Event()

    async def slow_provider(request):
        started.set()
        await asyncio.sleep(5)
        return AIMessage(content="late")

    middleware = make_middleware(control)
    request = model_request(system="SYS", messages=[HumanMessage(content="问")])
    task = asyncio.create_task(middleware.awrap_model_call(request, slow_provider))
    await started.wait()
    with pytest.raises(RunActiveTimeout):
        await task
    assert control.view().usage.token_status == "unavailable"
