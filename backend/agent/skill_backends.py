"""Skills 后端构造模块。

只保留仓库内置技能根常量；严格过滤的 CompositeBackend 构造统一由
`agent.skill_catalog` 提供并在此再导出，保持既有 import 路径稳定。
"""
from __future__ import annotations

from pathlib import Path

from agent.skill_catalog import (
    SKILLS_SYSTEM_PROMPT,
    build_builtin_skill_backend,
    build_skill_backend,
)

BUILTIN_SKILLS_DIR = Path(__file__).resolve().parent / "builtin_skills"

__all__ = [
    "BUILTIN_SKILLS_DIR",
    "SKILLS_SYSTEM_PROMPT",
    "build_builtin_skill_backend",
    "build_skill_backend",
]
