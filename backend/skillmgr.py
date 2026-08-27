"""本地技能管理器：用户技能根的枚举、启停、删除与导入。

只做本地磁盘操作：FastAPI 路由直接调用，不触 LangGraph、不调模型。
启停由目录位置表达——活动根为启用，同级 `<name>.disabled` 根为停用；
进程启动时对 settings.json 的 `skills.path` 做快照，任何变更操作前在
锁内复核该路径未漂移，防止两个根被移动后写错位置。所有错误消息为
固定中文，不含物理路径与原始异常内容。
"""
from __future__ import annotations

import base64
import binascii
import contextlib
import errno
import json
import logging
import os
import re
import shutil
import stat
import tempfile
import threading
import zipfile
from dataclasses import dataclass
from functools import lru_cache
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable, Literal

from agent.settings import AgentSettingsError, agent_settings_path, load_agent_settings
from agent.skill_backends import BUILTIN_SKILLS_DIR
from agent.skill_catalog import (
    MAX_SKILL_FILE_SIZE,
    SkillValidationError,
    parse_skill_document,
    parse_skill_file,
    skill_name_grammar_valid,
    valid_skill_names,
)

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()

MAX_BODY_BYTES = 36 * 1024 * 1024
MAX_EXTRACTED_BYTES = 25 * 1024 * 1024
MAX_FILES = 256
_CHUNK = 64 * 1024

_MACOS_NOISE_DIRS = frozenset({"__MACOSX"})
_MACOS_NOISE_FILES = frozenset({".DS_Store"})
_SUPPORTED_ZIP_COMPRESSION = frozenset({zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED})

SourceKind = Literal["builtin", "user"]


class SkillManagerError(RuntimeError):
    def __init__(self, kind: Literal["bad_request", "not_found", "conflict", "too_large", "unavailable", "internal"], message: str):
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True, slots=True)
class SkillRoots:
    settings_path: Path
    active: Path
    disabled: Path


def _map_oserror(exc: OSError) -> SkillManagerError:
    if exc.errno in (errno.EEXIST, errno.ENOTEMPTY):
        return SkillManagerError("conflict", "目标目录已存在同名技能")
    if exc.errno in (errno.EACCES, errno.EPERM, errno.EROFS):
        return SkillManagerError("unavailable", "技能目录不可写")
    return SkillManagerError("internal", "本地文件操作失败")


def decode_base64(value: str) -> bytes:
    encoded = value
    if value.startswith("data:"):
        header, separator, encoded = value.partition(",")
        if not separator or not re.fullmatch(r"data:[^,;]+(?:;[^,;=]+=[^,;]*)*;base64", header):
            raise SkillManagerError("bad_request", "data URI 必须包含非空 MIME 与 base64 标记")
    try:
        return base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise SkillManagerError("bad_request", "base64 内容无效") from None


def normalize_member_path(value: str) -> PurePosixPath:
    normalized = value.replace("\\", "/")
    raw_parts = normalized.split("/")
    if (
        not normalized or "\x00" in normalized or normalized.startswith("/")
        or re.match(r"^[A-Za-z]:/", normalized)
        or any(part in {"", ".", ".."} for part in raw_parts)
    ):
        raise SkillManagerError("bad_request", "导入路径无效")
    return PurePosixPath(*raw_parts)


def _is_macos_noise(path: PurePosixPath) -> bool:
    return path.parts[0] in _MACOS_NOISE_DIRS or path.name in _MACOS_NOISE_FILES


def identify_skill_root(paths: set[PurePosixPath]) -> PurePosixPath:
    candidates = [path.parent for path in paths if path.name == "SKILL.md"]
    if len(candidates) != 1 or len(candidates[0].parts) > 1:
        raise SkillManagerError("bad_request", "导入包必须有且仅有一个技能根")
    return candidates[0]


def _settings_skills_path(settings_path: Path) -> Path | None:
    """只读解析 settings.json 的 skills.path；缺失/非法返回 None。"""
    try:
        payload = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    value = payload.get("skills")
    if not isinstance(value, dict):
        return None
    raw = value.get("path")
    if not isinstance(raw, str) or not raw.strip():
        return None
    return Path(raw).expanduser().resolve()


class SkillManager:
    """用户技能目录的管理面；`roots=None` 表示用户配置不可用（只枚举内置）。"""

    def __init__(
        self,
        builtin_root: Path,
        *,
        roots: SkillRoots | None,
        user_error: str | None = None,
    ):
        self._builtin_root = Path(builtin_root).resolve()
        self._roots = (
            SkillRoots(
                settings_path=roots.settings_path.resolve(),
                active=roots.active.resolve(),
                disabled=roots.disabled.resolve(),
            )
            if roots is not None
            else None
        )
        self._user_error = user_error

    @property
    def active_root(self) -> Path:
        self._require_roots()
        assert self._roots is not None
        return self._roots.active

    @property
    def disabled_root(self) -> Path:
        self._require_roots()
        assert self._roots is not None
        return self._roots.disabled

    def _require_roots(self) -> SkillRoots:
        if self._roots is None:
            raise SkillManagerError("unavailable", self._user_error or "Agent 设置缺失或无效")
        return self._roots

    # ------------------------------------------------------------------
    # 枚举
    # ------------------------------------------------------------------

    def _parse_user_skill(self, directory: Path, name: str) -> tuple[bool, str | None]:
        """返回 (valid, safe_error)；解析失败给安全中文原因。"""
        try:
            parse_skill_file(directory / "SKILL.md", f"/user/{name}/SKILL.md")
        except SkillValidationError as exc:
            return False, str(exc)
        except OSError:
            return False, "SKILL.md 缺失或不可读"
        return True, None

    def _scan_root(self, root: Path) -> dict[str, Path]:
        entries: dict[str, Path] = {}
        if not root.is_dir():
            return entries
        for child in root.iterdir():
            if not child.is_dir() or child.is_symlink():
                continue
            if not skill_name_grammar_valid(child.name):
                continue
            entries[child.name] = child
        return entries

    def _summary(
        self,
        *,
        name: str,
        description: str | None,
        source: SourceKind,
        enabled: bool,
        valid: bool,
        effective: bool,
        error: str | None,
    ) -> dict[str, object]:
        return {
            "name": name,
            "description": description,
            "source": source,
            "enabled": enabled,
            "valid": valid,
            "effective": effective,
            "error": error,
        }

    def list_skills(self) -> dict[str, object]:
        builtin_names = valid_skill_names(self._builtin_root)
        builtin: list[dict[str, object]] = []
        for name in sorted(builtin_names):
            description: str | None
            try:
                parsed = parse_skill_file(
                    self._builtin_root / name / "SKILL.md", f"/builtin/{name}/SKILL.md"
                )
                description = parsed.metadata["description"]
            except (OSError, SkillValidationError):
                continue
            builtin.append(self._summary(
                name=name, description=description, source="builtin",
                enabled=True, valid=True, effective=True, error=None,
            ))

        if self._roots is None:
            return {
                "builtin": builtin,
                "user": [],
                "user_available": False,
                "user_error": self._user_error or "Agent 设置缺失或无效",
            }

        active = self._scan_root(self._roots.active)
        disabled = self._scan_root(self._roots.disabled)
        user: list[dict[str, object]] = []
        for name in sorted(set(active) | set(disabled)):
            in_active = name in active
            in_disabled = name in disabled
            directory = active.get(name) or disabled.get(name)
            assert directory is not None
            valid, error = self._parse_user_skill(directory, name)
            description = None
            if valid:
                try:
                    parsed = parse_skill_file(directory / "SKILL.md", f"/user/{name}/SKILL.md")
                    description = parsed.metadata["description"]
                except (OSError, SkillValidationError):
                    pass
            if in_active and in_disabled:
                user.append(self._summary(
                    name=name, description=description, source="user",
                    enabled=True, valid=valid, effective=False,
                    error="活动与停用目录同时存在同名技能，请先手动清理",
                ))
                continue
            if in_active and name in builtin_names:
                user.append(self._summary(
                    name=name, description=description, source="user",
                    enabled=True, valid=valid, effective=False,
                    error="与内置技能同名，已阻止加载",
                ))
                continue
            user.append(self._summary(
                name=name, description=description, source="user",
                enabled=in_active, valid=valid,
                effective=in_active and valid and name not in builtin_names,
                error=error,
            ))
        return {"builtin": builtin, "user": user, "user_available": True}

    def get_skill(self, source: SourceKind, name: str) -> dict[str, object]:
        if source not in ("builtin", "user"):
            raise SkillManagerError("bad_request", "技能来源必须是 builtin 或 user")
        if not skill_name_grammar_valid(name):
            raise SkillManagerError("bad_request", "技能名格式无效")
        if source == "builtin":
            directory = self._builtin_root / name
            try:
                parsed = parse_skill_file(directory / "SKILL.md", f"/builtin/{name}/SKILL.md")
            except (OSError, SkillValidationError):
                raise SkillManagerError("not_found", "内置技能不存在") from None
            summary = self._summary(
                name=name, description=parsed.metadata["description"], source="builtin",
                enabled=True, valid=True, effective=True, error=None,
            )
            summary["path"] = f"/builtin/{name}/SKILL.md"
            summary["instructions"] = parsed.instructions
            return summary

        roots = self._require_roots()
        active = self._scan_root(roots.active)
        disabled = self._scan_root(roots.disabled)
        directory = active.get(name) or disabled.get(name)
        if directory is None:
            raise SkillManagerError("not_found", "用户技能不存在")
        valid, error = self._parse_user_skill(directory, name)
        description = None
        instructions = None
        if valid:
            try:
                parsed = parse_skill_file(directory / "SKILL.md", f"/user/{name}/SKILL.md")
                description = parsed.metadata["description"]
                instructions = parsed.instructions
            except (OSError, SkillValidationError):
                valid = False
                error = "SKILL.md 解析失败"
        builtin_names = valid_skill_names(self._builtin_root)
        in_active = name in active
        in_disabled = name in disabled
        if in_active and in_disabled:
            effective = False
            error = "活动与停用目录同时存在同名技能，请先手动清理"
        elif in_active and name in builtin_names:
            effective = False
            error = "与内置技能同名，已阻止加载"
        else:
            effective = in_active and valid and name not in builtin_names
        summary = self._summary(
            name=name, description=description, source="user",
            enabled=in_active, valid=valid, effective=effective, error=error,
        )
        summary["path"] = f"/user/{name}/SKILL.md"
        summary["instructions"] = instructions
        return summary

    # ------------------------------------------------------------------
    # 变更（启停 / 删除）
    # ------------------------------------------------------------------

    def _guard_mutation(self, name: str) -> tuple[SkillRoots, Path | None, Path | None]:
        """复核 settings 快照并返回 (roots, active_dir, disabled_dir)。

        调用方必须已持有 _LOCK（检查与移动必须对同一磁盘状态原子执行）。
        """
        if not skill_name_grammar_valid(name):
            raise SkillManagerError("bad_request", "技能名格式无效")
        roots = self._require_roots()
        if _settings_skills_path(roots.settings_path) != roots.active:
            raise SkillManagerError("conflict", "Agent 设置的技能路径已变更，请重启服务")
        active_dir = roots.active / name if (roots.active / name).is_dir() else None
        disabled_dir = roots.disabled / name if (roots.disabled / name).is_dir() else None
        if active_dir is not None and disabled_dir is not None:
            raise SkillManagerError("conflict", "活动与停用目录同时存在同名技能，请先手动清理")
        return roots, active_dir, disabled_dir

    def set_enabled(self, name: str, enabled: bool) -> dict[str, object]:
        with _LOCK:
            roots, active_dir, disabled_dir = self._guard_mutation(name)
            if name in valid_skill_names(self._builtin_root) and not (active_dir or disabled_dir):
                raise SkillManagerError("bad_request", "不能修改内置技能")
            if active_dir is None and disabled_dir is None:
                raise SkillManagerError("not_found", "用户技能不存在")
            source_dir = active_dir or disabled_dir
            assert source_dir is not None
            target_root = roots.active if enabled else roots.disabled
            if source_dir.parent == target_root:
                return self.get_skill("user", name)
            try:
                target_root.mkdir(parents=True, exist_ok=True)
                os.replace(source_dir, target_root / name)
            except OSError as exc:
                raise _map_oserror(exc) from None
        return self.get_skill("user", name)

    def delete(self, name: str) -> dict[str, bool]:
        with _LOCK:
            _, active_dir, disabled_dir = self._guard_mutation(name)
            if active_dir is None and disabled_dir is None:
                if name in valid_skill_names(self._builtin_root):
                    raise SkillManagerError("bad_request", "内置技能不可删除")
                raise SkillManagerError("not_found", "用户技能不存在")
            target = active_dir or disabled_dir
            assert target is not None
            try:
                _rmtree(target)
            except OSError as exc:
                raise _map_oserror(exc) from None
        return {"ok": True}

    # ------------------------------------------------------------------
    # 导入（文件夹 / ZIP 共享 staging 管道）
    # ------------------------------------------------------------------

    def import_folder(self, files: list[dict]) -> dict[str, object]:
        """从浏览器目录读取的文件清单导入；默认落在停用根。"""
        prepared: list[tuple[PurePosixPath, bytes]] = []
        seen_paths: set[PurePosixPath] = set()
        for item in files:
            path = normalize_member_path(item["path"])
            if path in seen_paths:
                raise SkillManagerError("bad_request", "导入路径重复")
            seen_paths.add(path)
            prepared.append((path, decode_base64(item["content_b64"])))
        return self._stage_and_commit(lambda temp_root: self._stage_prepared(temp_root, prepared))

    def import_zip(self, filename: str, content_b64: str) -> dict[str, object]:
        """流式解包 ZIP（不使用 extractall）；默认落在停用根。"""
        archive_bytes = decode_base64(content_b64)

        def stage(temp_root: Path) -> None:
            self._stage_zip(temp_root, archive_bytes)

        return self._stage_and_commit(stage)

    def _stage_prepared(self, temp_root: Path, prepared: list[tuple[PurePosixPath, bytes]]) -> None:
        if not 0 < len(prepared) <= MAX_FILES:
            raise SkillManagerError("too_large", f"导入文件数必须介于 1 与 {MAX_FILES} 之间")
        root = identify_skill_root({path for path, _ in prepared})
        total = [0]
        for path, data in prepared:
            self._stage_member(
                temp_root, root, path,
                (data[offset:offset + _CHUNK] for offset in range(0, len(data), _CHUNK)),
                total,
            )

    def _stage_zip(self, temp_root: Path, archive_bytes: bytes) -> None:
        try:
            archive = zipfile.ZipFile(BytesIO(archive_bytes))
        except (zipfile.BadZipFile, OSError, ValueError):
            raise SkillManagerError("bad_request", "ZIP 文件无效") from None
        with archive:
            infos = [info for info in archive.infolist() if not info.is_dir()]
            if not 0 < len(infos) <= MAX_FILES:
                raise SkillManagerError("too_large", f"ZIP 成员数必须介于 1 与 {MAX_FILES} 之间")
            for info in infos:
                if info.flag_bits & 0x1:
                    raise SkillManagerError("bad_request", "ZIP 包含加密成员")
                if info.compress_type not in _SUPPORTED_ZIP_COMPRESSION:
                    raise SkillManagerError("bad_request", "ZIP 使用不支持的压缩格式")
                if stat.S_ISLNK(info.external_attr >> 16):
                    raise SkillManagerError("bad_request", "ZIP 包含符号链接")
            normalized = [normalize_member_path(info.filename) for info in infos]
            root = identify_skill_root(set(normalized))
            total = [0]
            try:
                for info, path in zip(infos, normalized, strict=True):
                    with archive.open(info) as stream:
                        self._stage_member(
                            temp_root, root, path,
                            iter(lambda: stream.read(_CHUNK), b""),
                            total,
                        )
            except SkillManagerError:
                raise
            except (zipfile.BadZipFile, OSError, ValueError, RuntimeError):
                raise SkillManagerError("bad_request", "ZIP 成员解压失败") from None

    def _stage_member(
        self,
        temp_root: Path,
        root: PurePosixPath,
        path: PurePosixPath,
        chunks: Iterable[bytes],
        total: list[int],
    ) -> None:
        """把单个成员按 64 KiB 块写入临时根，统一计数与边界检查。

        macOS 噪音成员（__MACOSX/、.DS_Store）同样计数但不落盘；ZIP 源
        逐块累计真实字节数，绝不信任 ZipInfo.file_size。
        """
        sink_target: Path | None = None
        if not _is_macos_noise(path):
            relative = path.relative_to(root)
            sink_target = temp_root / relative
            if not sink_target.resolve().is_relative_to(temp_root.resolve()):
                raise SkillManagerError("bad_request", "导入路径无效")
            sink_target.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.nullcontext(None) if sink_target is None else open(sink_target, "wb") as sink:
            for chunk in chunks:
                total[0] += len(chunk)
                if total[0] > MAX_EXTRACTED_BYTES:
                    raise SkillManagerError("too_large", "解包后总大小超过 25 MiB")
                if sink is not None:
                    sink.write(chunk)

    def _stage_and_commit(self, stage: Callable[[Path], None]) -> dict[str, object]:
        """共享管道：临时根 staging → 严格解析 → 锁内碰撞检查 → 原子落位。"""
        roots = self._require_roots()
        temp_root = Path(tempfile.mkdtemp(dir=roots.disabled.parent, prefix=".import-"))
        try:
            stage(temp_root)
            staged_root = temp_root if temp_root.joinpath("SKILL.md").is_file() else None
            if staged_root is None:
                wrappers = [child for child in temp_root.iterdir() if child.is_dir() and (child / "SKILL.md").is_file()]
                if len(wrappers) != 1:
                    raise SkillManagerError("bad_request", "导入包必须有且仅有一个技能根")
                staged_root = wrappers[0]
            raw = (staged_root / "SKILL.md").read_bytes()
            if len(raw) > MAX_SKILL_FILE_SIZE:
                raise SkillManagerError("bad_request", "SKILL.md 超过 10 MiB")
            try:
                content = raw.decode("utf-8")
            except UnicodeDecodeError:
                raise SkillManagerError("bad_request", "SKILL.md 必须是 UTF-8 文本") from None
            try:
                parsed = parse_skill_document(content, None, "/user/import/SKILL.md")
            except SkillValidationError as exc:
                raise SkillManagerError("bad_request", str(exc)) from None
            name = parsed.metadata["name"]
            destination = self._commit_staged(roots, staged_root, name)
        finally:
            try:
                _rmtree(temp_root)
            except OSError:
                logger.warning("临时导入目录清理失败")
        return self.get_skill("user", name)

    def _commit_staged(self, roots: SkillRoots, staged_root: Path, name: str) -> Path:
        with _LOCK:
            if _settings_skills_path(roots.settings_path) != roots.active:
                raise SkillManagerError("conflict", "Agent 设置的技能路径已变更，请重启服务")
            if (roots.active / name).exists() or (roots.disabled / name).exists():
                raise SkillManagerError("conflict", "同名技能已存在，导入不会覆盖")
            if name in valid_skill_names(self._builtin_root):
                raise SkillManagerError("conflict", "与内置技能同名，不能导入")
            try:
                roots.disabled.mkdir(parents=True, exist_ok=True)
                os.replace(staged_root, roots.disabled / name)
            except OSError as exc:
                raise _map_oserror(exc) from None
        return roots.disabled / name


def _rmtree(path: Path) -> None:
    import shutil

    shutil.rmtree(path)


@lru_cache(maxsize=1)
def get_skill_manager() -> SkillManager:
    settings_path = agent_settings_path().expanduser().resolve()
    try:
        settings = load_agent_settings(settings_path)
    except AgentSettingsError:
        return SkillManager(BUILTIN_SKILLS_DIR, roots=None, user_error="Agent 设置缺失或无效")
    active = settings.skills.path
    disabled = active.parent / f"{active.name}.disabled"
    return SkillManager(
        BUILTIN_SKILLS_DIR,
        roots=SkillRoots(settings_path=settings_path, active=active, disabled=disabled),
    )
