"""Skill 注册表：确定性、不跟随 symlink 的扫描与安全资源解析。

1C 约束：
- Skill 身份来自 SKILL.md frontmatter 的 name，不是目录名；
- 无效 Skill 仍返回目录显示名与稳定错误码，错误说明不含绝对路径；
- digest 覆盖 frontmatter、指令与受控 manifest，不含真实路径。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import threading
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

import yaml

# ---------------------------------------------------------------------------
# 错误
# ---------------------------------------------------------------------------


class SkillError(RuntimeError):
    code = "SKILL_INVALID"


class SkillUnavailable(SkillError):
    code = "SKILL_UNAVAILABLE"


class SkillConflict(SkillError):
    code = "SKILL_CONFLICT"


class SkillResourceForbidden(SkillError):
    code = "SKILL_RESOURCE_FORBIDDEN"


class SkillInUse(SkillError):
    code = "SKILL_IN_USE"


class SkillArchiveRejected(SkillError):
    code = "SKILL_ARCHIVE_REJECTED"


class SkillChanged(SkillError):
    code = "SKILL_CHANGED"


# ---------------------------------------------------------------------------
# 常量与限制
# ---------------------------------------------------------------------------

NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
SKILL_MD_LIMIT = 256 * 1024
INSTRUCTION_CHAR_LIMIT = 60_000
REFERENCE_LIMIT = 1024 * 1024
ASSET_DOWNLOAD_LIMIT = 20 * 1024 * 1024
RESOURCE_CHAR_LIMIT = 60_000
OWNED_PREFIXES = (".skill-upload-", ".skill-import-", ".skill-backup-")

SAFE_ASSET_MIMES = {
    "image/png", "image/jpeg", "image/webp", "image/gif",
    "application/pdf", "text/plain", "text/markdown", "application/json",
}

_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"%PDF-", "application/pdf"),
)

# RIFF....WEBP
_WEBP_MAGIC = b"WEBP"


# 扩展名 → 内容检测必须匹配的 MIME 集合；不一致时拒绝预览/下载
_EXT_EXPECTED_MIMES: dict[str, frozenset[str]] = {
    ".png": frozenset({"image/png"}),
    ".jpg": frozenset({"image/jpeg"}),
    ".jpeg": frozenset({"image/jpeg"}),
    ".gif": frozenset({"image/gif"}),
    ".webp": frozenset({"image/webp"}),
    ".pdf": frozenset({"application/pdf"}),
    ".json": frozenset({"application/json"}),
    ".md": frozenset({"text/plain", "text/markdown"}),
    ".markdown": frozenset({"text/plain", "text/markdown"}),
    ".txt": frozenset({"text/plain", "text/markdown"}),
}


def _redact(text: str) -> str:
    """错误说明里不允许出现绝对路径——按段裁掉含 / 的可疑部分。"""
    cleaned = re.sub(r"(?<!\w)/[^\s，。;；]*", "<路径已省略>", text)
    return cleaned


# ---------------------------------------------------------------------------
# 不可变模型
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SkillFile:
    relative_path: str
    category: Literal["skill", "reference", "asset", "script", "other"]
    size: int
    mtime_ns: int
    sha256: str
    mime: str | None
    downloadable: bool


@dataclass(frozen=True)
class SkillRecord:
    directory: str
    name: str | None
    description: str | None
    digest: str | None
    valid: bool
    instructions: str | None
    files: tuple[SkillFile, ...]
    error_code: str | None = None
    error_detail: str | None = None

    def by_path(self, relative_path: str) -> SkillFile | None:
        return next((f for f in self.files if f.relative_path == relative_path), None)


@dataclass(frozen=True)
class SkillGeneration:
    number: int
    skills: tuple[SkillRecord, ...]

    def by_directory(self, directory: str) -> SkillRecord:
        item = next((s for s in self.skills if s.directory == directory), None)
        if item is None:
            raise SkillUnavailable(f"Skill 目录不存在: {directory}")
        return item

    def by_name(self, name: str) -> SkillRecord | None:
        return next((s for s in self.skills if s.valid and s.name == name), None)

    def require(self, name: str) -> SkillRecord:
        item = self.by_name(name)
        if item is None:
            raise SkillUnavailable(f"Skill 不存在或不可用: {name}")
        return item


# ---------------------------------------------------------------------------
# 内部解析工具
# ---------------------------------------------------------------------------


def _validate_relative_path(parts: tuple[str, ...]) -> str | None:
    """返回规范化 POSIX 相对路径；非法返回 None。"""
    if not parts:
        return None
    for part in parts:
        if not part or part in (".", "..") or "\\" in part or "\x00" in part:
            return None
    joined = "/".join(parts)
    if unicodedata.normalize("NFC", joined) != joined:
        return None  # 强制 NFC；碰撞检查另行处理
    return joined


def _detect_mime(head: bytes, full_path: Path, size: int) -> str | None:
    """按内容检测 MIME；不信任扩展名，并与扩展名做一致性交叉验证。"""
    detected: str | None = None
    for signature, mime in _SIGNATURES:
        if head.startswith(signature):
            detected = mime
            break
    if detected is None and head[:4] == b"RIFF" and head[8:12] == _WEBP_MAGIC:
        detected = "image/webp"
    if detected is None:
        # 文本类：必须能完整 UTF-8 解码
        try:
            text = full_path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError, ValueError):
            return None
        stripped = text.lstrip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                import json

                json.loads(text)
                detected = "application/json"
            except ValueError:
                return None
        else:
            detected = "text/plain"
    # 扩展名与内容签名不一致 → 拒绝
    expected = _EXT_EXPECTED_MIMES.get(full_path.suffix.lower())
    if expected is not None and detected not in expected:
        return None
    return detected


def _category_for(relative_path: str) -> str:
    if relative_path == "SKILL.md":
        return "skill"
    top = relative_path.split("/", 1)[0]
    if top == "references":
        return "reference"
    if top == "assets":
        return "asset"
    if top == "scripts":
        return "script"
    return "other"


def _hash_file(path: Path) -> tuple[str, bytes]:
    digest = hashlib.sha256()
    head = b""
    with path.open("rb") as handle:
        first = True
        while chunk := handle.read(65536):
            if first:
                head = chunk[:64]
                first = False
            digest.update(chunk)
    return digest.hexdigest(), head


def _parse_frontmatter(text: str) -> tuple[dict, str] | None:
    if not text.startswith("---"):
        return None
    lines = text.splitlines()
    if len(lines) < 2 or lines[0].strip() != "---":
        return None
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return None
    try:
        frontmatter = yaml.safe_load("\n".join(lines[1:end]))
    except yaml.YAMLError:
        return None
    if not isinstance(frontmatter, dict):
        return None
    instructions = "\n".join(lines[end + 1:])
    return frontmatter, instructions


# ---------------------------------------------------------------------------
# 扫描器
# ---------------------------------------------------------------------------


def _scan_skill(directory_name: str, directory: Path) -> SkillRecord:
    """扫描单个 Skill 目录；任何违规 → 整个 Skill 无效。"""
    name: str | None = None
    description: str | None = None
    instructions: str | None = None
    files: list[SkillFile] = []

    def invalid(detail: str) -> SkillRecord:
        return SkillRecord(
            directory=directory_name, name=name, description=description,
            digest=None, valid=False, instructions=None,
            files=tuple(files), error_code="SKILL_INVALID", error_detail=_redact(detail),
        )

    # ---- SKILL.md ----
    skill_md = directory / "SKILL.md"
    try:
        st = os.lstat(skill_md)
    except OSError:
        return invalid("缺少 SKILL.md")
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        return invalid("SKILL.md 必须是普通文件")
    if st.st_size > SKILL_MD_LIMIT:
        return invalid("SKILL.md 超过 256 KB")
    try:
        text = skill_md.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return invalid("SKILL.md 不是合法 UTF-8")
    parsed = _parse_frontmatter(text)
    if parsed is None:
        return invalid("SKILL.md frontmatter 无法解析")
    frontmatter, instructions = parsed
    raw_name = frontmatter.get("name")
    raw_description = frontmatter.get("description")
    # 先记录可解析的 name/description（无效 Skill 详情仍需展示），再做校验
    if isinstance(raw_name, str):
        name = raw_name
    if isinstance(raw_description, str):
        description = raw_description
    if not isinstance(raw_name, str) or not NAME_RE.fullmatch(raw_name):
        return invalid("name 不符合 [a-z0-9-] 规则（1-64 字符）")
    if not isinstance(raw_description, str) or not (1 <= len(raw_description) <= 1024):
        return invalid("description 必须是 1-1024 字符")
    name, description = raw_name, raw_description
    if len(instructions) > INSTRUCTION_CHAR_LIMIT:
        return invalid("指令超过 60,000 字符")

    # ---- 文件树（不跟随 symlink）----
    seen_folded: set[str] = set()

    def walk(current: Path) -> None | str:
        with os.scandir(current) as entries:
            for entry in sorted(entries, key=lambda e: e.name):
                if entry.is_symlink():
                    return f"目录树包含 symlink: {entry.name}"
                if entry.is_dir(follow_symlinks=False):
                    problem = walk(Path(entry.path))
                    if problem:
                        return problem
                    continue
                if not entry.is_file(follow_symlinks=False):
                    return f"特殊文件: {entry.name}"
                rel = os.path.relpath(entry.path, directory)
                parts = tuple(rel.split(os.sep))
                rel_norm = _validate_relative_path(parts)
                if rel_norm is None:
                    return f"路径非法: {entry.name}"
                folded = unicodedata.normalize("NFC", rel_norm).casefold()
                if folded in seen_folded:
                    return f"NFC/casefold 路径碰撞: {entry.name}"
                seen_folded.add(folded)
                stat_result = entry.stat(follow_symlinks=False)
                size = stat_result.st_size
                mtime_ns = stat_result.st_mtime_ns
                category = _category_for(rel_norm)
                if rel_norm != "SKILL.md":
                    if category == "reference" and size > REFERENCE_LIMIT:
                        return f"reference 超过 1 MB: {entry.name}"
                    sha, head = _hash_file(Path(entry.path))
                    mime = _detect_mime(head, Path(entry.path), size)
                    downloadable = (
                        category in ("reference", "asset")
                        and mime in SAFE_ASSET_MIMES
                        and size <= ASSET_DOWNLOAD_LIMIT
                    )
                    files.append(SkillFile(rel_norm, category, size, mtime_ns, sha, mime, downloadable))
                else:
                    files.append(SkillFile("SKILL.md", "skill", size, mtime_ns,
                                           hashlib.sha256(text.encode("utf-8")).hexdigest(),
                                           "text/markdown", False))
        return None

    problem = walk(directory)
    if problem is not None:
        return invalid(problem)
    files.sort(key=lambda f: f.relative_path)

    # ---- digest：frontmatter + 指令 + manifest，不含绝对路径 ----
    manifest_text = "\n".join(
        f"{f.relative_path}\x00{f.category}\x00{f.size}\x00{f.sha256}" for f in files
    )
    digest_input = (
        f"name={name}\x00description={description}\x00"
        f"instructions={hashlib.sha256(instructions.encode('utf-8')).hexdigest()}\x00"
        f"manifest={hashlib.sha256(manifest_text.encode('utf-8')).hexdigest()}"
    )
    digest = hashlib.sha256(digest_input.encode("utf-8")).hexdigest()
    return SkillRecord(
        directory=directory_name, name=name, description=description,
        digest=digest, valid=True, instructions=instructions, files=tuple(files),
    )


class SkillRegistry:
    """扫描 Skill 根目录的直接子目录，产出不可变 generation。"""

    def __init__(self, root: Path):
        self._root = Path(root)
        self._lock = threading.RLock()
        self._generation: SkillGeneration | None = None
        self._counter = 0

    @property
    def root(self) -> Path:
        return self._root

    # ---- 扫描 ----

    def refresh(self) -> SkillGeneration:
        with self._lock:
            records: list[SkillRecord] = []
            self._root.mkdir(parents=True, exist_ok=True)
            with os.scandir(self._root) as entries:
                children = sorted(
                    (e for e in entries if not e.name.startswith(".")),
                    key=lambda e: e.name,
                )
            scanned = []
            for entry in children:
                if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                    records.append(SkillRecord(
                        directory=entry.name, name=None, description=None, digest=None,
                        valid=False, instructions=None, files=(),
                        error_code="SKILL_INVALID",
                        error_detail=_redact(f"Skill 条目不是普通目录: {entry.name}"),
                    ))
                    continue
                scanned.append(_scan_skill(entry.name, Path(entry.path)))
            # 同代重复规范化 name：全部判无效
            seen: dict[str, list[int]] = {}
            for idx, record in enumerate(scanned):
                if record.name is not None:
                    folded = unicodedata.normalize("NFC", record.name).casefold()
                    seen.setdefault(folded, []).append(idx)
            dup_indexes: set[int] = set()
            for indexes in seen.values():
                if len(indexes) > 1:
                    dup_indexes.update(indexes)
            for idx, record in enumerate(scanned):
                if idx in dup_indexes:
                    scanned[idx] = SkillRecord(
                        directory=record.directory, name=record.name,
                        description=record.description, digest=None, valid=False,
                        instructions=None, files=(),
                        error_code="SKILL_INVALID", error_detail="与其他 Skill 的 name 规范化后冲突",
                    )
            records.extend(scanned)
            records.sort(key=lambda r: r.directory)
            self._counter += 1
            self._generation = SkillGeneration(number=self._counter, skills=tuple(records))
            return self._generation

    # ---- 读取 ----

    def current(self) -> SkillGeneration | None:
        with self._lock:
            return self._generation

    def list(self) -> list[SkillRecord]:
        current = self.current()
        return list(current.skills) if current else []

    def require(self, name: str) -> SkillRecord:
        current = self.current()
        if current is None:
            raise SkillUnavailable("Skill 注册表尚未扫描")
        return current.require(name)

    def resolve_file(self, name: str, relative_path: str) -> tuple[SkillRecord, SkillFile, Path]:
        """按当前 generation 的 manifest 精确解析；绝不拼接未校验文本。"""
        record = self.require(name)
        target = unicodedata.normalize("NFC", relative_path)
        entry = record.by_path(target)
        if entry is None or entry not in record.files:
            raise SkillResourceForbidden("路径不在受控 manifest 中")
        if entry.category in ("script", "other") or (
            entry.category == "skill" and target != "SKILL.md"
        ):
            raise SkillResourceForbidden("该类别不允许读取")
        resolved = self._root / record.directory
        for part in PurePosixPath(target).parts:
            resolved = resolved / part
        if not resolved.is_relative_to(self._root / record.directory) or resolved.is_symlink():
            raise SkillResourceForbidden("解析后的路径越界")
        return record, entry, resolved


# ---------------------------------------------------------------------------
# 导入 / 删除 / 恢复
# ---------------------------------------------------------------------------

UPLOAD_LIMIT = 20 * 1024 * 1024
EXTRACT_LIMIT = 50 * 1024 * 1024
ENTRY_LIMIT = 500
CHUNK_SIZE = 65536

_UPLOAD_RE = re.compile(r"^\.skill-upload-([^.]+)\.tmp$")
_IMPORT_RE = re.compile(r"^\.skill-import-([^.]+)\.tmp$")
_BACKUP_RE = re.compile(r"^\.skill-backup-([^.]+)\.tmp$")



def _remove_tree(path: Path) -> None:
    """删除目录树；对消失的路径静默。"""
    import shutil

    shutil.rmtree(path, ignore_errors=True)


def _fsync_children(directory: Path) -> None:
    """递归 fsync 普通文件与各级目录（落位前的持久化保证）。"""
    for current, dirnames, filenames in os.walk(directory, followlinks=False):
        for filename in filenames:
            try:
                fd = os.open(os.path.join(current, filename), os.O_RDONLY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
            except OSError:
                pass
        _fsync_dir(Path(current))

def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


@dataclass(frozen=True)
class SkillInstallResult:
    record: SkillRecord
    created: bool  # True=新导入 False=覆盖


class SkillImporter:
    """限量暂存 → zip 初检 → 逐 entry 受控解压 → 原子落位。"""

    def __init__(self, root: Path, registry: SkillRegistry):
        self._root = Path(root)
        self._registry = registry

    # ---- 暂存 ----

    def receive_chunks(self, chunks) -> Path:
        """固定 chunk 写入根目录内暂存文件；超 20MB 立即删除并拒绝。"""
        import uuid

        self._root.mkdir(parents=True, exist_ok=True)
        staged = self._root / f".skill-upload-{uuid.uuid4().hex}.tmp"
        written = 0
        try:
            with staged.open("wb") as handle:
                for chunk in chunks:
                    written += len(chunk)
                    if written > UPLOAD_LIMIT:
                        raise SkillArchiveRejected("上传超过 20 MB 限制")
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            return staged
        except BaseException:
            staged.unlink(missing_ok=True)
            raise

    async def receive(self, upload) -> Path:
        """async multipart 接收：逐块读 UploadFile，共用 20MB 上限与清理语义。"""
        import asyncio
        import uuid

        self._root.mkdir(parents=True, exist_ok=True)
        staged = self._root / f".skill-upload-{uuid.uuid4().hex}.tmp"
        written = 0
        try:
            def write_chunk(chunk: bytes) -> None:
                nonlocal written
                written += len(chunk)
                if written > UPLOAD_LIMIT:
                    raise SkillArchiveRejected("上传超过 20 MB 限制")

            with staged.open("wb") as handle:
                while True:
                    chunk = await upload.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    write_chunk(chunk)
                    await asyncio.to_thread(handle.write, chunk)
                await asyncio.to_thread(handle.flush)
                await asyncio.to_thread(os.fsync, handle.fileno())
            return staged
        except BaseException:
            await asyncio.to_thread(staged.unlink, True)
            raise

    # ---- 安装 ----

    def install(self, upload_path: Path, *, overwrite: bool, expected_digest: str | None,
                in_use_check=None) -> SkillInstallResult:
        import uuid

        self._root.mkdir(parents=True, exist_ok=True)
        if Path(upload_path).stat().st_size > UPLOAD_LIMIT:
            Path(upload_path).unlink(missing_ok=True)
            raise SkillArchiveRejected("上传超过 20 MB 限制")

        stage: Path | None = None
        try:
            # zip 中央目录初检
            try:
                archive = zipfile.ZipFile(upload_path)
            except (zipfile.BadZipFile, OSError) as exc:
                raise SkillArchiveRejected(f"不是合法 zip: {exc}") from exc
            with archive:
                infos = archive.infolist()
                if len(infos) > ENTRY_LIMIT:
                    raise SkillArchiveRejected(f"zip entry 超过 {ENTRY_LIMIT}")
                declared = sum(zi.file_size for zi in infos)
                if declared > EXTRACT_LIMIT:
                    raise SkillArchiveRejected(f"声明解压总量超过 {EXTRACT_LIMIT // (1024 * 1024)} MB")
                stage = self._root / f".skill-import-{uuid.uuid4().hex}.tmp"
                stage.mkdir()
                self._extract(archive, infos, stage)
            payload_root = self._payload_root(stage)
            staged_record = _scan_skill(payload_root.name, payload_root)
            if not staged_record.valid:
                raise SkillArchiveRejected(f"压缩包内 Skill 无效: {staged_record.error_detail}")
            assert staged_record.name is not None
            target = self._root / staged_record.name
            created = True
            with self._registry._lock:
                existing = target if target.exists() else None
                if existing is not None:
                    current = self._registry.refresh()
                    existing_record = next(
                        (r for r in current.skills if r.directory == staged_record.name and r.valid), None)
                    if overwrite and in_use_check is not None and in_use_check(staged_record.name):
                        raise SkillInUse(f"Skill {staged_record.name} 正在被活跃运行引用")
                    if not overwrite:
                        raise SkillConflict(f"Skill 已存在: {staged_record.name}")
                    if expected_digest is None or existing_record is None \
                            or existing_record.digest != expected_digest:
                        raise SkillConflict("覆盖需要匹配的 expected_digest")
                    # 旧目录改名 backup → 新目录落位 → fsync → 删 backup
                    backup = self._root / f".skill-backup-{uuid.uuid4().hex}.tmp"
                    os.replace(target, backup)
                    try:
                        _fsync_children(payload_root)
                        os.replace(payload_root, target)
                        _fsync_dir(self._root)
                    except BaseException:
                        # 落位失败：恢复 backup，保持可恢复状态
                        if not target.exists() and backup.exists():
                            os.replace(backup, target)
                        raise
                    _remove_tree(backup)
                    created = False
                else:
                    _fsync_children(payload_root)
                    os.replace(payload_root, target)
                    _fsync_dir(self._root)
            generation = self._registry.refresh()
            record = generation.require(staged_record.name)
            # 清理 stage 外壳与上传文件
            if stage.exists():
                _remove_tree(stage)
            Path(upload_path).unlink(missing_ok=True)
            return SkillInstallResult(record=record, created=created)
        except SkillArchiveRejected:
            # 无效压缩包：立即清理暂存与上传，不留 residue
            if stage is not None and stage.exists():
                _remove_tree(stage)
            Path(upload_path).unlink(missing_ok=True)
            raise
        except BaseException:
            if stage is not None and stage.exists():
                _remove_tree(stage)
            raise

    # ---- 删除 ----

    def delete(self, name: str, expected_digest: str | None) -> SkillRecord:
        with self._registry._lock:
            record = self._registry.require(name)
            if expected_digest is None or record.digest != expected_digest:
                raise SkillConflict("删除需要匹配的 expected_digest")
            target = self._root / record.directory
            if target.exists():
                _remove_tree(target)
                _fsync_dir(self._root)
            self._registry.refresh()
            return record

    # ---- 恢复 ----

    def recover(self) -> list[str]:
        """只处理自有固定名称；归属不明的保留并返回相对名警告。"""
        warnings: list[str] = []
        self._root.mkdir(parents=True, exist_ok=True)
        backups = {m.group(1): p for p in self._root.iterdir()
                   if p.name.startswith(".") and (m := _BACKUP_RE.fullmatch(p.name))}
        for suffix, backup in backups.items():
            if not backup.is_dir():
                warnings.append(backup.name)
                continue
            inner = [p for p in backup.iterdir() if p.name != "SKILL.md" or True]
            # 判断 backup 内是否是「唯一 Skill 目录」形状（一个子目录 + 其余为单 Skill 文件）
            subdirs = [p for p in backup.iterdir() if p.is_dir()]
            has_skill_md = (backup / "SKILL.md").exists()
            target_name: str | None = None
            if has_skill_md and subdirs:
                warnings.append(backup.name)  # SKILL.md 与子目录并存：归属不明确
                continue
            if has_skill_md and not subdirs:
                # 目录名被 rename 掩盖——从 SKILL.md frontmatter 解析目标 name
                try:
                    text = (backup / "SKILL.md").read_text(encoding="utf-8")
                    parsed = _parse_frontmatter(text)
                    if parsed and isinstance(parsed[0].get("name"), str) \
                            and NAME_RE.fullmatch(parsed[0]["name"]):
                        target_name = parsed[0]["name"]
                except (OSError, UnicodeDecodeError):
                    target_name = None
            if target_name is None and len(subdirs) == 1 and (subdirs[0] / "SKILL.md").exists():
                target_name = subdirs[0].name
            if target_name is None:
                warnings.append(backup.name)
                continue
            target = self._root / target_name
            if target.exists():
                # target 已提交：删除 backup
                _remove_tree(backup)
            else:
                # 只有合法 backup：恢复
                if (backup / "SKILL.md").exists() and not subdirs:
                    os.replace(backup, target)
                else:
                    inner = subdirs[0]
                    os.replace(inner, target)
                    _remove_tree(backup)
                _fsync_dir(self._root)
        for pattern, regex in ((".skill-upload-*", _UPLOAD_RE), (".skill-import-*", _IMPORT_RE)):
            for stray in self._root.glob(pattern):
                if regex.fullmatch(stray.name):
                    if stray.is_dir():
                        _remove_tree(stray)
                    else:
                        stray.unlink(missing_ok=True)
        return warnings

    # ---- 内部 ----

    def _extract(self, archive: zipfile.ZipFile, infos: list[zipfile.ZipInfo], stage: Path) -> None:
        seen_folded: set[str] = set()
        extracted = 0
        for info in infos:
            name = info.filename
            if info.flag_bits & 0x1:
                raise SkillArchiveRejected(f"加密 entry: {name}")
            mode = info.external_attr >> 16
            file_type = mode & 0o170000  # 类型位；writestr 产物类型位为 0（普通文件）
            if info.create_system == 3 and file_type and file_type != 0o100000:
                raise SkillArchiveRejected(f"特殊 Unix 文件 mode（symlink/设备/FIFO）: {name}")
            if name.endswith("/"):
                continue  # 目录 entry：由文件 entry 创建
            parts = tuple(name.split("/"))
            rel = _validate_relative_path(parts)
            if rel is None:
                raise SkillArchiveRejected(f"路径非法: {name}")
            folded = unicodedata.normalize("NFC", rel).casefold()
            if folded in seen_folded:
                raise SkillArchiveRejected(f"NFC/casefold 路径碰撞: {name}")
            seen_folded.add(folded)
            target = stage.joinpath(*parts)
            if not target.resolve().is_relative_to(stage.resolve()):
                raise SkillArchiveRejected(f"目录逃逸: {name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            remaining = EXTRACT_LIMIT - extracted
            if info.file_size > remaining:
                raise SkillArchiveRejected("实际解压总量超过 50 MB")
            with archive.open(info) as src, target.open("wb") as dst:
                copied = 0
                while chunk := src.read(CHUNK_SIZE):
                    copied += len(chunk)
                    if copied > info.file_size:
                        raise SkillArchiveRejected(f"实际大小超过声明: {name}")
                    dst.write(chunk)
                extracted += copied
                dst.flush()
                os.fsync(dst.fileno())
        _fsync_dir(stage)

    def _payload_root(self, stage: Path) -> Path:
        children = [p for p in stage.iterdir()]
        if (stage / "SKILL.md").is_file():
            return stage
        dirs = [p for p in children if p.is_dir()]
        files = [p for p in children if p.is_file()]
        if len(dirs) == 1 and not files and (dirs[0] / "SKILL.md").is_file():
            return dirs[0]
        raise SkillArchiveRejected("zip 必须根级或唯一顶层目录内包含 SKILL.md")


# ---------------------------------------------------------------------------
# 渐进加载：run 快照 + 两个只读工具
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SkillRuntimeItem:
    name: str
    description: str
    digest: str
    instructions: str
    files: tuple[SkillFile, ...]


@dataclass(frozen=True)
class SkillRuntimeSnapshot:
    """一次 run 持有的不可变 Skill 快照；闭包只捕获快照与注册表路径解析器。"""

    generation: int
    items: tuple[SkillRuntimeItem, ...]

    @classmethod
    def from_records(cls, generation: int, items: tuple[SkillRuntimeItem, ...]) -> "SkillRuntimeSnapshot":
        return cls(generation=generation, items=items)

    def by_name(self, name: str) -> SkillRuntimeItem | None:
        return next((i for i in self.items if i.name == name), None)

    def render_catalog(self) -> str:
        """系统上下文目录：只含 name/description，明确是用户选择的外部能力。"""
        if not self.items:
            return ""
        lines = ["\n\n## 用户已启用的 Skill（外部能力，仅提供客观资料与框架，不构成投资建议）"]
        for item in self.items:
            lines.append(f"- {item.name}: {item.description}")
        lines.append("调用 load_skill 工具按需获取。")
        return "\n".join(lines)

    def build_tools(self, registry: SkillRegistry) -> tuple:
        """恰好两个工具：load_skill / read_skill_resource。"""
        from langchain_core.tools import StructuredTool

        snapshot = self

        async def load_skill(name: str) -> str:
            item = snapshot.by_name(name)
            if item is None:
                return json.dumps({"error": "SKILL_UNAVAILABLE", "detail": f"Skill 不在本次运行中: {name}"},
                                  ensure_ascii=False)
            resource_index = [
                {"relative_path": f.relative_path, "category": f.category, "size": f.size}
                for f in item.files if f.category in ("reference", "asset")
            ]
            return json.dumps({
                "name": item.name, "digest": item.digest,
                "instructions": item.instructions, "resources": resource_index,
            }, ensure_ascii=False)

        async def read_skill_resource(name: str, relative_path: str) -> str:
            item = snapshot.by_name(name)
            if item is None:
                return json.dumps({"error": "SKILL_UNAVAILABLE", "detail": f"Skill 不在本次运行中: {name}"},
                                  ensure_ascii=False)
            entry = next((f for f in item.files
                          if f.relative_path == relative_path and f.category == "reference"), None)
            if entry is None:
                return json.dumps({"error": "SKILL_RESOURCE_FORBIDDEN",
                                   "detail": "路径不在快照 manifest 的 references/ 中"}, ensure_ascii=False)
            try:
                _, manifest_entry, path = registry.resolve_file(name, relative_path)
            except SkillError as exc:
                return json.dumps({"error": exc.code, "detail": str(exc)}, ensure_ascii=False)
            try:
                payload = path.read_bytes()
            except OSError as exc:
                return json.dumps({"error": "SKILL_UNAVAILABLE", "detail": f"读取失败: {exc}"},
                                  ensure_ascii=False)
            if hashlib.sha256(payload).hexdigest() != manifest_entry.sha256:
                return json.dumps({"error": "SKILL_CHANGED",
                                   "detail": "文件内容已变化，请刷新 Skill 后重试"}, ensure_ascii=False)
            try:
                text = payload.decode("utf-8")
            except UnicodeDecodeError:
                return json.dumps({"error": "SKILL_RESOURCE_FORBIDDEN", "detail": "非 UTF-8 文本"},
                                  ensure_ascii=False)
            if len(text) > RESOURCE_CHAR_LIMIT:
                return json.dumps({
                    "content": text[:RESOURCE_CHAR_LIMIT], "truncated": True,
                    "original_chars": len(text),
                }, ensure_ascii=False)
            return json.dumps({"content": text, "truncated": False}, ensure_ascii=False)

        return (
            StructuredTool.from_function(
                coroutine=load_skill, name="load_skill",
                description="加载当前会话已选择 Skill 的完整指令与资源索引（只接受本次运行中的 Skill 名）",
                args_schema={"type": "object", "properties": {
                    "name": {"type": "string", "description": "Skill 名称"}},
                    "required": ["name"]},
            ),
            StructuredTool.from_function(
                coroutine=read_skill_resource, name="read_skill_resource",
                description="读取当前快照 manifest 中 references/ 文本资源；文件变化时返回 SKILL_CHANGED",
                args_schema={"type": "object", "properties": {
                    "name": {"type": "string"}, "relative_path": {"type": "string"}},
                    "required": ["name", "relative_path"]},
            ),
        )
