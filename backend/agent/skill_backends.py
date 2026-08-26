"""Skills 后端构造模块。

提供只读 /builtin/ 与 /user/ 命名空间的 CompositeBackend 构造。
"""
from __future__ import annotations

from pathlib import Path

from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend

BUILTIN_SKILLS_DIR = Path(__file__).resolve().parent / "builtin_skills"


def build_skill_backend(builtin_root: Path | str, user_root: Path | str) -> CompositeBackend:
    """构造支持 /builtin/ 与 /user/ 命名空间的只读 CompositeBackend。"""
    builtin_path = Path(builtin_root).resolve()
    user_path = Path(user_root).resolve()
    return CompositeBackend(
        default=StateBackend(),
        routes={
            "/builtin/": FilesystemBackend(root_dir=str(builtin_path), virtual_mode=True),
            "/user/": FilesystemBackend(root_dir=str(user_path), virtual_mode=True),
        },
    )
