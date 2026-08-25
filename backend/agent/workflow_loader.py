"""工作流 YAML 配置加载与严格校验。

支持两种固定图类型：
1. staged_research：分阶段投研工作流（如 debate 多空辩论）
2. single_pass：单步分析工作流（如 reflection 反思审计、daily_review 每日复盘、news_digest 资讯速递）

校验规则：
- 禁止未知字段（extra="forbid"）；
- 校验 schema_version == 1 与 config_version >= 1；
- 文件名必须与 id 严格一致；
- 工具名必须在系统白名单内；
- 软限制不可超过 HARD_LIMITS 代码硬上限；
- 严格检测路径遍历逃逸；
- 输入字段引用完整校验。
"""
from __future__ import annotations

from pathlib import Path
import re
from typing import Annotated, Any, Literal, Union
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
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


class WorkflowConfigError(Exception):
    """工作流配置校验异常。"""
    pass


class DossierSectionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    tool: str
    title: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    empty_policy: Literal["gap_if_empty", "allow_no_record"] = "gap_if_empty"


class DossierConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section_chars: int
    dossier_summary_chars: int
    sections: list[DossierSectionConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_limits(self) -> "DossierConfig":
        if self.section_chars > HARD_LIMITS["section_chars"]:
            raise ValueError(f"section_chars {self.section_chars} 超出硬上限 {HARD_LIMITS['section_chars']}")
        if self.dossier_summary_chars > HARD_LIMITS["dossier_summary_chars"]:
            raise ValueError(f"dossier_summary_chars {self.dossier_summary_chars} 超出硬上限 {HARD_LIMITS['dossier_summary_chars']}")
        return self


class StageConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    skill: str
    instruction: str
    on_error: Literal["continue", "fail"] = "continue"
    output_chars: int
    context_chars: int

    @model_validator(mode="after")
    def check_limits(self) -> "StageConfig":
        if self.output_chars > HARD_LIMITS["stage_output_chars"]:
            raise ValueError(f"stage output_chars {self.output_chars} 超出硬上限 {HARD_LIMITS['stage_output_chars']}")
        if self.context_chars > HARD_LIMITS["stage_context_chars"]:
            raise ValueError(f"stage context_chars {self.context_chars} 超出硬上限 {HARD_LIMITS['stage_context_chars']}")
        return self


class StagedResearchConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    config_version: int
    id: str
    kind: Literal["staged_research"] = "staged_research"
    input: dict[str, Any]
    dossier: DossierConfig
    stages: list[StageConfig]
    variants: dict[str, list[str]]

    @model_validator(mode="after")
    def validate_staged_internals(self) -> "StagedResearchConfig":
        if self.config_version < 1:
            raise ValueError(f"config_version {self.config_version} 必须为正整数 (>= 1)")

        # 工具检查
        for sec in self.dossier.sections:
            if sec.tool not in legacy_tools.TOOL_NAMES:
                raise ValueError(f"未知的工具名称：{sec.tool}")

        # 阶段唯一性检查
        stage_ids = [s.id for s in self.stages]
        if len(stage_ids) != len(set(stage_ids)):
            raise ValueError(f"存在重复阶段 ID：{stage_ids}")

        stage_id_set = set(stage_ids)
        for variant_name, v_stages in self.variants.items():
            for sid in v_stages:
                if sid not in stage_id_set:
                    raise ValueError(f"variant '{variant_name}' 引用了未定义的阶段：{sid}")

        # 变量引用检查：${input.<field>}
        input_keys = set(self.input.keys())
        ref_pattern = re.compile(r"\$\{input\.([^}]+)\}")
        for sec in self.dossier.sections:
            for val in _extract_all_strings(sec.args):
                for match in ref_pattern.finditer(val):
                    ref_field = match.group(1)
                    if ref_field not in input_keys:
                        raise ValueError(f"工具参数引用了不存在的 input 字段：input.{ref_field}")

        return self


class SinglePassInputConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text_field: str
    max_chars: int

    @model_validator(mode="after")
    def check_limits(self) -> "SinglePassInputConfig":
        if self.max_chars > HARD_LIMITS["max_chars"]:
            raise ValueError(f"max_chars {self.max_chars} 超出硬上限 {HARD_LIMITS['max_chars']}")
        return self


class SinglePassConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    config_version: int
    id: str
    kind: Literal["single_pass"] = "single_pass"
    skill: str
    instruction: str
    input: SinglePassInputConfig

    @model_validator(mode="after")
    def validate_single_pass(self) -> "SinglePassConfig":
        if self.config_version < 1:
            raise ValueError(f"config_version {self.config_version} 必须为正整数 (>= 1)")
        return self


WorkflowConfig = Union[StagedResearchConfig, SinglePassConfig]


def _extract_all_strings(obj: Any) -> list[str]:
    strings = []
    if isinstance(obj, str):
        strings.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            strings.extend(_extract_all_strings(v))
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            strings.extend(_extract_all_strings(item))
    return strings


def _verify_path_safety(instruction: str, skill_prefix: str, root: Path | None) -> None:
    if ".." in instruction or instruction.startswith("/"):
        raise WorkflowConfigError(f"路径逃逸违规 (Path traversal)：{instruction}")


def validate_workflow_config(
    raw_dict: dict[str, Any],
    workflow_id: str | None = None,
    builtin_skills_root: Path | None = None,
) -> WorkflowConfig:
    """严格校验工作流配置字典。"""
    if not isinstance(raw_dict, dict):
        raise WorkflowConfigError("工作流配置必须是字典结构")

    # 基础元信息检查
    if "schema_version" in raw_dict and raw_dict["schema_version"] != 1:
        raise WorkflowConfigError(f"不支持的 schema_version: {raw_dict['schema_version']}")

    if "config_version" in raw_dict and (not isinstance(raw_dict["config_version"], int) or raw_dict["config_version"] < 1):
        raise WorkflowConfigError(f"无效的 config_version: {raw_dict.get('config_version')}")

    actual_id = raw_dict.get("id")
    if workflow_id and actual_id != workflow_id:
        raise WorkflowConfigError(f"配置 ID 与期望 ID 不匹配：'{actual_id}' != '{workflow_id}'")

    kind = raw_dict.get("kind")
    if kind == "staged_research":
        try:
            cfg = StagedResearchConfig.model_validate(raw_dict)
        except ValidationError as e:
            raise WorkflowConfigError(f"staged_research 配置校验失败：{e}") from e

        # 检查指令路径安全
        for s in cfg.stages:
            _verify_path_safety(s.instruction, s.skill, builtin_skills_root)
        return cfg

    elif kind == "single_pass":
        try:
            cfg = SinglePassConfig.model_validate(raw_dict)
        except ValidationError as e:
            raise WorkflowConfigError(f"single_pass 配置校验失败：{e}") from e

        _verify_path_safety(cfg.instruction, cfg.skill, builtin_skills_root)
        return cfg

    else:
        raise WorkflowConfigError(f"未知的工作流 kind: '{kind}'")


def load_workflow_config_from_string(
    content: str,
    workflow_id: str | None = None,
    builtin_skills_root: Path | None = None,
) -> WorkflowConfig:
    """从 YAML 字符串解析并校验工作流配置。"""
    try:
        data = yaml.safe_load(content)
    except Exception as e:
        raise WorkflowConfigError(f"YAML 解析失败：{e}") from e
    return validate_workflow_config(data, workflow_id=workflow_id, builtin_skills_root=builtin_skills_root)


def load_workflow_config_from_file(
    file_path: Path | str,
    builtin_skills_root: Path | None = None,
) -> WorkflowConfig:
    """从文件加载并校验工作流配置，校验文件名与 ID 强一致。"""
    path = Path(file_path).resolve()
    if not path.is_file():
        raise WorkflowConfigError(f"配置文件不存在：{path}")

    expected_id = path.stem
    content = path.read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(content)
    except Exception as e:
        raise WorkflowConfigError(f"YAML 解析失败：{e}") from e

    if not isinstance(data, dict):
        raise WorkflowConfigError(f"配置文件内容格式无效：{path}")

    if data.get("id") != expected_id:
        raise WorkflowConfigError(f"配置 ID 与文件名不匹配：id='{data.get('id')}' != filename_stem='{expected_id}'")

    return validate_workflow_config(data, workflow_id=expected_id, builtin_skills_root=builtin_skills_root)


def load_all_production_workflows(
    workflows_dir: Path | str | None = None,
    builtin_skills_root: Path | None = None,
) -> dict[str, WorkflowConfig]:
    """加载并校验目录下的所有生产工作流配置。"""
    target_dir = Path(workflows_dir).resolve() if workflows_dir else Path(__file__).parent / "workflows"
    if not target_dir.is_dir():
        return {}

    configs: dict[str, WorkflowConfig] = {}
    for yaml_file in sorted(target_dir.glob("*.yaml")):
        cfg = load_workflow_config_from_file(yaml_file, builtin_skills_root=builtin_skills_root)
        configs[cfg.id] = cfg
    return configs
