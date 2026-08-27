"""工作流配置加载器与校验契约测试。

验证：
- 合法 staged_research 与 single_pass 结构解析；
- 未知字段 extra="forbid"；
- 不支持的 schema_version、非正 config_version、文件名与 ID 不匹配；
- 未知 kind、未知 tool、未知 Skill、重复阶段 ID、缺失阶段 ID；
- 阶段前向引用限制与无效输入引用；
- 路径逃逸检测；
- 非法 empty_policy / on_error；
- 软限制超出代码硬上限（HARD_LIMITS）；
- 禁止 result.field 动态属性；
- 生产 4 个工作流（debate / reflection / daily_review / news_digest）加载与完整性；
- 13 项底稿抓取清单与 5 个辩论阶段、2 种变体（standard / cross_exam）；
- 技能与引用文本中立规则与边界词汇（分歧点、验证清单、不给买卖结论、不判胜负）。
"""
from __future__ import annotations

from pathlib import Path
import traceback
import pytest
import yaml

from agent.skill_backends import BUILTIN_SKILLS_DIR
from agent.tool_executor import (
    EASTMONEY_SERIAL_TOOLS,
    PARALLEL_SAFE_TOOLS,
    ToolExecutionPolicy,
    tool_policy,
)
from agent.workflow_loader import (
    HARD_LIMITS,
    SinglePassConfig,
    StagedResearchConfig,
    WorkflowConfigError,
    load_all_production_workflows,
    load_workflow_config_from_file,
    load_workflow_config_from_string,
    validate_staged_input,
    validate_workflow_config,
)

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "workflows"
PRODUCTION_WORKFLOWS_DIR = Path(__file__).resolve().parents[2] / "agent" / "workflows"


def test_load_valid_staged_workflow_file() -> None:
    path = FIXTURES_DIR / "staged_valid.yaml"
    cfg = load_workflow_config_from_file(path)
    assert isinstance(cfg, StagedResearchConfig)
    assert cfg.id == "staged_valid"
    assert cfg.kind == "staged_research"
    assert len(cfg.stages) == 3
    assert "standard" in cfg.variants
    assert cfg.result_stage == "referee"
    assert cfg.stages[1].label == "空方研究员"
    assert cfg.stages[1].context == ["dossier", "stage.bull"]


def test_load_valid_single_pass_file() -> None:
    path = FIXTURES_DIR / "single_pass_valid.yaml"
    cfg = load_workflow_config_from_file(path)
    assert isinstance(cfg, SinglePassConfig)
    assert cfg.id == "single_pass_valid"
    assert cfg.kind == "single_pass"
    assert cfg.input.text_field == "source"
    assert cfg.input.max_chars == 12000


def test_reject_unknown_field() -> None:
    doc = {
        "schema_version": 1,
        "config_version": 1,
        "id": "test_wf",
        "kind": "single_pass",
        "skill": "builtin/reflection-audit",
        "instruction": "SKILL.md",
        "input": {"text_field": "source", "max_chars": 1000},
        "unknown_extra_field": "forbidden",
    }
    with pytest.raises(WorkflowConfigError, match="Extra inputs are not permitted|extra"):
        validate_workflow_config(doc, workflow_id="test_wf")


def test_reject_unsupported_schema_version() -> None:
    doc = {
        "schema_version": 2,
        "config_version": 1,
        "id": "test_wf",
        "kind": "single_pass",
        "skill": "builtin/reflection-audit",
        "instruction": "SKILL.md",
        "input": {"text_field": "source", "max_chars": 1000},
    }
    with pytest.raises(WorkflowConfigError, match="schema_version"):
        validate_workflow_config(doc, workflow_id="test_wf")


def test_reject_non_positive_config_version() -> None:
    doc = {
        "schema_version": 1,
        "config_version": 0,
        "id": "test_wf",
        "kind": "single_pass",
        "skill": "builtin/reflection-audit",
        "instruction": "SKILL.md",
        "input": {"text_field": "source", "max_chars": 1000},
    }
    with pytest.raises(WorkflowConfigError, match="config_version"):
        validate_workflow_config(doc, workflow_id="test_wf")


def test_reject_filename_id_mismatch(tmp_path: Path) -> None:
    file = tmp_path / "mismatch_name.yaml"
    file.write_text(
        yaml.safe_dump({
            "schema_version": 1,
            "config_version": 1,
            "id": "different_id",
            "kind": "single_pass",
            "skill": "builtin/reflection-audit",
            "instruction": "SKILL.md",
            "input": {"text_field": "source", "max_chars": 1000},
        }),
        encoding="utf-8",
    )
    with pytest.raises(WorkflowConfigError, match="字段 id：与文件名不一致"):
        load_workflow_config_from_file(file, builtin_skills_root=tmp_path)


def test_reject_unknown_kind() -> None:
    doc = {
        "schema_version": 1,
        "config_version": 1,
        "id": "test_wf",
        "kind": "unknown_workflow_kind",
        "skill": "builtin/test",
        "instruction": "SKILL.md",
        "input": {"text_field": "source", "max_chars": 1000},
    }
    with pytest.raises(WorkflowConfigError, match="kind"):
        validate_workflow_config(doc, workflow_id="test_wf")


def test_reject_unknown_tool() -> None:
    doc = {
        "schema_version": 1,
        "config_version": 1,
        "id": "test_wf",
        "kind": "staged_research",
        "result_stage": "s1",
        "input": {"code": {"type": "string", "pattern": "^[0-9]{6}$"}},
        "dossier": {
            "section_chars": 1800,
            "dossier_summary_chars": 6000,
            "sections": [
                {
                    "id": "bad_tool_sec",
                    "tool": "non_existent_tool_name",
                    "args": {},
                    "empty_policy": "gap_if_empty",
                }
            ],
        },
        "stages": [
            {
                "id": "s1",
                "label": "研究员",
                "skill": "builtin/debate",
                "instruction": "references/bull.md",
                "context": [],
                "on_error": "continue",
                "output_chars": 1000,
                "context_chars": 5000,
            }
        ],
        "variants": {"standard": ["s1"]},
    }
    with pytest.raises(WorkflowConfigError, match="dossier.sections.0.tool"):
        validate_workflow_config(doc, workflow_id="test_wf")


def test_reject_duplicate_or_missing_stage() -> None:
    doc_duplicate = {
        "schema_version": 1,
        "config_version": 1,
        "id": "test_wf",
        "kind": "staged_research",
        "result_stage": "dup",
        "input": {"code": {"type": "string", "pattern": "^[0-9]{6}$"}},
        "dossier": {"section_chars": 1800, "dossier_summary_chars": 6000, "sections": []},
        "stages": [
            {"id": "dup", "label": "阶段", "skill": "builtin/debate", "instruction": "references/bull.md", "context": [], "on_error": "continue", "output_chars": 1000, "context_chars": 5000},
            {"id": "dup", "label": "阶段", "skill": "builtin/debate", "instruction": "references/bull.md", "context": [], "on_error": "continue", "output_chars": 1000, "context_chars": 5000},
        ],
        "variants": {"v1": ["dup"]},
    }
    with pytest.raises(WorkflowConfigError, match="重复阶段 ID"):
        validate_workflow_config(doc_duplicate, workflow_id="test_wf")

    doc_missing_variant_stage = {
        "schema_version": 1,
        "config_version": 1,
        "id": "test_wf",
        "kind": "staged_research",
        "result_stage": "s1",
        "input": {"code": {"type": "string", "pattern": "^[0-9]{6}$"}},
        "dossier": {"section_chars": 1800, "dossier_summary_chars": 6000, "sections": []},
        "stages": [
            {"id": "s1", "label": "阶段", "skill": "builtin/debate", "instruction": "references/bull.md", "context": [], "on_error": "continue", "output_chars": 1000, "context_chars": 5000},
        ],
        "variants": {"v1": ["s1", "non_existent_stage"]},
    }
    with pytest.raises(WorkflowConfigError, match="未声明阶段"):
        validate_workflow_config(doc_missing_variant_stage, workflow_id="test_wf")


def test_reject_invalid_input_reference() -> None:
    doc = {
        "schema_version": 1,
        "config_version": 1,
        "id": "test_wf",
        "kind": "staged_research",
        "result_stage": "s1",
        "input": {"code": {"type": "string", "pattern": "^[0-9]{6}$"}},
        "dossier": {
            "section_chars": 1800,
            "dossier_summary_chars": 6000,
            "sections": [
                {
                    "id": "sec1",
                    "tool": "query_quote",
                    "args": {"codes": ["${input.non_existent_field}"]},
                    "empty_policy": "gap_if_empty",
                }
            ],
        },
        "stages": [
            {"id": "s1", "label": "阶段", "skill": "builtin/debate", "instruction": "references/bull.md", "context": [], "on_error": "continue", "output_chars": 1000, "context_chars": 5000},
        ],
        "variants": {"v1": ["s1"]},
    }
    with pytest.raises(WorkflowConfigError, match="dossier.sections.0.args.codes.0"):
        validate_workflow_config(doc, workflow_id="test_wf")


def test_reject_path_escape(tmp_path: Path) -> None:
    doc = {
        "schema_version": 1,
        "config_version": 1,
        "id": "test_wf",
        "kind": "single_pass",
        "skill": "builtin/reflection-audit",
        "instruction": "../../../etc/passwd",
        "input": {"text_field": "source", "max_chars": 1000},
    }
    with pytest.raises(WorkflowConfigError, match="不得逃逸"):
        validate_workflow_config(doc, workflow_id="test_wf", builtin_skills_root=tmp_path)


def test_reject_invalid_empty_policy_and_on_error() -> None:
    doc_bad_empty = {
        "schema_version": 1,
        "config_version": 1,
        "id": "test_wf",
        "kind": "staged_research",
        "result_stage": "s1",
        "input": {"code": {"type": "string", "pattern": "^[0-9]{6}$"}},
        "dossier": {
            "section_chars": 1800,
            "dossier_summary_chars": 6000,
            "sections": [
                {"id": "s", "tool": "query_quote", "args": {"codes": ["${input.code}"]}, "empty_policy": "invalid_empty_policy"}
            ],
        },
        "stages": [
            {"id": "s1", "label": "阶段", "skill": "builtin/debate", "instruction": "references/bull.md", "context": [], "on_error": "continue", "output_chars": 1000, "context_chars": 5000}
        ],
        "variants": {"v1": ["s1"]},
    }
    with pytest.raises(WorkflowConfigError, match="empty_policy"):
        validate_workflow_config(doc_bad_empty, workflow_id="test_wf")


def test_reject_soft_limit_above_hard_limit() -> None:
    doc = {
        "schema_version": 1,
        "config_version": 1,
        "id": "test_wf",
        "kind": "staged_research",
        "result_stage": "s1",
        "input": {"code": {"type": "string", "pattern": "^[0-9]{6}$"}},
        "dossier": {
            "section_chars": HARD_LIMITS["section_chars"] + 1000,
            "dossier_summary_chars": 6000,
            "sections": [],
        },
        "stages": [
            {"id": "s1", "label": "阶段", "skill": "builtin/debate", "instruction": "references/bull.md", "context": [], "on_error": "continue", "output_chars": 1000, "context_chars": 5000}
        ],
        "variants": {"v1": ["s1"]},
    }
    with pytest.raises(WorkflowConfigError, match="section_chars"):
        validate_workflow_config(doc, workflow_id="test_wf")


def test_reject_forbidden_result_field() -> None:
    doc = {
        "schema_version": 1,
        "config_version": 1,
        "id": "test_wf",
        "kind": "single_pass",
        "skill": "builtin/reflection-audit",
        "instruction": "SKILL.md",
        "input": {"text_field": "source", "max_chars": 1000},
        "result": {"field": "custom_audit_field"},
    }
    with pytest.raises(WorkflowConfigError, match="Extra inputs are not permitted|result"):
        validate_workflow_config(doc, workflow_id="test_wf")


# ---------------------------------------------------------------------------
# 生产配置与技能内容契约测试
# ---------------------------------------------------------------------------

def test_load_all_production_workflows_returns_four_configs() -> None:
    configs = load_all_production_workflows(PRODUCTION_WORKFLOWS_DIR, BUILTIN_SKILLS_DIR)
    assert set(configs.keys()) == {"debate", "reflection", "daily_review", "news_digest"}

    debate = configs["debate"]
    assert isinstance(debate, StagedResearchConfig)
    assert debate.id == "debate"
    assert debate.variants == {
        "standard": ["bull", "bear", "referee"],
        "cross_exam": ["bull", "bear", "bull_rebut", "bear_rebut", "referee"],
    }
    assert debate.result_stage == "referee"

    expected_stage_contract = {
        "bull": ("多方研究员", ["dossier"]),
        "bear": ("空方研究员", ["dossier", "stage.bull"]),
        "bull_rebut": ("多方反驳", ["dossier", "stage.bull", "stage.bear"]),
        "bear_rebut": ("空方反驳", ["dossier", "stage.bull", "stage.bear", "stage.bull_rebut"]),
        "referee": ("中立主持", ["dossier.summary", "dossier.missing", "stages"]),
    }
    assert {stage.id: (stage.label, stage.context) for stage in debate.stages} == expected_stage_contract

    # 13 items mapping
    expected_sections = [
        ("quote", "query_quote", "gap_if_empty"),
        ("valuation", "query_valuation", "gap_if_empty"),
        ("valuation_percentile", "query_valuation_percentile", "gap_if_empty"),
        ("financials", "query_financials", "gap_if_empty"),
        ("kline", "query_kline", "gap_if_empty"),
        ("fund_flow", "query_fund_flow", "gap_if_empty"),
        ("margin", "query_margin", "allow_no_record"),
        ("holders", "query_holders", "allow_no_record"),
        ("announcements", "query_announcements", "allow_no_record"),
        ("lockup", "query_lockup", "allow_no_record"),
        ("concepts", "query_concepts", "gap_if_empty"),
        ("reports", "query_reports", "allow_no_record"),
        ("news", "query_news", "allow_no_record"),
    ]
    assert len(debate.dossier.sections) == 13
    for i, (sid, tool, empty_policy) in enumerate(expected_sections):
        sec = debate.dossier.sections[i]
        assert sec.id == sid
        assert sec.tool == tool
        assert sec.empty_policy == empty_policy

    # Single pass configs
    assert isinstance(configs["reflection"], SinglePassConfig)
    assert configs["reflection"].input.text_field == "source"
    assert isinstance(configs["daily_review"], SinglePassConfig)
    assert configs["daily_review"].input.text_field == "market_snapshot"
    assert isinstance(configs["news_digest"], SinglePassConfig)
    assert configs["news_digest"].input.text_field == "news_snapshot"


def test_production_skills_contain_neutral_boundaries() -> None:
    # 检查 debate 裁判与角色
    referee_path = BUILTIN_SKILLS_DIR / "debate" / "references" / "referee.md"
    assert referee_path.exists()
    ref_text = referee_path.read_text(encoding="utf-8")
    assert "分歧点" in ref_text
    assert "验证清单" in ref_text
    assert "绝对不要" in ref_text or "不给出" in ref_text
    for winner_word in ("谁赢了", "哪方获胜", "多方胜", "空方胜", "判定胜者"):
        assert winner_word not in ref_text

    # 检查 reflection-audit
    reflect_path = BUILTIN_SKILLS_DIR / "reflection-audit" / "SKILL.md"
    assert reflect_path.exists()
    reflect_text = reflect_path.read_text(encoding="utf-8")
    assert "审计已有文本" in reflect_text or "审计已有" in reflect_text
    assert "验证清单" in reflect_text
    assert "不要给出你自己的投资判断" in reflect_text

    # 检查 market-review
    review_path = BUILTIN_SKILLS_DIR / "market-review" / "SKILL.md"
    assert review_path.exists()
    review_text = review_path.read_text(encoding="utf-8")
    assert "不推荐买卖" in review_text

    # 检查 news-digest
    news_path = BUILTIN_SKILLS_DIR / "news-digest" / "SKILL.md"
    assert news_path.exists()
    news_text = news_path.read_text(encoding="utf-8")
    assert "不推荐买卖" in news_text


def test_reject_variant_with_duplicate_stages() -> None:
    doc = {
        "schema_version": 1,
        "config_version": 1,
        "id": "dup_variant",
        "kind": "staged_research",
        "result_stage": "referee",
        "input": {"code": {"type": "string", "pattern": "^[0-9]{6}$"}},
        "dossier": {
            "section_chars": 1000,
            "dossier_summary_chars": 2000,
            "sections": [{"id": "quote", "tool": "query_quote", "empty_policy": "gap_if_empty"}],
        },
        "stages": [
            {"id": "bull", "label": "多方", "skill": "builtin/debate", "instruction": "references/bull.md", "context": ["dossier"], "output_chars": 1000, "context_chars": 2000},
            {"id": "referee", "label": "主持", "skill": "builtin/debate", "instruction": "references/referee.md", "context": ["stages"], "output_chars": 1000, "context_chars": 2000},
        ],
        "variants": {
            "standard": ["bull", "bull", "referee"],
        },
    }
    with pytest.raises(WorkflowConfigError, match="存在重复阶段 ID"):
        validate_workflow_config(doc, workflow_id="dup_variant")


def test_reject_nonexistent_skill_file(tmp_path: Path) -> None:
    doc = {
        "schema_version": 1,
        "config_version": 1,
        "id": "missing_skill_wf",
        "kind": "single_pass",
        "skill": "builtin/nonexistent-skill-xyz",
        "instruction": "SKILL.md",
        "input": {"text_field": "source", "max_chars": 1000},
    }
    with pytest.raises(WorkflowConfigError, match="引用的内置技能指令文件不存在"):
        validate_workflow_config(doc, workflow_id="missing_skill_wf")


def test_reject_partial_input_reference() -> None:
    doc = yaml.safe_load((PRODUCTION_WORKFLOWS_DIR / "debate.yaml").read_text(encoding="utf-8"))
    doc["id"] = "partial_ref"
    doc["dossier"]["sections"][0]["args"]["codes"] = ["prefix-${input.code}"]

    with pytest.raises(WorkflowConfigError, match="input.*引用|完整引用"):
        validate_workflow_config(doc, workflow_id="partial_ref")


def test_reject_invalid_context_reference() -> None:
    doc = yaml.safe_load((PRODUCTION_WORKFLOWS_DIR / "debate.yaml").read_text(encoding="utf-8"))
    doc["id"] = "invalid_context"
    doc["stages"][0]["context"] = ["stage.future"]

    with pytest.raises(WorkflowConfigError, match="stages.0.context"):
        validate_workflow_config(doc, workflow_id="invalid_context")


def test_reject_empty_variant() -> None:
    doc = yaml.safe_load((PRODUCTION_WORKFLOWS_DIR / "debate.yaml").read_text(encoding="utf-8"))
    doc["id"] = "empty_variant"
    doc["variants"]["standard"] = []

    with pytest.raises(WorkflowConfigError, match="variants.standard"):
        validate_workflow_config(doc, workflow_id="empty_variant")


def test_validate_staged_input_accepts_matching_value() -> None:
    cfg = load_workflow_config_from_file(
        PRODUCTION_WORKFLOWS_DIR / "debate.yaml",
        builtin_skills_root=BUILTIN_SKILLS_DIR,
    )
    assert isinstance(cfg, StagedResearchConfig)

    validate_staged_input(cfg, {"code": "600519"})


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ([], "必须是对象"),
        ({}, "input.code.*缺失"),
        ({"code": 600519}, "input.code.*类型错误"),
        ({"code": "abc"}, "input.code.*格式不符合"),
    ],
)
def test_validate_staged_input_rejects_invalid_values(
    values: object,
    message: str,
) -> None:
    cfg = load_workflow_config_from_file(
        PRODUCTION_WORKFLOWS_DIR / "debate.yaml",
        builtin_skills_root=BUILTIN_SKILLS_DIR,
    )
    assert isinstance(cfg, StagedResearchConfig)

    with pytest.raises(ValueError, match=message):
        validate_staged_input(cfg, values)  # type: ignore[arg-type]


def test_configuration_errors_do_not_echo_sensitive_input_values() -> None:
    secret = "Basic dXNlcjpwYXNz secret-input-value"
    doc = {
        "schema_version": 1,
        "config_version": 1,
        "id": "safe_error",
        "kind": "single_pass",
        "skill": "builtin/reflection-audit",
        "instruction": "SKILL.md",
        "input": {"text_field": "source", "max_chars": 1000, "secret": secret},
    }

    with pytest.raises(WorkflowConfigError) as exc_info:
        validate_workflow_config(doc, workflow_id="safe_error")

    message = str(exc_info.value)
    assert "input.secret" in message
    assert secret not in message
    assert "Basic" not in message
    assert "input_value" not in message
    assert "validation error" not in message.lower()


def test_configuration_error_traceback_does_not_chain_sensitive_validation_error() -> None:
    secret = "Basic dHJhY2U6c2VjcmV0 traceback-secret"
    doc = {
        "schema_version": 1,
        "config_version": 1,
        "id": "safe_traceback",
        "kind": "single_pass",
        "skill": "builtin/reflection-audit",
        "instruction": "SKILL.md",
        "input": {"text_field": "source", "max_chars": 1000, "secret": secret},
    }

    try:
        validate_workflow_config(doc, workflow_id="safe_traceback")
    except WorkflowConfigError:
        formatted = traceback.format_exc()
    else:
        pytest.fail("应拒绝未知配置字段")

    assert secret not in formatted
    assert "Basic" not in formatted
    assert "input_value" not in formatted


@pytest.mark.parametrize(
    "skill_content",
    [
        "---\nname: wrong-name\ndescription: 错误的 Skill 名称\n---\n\n# 内容\n",
        "---\nname: reflection-audit\n---\n\n# 缺少 description\n",
    ],
)
def test_malformed_builtin_skill_frontmatter_blocks_workflow_loading(
    tmp_path: Path,
    skill_content: str,
) -> None:
    skill_dir = tmp_path / "reflection-audit"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(skill_content, encoding="utf-8")
    doc = {
        "schema_version": 1,
        "config_version": 1,
        "id": "bad_skill",
        "kind": "single_pass",
        "skill": "builtin/reflection-audit",
        "instruction": "SKILL.md",
        "input": {"text_field": "source", "max_chars": 1000},
    }

    with pytest.raises(WorkflowConfigError, match="Skill.*frontmatter|Skill.*元数据"):
        validate_workflow_config(doc, workflow_id="bad_skill", builtin_skills_root=tmp_path)


# debate 底稿固定契约：13 项 (section_id, tool, args, empty_policy, 执行策略)。
# 加载器运行时只做通用 schema 校验，不比对契约；防回归不变量由下面的契约测试兜底。
DEBATE_DOSSIER_CONTRACT = [
    ("quote", "query_quote", {"codes": ["${input.code}"]}, "gap_if_empty", "parallel_safe"),
    ("valuation", "query_valuation", {"code": "${input.code}"}, "gap_if_empty", "eastmoney_serial"),
    ("valuation_percentile", "query_valuation_percentile", {"code": "${input.code}"}, "gap_if_empty", "parallel_safe"),
    ("financials", "query_financials", {"code": "${input.code}"}, "gap_if_empty", "parallel_safe"),
    ("kline", "query_kline", {"code": "${input.code}", "count": 60}, "gap_if_empty", "parallel_safe"),
    ("fund_flow", "query_fund_flow", {"code": "${input.code}", "days": 5}, "gap_if_empty", "eastmoney_serial"),
    ("margin", "query_margin", {"code": "${input.code}"}, "allow_no_record", "eastmoney_serial"),
    ("holders", "query_holders", {"code": "${input.code}"}, "allow_no_record", "eastmoney_serial"),
    ("announcements", "query_announcements", {"code": "${input.code}"}, "allow_no_record", "parallel_safe"),
    ("lockup", "query_lockup", {"code": "${input.code}"}, "allow_no_record", "eastmoney_serial"),
    ("concepts", "query_concepts", {"code": "${input.code}"}, "gap_if_empty", "eastmoney_serial"),
    ("reports", "query_reports", {"code": "${input.code}"}, "allow_no_record", "parallel_safe"),
    ("news", "query_news", {"code": "${input.code}"}, "allow_no_record", "parallel_safe"),
]


def test_debate_tool_contract_matches_executor_policies_and_yaml_arguments() -> None:
    debate = load_all_production_workflows(PRODUCTION_WORKFLOWS_DIR, BUILTIN_SKILLS_DIR)["debate"]
    assert isinstance(debate, StagedResearchConfig)
    expected = DEBATE_DOSSIER_CONTRACT
    actual = [
        (section.id, section.tool, section.args, section.empty_policy, tool_policy(section.tool).value)
        for section in debate.dossier.sections
    ]
    assert actual == expected
    expected_parallel = {tool for _, tool, _, _, policy in expected if policy == "parallel_safe"}
    expected_serial = {tool for _, tool, _, _, policy in expected if policy == "eastmoney_serial"}
    assert expected_parallel == set(PARALLEL_SAFE_TOOLS)
    assert expected_serial == set(EASTMONEY_SERIAL_TOOLS)
    assert all(tool_policy(tool) is ToolExecutionPolicy.PARALLEL_SAFE for tool in expected_parallel)
    assert all(tool_policy(tool) is ToolExecutionPolicy.EASTMONEY_SERIAL for tool in expected_serial)


@pytest.mark.parametrize(
    ("section_index", "field", "value"),
    [
        (4, "args", {"code": "${input.code}", "count": 61}),
        (6, "empty_policy", "gap_if_empty"),
    ],
)
def test_debate_dossier_contract_detects_drift(
    section_index: int,
    field: str,
    value: object,
) -> None:
    doc = yaml.safe_load((PRODUCTION_WORKFLOWS_DIR / "debate.yaml").read_text(encoding="utf-8"))
    doc["dossier"]["sections"][section_index][field] = value

    # 通用 schema 校验不再拦截契约漂移（加载应成功）；漂移必须能被
    # 上面的契约比对发现，否则防回归就失去了兜底。
    cfg = validate_workflow_config(doc, workflow_id="debate")
    assert isinstance(cfg, StagedResearchConfig)
    actual = [
        (section.id, section.tool, section.args, section.empty_policy, tool_policy(section.tool).value)
        for section in cfg.dossier.sections
    ]
    assert actual != DEBATE_DOSSIER_CONTRACT


@pytest.mark.parametrize("invalid_value", [True, "1000"])
def test_single_pass_numeric_fields_are_strict(invalid_value: object) -> None:
    doc = {
        "schema_version": 1,
        "config_version": 1,
        "id": "strict_number",
        "kind": "single_pass",
        "skill": "builtin/reflection-audit",
        "instruction": "SKILL.md",
        "input": {"text_field": "source", "max_chars": invalid_value},
    }
    with pytest.raises(WorkflowConfigError, match="input.max_chars"):
        validate_workflow_config(doc, workflow_id="strict_number")


@pytest.mark.parametrize("invalid_value", [True, "1200"])
def test_staged_numeric_fields_are_strict(invalid_value: object) -> None:
    doc = yaml.safe_load((PRODUCTION_WORKFLOWS_DIR / "debate.yaml").read_text(encoding="utf-8"))
    doc["id"] = "strict_stage_number"
    doc["stages"][0]["output_chars"] = invalid_value

    with pytest.raises(WorkflowConfigError, match="stages.0.output_chars"):
        validate_workflow_config(doc, workflow_id="strict_stage_number")


def test_staged_input_pattern_is_compiled_at_load_time() -> None:
    doc = yaml.safe_load((PRODUCTION_WORKFLOWS_DIR / "debate.yaml").read_text(encoding="utf-8"))
    doc["id"] = "bad_pattern"
    doc["input"]["code"]["pattern"] = "["

    with pytest.raises(WorkflowConfigError, match="input.code.pattern"):
        validate_workflow_config(doc, workflow_id="bad_pattern")
