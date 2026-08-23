"""pytest 配置：把 backend 目录加进 sys.path，注册 live 标记，隔离用户数据目录。"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

# 用户数据隔离：portfolio / myreports 在 import 时按 VR_DATA_DIR / VR_REPORTS_DIR 固化路径，
# 必须赶在任何测试模块 import app 之前指到临时目录——否则持仓 CRUD 类测试会增删真实
# ~/.vibe-research/ 里的用户数据（比如把用户真实持有的 600519 合并后删掉）。
_TEST_DATA_DIR = tempfile.mkdtemp(prefix="vr-test-data-")
os.environ["VR_DATA_DIR"] = _TEST_DATA_DIR
os.environ["VR_REPORTS_DIR"] = os.path.join(_TEST_DATA_DIR, "myreports")

# Agent 静态设置隔离：agent.settings 在 import/加载时按 VR_AGENT_SETTINGS 固化默认路径，
# 同样必须在任何测试模块 import agent 之前指向临时文件，绝不读取真实 ~/.vibe-research/。
_TEST_AGENT_DIR = Path(_TEST_DATA_DIR) / "agent-fixtures"
_TEST_SKILLS_DIR = _TEST_AGENT_DIR / "skills"
_TEST_SKILLS_DIR.mkdir(parents=True)
_TEST_SETTINGS = _TEST_AGENT_DIR / "settings.json"
_TEST_SETTINGS.write_text(json.dumps({
    "model": {
        "provider": "openai",
        "name": "test-model",
        "apiKey": "test-secret-never-send",
        "baseURL": "https://example.invalid/v1",
        "temperature": 0.2,
    },
    "skills": {"path": str(_TEST_SKILLS_DIR)},
    "mcpServers": {},
}), encoding="utf-8")
os.chmod(_TEST_SETTINGS, 0o600)
os.environ["VR_AGENT_SETTINGS"] = str(_TEST_SETTINGS)


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "live: 打真实数据源的网络冒烟测（会联网、可能受上游/限流影响；默认可 -m 'not live' 跳过）",
    )
