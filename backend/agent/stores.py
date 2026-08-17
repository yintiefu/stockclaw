"""Agent 数据目录的持久化层：原子 JSON 写、损坏隔离、revision CAS 与启动对账。

1B 约束：单个 FastAPI 进程独占一个 Agent 数据目录；不建全局索引文件。
"""

from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Generic, Literal, TypeVar
from uuid import uuid4

from pydantic import BaseModel, ValidationError

from agent.models import RunDocument, RunSummary, ThreadDocument

OWNED_TMP_RE = re.compile(r"^\.vr-agent-[0-9a-f]{32}\.tmp$")
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def utc_now() -> str:
    """带毫秒的 UTC ISO 时间戳（文件名安全部分另见 utc_stamp）。"""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")


def default_agent_root() -> Path:
    return Path(os.environ.get("VR_DATA_DIR") or Path.home() / ".vibe-research") / "agent"


@dataclass(frozen=True)
class AgentPaths:
    root: Path

    @property
    def threads(self) -> Path:
        return self.root / "threads"

    @property
    def runs(self) -> Path:
        return self.root / "runs"

    @property
    def skills(self) -> Path:
        return self.root / "skills"

    @property
    def mcp_config(self) -> Path:
        return self.root / "mcp.json"

    @property
    def mcp_work(self) -> Path:
        return self.root / "mcp-work"

    @property
    def policy(self) -> Path:
        return self.root / "policy.json"

    @property
    def artifacts_dir(self) -> Path:
        return self.root / "artifacts"


class StoreError(RuntimeError):
    code = "AGENT_STORE_ERROR"


class DocumentNotFound(StoreError):
    code = "DOCUMENT_NOT_FOUND"


class DocumentCorrupt(StoreError):
    code = "DOCUMENT_CORRUPT"


class RevisionConflict(StoreError):
    code = "THREAD_REVISION_CONFLICT"


class InvalidDocumentId(StoreError):
    code = "INVALID_DOCUMENT_ID"


def atomic_write_json(path: Path, payload: dict) -> None:
    """临时文件 + fsync + 原子替换；目录 fsync 尽力而为。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".vr-agent-{uuid4().hex}.tmp"
    try:
        with tmp.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass
    finally:
        if tmp.exists():
            tmp.unlink()


@dataclass(frozen=True)
class RecoveryWarning:
    code: Literal["DOCUMENT_CORRUPT", "ARTIFACT_ORPHAN", "ARTIFACT_MISSING_REF",
                  "ARTIFACT_CHAIN_INVALID", "ARTIFACT_STAGING_LEFTOVER"]
    document_type: Literal["thread", "run", "artifact"]
    filename: str


T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class ScanResult(Generic[T]):
    documents: list[T]
    warnings: list[RecoveryWarning]


class _JsonDocuments(Generic[T]):
    """路径校验、Pydantic 解码、损坏隔离与读写删的通用 JSON 文档仓库。"""

    def __init__(self, model: type[T], directory: Path, document_type: Literal["thread", "run"]):
        self._model = model
        self._directory = directory
        self._document_type = document_type
        self._lock = threading.RLock()

    # ---- 路径 ----

    def _path(self, doc_id: str) -> Path:
        if not ID_RE.fullmatch(doc_id or ""):
            # 在拼接任何路径之前拒绝，防止目录穿越
            raise InvalidDocumentId(f"{self._document_type} id 非法: {doc_id!r}")
        return self._directory / f"{doc_id}.json"

    # ---- 隔离 ----

    def _quarantine(self, path: Path) -> str:
        quarantined = path.with_name(f"{path.name}.corrupt-{utc_stamp()}")
        os.replace(path, quarantined)
        return quarantined.name

    # ---- 读取 ----

    def get(self, doc_id: str) -> T:
        path = self._path(doc_id)
        if not path.exists():
            raise DocumentNotFound(f"{self._document_type} 不存在: {doc_id}")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return self._model.model_validate(payload)
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            filename = self._quarantine(path)
            raise DocumentCorrupt(
                f"{self._document_type} 文件损坏，已隔离为 {filename}: {exc}"
            ) from exc

    def scan(self, *, include_preexisting_corrupt: bool = True) -> ScanResult[T]:
        """列出健康文档；刚隔离的损坏文件与既有 .corrupt-* 一并报告。"""
        documents: list[T] = []
        warnings: list[RecoveryWarning] = []
        self._directory.mkdir(parents=True, exist_ok=True)
        for path in sorted(self._directory.iterdir()):
            if ".corrupt-" in path.name:
                if include_preexisting_corrupt:
                    warnings.append(RecoveryWarning("DOCUMENT_CORRUPT", self._document_type, path.name))
                continue
            if path.suffix != ".json":
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                documents.append(self._model.model_validate(payload))
            except (json.JSONDecodeError, ValidationError, ValueError):
                filename = self._quarantine(path)
                warnings.append(RecoveryWarning("DOCUMENT_CORRUPT", self._document_type, filename))
        return ScanResult(documents, warnings)

    def list_documents(self, include_corrupt: bool = False) -> list[T]:
        return self.scan(include_preexisting_corrupt=include_corrupt).documents

    # ---- 写删 ----

    def write_document(self, doc: T) -> None:
        with self._lock:
            atomic_write_json(self._path(getattr(doc, "id")), doc.model_dump(mode="json"))

    def delete(self, doc_id: str) -> None:
        with self._lock:
            path = self._path(doc_id)
            if path.exists():
                path.unlink()


class ThreadStore:
    def __init__(self, paths: AgentPaths):
        self._docs: _JsonDocuments[ThreadDocument] = _JsonDocuments(ThreadDocument, paths.threads, "thread")

    def create(self, thread: ThreadDocument) -> None:
        self._docs.write_document(thread)

    def get(self, thread_id: str) -> ThreadDocument:
        return self._docs.get(thread_id)

    def delete(self, thread_id: str) -> None:
        self._docs.delete(thread_id)

    def list_documents(self) -> tuple[list[ThreadDocument], list[RecoveryWarning]]:
        result = self._docs.scan()
        threads = sorted(result.documents, key=lambda d: (d.updated_at, d.id), reverse=True)
        return threads, result.warnings

    def update(
        self,
        thread_id: str,
        expected_revision: int,
        mutate: Callable[[ThreadDocument], ThreadDocument],
    ) -> ThreadDocument:
        """同一把锁内完成 读 → 比较 → 递增 revision → 原子写。"""
        with self._docs._lock:
            current = self.get(thread_id)
            if current.revision != expected_revision:
                raise RevisionConflict(
                    f"线程 {thread_id} 期望 revision {expected_revision}，实际 {current.revision}"
                )
            updated = mutate(current).model_copy(update={
                "revision": current.revision + 1,
                "updated_at": utc_now(),
            })
            self._docs.write_document(updated)
            return updated

    def repair_last_run(self, thread_id: str, latest: RunSummary) -> ThreadDocument:
        """启动对账反向修复：仅当 RunSummary 实际变化时才递增 revision，消息保持原样。"""
        with self._docs._lock:
            current = self.get(thread_id)
            if current.last_run == latest:
                return current
            updated = current.model_copy(update={"last_run": latest, "updated_at": utc_now()})
            updated = updated.model_copy(update={"revision": current.revision + 1})
            self._docs.write_document(updated)
            return updated


@dataclass(frozen=True)
class RunPage:
    """一次文件系统观察产出的分页结果（文档与告警同源）。"""

    runs: list["RunListItem"]
    next_before: str | None
    warnings: list[RecoveryWarning]


class RunListItem(BaseModel):
    """历史 run 轻量摘要：不含消息、工具摘要、Source 或任何密钥。"""

    id: str
    status: str
    started_at: str
    updated_at: str
    ended_at: str | None = None
    retry_of: str | None = None
    error_code: str | None = None

    @classmethod
    def from_run(cls, run: RunDocument) -> "RunListItem":
        return cls(
            id=run.id,
            status=run.status,
            started_at=run.started_at,
            updated_at=run.updated_at,
            ended_at=run.ended_at,
            retry_of=run.retry_of,
            error_code=run.error_code,
        )


class RunStore:
    def __init__(self, paths: AgentPaths):
        self._docs: _JsonDocuments[RunDocument] = _JsonDocuments(RunDocument, paths.runs, "run")

    def replace(self, run: RunDocument) -> None:
        self._docs.write_document(run)

    def get(self, run_id: str) -> RunDocument:
        return self._docs.get(run_id)

    def delete(self, run_id: str) -> None:
        self._docs.delete(run_id)

    def list_documents(self, include_corrupt: bool = False) -> list[RunDocument]:
        return self._docs.list_documents(include_corrupt=include_corrupt)

    def scan(self) -> ScanResult[RunDocument]:
        return self._docs.scan()

    def page_for_thread(self, thread_id: str, *, limit: int = 50,
                        before: str | None = None) -> RunPage:
        """按 (started_at, id) 倒序分页；before 必须是同 thread 的 run ID。

        单次 scan 同时产出文档与告警（同一文件系统观察）。
        """
        result = self.scan()
        runs = [r for r in result.documents if r.thread_id == thread_id]
        if before is not None:
            anchor = next((r for r in runs if r.id == before), None)
            if anchor is None:
                raise ValueError(f"before 游标 {before!r} 不属于线程 {thread_id}")
            runs = [r for r in runs if (r.started_at, r.id) < (anchor.started_at, anchor.id)]
        runs.sort(key=lambda r: (r.started_at, r.id), reverse=True)
        page = runs[:limit]
        next_before = page[-1].id if len(runs) > limit and page else None
        return RunPage(runs=[RunListItem.from_run(r) for r in page],
                       next_before=next_before, warnings=result.warnings)

    def find_by_protocol_run_id(self, protocol_run_id: str) -> RunDocument | None:
        return next(
            (r for r in self.list_documents() if protocol_run_id in r.protocol_run_ids),
            None,
        )

    def find_by_trigger_message_id(self, message_id: str) -> RunDocument | None:
        return next(
            (r for r in self.list_documents() if r.trigger_message_id == message_id),
            None,
        )

    def runs_for_thread(self, thread_id: str) -> list[RunDocument]:
        return [r for r in self.list_documents() if r.thread_id == thread_id]


def latest_runs_by_thread(runs: list[RunDocument]) -> dict[str, RunDocument]:
    """每个线程取 (updated_at, id) 最大的 run，顺序确定。"""
    latest: dict[str, RunDocument] = {}
    for run in runs:
        current = latest.get(run.thread_id)
        if current is None or (run.updated_at, run.id) > (current.updated_at, current.id):
            latest[run.thread_id] = run
    return latest


def reconcile_agent_data(paths: AgentPaths, threads: ThreadStore, runs: RunStore,
                          artifact_warnings: list[RecoveryWarning] | None = None) -> None:
    """启动对账：活跃 run → interrupted；线程摘要反查修复；只清理自有临时文件。

    1D：先完成 interrupted-run 恢复，再执行 Artifact/tombstone 对账（调用方
    传入 `reconcile_artifacts` 的产出告警一并向调用方返回的场景见 router）。
    """
    all_runs = runs.list_documents(include_corrupt=False)
    reconciled_runs = []
    for run in all_runs:
        if run.status in ("running", "awaiting_approval"):
            run = run.model_copy(update={
                "status": "interrupted",
                "updated_at": utc_now(),
                "ended_at": utc_now(),
                "error_code": "BACKEND_RESTARTED",
                "error_message": "后端重启，原运行无法原位恢复",
            })
            runs.replace(run)
        reconciled_runs.append(run)
    for thread_id, latest in latest_runs_by_thread(reconciled_runs).items():
        try:
            threads.repair_last_run(thread_id, RunSummary(
                id=latest.id,
                status=latest.status,
                updated_at=latest.updated_at,
                retry_of=latest.retry_of,
            ))
        except (DocumentNotFound, DocumentCorrupt):
            # 线程文件已被删除或损坏（读取时已隔离）——不重建空线程，也不阻塞启动
            continue
    for directory in (paths.root, paths.threads, paths.runs, paths.root / "artifacts"):
        if not directory.exists():
            continue
        for child in directory.glob("*.tmp"):
            if OWNED_TMP_RE.fullmatch(child.name):
                child.unlink()


ARTIFACT_TOMBSTONE_SUFFIX = ".deleting-"


def reconcile_artifacts(paths: AgentPaths, threads: ThreadStore,
                        artifact_store) -> list[RecoveryWarning]:
    """Artifact/删除 tombstone 对账（在 interrupted-run 恢复之后执行）。

    - 只删除能证明是本写入管线未完成提交的严格 `<id>.<nonce>.artifact.tmp`。
    - delete tombstone：按 thread 提交点（thread 文件是否存在）回滚或清理。
    - orphan JSON / missing ref / 链问题只告警，不静默修改。
    """
    warnings: list[RecoveryWarning] = []
    root = paths.artifacts_dir
    if root.exists():
        for entry in sorted(root.iterdir()):
            if entry.is_symlink():
                continue
            if entry.is_dir() and ARTIFACT_TOMBSTONE_SUFFIX in entry.name:
                # 删除 tombstone：thread 文件存在 → 回滚 rename；否则清理
                original_id = entry.name.split(ARTIFACT_TOMBSTONE_SUFFIX)[0]
                if (paths.threads / f"{original_id}.json").exists():
                    target = root / original_id
                    if not target.exists():
                        try:
                            os.replace(entry, target)
                        except OSError:
                            pass
                else:
                    _remove_tree(entry)
                continue
            if not entry.is_dir():
                continue
            thread_id = entry.name
            for child in sorted(entry.iterdir()):
                if child.is_symlink():
                    continue
                name = child.name
                if name.endswith(".artifact.tmp"):
                    artifact_id = name.split(".")[0]
                    if not (entry / f"{artifact_id}.json").exists():
                        # 可证明未完成提交：staging 无对应最终文件
                        try:
                            child.unlink()
                        except OSError:
                            pass
                    else:
                        warnings.append(RecoveryWarning(
                            "ARTIFACT_STAGING_LEFTOVER", "artifact", name))
                elif name.endswith(".json"):
                    warnings.append(RecoveryWarning(
                        "ARTIFACT_ORPHAN", "artifact", f"{thread_id}/{name}"))
        # 引用与链完整性（thread artifact_ids ↔ 文件）
        from agent.artifacts import ArtifactStore  # 延迟导入避免环
        for thread in threads.list_documents()[0]:
            for artifact_id in thread.artifact_ids:
                if not (root / thread.id / f"{artifact_id}.json").exists():
                    warnings.append(RecoveryWarning(
                        "ARTIFACT_MISSING_REF", "artifact", f"{thread.id}/{artifact_id}.json"))
            issues = artifact_store.detect_chain_issues(thread.id) if hasattr(
                artifact_store, "detect_chain_issues") else {}
            for artifact_id, reason in issues.items():
                warnings.append(RecoveryWarning(
                    "ARTIFACT_CHAIN_INVALID", "artifact", f"{thread.id}/{artifact_id}.json:{reason}"))
    return warnings


def _remove_tree(directory: Path) -> None:
    import shutil
    shutil.rmtree(directory, ignore_errors=True)
