"""1B run 准入持久化契约：准入先落盘、服务端权威历史、重复/冲突拒绝。"""

import asyncio
import json
from pathlib import Path

import pytest
from ag_ui.core.types import UserMessage

from agent.models import AgentMessage, ModelRef, RunSecrets, ThreadDocument
from agent.protocol import PendingInterrupt
from agent.runs import (
    DuplicateRunActive,
    DuplicateRunTerminal,
    MessageConflict,
    RunCoordinator,
)
from agent.stores import AgentPaths, RevisionConflict, RunStore, ThreadStore, utc_now
from tests.agent.fakes import ScriptedChatModel

NOW = "2026-08-15T12:00:00Z"
MODEL_REF = ModelRef(provider="fixture", baseURL="https://example.com/v1", model="fixture-model")
SECRETS = RunSecrets.model_validate({"model_api_key": "request-only-key"})


class CountingBuilder:
    def __init__(self):
        self.count = 0
        self.observed: list[dict] = []

    def __call__(self, model_ref, secrets):
        self.count += 1
        self.observed.append({})
        return ScriptedChatModel([])


def make_coordinator(tmp_path: Path):
    paths = AgentPaths(tmp_path / "agent")
    threads = ThreadStore(paths)
    runs = RunStore(paths)
    return RunCoordinator(threads=threads, runs=runs), threads, runs


def user_msg(msg_id: str, content: str) -> UserMessage:
    return UserMessage(id=msg_id, content=content)


async def start(coordinator, *, thread_id="thread-1", protocol_run_id="protocol-1",
                user_id="user-1", content="分析现金流", revision=0, builder=None):
    return await coordinator.acquire_start(
        model_ref=MODEL_REF,
        secrets=SECRETS,
        model_builder=builder or CountingBuilder(),
        tools=[],
        thread_id=thread_id,
        middleware=(),
        protocol_run_id=protocol_run_id,
        messages=[user_msg(user_id, content)],
        client_revision=revision,
    )


@pytest.mark.asyncio
async def test_start_persists_user_and_running_run_before_model_execution(tmp_path):
    coordinator, threads, runs = make_coordinator(tmp_path)
    observed: dict[str, object] = {}

    def observing_builder(model_ref, secrets):
        observed["thread_bytes"] = (threads._docs._path("thread-1")).read_bytes()
        observed["run"] = runs.get("run-observed") if False else None
        return ScriptedChatModel([])

    admission = await coordinator.acquire_start(
        model_ref=MODEL_REF,
        secrets=SECRETS,
        model_builder=observing_builder,
        tools=[],
        thread_id="thread-1",
        middleware=(),
        protocol_run_id="protocol-1",
        messages=[user_msg("user-1", "分析现金流")],
        client_revision=None,
    )
    # model_builder 在持久化之后运行：此处线程文档已经包含被接受的用户消息
    thread = threads.get("thread-1")
    assert [m.id for m in thread.messages] == ["user-1"]
    assert thread.revision == 1
    assert admission.run.status == "running"
    assert admission.run.protocol_run_ids == ["protocol-1"]
    assert admission.run.trigger_message_id == "user-1"
    assert admission.handle.product_run_id == admission.run.id
    assert admission.handle.thread_revision == 1
    persisted_runs = runs.list_documents()
    assert len(persisted_runs) == 1 and persisted_runs[0].status == "running"
    assert "request-only-key" not in json.dumps(persisted_runs[0].model_dump(mode="json"))


@pytest.mark.asyncio
async def test_start_rebuilds_graph_input_from_complete_server_history(tmp_path):
    coordinator, threads, runs = make_coordinator(tmp_path)
    seed = ThreadDocument.new("thread-1", "研究", now=NOW).model_copy(update={"messages": [
        AgentMessage(id="user-old", role="user", content="上一轮问题"),
        AgentMessage(id="assistant-partial", role="assistant", content="未完成回答", partial=True),
    ], "revision": 0})
    threads.create(seed)

    admission = await coordinator.acquire_start(
        model_ref=MODEL_REF,
        secrets=SECRETS,
        model_builder=CountingBuilder(),
        tools=[],
        thread_id="thread-1",
        middleware=(),
        protocol_run_id="protocol-1",
        messages=[user_msg("user-old", "上一轮问题"), user_msg("user-new", "新问题")],
        client_revision=0,
    )

    ids = [m.id for m in admission.input.messages]
    assert ids == ["user-old", "user-new"]  # partial 不进入模型输入
    assert admission.input.messages[-1].content == "新问题"


@pytest.mark.asyncio
async def test_start_rejects_stale_revision_and_forked_head_before_model_builder(tmp_path):
    coordinator, threads, runs = make_coordinator(tmp_path)
    builder = CountingBuilder()
    seed = ThreadDocument.new("thread-1", "研究", now=NOW).model_copy(update={"messages": [
        AgentMessage(id="user-old", role="user", content="上一轮"),
    ], "revision": 3})
    threads.create(seed)

    with pytest.raises(RevisionConflict):
        await start(coordinator, user_id="user-new", content="新问题", revision=0, builder=builder)

    # forked head：客户端前缀与服务端历史不一致
    forked_messages = [user_msg("user-old", "被篡改的内容"), user_msg("user-new", "新问题")]
    with pytest.raises(MessageConflict):
        await coordinator.acquire_start(
            model_ref=MODEL_REF, secrets=SECRETS, model_builder=builder, tools=[],
            thread_id="thread-1", middleware=(),
            protocol_run_id="protocol-x", messages=forked_messages, client_revision=3,
        )

    assert builder.count == 0
    assert threads.get("thread-1").revision == 3
    assert [m.id for m in threads.get("thread-1").messages] == ["user-old"]
    assert runs.list_documents() == []


@pytest.mark.asyncio
async def test_active_and_terminal_duplicates_do_not_write_or_build_a_graph(tmp_path):
    coordinator, threads, runs = make_coordinator(tmp_path)

    admission = await start(coordinator, protocol_run_id="protocol-1", revision=None)
    thread_bytes = (threads._docs._path("thread-1")).read_bytes()
    builder = CountingBuilder()

    with pytest.raises(DuplicateRunActive):
        await start(coordinator, protocol_run_id="protocol-1", revision=1, builder=builder)
    assert (threads._docs._path("thread-1")).read_bytes() == thread_bytes
    assert builder.count == 0

    # 结束旧 run 后，同一 protocol run id 重放 → 终态重复
    await coordinator.finish_if_terminal("thread-1", "completed")
    thread_bytes = (threads._docs._path("thread-1")).read_bytes()  # 终态落盘后的新基线
    with pytest.raises(DuplicateRunTerminal):
        await start(coordinator, protocol_run_id="protocol-1", revision=None, builder=builder)
    assert (threads._docs._path("thread-1")).read_bytes() == thread_bytes
    assert builder.count == 0


@pytest.mark.asyncio
async def test_existing_message_id_with_different_content_is_message_conflict(tmp_path):
    coordinator, threads, runs = make_coordinator(tmp_path)
    builder = CountingBuilder()
    seed = ThreadDocument.new("thread-1", "研究", now=NOW).model_copy(update={"messages": [
        AgentMessage(id="user-1", role="user", content="原始内容"),
    ], "revision": 0})
    threads.create(seed)

    with pytest.raises(MessageConflict):
        await start(coordinator, user_id="user-1", content="不同内容", revision=0, builder=builder)

    assert builder.count == 0
    assert threads.get("thread-1").messages[0].content == "原始内容"
    assert threads.get("thread-1").revision == 0
    assert runs.list_documents() == []


@pytest.mark.asyncio
async def test_resume_appends_protocol_id_to_same_product_run(tmp_path):
    coordinator, threads, runs = make_coordinator(tmp_path)
    admission = await start(coordinator, protocol_run_id="protocol-1", revision=None)

    handle = coordinator.active("thread-1")
    handle.phase = "awaiting_approval"
    handle.pending_interrupts = [PendingInterrupt(bridge_interrupt_id="int-1", order=0, tool_call_id="call-1", value={})]

    await coordinator.acquire_resume(
        "thread-1",
        model_ref=MODEL_REF,
        secrets=SECRETS,
        model_builder=CountingBuilder(),
        validate=lambda pending: {"int-1": {"accepted": True}},
        client_revision=handle.thread_revision,
        protocol_run_id="protocol-2",
    )

    run = runs.get(admission.run.id)
    assert run.id == admission.run.id  # 同一个产品 run
    assert run.protocol_run_ids == ["protocol-1", "protocol-2"]


@pytest.mark.asyncio
async def test_steer_away_cancels_old_run_before_persisting_new_user_and_run(tmp_path):
    coordinator, threads, runs = make_coordinator(tmp_path)
    admission = await start(coordinator, protocol_run_id="protocol-1", revision=None)

    handle = coordinator.active("thread-1")
    handle.phase = "awaiting_approval"
    handle.pending_interrupts = [PendingInterrupt(bridge_interrupt_id="int-1", order=0, tool_call_id="call-1", value={})]
    old_run_id = admission.run.id

    statuses_seen_in_builder: list[str] = []

    def checking_builder(model_ref, secrets):
        statuses_seen_in_builder.append(runs.get(old_run_id).status)
        return ScriptedChatModel([])

    new_admission = await coordinator.acquire_steer_away(
        "thread-1",
        entries=[{"interruptId": "int-1", "status": "cancelled"}],
        validate=lambda pending, entries: None,
        model_ref=MODEL_REF,
        secrets=SECRETS,
        model_builder=checking_builder,
        tools=[],
        middleware=(),
        messages=[user_msg("user-1", "分析现金流"), user_msg("user-2", "换个方向")],
        client_revision=handle.thread_revision,
        protocol_run_id="protocol-2",
    )

    assert statuses_seen_in_builder == ["cancelled"]  # 新 builder 运行前旧 run 已是 cancelled
    assert runs.get(old_run_id).status == "cancelled"
    thread = threads.get("thread-1")
    assert [m.id for m in thread.messages] == ["user-1", "user-2"]
    assert new_admission.handle.product_run_id != old_run_id
    assert runs.get(new_admission.handle.product_run_id).status == "running"
    assert new_admission.handle.runtime.graph is not None
    assert handle.runtime.graph is None  # 旧句柄已释放


@pytest.mark.asyncio
async def test_graph_failure_after_persistence_finalizes_run_failed(tmp_path):
    coordinator, threads, runs = make_coordinator(tmp_path)

    def failing_builder(model_ref, secrets):
        raise RuntimeError("provider unavailable")

    with pytest.raises(RuntimeError):
        await coordinator.acquire_start(
            model_ref=MODEL_REF, secrets=SECRETS, model_builder=failing_builder, tools=[],
            thread_id="thread-1", middleware=(),
            protocol_run_id="protocol-1",
            messages=[user_msg("user-1", "分析")], client_revision=None,
        )

    # 用户消息保留，run 终态 failed，线程 last_run 同步
    thread = threads.get("thread-1")
    assert [m.id for m in thread.messages] == ["user-1"]
    assert coordinator.active("thread-1") is None
    persisted = runs.list_documents()
    assert persisted[0].status == "failed"
    assert thread.last_run is not None and thread.last_run.status == "failed"
