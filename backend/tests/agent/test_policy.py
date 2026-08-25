from __future__ import annotations

from pathlib import Path
import yaml

from agent.policy import fixed_system_policy
from agent.skill_backends import build_skill_backend


def test_policy_keeps_every_product_red_line() -> None:
    text = fixed_system_policy("Agent 工作台")
    for phrase in ("不推荐买卖", "不预测涨跌", "不给目标价", "不评级", "不排名", "不给交易时机"):
        assert phrase in text
    assert "Agent 工作台" in text


def test_policy_handles_empty_context() -> None:
    text = fixed_system_policy("")
    assert "（无）" in text


def test_skill_backend_exposes_separate_read_only_namespaces(tmp_path: Path) -> None:
    builtin = tmp_path / "builtin"
    user = tmp_path / "user"
    builtin.mkdir()
    user.mkdir()
    backend = build_skill_backend(builtin, user)
    assert set(backend.routes) == {"/builtin/", "/user/"}
    assert backend.routes["/builtin/"].virtual_mode is True
    assert backend.routes["/user/"].virtual_mode is True


def test_stock_analysis_skill_contains_dimensions_and_boundary() -> None:
    skill_file = Path(__file__).resolve().parents[2] / "agent" / "builtin_skills" / "stock-analysis" / "SKILL.md"
    assert skill_file.exists(), f"Skill file {skill_file} must exist"
    content = skill_file.read_text(encoding="utf-8")
    assert content.startswith("---")
    parts = content.split("---", 2)
    assert len(parts) >= 3
    meta = yaml.safe_load(parts[1])
    assert meta.get("name") == "stock-analysis"
    assert meta.get("description")

    # 5 dimensions
    for dim in ("估值", "资金面", "财报质量", "行业景气", "事件催化与风险"):
        assert dim in content

    # neutral boundaries
    for boundary in ("不给买卖结论", "只陈述客观事实", "不推荐买卖"):
        assert boundary in content or "不给买卖" in content
