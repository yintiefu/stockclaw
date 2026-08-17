"""stores.py 持久化契约：原子写、损坏隔离、revision CAS、重复查询与启动对账。"""

import json
import re
from pathlib import Path

import pytest

from agent.artifacts import ArtifactStore
from agent.models import (
    AgentMessage,
    ArtifactDocument,
    MarkdownContent,
    ModelRef,
    RunDocument,
    RunSummary,
    ThreadDocument,
)
from agent.stores import (
    AgentPaths,
    DocumentCorrupt,
    InvalidDocumentId,
    OWNED_TMP_RE,
    RecoveryWarning,
    RevisionConflict,
    RunStore,
    ThreadStore,
    atomic_write_json,
    latest_runs_by_thread,
    reconcile_agent_data,
    reconcile_artifacts,
)

NOW = "2026-08-15T12:00:00Z"


def make_paths(tmp_path: Path) -> AgentPaths:
    return AgentPaths(tmp_path / "agent")


def make_run(run_id="run-1", thread_id="thread-1", status="completed", updated_at=NOW):
    run = RunDocument.start(
        run_id=run_id,
        thread_id=thread_id,
        protocol_run_id=f"protocol-{run_id}",
        model_ref=ModelRef(provider="fixture", baseURL="https://example.com/v1", model="fixture-model"),
        trigger_message_id="user-1",
        history_head_id="user-1",
        now=NOW,
    )
    return run.model_copy(update={"status": status, "updated_at": updated_at})


def make_artifact(artifact_id="artifact-1", thread_id="thread-1", parent_artifact_id=None):
    return ArtifactDocument(
        id=artifact_id,
        thread_id=thread_id,
        run_id="run-1",
        type="markdown",
        title="研究摘录",
        created_at=NOW,
        parent_artifact_id=parent_artifact_id,
        content=MarkdownContent(markdown="# 摘录"),
    )


def test_atomic_write_flushes_file_replaces_and_fsyncs_parent(tmp_path, monkeypatch):
    target = tmp_path / "threads" / "th-1.json"
    order: list[str] = []
    real_fsync, real_replace = __import__("os").fsync, __import__("os").replace

    def spy_fsync(fd):
        order.append("fsync")
        return real_fsync(fd)

    def spy_replace(src, dst):
        order.append("replace")
        return real_replace(src, dst)

    monkeypatch.setattr("agent.stores.os.fsync", spy_fsync)
    monkeypatch.setattr("agent.stores.os.replace", spy_replace)

    atomic_write_json(target, {"id": "th-1", "ok": True})

    assert order == ["fsync", "replace", "fsync"]
    assert json.loads(target.read_text(encoding="utf-8")) == {"id": "th-1", "ok": True}
    assert [p.name for p in target.parent.iterdir()] == ["th-1.json"]


def test_atomic_write_survives_directory_fsync_failure(tmp_path, monkeypatch):
    target = tmp_path / "threads" / "th-1.json"
    real_open = __import__("os").open

    def fail_dir_open(path, flags, *args, **kwargs):
        if Path(path).is_dir():
            raise OSError("directory fsync unsupported")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr("agent.stores.os.open", fail_dir_open)
    atomic_write_json(target, {"id": "th-1"})
    assert json.loads(target.read_text(encoding="utf-8"))["id"] == "th-1"
    assert not list(target.parent.glob("*.tmp"))


def test_thread_compare_and_swap_rejects_stale_revision_without_writing(tmp_path):
    paths = make_paths(tmp_path)
    threads = ThreadStore(paths)
    threads.create(ThreadDocument.new("th-1", "新会话", now=NOW))

    def rename(doc: ThreadDocument) -> ThreadDocument:
        return doc.model_copy(update={"title": "改名"})

    updated = threads.update("th-1", expected_revision=0, mutate=rename)
    assert updated.revision == 1 and updated.title == "改名"

    thread_file = paths.threads / "th-1.json"
    bytes_before = thread_file.read_bytes()
    with pytest.raises(RevisionConflict):
        threads.update("th-1", expected_revision=0, mutate=rename)
    assert thread_file.read_bytes() == bytes_before


def test_list_threads_sorts_updated_at_desc_without_index_file(tmp_path):
    paths = make_paths(tmp_path)
    threads = ThreadStore(paths)
    older = ThreadDocument.new("th-old", "旧", now="2026-08-14T00:00:00Z").model_copy(
        update={"updated_at": "2026-08-14T00:00:00Z"}
    )
    newer = ThreadDocument.new("th-new", "新", now=NOW).model_copy(
        update={"updated_at": "2026-08-15T12:00:00Z"}
    )
    threads.create(older)
    threads.create(newer)

    listed, warnings = threads.list_documents()

    assert [t.id for t in listed] == ["th-new", "th-old"]
    assert warnings == []
    assert list(paths.root.glob("*index*")) == []


def test_protocol_and_trigger_message_lookup(tmp_path):
    paths = make_paths(tmp_path)
    runs = RunStore(paths)
    runs.replace(make_run(run_id="run-1"))

    assert runs.find_by_protocol_run_id("protocol-run-1").id == "run-1"
    assert runs.find_by_trigger_message_id("user-1").id == "run-1"
    assert runs.find_by_protocol_run_id("protocol-unknown") is None
    assert runs.find_by_trigger_message_id("user-unknown") is None


def test_corrupt_json_is_quarantined_and_reported(tmp_path):
    paths = make_paths(tmp_path)
    threads = ThreadStore(paths)
    target = paths.threads / "th-broken.json"
    atomic_write_json(target, {"placeholder": True})
    target.write_text("{broken", encoding="utf-8")

    with pytest.raises(DocumentCorrupt) as excinfo:
        threads.get("th-broken")
    assert "th-broken.json.corrupt-" in str(excinfo.value)

    assert not target.exists()
    quarantined = [p.name for p in paths.threads.glob("*.corrupt-*")]
    assert len(quarantined) == 1


def test_rejects_invalid_document_ids_before_path_construction(tmp_path):
    paths = make_paths(tmp_path)
    threads = ThreadStore(paths)
    runs = RunStore(paths)

    for bad_id in ["", "../escape", "a/b", "x" * 129]:
        with pytest.raises(InvalidDocumentId):
            threads.get(bad_id)
        with pytest.raises(InvalidDocumentId):
            runs.get(bad_id)

    assert list(paths.threads.glob("**/*")) == []
    assert list(paths.runs.glob("**/*")) == []
    assert not (tmp_path / "escape").exists()


def test_thread_scan_keeps_healthy_documents_and_reports_quarantined_files(tmp_path):
    paths = make_paths(tmp_path)
    threads = ThreadStore(paths)
    threads.create(ThreadDocument.new("th-ok", "健康", now=NOW))

    broken = paths.threads / "th-bad.json"
    atomic_write_json(broken, {"placeholder": True})
    broken.write_text("{not json", encoding="utf-8")
    preexisting = paths.threads / "th-old.json.corrupt-20260815T000000000000"
    preexisting.write_text("{older", encoding="utf-8")

    listed, warnings = threads.list_documents()

    assert [t.id for t in listed] == ["th-ok"]
    names = {w.filename for w in warnings}
    assert len(warnings) == 2
    assert any(re.fullmatch(r"th-bad\.json\.corrupt-\d{8}T\d+", n.split("Z")[0].rstrip()) or n.startswith("th-bad.json.corrupt-") for n in names)
    assert "th-old.json.corrupt-20260815T000000000000" in names
    assert all(w.code == "DOCUMENT_CORRUPT" and w.document_type == "thread" for w in warnings)
    # 损坏文件绝不能被当成空线程返回
    assert all(t.id != "th-bad" for t in listed)


def test_reconcile_repairs_run_status_and_thread_summary_then_removes_only_owned_tmp_files(tmp_path):
    paths = make_paths(tmp_path)
    threads = ThreadStore(paths)
    runs = RunStore(paths)

    threads.create(ThreadDocument.new("th-active", "活跃", now=NOW))
    threads.create(ThreadDocument.new("th-done", "已完成", now=NOW))
    # 故意留下陈旧的线程摘要（run 文件已到终态，但线程 last_run 未提交）
    threads.update("th-done", expected_revision=0, mutate=lambda d: d)
    threads.update("th-active", expected_revision=0, mutate=lambda d: d)

    active = make_run(run_id="run-active", thread_id="th-active", status="running", updated_at="2026-08-15T11:00:00Z")
    terminal = make_run(run_id="run-done", thread_id="th-done", status="completed", updated_at="2026-08-15T10:00:00Z")
    runs.replace(active)
    runs.replace(terminal)

    owned_tmp = paths.root / (".vr-agent-" + "0" * 32 + ".tmp")
    owned_tmp.write_text("{}", encoding="utf-8")
    unowned_tmp = paths.root / "notes.tmp"
    unowned_tmp.write_text("keep me", encoding="utf-8")
    assert OWNED_TMP_RE.fullmatch(owned_tmp.name)

    reconcile_agent_data(paths, threads, runs)

    reconciled_active = runs.get("run-active")
    assert reconciled_active.status == "interrupted"
    assert reconciled_active.error_code == "BACKEND_RESTARTED"
    assert reconciled_active.ended_at is not None
    assert runs.get("run-done").status == "completed"

    # 线程摘要从 run 文件反查修复；消息保持原样
    active_thread = threads.get("th-active")
    assert active_thread.last_run is not None
    assert active_thread.last_run.id == "run-active"
    assert active_thread.last_run.status == "interrupted"
    assert threads.get("th-done").last_run.id == "run-done"
    assert threads.get("th-done").last_run.status == "completed"

    assert not owned_tmp.exists()
    assert unowned_tmp.exists()


def test_reconcile_restores_run_tombstone_before_thread_delete_commit(tmp_path):
    paths = make_paths(tmp_path)
    threads = ThreadStore(paths)
    runs = RunStore(paths)
    threads.create(ThreadDocument.new("thread-1", "待删", now=NOW))
    runs.replace(make_run())
    original = paths.runs / "run-1.json"
    tombstone = paths.runs / "run-1.json.deleting-20260817T120000000000"
    original.replace(tombstone)

    warnings = reconcile_artifacts(paths, threads, ArtifactStore(paths.root))

    assert warnings == []
    assert original.exists()
    assert not tombstone.exists()
    assert runs.get("run-1").thread_id == "thread-1"


def test_reconcile_does_not_restore_run_tombstone_with_mismatched_payload_id(tmp_path):
    paths = make_paths(tmp_path)
    threads = ThreadStore(paths)
    runs = RunStore(paths)
    threads.create(ThreadDocument.new("thread-1", "待删", now=NOW))
    runs.replace(make_run(run_id="run-payload"))
    original = paths.runs / "run-payload.json"
    tombstone = paths.runs / "run-file.json.deleting-20260817T120000000000"
    original.replace(tombstone)

    warnings = reconcile_artifacts(paths, threads, ArtifactStore(paths.root))

    assert not (paths.runs / "run-file.json").exists()
    assert tombstone.exists()
    assert any(w.code == "DELETE_TOMBSTONE_LEFTOVER" and w.filename == tombstone.name
               for w in warnings)


def test_reconcile_cleans_run_tombstone_after_thread_delete_commit_and_retries_failures(tmp_path, monkeypatch):
    paths = make_paths(tmp_path)
    threads = ThreadStore(paths)
    runs = RunStore(paths)
    runs.replace(make_run())
    original = paths.runs / "run-1.json"
    tombstone = paths.runs / "run-1.json.deleting-20260817T120000000000"
    original.replace(tombstone)

    real_unlink = Path.unlink

    def fail_tombstone_cleanup(path, *args, **kwargs):
        if path == tombstone:
            raise OSError("cleanup blocked")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_tombstone_cleanup)
    warnings = reconcile_artifacts(paths, threads, ArtifactStore(paths.root))

    assert tombstone.exists()
    assert any(w.code == "DELETE_TOMBSTONE_LEFTOVER" and w.document_type == "run"
               and w.filename == tombstone.name for w in warnings)

    monkeypatch.setattr(Path, "unlink", real_unlink)
    warnings = reconcile_artifacts(paths, threads, ArtifactStore(paths.root))
    assert warnings == []
    assert not original.exists()
    assert not tombstone.exists()


def test_reconcile_artifacts_only_cleans_strict_staging_without_following_symlinks(tmp_path):
    paths = make_paths(tmp_path)
    threads = ThreadStore(paths)
    thread = ThreadDocument.new("thread-1", "研究", now=NOW)
    threads.create(thread)
    store = ArtifactStore(paths.root)
    artifact_dir = paths.artifacts_dir / thread.id
    artifact_dir.mkdir(parents=True)
    strict = artifact_dir / "artifact-1.abcdef0123456789abcdef0123456789.artifact.tmp"
    malformed = artifact_dir / "artifact-2.ABCDEF0123456789ABCDEF0123456789.artifact.tmp"
    nested = artifact_dir / "nested"
    nested.mkdir()
    nested_strict = nested / "artifact-3.abcdef0123456789abcdef0123456789.artifact.tmp"
    strict.write_text("{}", encoding="utf-8")
    malformed.write_text("{}", encoding="utf-8")
    nested_strict.write_text("{}", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_staging = outside / "artifact-4.abcdef0123456789abcdef0123456789.artifact.tmp"
    outside_staging.write_text("{}", encoding="utf-8")
    (paths.artifacts_dir / "thread-link").symlink_to(outside, target_is_directory=True)
    symlink_final_staging = artifact_dir / "artifact-5.abcdef0123456789abcdef0123456789.artifact.tmp"
    symlink_final_staging.write_text("{}", encoding="utf-8")
    (artifact_dir / "artifact-5.json").symlink_to(outside / "artifact-5.json")
    (outside / "artifact-5.json").write_text("{}", encoding="utf-8")
    nested_json = nested / "artifact-nested.json"
    nested_json.write_text("{}", encoding="utf-8")

    reconcile_artifacts(paths, threads, store)

    assert not strict.exists()
    assert malformed.exists()
    assert not nested_strict.exists()
    assert outside_staging.exists()
    assert not symlink_final_staging.exists()
    assert not nested_json.exists()
    assert list(nested.glob("artifact-nested.json.corrupt-*"))


def test_reconcile_validates_artifacts_restored_from_precommit_tombstone(tmp_path):
    paths = make_paths(tmp_path)
    threads = ThreadStore(paths)
    threads.create(ThreadDocument.new("thread-1", "研究", now=NOW))
    store = ArtifactStore(paths.root)
    tombstone = paths.artifacts_dir / "thread-1.deleting-20260817T120000000000"
    tombstone.mkdir(parents=True)
    tombstone.joinpath("artifact-identity.json").write_text(
        json.dumps(make_artifact("artifact-payload").model_dump(mode="json")), encoding="utf-8")

    warnings = reconcile_artifacts(paths, threads, store)

    restored = paths.artifacts_dir / "thread-1"
    assert restored.exists()
    assert not (restored / "artifact-identity.json").exists()
    assert list(restored.glob("artifact-identity.json.corrupt-*"))
    assert any(w.code == "ARTIFACT_CHAIN_INVALID" and "artifact-identity" in w.filename
               for w in warnings)


def test_reconcile_artifacts_keeps_referenced_files_and_quarantines_identity_and_chain_failures(tmp_path):
    paths = make_paths(tmp_path)
    threads = ThreadStore(paths)
    threads.create(ThreadDocument.new("thread-1", "研究", now=NOW).model_copy(
        update={"artifact_ids": ["artifact-ok"]}))
    store = ArtifactStore(paths.root)
    store.publish(store.stage(make_artifact("artifact-ok")))
    store.publish(store.stage(make_artifact("artifact-orphan")))
    store.publish(store.stage(make_artifact("artifact-payload")))
    artifact_dir = paths.artifacts_dir / "thread-1"
    (artifact_dir / "artifact-payload.json").replace(artifact_dir / "artifact-identity.json")
    store.publish(store.stage(make_artifact("artifact-chain", parent_artifact_id="missing-parent")))

    warnings = reconcile_artifacts(paths, threads, store)

    assert not any(w.code == "ARTIFACT_ORPHAN" and "artifact-ok" in w.filename for w in warnings)
    assert any(w.code == "ARTIFACT_ORPHAN" and "artifact-orphan" in w.filename for w in warnings)
    assert any(w.code == "ARTIFACT_CHAIN_INVALID" and "artifact-identity" in w.filename for w in warnings)
    assert any(w.code == "ARTIFACT_CHAIN_INVALID" and "artifact-chain" in w.filename for w in warnings)
    assert store.get("thread-1", "artifact-ok").id == "artifact-ok"
    assert not (artifact_dir / "artifact-identity.json").exists()
    assert not (artifact_dir / "artifact-chain.json").exists()
    assert list(artifact_dir.glob("artifact-identity.json.corrupt-*"))
    assert list(artifact_dir.glob("artifact-chain.json.corrupt-*"))


def test_latest_runs_by_thread_prefers_max_updated_at_then_id():
    early = make_run(run_id="run-a", status="completed", updated_at="2026-08-15T10:00:00Z")
    late = make_run(run_id="run-b", status="completed", updated_at="2026-08-15T11:00:00Z")
    tie = make_run(run_id="run-c", thread_id="thread-2", status="completed", updated_at="2026-08-15T11:00:00Z")
    latest = latest_runs_by_thread([early, late, tie])
    assert latest["thread-1"].id == "run-b"
    assert latest["thread-2"].id == "run-c"

    tie_same_thread = make_run(run_id="run-d", status="completed", updated_at="2026-08-15T11:00:00Z")
    latest = latest_runs_by_thread([late, tie_same_thread])
    assert latest["thread-1"].id == "run-d"


def test_repair_last_run_only_bumps_revision_when_summary_differs(tmp_path):
    paths = make_paths(tmp_path)
    threads = ThreadStore(paths)
    threads.create(ThreadDocument.new("th-1", "新会话", now=NOW))
    summary = RunSummary(id="run-1", status="completed", updated_at=NOW)

    first = threads.repair_last_run("th-1", summary)
    assert first.revision == 1 and first.last_run == summary
    again = threads.repair_last_run("th-1", summary)
    assert again.revision == 1
    changed = threads.repair_last_run("th-1", RunSummary(id="run-1", status="failed", updated_at=NOW))
    assert changed.revision == 2


def test_message_history_excludes_partial_and_pending(tmp_path):
    paths = make_paths(tmp_path)
    threads = ThreadStore(paths)
    doc = ThreadDocument.new("th-1", "新会话", now=NOW).model_copy(update={
        "messages": [
            AgentMessage(id="u1", role="user", content="问题"),
            AgentMessage(id="a1", role="assistant", content="部分回答", partial=True),
            AgentMessage(id="a2", role="assistant", content="挂起", pending_interrupt=True),
            AgentMessage(id="t1", role="tool", content="结果", tool_call_id="call-1"),
        ],
    })
    threads.create(doc)
    assert [m.id for m in threads.get("th-1").model_history()] == ["u1", "t1"]


# ---- 1D：分页历史 run 读取 ----

def _seed_runs(paths, thread_id="thread-page", other="thread-other", count=5):
    runs = RunStore(paths)
    ref = ModelRef(provider="fixture", base_url="https://example.com/v1", model="m")
    made = []
    for i in range(count):
        run = RunDocument.start(
            run_id=f"run-p{i}", thread_id=thread_id, protocol_run_id=f"protocol-p{i}",
            model_ref=ref, trigger_message_id="u", history_head_id=None,
            now=f"2026-08-1{i}T12:00:0{i}Z")
        runs.replace(run)
        made.append(run)
    other_run = RunDocument.start(
        run_id="run-other", thread_id=other, protocol_run_id="protocol-other",
        model_ref=ref, trigger_message_id="u", history_head_id=None,
        now="2026-08-15T12:00:00Z")
    runs.replace(other_run)
    return runs, made


def test_page_for_thread_orders_desc_and_pages_with_cursor(tmp_path):
    paths = AgentPaths(tmp_path / "agent")
    runs, made = _seed_runs(paths)
    page = runs.page_for_thread("thread-page", limit=2)
    assert [r.id for r in page.runs] == ["run-p4", "run-p3"]
    assert page.next_before == "run-p3"
    second = runs.page_for_thread("thread-page", limit=2, before="run-p3")
    assert [r.id for r in second.runs] == ["run-p2", "run-p1"]
    assert second.next_before == "run-p1"  # 还有第三页
    third = runs.page_for_thread("thread-page", limit=2, before="run-p1")
    assert [r.id for r in third.runs] == ["run-p0"]
    assert third.next_before is None


def test_page_for_thread_rejects_foreign_cursor(tmp_path):
    paths = AgentPaths(tmp_path / "agent")
    runs, _ = _seed_runs(paths)
    with pytest.raises(ValueError):
        runs.page_for_thread("thread-page", limit=2, before="run-other")


def test_page_for_thread_reports_scan_warnings_once(tmp_path):
    paths = AgentPaths(tmp_path / "agent")
    runs, _ = _seed_runs(paths)
    corrupt = paths.runs / "run-corrupt.json"
    corrupt.parent.mkdir(parents=True, exist_ok=True)
    corrupt.write_text("{broken", encoding="utf-8")
    page = runs.page_for_thread("thread-page", limit=10)
    assert [r.id for r in page.runs][0] == "run-p4"
    assert any("run-corrupt" in w.filename for w in page.warnings)
    # 轻量字段：绝不包含消息/工具摘要/密钥
    assert set(page.runs[0].model_dump()) == {
        "id", "status", "started_at", "updated_at", "ended_at", "retry_of", "error_code"}
