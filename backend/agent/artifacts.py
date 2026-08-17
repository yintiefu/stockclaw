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
