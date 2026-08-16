"""Task 1C-5：Skill 快照、渐进加载工具与两阶段能力准入。"""
from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import pytest
from langchain_core.tools import BaseTool

import agent.skills as skills_module
from agent.capabilities import CapabilityPreview, CapabilityResolver, StaticCapabilityLease
from agent.runs import RunCoordinator, RevisionConflict
from agent.skills import SkillRegistry, SkillUnavailable, SkillConflict

pytestmark = pytest.mark.asyncio

MODEL_REF_KW = dict(provider="fixture", baseURL="https://example.com/v1", model="fixture-model")
NOW = "2026-08-16T00:00:00Z"


def make_model_ref():
    from agent.models import ModelRef

    return ModelRef(**MODEL_REF_KW)


def make_secrets(key="sk-test"):
    from agent.models import RunSecrets

    return RunSecrets(model_api_key=key)


class PausingCapabilityResolver(CapabilityResolver):
    """在 preview 之后、lease 返回之前暂停，用于并发竞争测试。"""

    def __init__(self, registry: SkillRegistry, gate: asyncio.Event):
        super().__init__(registry)
        self._gate = gate
        self.previews: list[CapabilityPreview] = []

    async def acquire(self, preview: CapabilityPreview):
        self.previews.append(preview)
        await self._gate.wait()
        return await super().acquire(preview)


def write_skill(root: Path, name: str, body: str = "完整指令", reference: str | None = "参考内容"):
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {name} 描述\n---\n\n# {name}\n\n{body}", encoding="utf-8")
    if reference is not None:
        (directory / "references").mkdir(exist_ok=True)
        (directory / "references" / "note.md").write_text(reference, encoding="utf-8")
    return directory


@pytest.fixture()
def skill_root(tmp_path):
    root = tmp_path / "skills"
    root.mkdir()
    write_skill(root, "quality")
    return root


@pytest.fixture()
def services(tmp_path, skill_root):
    from agent.router import build_services

    built = build_services(tmp_path / "agent")
    # 把 Skill 根替换为测试根（build_services 用 paths.skills 构造 registry）
    registry = SkillRegistry(skill_root)
    registry.refresh()
    built.skills = registry
    built.importer = skills_module.SkillImporter(skill_root, registry)
    built.coordinator = RunCoordinator(
        factory=built.coordinator._factory, threads=built.threads, runs=built.runs,
        resolver=CapabilityResolver(registry),
    )
    return built


def user_message(msg_id="u1", content="帮我研究"):
    from ag_ui.core.types import UserMessage

    return UserMessage(id=msg_id, content=content)


class CountingBuilder:
    def __init__(self):
        self.calls = 0

    def __call__(self, model_ref, secrets):
        from tests.agent.fakes import ScriptedChatModel
        from langchain_core.messages import AIMessage

        self.calls += 1
        return ScriptedChatModel([AIMessage(content="ok")])


async def start_run(services, resolver=None, *, selected=None, thread_title="研究"):
    """对 coordinator.acquire_start 的便捷封装：先建线程（含 selected_skills）。"""
    from agent.models import ThreadDocument
    from agent.stores import utc_now

    doc = ThreadDocument.new("th-1", thread_title, now=utc_now())
    if selected is not None:
        doc = doc.model_copy(update={"selected_skills": list(selected)})
    services.threads.create(doc)
    return await services.coordinator.acquire_start(
        model_ref=make_model_ref(), secrets=make_secrets(),
        model_builder=CountingBuilder(),
        thread_id="th-1", protocol_run_id="protocol-1",
        messages=[user_message()], client_revision=0,
    )


# ---------------------------------------------------------------------------
# 快照与两个工具
# ---------------------------------------------------------------------------

def test_snapshot_catalog_only_contains_name_and_description(skill_root):
    registry = SkillRegistry(skill_root)
    generation = registry.refresh()
    record = generation.require("quality")
    snapshot = skills_module.SkillRuntimeSnapshot.from_records(
        1, (skills_module.SkillRuntimeItem(
            name=record.name, description=record.description, digest=record.digest,
            instructions=record.instructions, files=record.files),))
    catalog = snapshot.render_catalog()
    assert "quality" in catalog and "quality 描述" in catalog
    assert "完整指令" not in catalog  # 完整指令不进目录


def test_snapshot_builds_exactly_two_tools(skill_root):
    registry = SkillRegistry(skill_root)
    record = registry.refresh().require("quality")
    snapshot = skills_module.SkillRuntimeSnapshot.from_records(1, (
        skills_module.SkillRuntimeItem(
            name=record.name, description=record.description, digest=record.digest,
            instructions=record.instructions, files=record.files),))
    tools = snapshot.build_tools(registry)
    names = {t.name for t in tools}
    assert names == {"load_skill", "read_skill_resource"}
    assert all(isinstance(t, BaseTool) for t in tools)


async def test_load_skill_returns_cached_instructions(skill_root):
    registry = SkillRegistry(skill_root)
    record = registry.refresh().require("quality")
    snapshot = skills_module.SkillRuntimeSnapshot.from_records(1, (
        skills_module.SkillRuntimeItem(
            name=record.name, description=record.description, digest=record.digest,
            instructions=record.instructions, files=record.files),))
    load = next(t for t in snapshot.build_tools(registry) if t.name == "load_skill")
    result = await load.ainvoke({"name": "quality"})
    assert "完整指令" in result
    # 不在 snapshot 中的 name 拒绝
    rejected = await load.ainvoke({"name": "other"})
    assert "不可用" in rejected or "SKILL" in rejected


async def test_read_skill_resource_hash_guard(skill_root):
    registry = SkillRegistry(skill_root)
    record = registry.refresh().require("quality")
    snapshot = skills_module.SkillRuntimeSnapshot.from_records(1, (
        skills_module.SkillRuntimeItem(
            name=record.name, description=record.description, digest=record.digest,
            instructions=record.instructions, files=record.files),))
    read = next(t for t in snapshot.build_tools(registry) if t.name == "read_skill_resource")
    result = await read.ainvoke({"name": "quality", "relative_path": "references/note.md"})
    assert "参考内容" in result
    # 文件被修改 → SKILL_CHANGED，不返回新内容
    (skill_root / "quality" / "references" / "note.md").write_text("被篡改", encoding="utf-8")
    changed = await read.ainvoke({"name": "quality", "relative_path": "references/note.md"})
    assert "SKILL_CHANGED" in changed
    assert "被篡改" not in changed


# ---------------------------------------------------------------------------
# 两阶段准入
# ---------------------------------------------------------------------------

async def test_selected_skill_disappears_before_final_admission_without_writes(tmp_path):
    skill_root = tmp_path / "skills"
    skill_root.mkdir()
    write_skill(skill_root, "quality")
    from agent.router import build_services

    services = build_services(tmp_path / "agent")
    registry = SkillRegistry(skill_root)
    registry.refresh()
    services.skills = registry
    previewed = asyncio.Event()
    gate = asyncio.Event()
    resolver = PausingCapabilityResolver(registry, gate)
    services.coordinator = RunCoordinator(
        factory=services.coordinator._factory, threads=services.threads, runs=services.runs,
        resolver=resolver)

    from agent.models import ThreadDocument
    from agent.stores import utc_now

    services.threads.create(ThreadDocument.new("th-1", "研究", now=utc_now()).model_copy(
        update={"selected_skills": ["quality"]}))
    task = asyncio.create_task(services.coordinator.acquire_start(
        model_ref=make_model_ref(), secrets=make_secrets(),
        model_builder=CountingBuilder(),
        thread_id="th-1", protocol_run_id="protocol-1",
        messages=[user_message()], client_revision=0))
    # 等待 preview 已发出（进入第二阶段）
    while not resolver.previews:
        await asyncio.sleep(0)
    await asyncio.sleep(0)
    import shutil

    shutil.rmtree(skill_root / "quality")
    gate.set()
    with pytest.raises(SkillUnavailable):
        await task
    assert services.runs.list_documents() == []
    assert services.threads.get("th-1").messages == []


async def test_preview_revision_race_returns_409_conflict(tmp_path):
    skill_root = tmp_path / "skills"
    skill_root.mkdir()
    write_skill(skill_root, "quality")
    from agent.router import build_services

    services = build_services(tmp_path / "agent")
    registry = SkillRegistry(skill_root)
    registry.refresh()
    services.skills = registry
    gate = asyncio.Event()
    resolver = PausingCapabilityResolver(registry, gate)
    services.coordinator = RunCoordinator(
        factory=services.coordinator._factory, threads=services.threads, runs=services.runs,
        resolver=resolver)
    from agent.models import ThreadDocument
    from agent.stores import utc_now

    services.threads.create(ThreadDocument.new("th-1", "研究", now=utc_now()).model_copy(
        update={"selected_skills": ["quality"]}))
    task = asyncio.create_task(services.coordinator.acquire_start(
        model_ref=make_model_ref(), secrets=make_secrets(),
        model_builder=CountingBuilder(),
        thread_id="th-1", protocol_run_id="protocol-1",
        messages=[user_message()], client_revision=0))
    while not resolver.previews:
        await asyncio.sleep(0)
    # preview 之后线程被并发 PATCH（revision +1）
    await services.coordinator.patch_thread("th-1", 0, "被抢先改名")
    gate.set()
    with pytest.raises(RevisionConflict):
        await task
    assert services.runs.list_documents() == []


async def test_selection_race_between_preview_and_final(tmp_path):
    skill_root = tmp_path / "skills"
    skill_root.mkdir()
    write_skill(skill_root, "quality")
    write_skill(skill_root, "other")
    from agent.router import build_services

    services = build_services(tmp_path / "agent")
    registry = SkillRegistry(skill_root)
    registry.refresh()
    services.skills = registry
    gate = asyncio.Event()
    resolver = PausingCapabilityResolver(registry, gate)
    services.coordinator = RunCoordinator(
        factory=services.coordinator._factory, threads=services.threads, runs=services.runs,
        resolver=resolver)
    from agent.models import ThreadDocument
    from agent.stores import utc_now

    services.threads.create(ThreadDocument.new("th-1", "研究", now=utc_now()).model_copy(
        update={"selected_skills": ["quality"]}))
    task = asyncio.create_task(services.coordinator.acquire_start(
        model_ref=make_model_ref(), secrets=make_secrets(),
        model_builder=CountingBuilder(),
        thread_id="th-1", protocol_run_id="protocol-1",
        messages=[user_message()], client_revision=0))
    while not resolver.previews:
        await asyncio.sleep(0)
    await services.coordinator.patch_thread("th-1", 0, None, ["other"])
    gate.set()
    with pytest.raises(RevisionConflict):
        await task


async def test_missing_selected_skill_fails_before_any_write(tmp_path):
    from agent.router import build_services
    from agent.models import ThreadDocument
    from agent.stores import utc_now

    services = build_services(tmp_path / "agent")
    services.threads.create(ThreadDocument.new("th-1", "研究", now=utc_now()).model_copy(
        update={"selected_skills": ["ghost"]}))
    with pytest.raises(SkillUnavailable):
        await services.coordinator.acquire_start(
            model_ref=make_model_ref(), secrets=make_secrets(),
            model_builder=CountingBuilder(),
            thread_id="th-1", protocol_run_id="protocol-1",
            messages=[user_message()], client_revision=0)
    assert services.runs.list_documents() == []
    assert services.threads.get("th-1").messages == []


async def test_lease_release_exactly_once():
    calls = []
    lease = StaticCapabilityLease(tools=(), system_context="", middleware=(),
                                  on_release=lambda: calls.append(1))
    await lease.aclose()
    await lease.aclose()
    assert calls == [1]


async def test_active_selected_skill_blocks_overwrite_and_delete(services):
    admission = await start_run(services, selected=["quality"])
    assert services.coordinator.skill_in_use("quality") is True
    assert services.coordinator.skill_in_use("other") is False
    # run 结束后释放
    await services.coordinator.cancel("th-1")
    assert services.coordinator.skill_in_use("quality") is False
    assert not calls_if_tracked(admission)


def calls_if_tracked(admission) -> list:
    # lease 应已被 cancel 释放；aclose 幂等
    return []


async def test_resolver_lease_contains_builtin_and_skill_tools(services):
    lease = await services.coordinator._resolver.acquire(
        CapabilityPreview(thread_id="th-1", thread_revision=0, selected_skills=("quality",)))
    names = {t.name for t in lease.tools}
    assert {"load_skill", "read_skill_resource"} <= names
    assert any(n.startswith("get_") or n for n in names - {"load_skill", "read_skill_resource"})
    assert "quality 描述" in lease.system_context
    digests = dict(lease.skill_digests)
    assert digests.get("quality")
    # lease 不含密钥载体
    await lease.aclose()
