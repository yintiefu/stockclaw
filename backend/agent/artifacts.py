"""1D Artifact 存储：受控路径、staging/publish 原子落盘与不可变链检查。

不提供任何原地更新；版本链只允许经 parent_artifact_id 线性延伸（Task 11 的
提交计划负责 leaf/cycle/fork 校验，这里提供事实读取）。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from agent.models import ARTIFACT_MAX_BYTES, ArtifactDocument, ArtifactMetadata
from agent.stores import ID_RE, DocumentCorrupt

STAGING_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}\.[0-9a-f]{32}\.artifact\.tmp$")


class ArtifactError(RuntimeError):
    code = "ARTIFACT_ERROR"


class ArtifactInvalid(ArtifactError):
    code = "ARTIFACT_INVALID"


class ArtifactNotFound(ArtifactError):
    code = "ARTIFACT_NOT_FOUND"


def encode_artifact(artifact: ArtifactDocument) -> bytes:
    """canonical JSON（排序/紧凑/非 ASCII/无 NaN）+ 末尾换行；超 1MB 拒绝。"""
    payload = artifact.model_dump(mode="json")
    encoded = (
        json.dumps(payload, ensure_ascii=False, sort_keys=True,
                   separators=(",", ":"), allow_nan=False)
        + "\n"
    ).encode("utf-8")
    if len(encoded) > ARTIFACT_MAX_BYTES:
        raise ArtifactInvalid("ARTIFACT_INVALID", "Artifact 超过 1 MB")
    return encoded


@dataclass(frozen=True)
class StagedArtifact:
    artifact: ArtifactDocument
    path: Path


class ArtifactStore:
    """<root>/artifacts/<thread-id>/<artifact-id>.json；不跟随 symlink。"""

    def __init__(self, root: Path):
        self._root = Path(root) / "artifacts"

    # ---- 路径 ----

    def _thread_dir(self, thread_id: str) -> Path:
        if not ID_RE.fullmatch(thread_id or ""):
            raise ArtifactInvalid("ARTIFACT_INVALID", f"thread id 非法: {thread_id!r}")
        return self._root / thread_id

    def _path(self, thread_id: str, artifact_id: str) -> Path:
        if not ID_RE.fullmatch(artifact_id or ""):
            raise ArtifactInvalid("ARTIFACT_INVALID", f"artifact id 非法: {artifact_id!r}")
        return self._thread_dir(thread_id) / f"{artifact_id}.json"

    # ---- 写 ----

    def stage(self, artifact: ArtifactDocument) -> StagedArtifact:
        """只写 <artifact-id>.<nonce>.artifact.tmp（flush+fsync），不动权威状态。"""
        encoded = encode_artifact(artifact)
        directory = self._thread_dir(artifact.thread_id)
        directory.mkdir(parents=True, exist_ok=True)
        tmp = directory / f"{artifact.id}.{uuid4().hex}.artifact.tmp"
        with tmp.open("wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        return StagedArtifact(artifact=artifact, path=tmp)

    def publish(self, staged: StagedArtifact) -> Path:
        """os.replace 落为最终 JSON 并 fsync 目录；已存在 id 视为冲突。"""
        artifact = staged.artifact
        final = self._path(artifact.thread_id, artifact.id)
        if final.exists() or final.is_symlink():
            staged.path.unlink(missing_ok=True)
            raise ArtifactInvalid("ARTIFACT_INVALID", f"Artifact 已存在: {artifact.id}")
        os.replace(staged.path, final)
        self._fsync_dir(final.parent)
        return final

    def delete_file(self, thread_id: str, artifact_id: str) -> None:
        final = self._path(thread_id, artifact_id)
        if final.is_symlink() or final.exists():
            final.unlink()
            self._fsync_dir(final.parent)

    # ---- 读 ----

    def get(self, thread_id: str, artifact_id: str) -> ArtifactDocument:
        final = self._path(thread_id, artifact_id)
        if final.is_symlink() or not final.exists():
            raise ArtifactNotFound(f"Artifact 不存在: {artifact_id}")
        try:
            payload = json.loads(final.read_text(encoding="utf-8"))
            artifact = ArtifactDocument.model_validate(payload)
        except (json.JSONDecodeError, ValueError) as exc:
            raise DocumentCorrupt(
                f"Artifact 文件损坏: {final.name}: {exc}") from exc
        if artifact.thread_id != thread_id or artifact.id != artifact_id:
            raise DocumentCorrupt(
                f"Artifact 文件身份不匹配: {final.name}")
        return artifact

    def list_for_thread(self, thread_id: str) -> list[ArtifactDocument]:
        directory = self._thread_dir(thread_id)
        if not directory.exists():
            return []
        artifacts: list[ArtifactDocument] = []
        for path in sorted(directory.iterdir()):
            if path.suffix != ".json" or path.is_symlink():
                continue
            try:
                artifacts.append(self.get(thread_id, path.name[: -len(".json")]))
            except DocumentCorrupt:
                continue  # 损坏文件保留供诊断；Task 12 对账负责上报 warning
        artifacts.sort(key=lambda item: (item.created_at, item.id))
        return artifacts

    def metadata_for_thread(self, thread_id: str) -> list[ArtifactMetadata]:
        return [ArtifactMetadata.from_artifact(a) for a in self.list_for_thread(thread_id)]

    def children_map(self, thread_id: str) -> dict[str, list[str]]:
        """parent_artifact_id → 子 ID 列表（leaf/fork 检查的事实来源）。"""
        children: dict[str, list[str]] = {}
        for artifact in self.list_for_thread(thread_id):
            if artifact.parent_artifact_id:
                children.setdefault(artifact.parent_artifact_id, []).append(artifact.id)
        return children

    def detect_chain_issues(self, thread_id: str) -> dict[str, str]:
        """返回 artifact id → 问题（跨 thread/type parent、fork、cycle）。"""
        by_id = {a.id: a for a in self.list_for_thread(thread_id)}
        children = self.children_map(thread_id)
        issues: dict[str, str] = {}
        for artifact in by_id.values():
            if len(children.get(artifact.id, [])) > 1:
                issues[artifact.id] = "fork"
            parent_id = artifact.parent_artifact_id
            if parent_id is None:
                continue
            parent = by_id.get(parent_id)
            if parent is None:
                issues[artifact.id] = "missing_parent"
            elif parent.type != artifact.type:
                issues[artifact.id] = "parent_type_mismatch"
            else:
                # cycle 检测：沿 parent 链最多走 |by_id| 步
                seen = {artifact.id}
                cursor = parent_id
                steps = 0
                while cursor is not None and steps <= len(by_id):
                    if cursor in seen:
                        issues[artifact.id] = "cycle"
                        break
                    seen.add(cursor)
                    cursor = by_id[cursor].parent_artifact_id if cursor in by_id else None
                    steps += 1
        return issues

    # ---- 内部 ----

    @staticmethod
    def _fsync_dir(directory: Path) -> None:
        try:
            directory_fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass


class ArtifactPersistenceFailed(ArtifactError):
    """终局性持久化失败：绝不能作为可纠正工具结果返回。"""

    code = "ARTIFACT_PERSISTENCE_FAILED"


@dataclass(frozen=True)
class ArtifactCommitPlan:
    """coordinator 短锁内生成的不可变提交计划（无密钥、无外部引用）。"""

    artifact: ArtifactDocument
    new_sources: tuple
    thread_revision: int
    parent_children_snapshot: tuple
    run_sources_count: int


class ArtifactService:
    """create_artifact 的唯一事务入口：计划 → staging → 复验 → 短提交。"""

    def __init__(self, *, store: ArtifactStore, threads, runs, thread_lock):
        self.store = store
        self._threads = threads
        self._runs = runs
        self._thread_lock = thread_lock

    # ---- 可纠正错误 ----

    @staticmethod
    def _invalid(reason: str, **extra) -> dict:
        return {"ok": False, "code": "ARTIFACT_INVALID", "reason": reason, **extra}

    @staticmethod
    def _source_invalid(index: int, reason: str, remaining: int) -> dict:
        return {"ok": False, "code": "ARTIFACT_SOURCE_INVALID",
                "descriptor_index": index, "reason": reason,
                "remaining_capacity": remaining}

    # ---- 入口 ----

    async def create_artifact(self, *, thread_id: str, run_id: str, control,
                              executor=None, lease=None, deadline: float = 0.0,
                              args: dict) -> dict:
        import asyncio as _asyncio
        from agent.models import ModelUrlSource
        from agent.provenance import SOURCE_CAPACITY, normalize_source_url, SourceUrlInvalid

        tool_type = args.get("type")
        if tool_type not in ("markdown", "table", "json", "sources"):
            return self._invalid("type 必须是 markdown/table/json/sources")
        title = args.get("title")
        if not isinstance(title, str) or not (1 <= len(title.strip()) <= 200):
            return self._invalid("title 长度必须在 1-200（去除首尾空白后）")
        content_raw = args.get("content")
        parent_artifact_id = args.get("parent_artifact_id")

        async with control.artifact_mutation_lock:
            # ---- 计划（短暂持有 coordinator thread lock） ----
            async with self._thread_lock(thread_id):
                run = self._runs.get(run_id)
                thread = self._threads.get(thread_id)
                if parent_artifact_id is not None:
                    try:
                        parent = self.store.get(thread_id, parent_artifact_id)
                    except ArtifactError:
                        return self._invalid("parent_artifact_id 不存在")
                    if parent.type != tool_type:
                        return self._invalid("parent 类型必须与新产品相同")
                    children = self.store.children_map(thread_id).get(parent_artifact_id, [])
                    if children:
                        return self._invalid("parent 已有子版本（链必须线性）",
                                             parent_children=children)
                else:
                    parent = None

                # 来源描述符按输入顺序解析
                descriptors = args.get("sources") or []
                if len(descriptors) > SOURCE_CAPACITY:
                    return self._source_invalid(0, "too_many_descriptors", 0)
                existing_by_call = {
                    s.tool_call_id: s for s in run.sources
                    if s.kind == "tool_execution"}
                existing_url_keys = {}
                for record in run.sources:
                    if record.kind == "model_url":
                        normalized = normalize_source_url(record.url)
                        if normalized is not None:
                            existing_url_keys[normalized.key] = record
                resolved_ids: list[str] = []
                new_sources: list = []
                request_keys: set[str] = set()
                request_calls: set[str] = set()
                remaining = SOURCE_CAPACITY - len(run.sources)
                for index, descriptor in enumerate(descriptors):
                    kind = descriptor.get("kind")
                    if kind == "tool_call":
                        call_id = descriptor.get("tool_call_id")
                        if call_id in request_calls:
                            return self._source_invalid(index, "duplicate_descriptor", remaining)
                        record = existing_by_call.get(call_id)
                        if record is None:
                            return self._source_invalid(index, "tool_call_not_completed", remaining)
                        request_calls.add(call_id)
                        if record.id not in resolved_ids:
                            resolved_ids.append(record.id)
                    elif kind == "url":
                        try:
                            normalized = normalize_source_url(descriptor.get("url", ""), strict=True)
                        except SourceUrlInvalid as exc:
                            return self._source_invalid(index, str(exc), remaining)
                        if normalized.key in request_keys:
                            return self._source_invalid(index, "duplicate_descriptor", remaining)
                        request_keys.add(normalized.key)
                        existing = existing_url_keys.get(normalized.key)
                        if existing is not None:
                            if existing.id not in resolved_ids:
                                resolved_ids.append(existing.id)
                            continue
                        if remaining <= 0:
                            return self._source_invalid(index, "source_capacity_exceeded", remaining)
                        remaining -= 1
                        new_id = f"source-{len(run.sources) + len(new_sources)}"
                        new_sources.append(ModelUrlSource(
                            id=new_id, kind="model_url", url=normalized.url,
                            label=descriptor.get("label"), created_at=utc_now_stamp(),
                            verification="model_provided_unverified"))
                        resolved_ids.append(new_id)
                    else:
                        return self._source_invalid(index, "unknown_descriptor_kind", remaining)

                # sources 类型：source_index 重写为不可变 source_id
                if tool_type == "sources":
                    items = []
                    raw_items = (content_raw or {}).get("items", [])
                    if len(raw_items) > SOURCE_CAPACITY:
                        return self._invalid("sources items 最多 200 条")
                    for item in raw_items:
                        source_index = item.get("source_index")
                        if not isinstance(source_index, int) or not (0 <= source_index < len(resolved_ids)):
                            return self._invalid("source_index 越界")
                        items.append({"source_id": resolved_ids[source_index],
                                      "note": item.get("note")})
                    content_payload = {"items": items}
                else:
                    content_payload = content_raw

                try:
                    artifact = _build_artifact_document(
                        thread_id=thread_id, run_id=run_id, tool_type=tool_type,
                        title=title, content_payload=content_payload,
                        parent_artifact_id=parent_artifact_id,
                        source_ids=_dedupe_ordered(resolved_ids))
                except ValueError as exc:
                    return self._invalid(str(exc))
                plan = ArtifactCommitPlan(
                    artifact=artifact,
                    new_sources=tuple(new_sources),
                    thread_revision=thread.revision,
                    parent_children_snapshot=tuple(
                        self.store.children_map(thread_id).get(parent_artifact_id or "", ())),
                    run_sources_count=len(run.sources),
                )

            # ---- staging（锁外；优先复用治理层 capacity lease） ----
            def _stage():
                return self.store.stage(artifact)

            try:
                if lease is not None and executor is not None:
                    staged = await executor.run_with_lease(lease, _stage, deadline)
                else:
                    staged = await _asyncio.to_thread(_stage)
            except ArtifactInvalid as exc:
                return self._invalid(str(exc))

            # ---- 复验 + 短提交（重新取得 coordinator thread lock） ----
            async with self._thread_lock(thread_id):
                try:
                    if control.terminal_error is not None:
                        raise ArtifactPersistenceFailed("运行控制已终结")
                    thread_now = self._threads.get(thread_id)
                    if thread_now.revision != plan.thread_revision:
                        raise ArtifactPersistenceFailed("thread revision 在提交前发生变化")
                    run_now = self._runs.get(run_id)
                    if len(run_now.sources) != plan.run_sources_count:
                        raise ArtifactPersistenceFailed("run 来源在提交前发生变化")
                    if plan.artifact.parent_artifact_id:
                        children_now = self.store.children_map(thread_id).get(
                            plan.artifact.parent_artifact_id, ())
                        if tuple(children_now) != plan.parent_children_snapshot:
                            raise ArtifactPersistenceFailed("parent 链在提交前分叉")

                    # 1) 独立 SourceRecord 先行提交（即使后续失败也不回滚）
                    if plan.new_sources:
                        view = control.note_sources_added(len(plan.new_sources))
                        self._runs.replace(run_now.model_copy(update={
                            "sources": [*run_now.sources, *plan.new_sources],
                            "sources_truncated": run_now.sources_truncated,
                            "control_revision": view.control_revision,
                            "updated_at": utc_now_stamp(),
                        }))
                    # 2) 发布最终 Artifact 文件
                    try:
                        self.store.publish(staged)
                    except OSError as exc:
                        raise ArtifactPersistenceFailed(f"Artifact 发布失败: {exc}") from exc
                    # 3) thread artifact_ids + revision
                    try:
                        updated_thread = self._threads.update(
                            thread_id, thread_now.revision,
                            lambda doc: doc.model_copy(update={
                                "artifact_ids": [*doc.artifact_ids, plan.artifact.id]}))
                    except Exception as exc:
                        # 未被引用的最终文件删除；删除失败记录 orphan（交给对账）
                        try:
                            self.store.delete_file(thread_id, plan.artifact.id)
                        except OSError:
                            pass
                        raise ArtifactPersistenceFailed(
                            f"thread 引用提交失败: {exc}") from exc
                except ArtifactPersistenceFailed:
                    staged.path.unlink(missing_ok=True)
                    raise

                return {
                    "ok": True,
                    "artifact": {
                        "id": plan.artifact.id,
                        "title": plan.artifact.title,
                        "type": plan.artifact.type,
                        "run_id": plan.artifact.run_id,
                        "parent_artifact_id": plan.artifact.parent_artifact_id,
                    },
                    "thread_revision": updated_thread.revision,
                }


def utc_now_stamp() -> str:
    from agent.stores import utc_now
    return utc_now()


def _dedupe_ordered(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _build_artifact_document(*, thread_id: str, run_id: str, tool_type: str,
                             title: str, content_payload, parent_artifact_id,
                             source_ids: list[str]) -> ArtifactDocument:
    from uuid import uuid4 as _uuid4
    from agent.models import (
        JsonContent, MarkdownContent, SourcesContent, SourcesContentItem, TableContent,
    )
    content_builders = {
        "markdown": lambda: MarkdownContent(markdown=content_payload.get("markdown", "")),
        "table": lambda: TableContent.model_validate(content_payload),
        "json": lambda: JsonContent(value=content_payload.get("value")),
        "sources": lambda: SourcesContent(items=[
            SourcesContentItem(source_id=item["source_id"], note=item.get("note"))
            for item in content_payload.get("items", [])]),
    }
    try:
        content = content_builders[tool_type]()
    except Exception as exc:
        raise ValueError(f"content 形状不合法: {exc}") from exc
    return ArtifactDocument(
        id=f"artifact-{_uuid4().hex}",
        thread_id=thread_id,
        run_id=run_id,
        type=tool_type,
        title=title.strip(),
        created_at=utc_now_stamp(),
        parent_artifact_id=parent_artifact_id,
        content=content,
        source_ids=source_ids,
    )
