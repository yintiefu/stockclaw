"""本地技能管理器：用户技能根的枚举、启停、删除与导入。

只做本地磁盘操作：FastAPI 路由直接调用，不触 LangGraph、不调模型。
启停由目录位置表达——活动根为启用，同级 `<name>.disabled` 根为停用；
进程启动时对 settings.json 的 `skills.path` 做快照，任何变更操作前在
锁内复核该路径未漂移，防止两个根被移动后写错位置。所有错误消息为
固定中文，不含物理路径与原始异常内容。
"""
from __future__ import annotations

import errno
import json
import logging
import os
import threading
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

from agent.settings import AgentSettingsError, agent_settings_path, load_agent_settings
from agent.skill_backends import BUILTIN_SKILLS_DIR
from agent.skill_catalog import (
    SkillValidationError,
    parse_skill_file,
    skill_name_grammar_valid,
    valid_skill_names,
)

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()

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
