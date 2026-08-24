"""Task 12：浏览器 E2E 的隔离 LangGraph Server 启动器。

要求 VR_E2E_ROOT（独立临时根，绝不与真实 ~/.vibe-research 重叠）与
VR_E2E_LANGGRAPH_PORT；复制浏览器图/配置/共享 Skills、写入 0600 测试设置
（无效模型凭据 + stdio 假 MCP 绝对路径），然后 execve 到固定版本的
langgraph CLI。不引入第四个网络服务。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
E2E = BACKEND / "tests" / "agent_e2e"

root_value = os.environ.get("VR_E2E_ROOT", "").strip()
if not root_value:
    raise SystemExit("VR_E2E_ROOT 未设置：浏览器 E2E 必须使用独立临时数据根")
root = Path(root_value).resolve()
home_root = (Path.home() / ".vibe-research").resolve()
if root == home_root or home_root in root.parents or root in home_root.parents:
    raise SystemExit(f"VR_E2E_ROOT 与真实用户数据根重叠，拒绝启动：{root}")

port = os.environ.get("VR_E2E_LANGGRAPH_PORT", "").strip()
if not port.isdigit():
    raise SystemExit("VR_E2E_LANGGRAPH_PORT 未设置或不是数字")

root.mkdir(parents=True, exist_ok=True)
shutil.copy2(E2E / "graph.py", root / "graph.py")
shutil.copy2(E2E / "langgraph.json", root / "langgraph.json")
shutil.copytree(E2E / "skills", root / "skills", dirs_exist_ok=True)

settings_path = root / "settings.json"
settings_path.write_text(json.dumps({
    "model": {
        "provider": "openai",
        "name": "test-model",
        "apiKey": "e2e-never-contact-provider",
        "baseURL": "https://example.invalid/v1",
        "temperature": 0.2,
    },
    "skills": {"path": str(root / "skills")},
    "mcpServers": {
        "fixture": {
            "transport": "stdio",
            "command": sys.executable,
            "args": [str(BACKEND / "tests/agent/fake_mcp_server.py")],
            "env": {},
        },
    },
    # E2E 断言需要 trace 事件；目录必须落在隔离临时根内，绝不写真实用户目录
    "trace": {"enabled": True, "dir": str(root / "traces")},
}, ensure_ascii=False), encoding="utf-8")
settings_path.chmod(0o600)

args = [
    str(BACKEND / ".venv/bin/langgraph"), "dev",
    "--config", str(root / "langgraph.json"),
    "--host", "127.0.0.1", "--port", port,
    "--no-browser", "--no-reload",
]
# 图路径 ./graph.py 按进程 cwd 解析（非配置文件目录）：切到隔离根后再 exec
os.chdir(root)
env = {**os.environ, "PYTHONPATH": str(BACKEND), "VR_AGENT_SETTINGS": str(settings_path)}
os.execve(args[0], args, env)
