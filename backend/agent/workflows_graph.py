"""导出四个专用工作流图：debate / reflection / daily_review / news_digest。

在进程启动阶段加载配置并静态编译导出，供 langgraph.json 注册。
"""
from __future__ import annotations

from pathlib import Path

from agent.model_factory import build_model
from agent.settings import load_agent_settings
from agent.skill_backends import BUILTIN_SKILLS_DIR
from agent.workflow_builder import build_workflow_graph
from agent.workflow_loader import load_all_production_workflows

WORKFLOWS_DIR = Path(__file__).resolve().parent / "workflows"

# 加载全部生产配置
_CONFIGS = load_all_production_workflows(WORKFLOWS_DIR, BUILTIN_SKILLS_DIR)
_SETTINGS = load_agent_settings()
_MODEL = build_model(_SETTINGS)

debate_graph = build_workflow_graph(_CONFIGS["debate"], model=_MODEL, builtin_skills_root=BUILTIN_SKILLS_DIR)
reflection_graph = build_workflow_graph(_CONFIGS["reflection"], model=_MODEL, builtin_skills_root=BUILTIN_SKILLS_DIR)
daily_review_graph = build_workflow_graph(_CONFIGS["daily_review"], model=_MODEL, builtin_skills_root=BUILTIN_SKILLS_DIR)
news_digest_graph = build_workflow_graph(_CONFIGS["news_digest"], model=_MODEL, builtin_skills_root=BUILTIN_SKILLS_DIR)
