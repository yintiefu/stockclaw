"""工作流 YAML 配置加载与严格校验。"""
from __future__ import annotations

from pathlib import Path, PurePosixPath
import re
from typing import Any, Literal, Union

from deepagents.middleware.skills import _parse_skill_metadata, _validate_skill_name
from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError
import yaml

import tools as legacy_tools
from agent.skill_backends import BUILTIN_SKILLS_DIR

HARD_LIMITS: dict[str, int] = {
    "section_chars": 4000,
    "dossier_summary_chars": 12000,
    "stage_output_chars": 4000,
    "stage_context_chars": 60000,
    "max_chars": 60000,
}

# debate 的 13 项底稿固定契约不在运行时校验：防回归不变量由
# tests/agent/test_workflow_loader.py 的契约测试在 CI 阶段兜底，
# 加载器只负责通用 schema 校验（工具注册名、参数引用、预算上限等）。

_INPUT_REFERENCE_RE = re.compile(r"^\$\{input\.([A-Za-z_][A-Za-z0-9_]*)\}$")
_BUILTIN_SKILL_RE = re.compile(r"^builtin/[a-z0-9][a-z0-9-]*$")
_CONTEXT_NAMES = frozenset({"dossier", "dossier.summary", "dossier.missing", "stages"})


class WorkflowConfigError(Exception):
    """只包含配置字段路径和稳定原因的安全异常。"""


class InputFieldConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["string"]
    pattern: str


class DossierSectionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    tool: str
    title: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    empty_policy: Literal["gap_if_empty", "allow_no_record"] = "gap_if_empty"


class DossierConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section_chars: StrictInt
    dossier_summary_chars: StrictInt
    sections: list[DossierSectionConfig] = Field(default_factory=list)


class StageConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    skill: str
    instruction: str
    context: list[str]
    on_error: Literal["continue", "fail"] = "continue"
    output_chars: StrictInt
    context_chars: StrictInt


class StagedResearchConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: StrictInt = 1
    config_version: StrictInt
    id: str
    kind: Literal["staged_research"] = "staged_research"
    result_stage: str
    input: dict[str, InputFieldConfig]
    dossier: DossierConfig
    stages: list[StageConfig]
    variants: dict[str, list[str]]


class SinglePassInputConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text_field: str
    max_chars: StrictInt


class SinglePassConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: StrictInt = 1
    config_version: StrictInt
    id: str
    kind: Literal["single_pass"] = "single_pass"
    skill: str
    instruction: str
    input: SinglePassInputConfig


WorkflowConfig = Union[StagedResearchConfig, SinglePassConfig]


def _field_path(location: tuple[Any, ...]) -> str:
    return ".".join(str(part) for part in location) or "配置"


def _safe_validation_message(error: ValidationError) -> str:
    reasons = {
        "extra_forbidden": "不允许未知字段",
        "missing": "缺少必填字段",
        "literal_error": "值不在允许范围",
        "string_type": "必须是字符串",
        "int_type": "必须是整数",
        "list_type": "必须是列表",
        "dict_type": "必须是对象",
    }
    parts: list[str] = []
    for item in error.errors(include_url=False, include_context=False, include_input=False):
        path = _field_path(tuple(item.get("loc", ())))
        reason = reasons.get(str(item.get("type")), "字段格式不符合契约")
        parts.append(f"字段 {path}：{reason}")
    return "配置校验失败：" + "；".join(parts)


def _config_error(field: str, reason: str) -> WorkflowConfigError:
    return WorkflowConfigError(f"字段 {field}：{reason}")


def _walk_strings(value: Any, path: str) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    if isinstance(value, str):
        found.append((path, value))
    elif isinstance(value, dict):
        for key, item in value.items():
            found.extend(_walk_strings(item, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_walk_strings(item, f"{path}.{index}"))
    return found


def _verify_instruction(
    skill: str,
    instruction: str,
    root: Path,
    field: str,
) -> None:
    if not _BUILTIN_SKILL_RE.fullmatch(skill):
        raise _config_error(field.rsplit(".", 1)[0] + ".skill", "必须使用 builtin/<name> 命名空间")

    instruction_path = PurePosixPath(instruction)
    if instruction_path.is_absolute() or ".." in instruction_path.parts:
        raise _config_error(field, "指令路径不得逃逸内置 Skill 目录")

    resolved_root = root.resolve()
    skill_root = (resolved_root / skill.removeprefix("builtin/")).resolve()
    target = (skill_root / instruction).resolve()
    try:
        skill_root.relative_to(resolved_root)
        target.relative_to(skill_root)
    except ValueError:
        raise _config_error(field, "指令路径不得逃逸内置 Skill 目录") from None

    skill_file = skill_root / "SKILL.md"
    if not skill_file.is_file():
        raise _config_error(field, "引用的内置技能指令文件不存在：Skill 根 SKILL.md 缺失")
    metadata = _parse_skill_metadata(
        skill_file.read_text(encoding="utf-8"),
        "SKILL.md",
        skill_root.name,
    )
    if metadata is None or not metadata.get("name") or not metadata.get("description"):
        raise _config_error(field, "Skill 根 SKILL.md frontmatter 元数据无效")
    name_valid, _ = _validate_skill_name(metadata["name"], skill_root.name)
    if not name_valid:
        raise _config_error(field, "Skill 根 SKILL.md frontmatter 的 name 与目录不一致")
    if not target.is_file():
        raise _config_error(field, "引用的内置技能指令文件不存在")


def _validate_limits(cfg: StagedResearchConfig | SinglePassConfig) -> None:
    if isinstance(cfg, SinglePassConfig):
        if cfg.input.max_chars > HARD_LIMITS["max_chars"]:
            raise _config_error("input.max_chars", "超出代码硬上限")
        if cfg.input.max_chars < 1:
            raise _config_error("input.max_chars", "必须为正整数")
        return

    checks = (
        ("dossier.section_chars", cfg.dossier.section_chars, "section_chars"),
        ("dossier.dossier_summary_chars", cfg.dossier.dossier_summary_chars, "dossier_summary_chars"),
    )
    for field, value, hard_limit in checks:
        if value < 1:
            raise _config_error(field, "必须为正整数")
        if value > HARD_LIMITS[hard_limit]:
            raise _config_error(field, "超出代码硬上限")
    for index, stage in enumerate(cfg.stages):
        if stage.output_chars < 1 or stage.output_chars > HARD_LIMITS["stage_output_chars"]:
            raise _config_error(f"stages.{index}.output_chars", "必须为正整数且不超过代码硬上限")
        if stage.context_chars < 1 or stage.context_chars > HARD_LIMITS["stage_context_chars"]:
            raise _config_error(f"stages.{index}.context_chars", "必须为正整数且不超过代码硬上限")


def _validate_staged_config(cfg: StagedResearchConfig, skills_root: Path) -> None:
    _validate_limits(cfg)
    if not cfg.input:
        raise _config_error("input", "至少声明一个输入字段")
    for name, input_cfg in cfg.input.items():
        try:
            re.compile(input_cfg.pattern)
        except re.error:
            raise _config_error(f"input.{name}.pattern", "不是有效正则表达式") from None

    section_ids = [section.id for section in cfg.dossier.sections]
    if len(section_ids) != len(set(section_ids)):
        raise _config_error("dossier.sections", "存在重复 section ID")
    for index, section in enumerate(cfg.dossier.sections):
        if section.tool not in legacy_tools.TOOL_NAMES:
            raise _config_error(f"dossier.sections.{index}.tool", "未注册的内置工具")
        for path, value in _walk_strings(section.args, f"dossier.sections.{index}.args"):
            if "${" not in value:
                continue
            match = _INPUT_REFERENCE_RE.fullmatch(value)
            if match is None:
                raise _config_error(path, "input 参数必须是完整引用 ${input.<field>}")
            if match.group(1) not in cfg.input:
                raise _config_error(path, "input 引用指向未声明字段")

    stage_ids = [stage.id for stage in cfg.stages]
    if not stage_ids:
        raise _config_error("stages", "至少声明一个阶段")
    if len(stage_ids) != len(set(stage_ids)):
        raise _config_error("stages", "存在重复阶段 ID")
    positions = {stage_id: index for index, stage_id in enumerate(stage_ids)}
    if cfg.result_stage not in positions:
        raise _config_error("result_stage", "必须引用已声明阶段")

    for index, stage in enumerate(cfg.stages):
        if not stage.label.strip():
            raise _config_error(f"stages.{index}.label", "不得为空")
        if len(stage.context) != len(set(stage.context)):
            raise _config_error(f"stages.{index}.context", "不得包含重复引用")
        for context_ref in stage.context:
            if context_ref in _CONTEXT_NAMES:
                continue
            if not context_ref.startswith("stage."):
                raise _config_error(f"stages.{index}.context", "包含非法上下文引用")
            referenced = context_ref.removeprefix("stage.")
            if referenced not in positions or positions[referenced] >= index:
                raise _config_error(f"stages.{index}.context", "只能引用已完成的前序阶段")
        _verify_instruction(stage.skill, stage.instruction, skills_root, f"stages.{index}.instruction")

    if not cfg.variants:
        raise _config_error("variants", "至少声明一个非空变体")
    for variant_name, variant_stages in cfg.variants.items():
        field = f"variants.{variant_name}"
        if not variant_name.strip() or not variant_stages:
            raise _config_error(field, "变体名和阶段列表不得为空")
        if len(variant_stages) != len(set(variant_stages)):
            raise _config_error(field, "存在重复阶段 ID")
        if any(stage_id not in positions for stage_id in variant_stages):
            raise _config_error(field, "引用了未声明阶段")
        if [positions[stage_id] for stage_id in variant_stages] != sorted(positions[stage_id] for stage_id in variant_stages):
            raise _config_error(field, "阶段必须按声明顺序执行")
        if variant_stages[-1] != cfg.result_stage:
            raise _config_error(field, "最后阶段必须为 result_stage")
        variant_positions = {stage_id: index for index, stage_id in enumerate(variant_stages)}
        for stage_id in variant_stages:
            stage = cfg.stages[positions[stage_id]]
            for context_ref in stage.context:
                if context_ref.startswith("stage."):
                    referenced = context_ref.removeprefix("stage.")
                    if referenced not in variant_positions or variant_positions[referenced] >= variant_positions[stage_id]:
                        raise _config_error(field, "stage context 必须引用该变体的前序阶段")


def validate_staged_input(cfg: StagedResearchConfig, values: dict[str, Any]) -> None:
    """在任何工具或模型调用前执行 YAML 输入 schema。"""
    if not isinstance(values, dict):
        raise ValueError("工作流输入必须是对象")
    for name, input_cfg in cfg.input.items():
        value = values.get(name)
        if not isinstance(value, str) or not value:
            raise ValueError(f"输入字段 input.{name} 缺失或类型错误")
        if re.fullmatch(input_cfg.pattern, value) is None:
            raise ValueError(f"输入字段 input.{name} 格式不符合约束")


def validate_workflow_config(
    raw_dict: dict[str, Any],
    workflow_id: str | None = None,
    builtin_skills_root: Path | None = None,
) -> WorkflowConfig:
    """严格校验工作流配置字典，不在异常中回显原始值。"""
    if not isinstance(raw_dict, dict):
        raise WorkflowConfigError("配置根节点必须是对象")
    schema_version = raw_dict.get("schema_version")
    if isinstance(schema_version, bool) or not isinstance(schema_version, int) or schema_version != 1:
        raise _config_error("schema_version", "不受支持")
    config_version = raw_dict.get("config_version")
    if isinstance(config_version, bool) or not isinstance(config_version, int) or config_version < 1:
        raise _config_error("config_version", "必须为正整数")
    if workflow_id and raw_dict.get("id") != workflow_id:
        raise _config_error("id", "与文件名或注册名不一致")

    kind = raw_dict.get("kind")
    model_type: type[StagedResearchConfig] | type[SinglePassConfig]
    if kind == "staged_research":
        model_type = StagedResearchConfig
    elif kind == "single_pass":
        model_type = SinglePassConfig
    else:
        raise _config_error("kind", "不在支持白名单")

    try:
        cfg = model_type.model_validate(raw_dict)
    except ValidationError as exc:
        safe_message = _safe_validation_message(exc)
        raise WorkflowConfigError(safe_message) from None

    skills_root = (builtin_skills_root or BUILTIN_SKILLS_DIR).resolve()
    if isinstance(cfg, StagedResearchConfig):
        _validate_staged_config(cfg, skills_root)
    else:
        _validate_limits(cfg)
        _verify_instruction(cfg.skill, cfg.instruction, skills_root, "instruction")
    return cfg


def load_workflow_config_from_string(
    content: str,
    workflow_id: str | None = None,
    builtin_skills_root: Path | None = None,
) -> WorkflowConfig:
    """从 YAML 字符串解析并校验工作流。"""
    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError:
        raise WorkflowConfigError("YAML 解析失败：请检查语法") from None
    return validate_workflow_config(data, workflow_id=workflow_id, builtin_skills_root=builtin_skills_root)


def load_workflow_config_from_file(
    file_path: Path | str,
    builtin_skills_root: Path | None = None,
) -> WorkflowConfig:
    """从文件加载配置，并校验文件名与 ID 一致。"""
    path = Path(file_path).resolve()
    if not path.is_file():
        raise WorkflowConfigError("配置文件不存在")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        raise WorkflowConfigError("YAML 解析失败：请检查语法") from None
    if not isinstance(data, dict):
        raise WorkflowConfigError("配置根节点必须是对象")
    if data.get("id") != path.stem:
        raise _config_error("id", "与文件名不一致")
    return validate_workflow_config(data, workflow_id=path.stem, builtin_skills_root=builtin_skills_root)


def load_all_production_workflows(
    workflows_dir: Path | str | None = None,
    builtin_skills_root: Path | None = None,
) -> dict[str, WorkflowConfig]:
    """加载并校验目录下所有生产工作流。"""
    target_dir = Path(workflows_dir).resolve() if workflows_dir else Path(__file__).parent / "workflows"
    if not target_dir.is_dir():
        return {}
    configs: dict[str, WorkflowConfig] = {}
    for yaml_file in sorted(target_dir.glob("*.yaml")):
        cfg = load_workflow_config_from_file(yaml_file, builtin_skills_root=builtin_skills_root)
        configs[cfg.id] = cfg
    return configs
