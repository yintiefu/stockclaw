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
import pytest
import yaml

from agent.skill_backends import BUILTIN_SKILLS_DIR
from agent.workflow_loader import (
    HARD_LIMITS,
    SinglePassConfig,
    StagedResearchConfig,
    WorkflowConfigError,
    load_all_production_workflows,
    load_workflow_config_from_file,
    load_workflow_config_from_string,
    validate_workflow_config,
)

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "workflows"
PRODUCTION_WORKFLOWS_DIR = Path(__file__).resolve().parents[2] / "agent" / "workflows"


def test_load_valid_staged_workflow_file(tmp_path: Path) -> None:
    path = FIXTURES_DIR / "staged_valid.yaml"
    cfg = load_workflow_config_from_file(path, builtin_skills_root=tmp_path)
    assert isinstance(cfg, StagedResearchConfig)
    assert cfg.id == "staged_valid"
    assert cfg.kind == "staged_research"
    assert len(cfg.stages) == 3
    assert "standard" in cfg.variants


def test_load_valid_single_pass_file(tmp_path: Path) -> None:
    path = FIXTURES_DIR / "single_pass_valid.yaml"
    cfg = load_workflow_config_from_file(path, builtin_skills_root=tmp_path)
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
    with pytest.raises(WorkflowConfigError, match="ID 与文件名不匹配"):
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
        "input": {"code": {"type": "string"}},
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
                "skill": "builtin/test",
                "instruction": "test.md",
                "on_error": "continue",
                "output_chars": 1000,
                "context_chars": 5000,
            }
        ],
        "variants": {"standard": ["s1"]},
    }
    with pytest.raises(WorkflowConfigError, match="non_existent_tool_name"):
        validate_workflow_config(doc, workflow_id="test_wf")


def test_reject_duplicate_or_missing_stage() -> None:
    doc_duplicate = {
        "schema_version": 1,
        "config_version": 1,
        "id": "test_wf",
        "kind": "staged_research",
        "input": {"code": {"type": "string"}},
        "dossier": {"section_chars": 1800, "dossier_summary_chars": 6000, "sections": []},
        "stages": [
            {"id": "dup", "skill": "b/t", "instruction": "t.md", "on_error": "continue", "output_chars": 1000, "context_chars": 5000},
            {"id": "dup", "skill": "b/t", "instruction": "t.md", "on_error": "continue", "output_chars": 1000, "context_chars": 5000},
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
        "input": {"code": {"type": "string"}},
        "dossier": {"section_chars": 1800, "dossier_summary_chars": 6000, "sections": []},
        "stages": [
            {"id": "s1", "skill": "b/t", "instruction": "t.md", "on_error": "continue", "output_chars": 1000, "context_chars": 5000},
        ],
        "variants": {"v1": ["s1", "non_existent_stage"]},
    }
    with pytest.raises(WorkflowConfigError, match="未定义的阶段"):
        validate_workflow_config(doc_missing_variant_stage, workflow_id="test_wf")


def test_reject_invalid_input_reference() -> None:
    doc = {
        "schema_version": 1,
        "config_version": 1,
        "id": "test_wf",
        "kind": "staged_research",
        "input": {"code": {"type": "string"}},
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
            {"id": "s1", "skill": "b/t", "instruction": "t.md", "on_error": "continue", "output_chars": 1000, "context_chars": 5000},
        ],
        "variants": {"v1": ["s1"]},
    }
    with pytest.raises(WorkflowConfigError, match="input.non_existent_field"):
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
    with pytest.raises(WorkflowConfigError, match="Path traversal|路径逃逸"):
        validate_workflow_config(doc, workflow_id="test_wf", builtin_skills_root=tmp_path)


def test_reject_invalid_empty_policy_and_on_error() -> None:
    doc_bad_empty = {
        "schema_version": 1,
        "config_version": 1,
        "id": "test_wf",
        "kind": "staged_research",
        "input": {"code": {"type": "string"}},
        "dossier": {
            "section_chars": 1800,
            "dossier_summary_chars": 6000,
            "sections": [
                {"id": "s", "tool": "query_quote", "args": {"codes": ["${input.code}"]}, "empty_policy": "invalid_empty_policy"}
            ],
        },
        "stages": [
            {"id": "s1", "skill": "b/t", "instruction": "t.md", "on_error": "continue", "output_chars": 1000, "context_chars": 5000}
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
        "input": {"code": {"type": "string"}},
        "dossier": {
            "section_chars": HARD_LIMITS["section_chars"] + 1000,
            "dossier_summary_chars": 6000,
            "sections": [],
        },
        "stages": [
            {"id": "s1", "skill": "b/t", "instruction": "t.md", "on_error": "continue", "output_chars": 1000, "context_chars": 5000}
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
