"""Skill 注册表：确定性、不跟随 symlink 的扫描与安全资源解析。

1C 约束：
- Skill 身份来自 SKILL.md frontmatter 的 name，不是目录名；
- 无效 Skill 仍返回目录显示名与稳定错误码，错误说明不含绝对路径；
- digest 覆盖 frontmatter、指令与受控 manifest，不含真实路径。
"""
from __future__ import annotations

import hashlib
import os
import re
import stat
import threading
import unicodedata
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
