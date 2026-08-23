"""Task 5：隔离 LangGraph Server 子进程生命周期（session 级 fixture 复用）。

在临时目录内复制夹具图 / 配置 / Skills，写入独立 0600 设置文件（含 stdio
MCP 绝对路径），随后以 `langgraph dev --no-reload` 启动并轮询 /ok 直到就绪。
绝不读取或复制真实 ~/.vibe-research/ 设置。
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

BACKEND = Path(__file__).parents[2]
E2E = BACKEND / "tests" / "agent_e2e"

READY_TIMEOUT_SECONDS = 90.0
STOP_TIMEOUT_SECONDS = 10.0


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class LangGraphServerHarness:
    """一个临时目录 + 一个固定端口的可重启 LangGraph Server。"""

    def __init__(self, cwd: Path) -> None:
        self.cwd = cwd
        self.frontend_origin = "http://127.0.0.1:5873"
        self.port = _free_loopback_port()
        self._process: subprocess.Popen | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def _prepare_files(self) -> None:
        shutil.copy2(E2E / "server_graph.py", self.cwd / "server_graph.py")
        shutil.copy2(E2E / "server_langgraph.json", self.cwd / "server_langgraph.json")
        shutil.copytree(E2E / "skills", self.cwd / "skills", dirs_exist_ok=True)
        settings = {
            "model": {
                "provider": "openai",
                "name": "test-model",
                "apiKey": "test-secret-never-send",
                "baseURL": "https://example.invalid/v1",
                "temperature": 0.2,
            },
            "skills": {"path": str(self.cwd / "skills")},
            "mcpServers": {
                "fixture": {
                    "transport": "stdio",
                    "command": sys.executable,
                    "args": [str(BACKEND / "tests/agent/fake_mcp_server.py")],
                    "env": {},
                },
            },
        }
        path = self.cwd / "settings.json"
        path.write_text(json.dumps(settings, ensure_ascii=False), encoding="utf-8")
        path.chmod(0o600)

    def start(self) -> None:
        if self._process is not None and self._process.poll() is None:
            return
        self._prepare_files()
        command = [
            str(BACKEND / ".venv/bin/langgraph"), "dev",
            "--config", str(self.cwd / "server_langgraph.json"),
            "--host", "127.0.0.1", "--port", str(self.port),
            "--no-browser", "--no-reload",
        ]
        env = {
            **os.environ,
            "PYTHONPATH": str(BACKEND),
            "VR_AGENT_SETTINGS": str(self.cwd / "settings.json"),
        }
        self._process = subprocess.Popen(
            command, cwd=self.cwd, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + READY_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                raise RuntimeError(f"LangGraph Server 提前退出：exit={self._process.returncode}")
            try:
                if httpx.get(f"{self.url}/ok", timeout=2).status_code == 200:
                    return
                if httpx.get(f"{self.url}/docs", timeout=2, follow_redirects=True).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.3)
        self.stop()
        raise RuntimeError("LangGraph Server 启动超时")

    def stop(self) -> None:
        process, self._process = self._process, None
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=STOP_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=STOP_TIMEOUT_SECONDS)
