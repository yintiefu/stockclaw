"""Task 1：干净 venv 安装 Agent 运行时契约的 live 测试（需要网络，默认跳过）。"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.agent.test_dependency_compat import EXPECTED_AGENT_VERSIONS


@pytest.mark.live
def test_clean_venv_installs_exact_agent_contract(tmp_path: Path) -> None:
    assert sys.version_info >= (3, 11)
    backend = Path(__file__).parents[2]
    venv = tmp_path / "venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
    pip = venv / "bin" / "pip"
    python = venv / "bin" / "python"
    subprocess.run([
        str(pip), "install", "-r", str(backend / "requirements.txt"),
        "-i", "https://pypi.tuna.tsinghua.edu.cn/simple",
    ], check=True)
    subprocess.run([str(pip), "check"], check=True)
    code = (
        "import json; from importlib.metadata import version; "
        f"names={list(EXPECTED_AGENT_VERSIONS)!r}; "
        "print(json.dumps({n: version(n) for n in names}))"
    )
    installed = json.loads(subprocess.check_output([str(python), "-c", code], text=True))
    assert installed == EXPECTED_AGENT_VERSIONS
    subprocess.run([
        str(python), "-c",
        "import deepagents, langchain, langgraph, langchain_mcp_adapters, mootdx",
    ], check=True)
