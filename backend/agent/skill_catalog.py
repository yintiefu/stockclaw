"""Agent Skills 严格契约：frontmatter 解析、目录扫描与过滤只读 backend。

解析规则对齐 Agent Skills 规范（agentskills.io），错误信息只含稳定中文
原因，不含物理路径或原始内容。所有文件系统调用方都必须经过
`parse_skill_file`（强制目录名一致性）；`parse_skill_document(content, None, ...)`
仅供导入 staging 在目录名未定时获取校验后的 frontmatter name。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import yaml
from deepagents.backends import BackendProtocol, CompositeBackend, FilesystemBackend, StateBackend
from deepagents.backends.protocol import FileDownloadResponse, LsResult, ReadResult
from deepagents.middleware.skills import SkillMetadata
from yaml.tokens import AliasToken, AnchorToken

MAX_SKILL_FILE_SIZE = 10 * 1024 * 1024
MAX_SKILL_NAME_LENGTH = 64
MAX_SKILL_DESCRIPTION_LENGTH = 1024
MAX_SKILL_COMPATIBILITY_LENGTH = 500


class SkillValidationError(ValueError):
    """只包含稳定中文原因，不包含物理路径或原始内容。"""


@dataclass(frozen=True, slots=True)
class ParsedSkill:
    metadata: SkillMetadata
    instructions: str


def _skill_name_valid(name: str, directory_name: str | None) -> bool:
    return (
        0 < len(name) <= MAX_SKILL_NAME_LENGTH
        and (directory_name is None or name == directory_name)
        and not name.startswith("-")
        and not name.endswith("-")
        and "--" not in name
        and all(char == "-" or char.isdigit() or (char.isalpha() and char.islower()) for char in name)
    )


def parse_skill_document(content: str, directory_name: str | None, virtual_path: str) -> ParsedSkill:
    if len(content.encode("utf-8")) > MAX_SKILL_FILE_SIZE:
        raise SkillValidationError("SKILL.md 超过 10 MiB")
    match = re.match(r"^---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|$)", content, re.DOTALL)
    if match is None:
        raise SkillValidationError("SKILL.md 缺少合法 YAML frontmatter")
    try:
        tokens = yaml.scan(match.group(1))
        if any(isinstance(token, (AnchorToken, AliasToken)) for token in tokens):
            raise SkillValidationError("YAML frontmatter 不允许 anchor 或 alias")
        raw = yaml.safe_load(match.group(1))
    except SkillValidationError:
        raise
    except yaml.YAMLError:
        raise SkillValidationError("YAML frontmatter 解析失败") from None
    if not isinstance(raw, dict):
        raise SkillValidationError("YAML frontmatter 必须是对象")
    name = raw.get("name")
    description = raw.get("description")
    if not isinstance(name, str) or not _skill_name_valid(name, directory_name):
        raise SkillValidationError("技能 name 格式无效或与目录名不一致")
    if not isinstance(description, str) or not 0 < len(description.strip()) <= MAX_SKILL_DESCRIPTION_LENGTH:
        raise SkillValidationError("技能 description 缺失或超过 1024 字符")
    license_value = raw.get("license")
    if license_value is not None and not isinstance(license_value, str):
        raise SkillValidationError("技能 license 必须是字符串")
    compatibility = raw.get("compatibility")
    if compatibility is not None and (
        not isinstance(compatibility, str)
        or not 0 < len(compatibility.strip()) <= MAX_SKILL_COMPATIBILITY_LENGTH
    ):
        raise SkillValidationError("技能 compatibility 必须是 1-500 字符的字符串")
    metadata_raw = raw.get("metadata", {})
    if not isinstance(metadata_raw, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in metadata_raw.items()
    ):
        raise SkillValidationError("技能 metadata 必须是字符串键值对象")
    tools_raw = raw.get("allowed-tools", [])
    if isinstance(tools_raw, str):
        allowed_tools = [tool for tool in re.split(r"[\s,]+", tools_raw) if tool]
    elif isinstance(tools_raw, list) and all(isinstance(tool, str) and tool.strip() for tool in tools_raw):
        allowed_tools = [tool.strip() for tool in tools_raw]
    else:
        raise SkillValidationError("技能 allowed-tools 必须是字符串或字符串数组")
    metadata = SkillMetadata(
        name=name, description=description.strip(), path=virtual_path,
        license=(license_value.strip() or None) if license_value is not None else None,
        compatibility=compatibility.strip() if compatibility is not None else None,
        metadata=dict(metadata_raw), allowed_tools=allowed_tools,
    )
    return ParsedSkill(metadata=metadata, instructions=content)


def parse_skill_file(path: Path, virtual_path: str) -> ParsedSkill:
    raw = path.read_bytes()
    if len(raw) > MAX_SKILL_FILE_SIZE:
        raise SkillValidationError("SKILL.md 超过 10 MiB")
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise SkillValidationError("SKILL.md 必须是 UTF-8 文本") from None
    return parse_skill_document(content, path.parent.name, virtual_path)


class FilteredSkillBackend(BackendProtocol):
    """在文件系统之上强制技能边界的只读 backend。

    CompositeBackend 会先剥离路由前缀（`/user/<name>/...` 到达时已是
    `/<name>/...`），因此授权判断以第一段路径为技能名。无效、冲突、
    symlink 目录与任何逃逸技能根的相对路径一律按不存在处理。
    """

    def __init__(self, root: Path | str, *, excluded_names: set[str] | frozenset[str] = frozenset()):
        self.root = Path(root).resolve()
        self._excluded = frozenset(excluded_names)
        self._delegate = FilesystemBackend(root_dir=self.root, virtual_mode=True)

    def _authorized(self, virtual_path: str) -> bool:
        normalized = PurePosixPath("/" + virtual_path.lstrip("/"))
        if ".." in normalized.parts or len(normalized.parts) < 2:
            return False
        name = normalized.parts[1]
        skill_root = self.root / name
        if name in self._excluded or skill_root.is_symlink():
            return False
        try:
            parse_skill_file(skill_root / "SKILL.md", f"/{name}/SKILL.md")
            (self.root / str(normalized).lstrip("/")).resolve().relative_to(skill_root.resolve())
        except (OSError, UnicodeDecodeError, ValueError, SkillValidationError):
            return False
        return True

    def ls(self, path: str) -> LsResult:
        if path.rstrip("/") in {"", "."}:
            result = self._delegate.ls("/")
            entries = [entry for entry in result.entries or [] if self._authorized(entry["path"])]
            return LsResult(error=result.error, entries=entries)
        return self._delegate.ls(path) if self._authorized(path) else LsResult(error="path_not_found")

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        return self._delegate.read(file_path, offset, limit) if self._authorized(file_path) else ReadResult(error="file_not_found")

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        return [
            self._delegate.download_files([path])[0]
            if self._authorized(path)
            else FileDownloadResponse(path=path, content=None, error="file_not_found")
            for path in paths
        ]


def valid_skill_names(root: Path) -> frozenset[str]:
    """枚举根目录下严格可解析的技能名（symlink 与无效目录一律跳过）。"""
    if not root.is_dir():
        return frozenset()
    names: set[str] = set()
    for child in root.iterdir():
        if not child.is_dir() or child.is_symlink() or not (child / "SKILL.md").is_file():
            continue
        try:
            parsed = parse_skill_file(child / "SKILL.md", f"/builtin/{child.name}/SKILL.md")
        except (OSError, SkillValidationError):
            continue
        names.add(parsed.metadata["name"])
    return frozenset(names)


SKILLS_SYSTEM_PROMPT = """## Skills
{skills_locations}{skills_load_warnings}

可用技能：
{skills_list}

先按名称与描述判断是否适用，再用 `read_file` 读取列出的 SKILL.md；附属文件和脚本仅作只读参考，不能执行。
"""


def build_skill_backend(builtin_root: Path | str, user_root: Path | str) -> CompositeBackend:
    """工作台复合视图：/builtin/ 与 /user/ 都挂过滤 backend，用户侧排除内置同名。"""
    builtin_names = valid_skill_names(Path(builtin_root))
    return CompositeBackend(default=StateBackend(), routes={
        "/builtin/": FilteredSkillBackend(builtin_root),
        "/user/": FilteredSkillBackend(user_root, excluded_names=builtin_names),
    })


def build_builtin_skill_backend(builtin_root: Path | str) -> CompositeBackend:
    """嵌入式视图：只暴露 /builtin/，永不挂 /user/。"""
    return CompositeBackend(default=StateBackend(), routes={
        "/builtin/": FilteredSkillBackend(builtin_root),
    })
