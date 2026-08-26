# Native LangGraph Agent Workspace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Agent workspace's custom FastAPI/AG-UI runtime with assistant-ui connected directly to a local LangGraph Server, while preserving objective research tools, static MCP/Skills configuration, per-call MCP approval, thread persistence, and Eastmoney serialization.

**Architecture:** FastAPI remains the data and legacy-AI service on `127.0.0.1:8900`; a separately launched LangGraph Server on `127.0.0.1:2024` imports the same `tools.py` and owns Agent threads, runs, checkpoints, interrupts, and persistence. The React Agent page uses `useStreamRuntime` plus a thin LangGraph SDK `RemoteThreadListAdapter`; model, MCP, and Skills configuration is loaded once from a local JSON file when the Agent Server imports its graph.

**Tech Stack:** Python 3.11+, LangChain 1.3.15, LangGraph 1.2.11 and LangGraph CLI 0.4.31, Deep Agents 0.7.7, langchain-mcp-adapters 0.3.2, Pydantic 2, React 19, TypeScript, assistant-ui 0.15.16, `@assistant-ui/react-langchain` 0.0.27, LangGraph JS SDK 1.9.31, pytest, Vitest, Playwright

---

## Scope And Invariants

Implement `docs/superpowers/specs/2026-08-23-native-langgraph-agent-workspace-design.md` only. Legacy `/api/chat`, `/api/debate`, and `/api/reflect` remain on FastAPI and keep their request-level model configuration and SSRF behavior. Agent artifacts, provenance, budget governance, Inspector, MCP/Skill management UI, session allowances, and old Agent JSON session migration are intentionally removed.

Keep these invariants green after every task:

- `chat.SYSTEM_PROMPT.format(context="Agent 工作台")` remains the fixed Agent system prompt. No new prompt may recommend buying/selling, predict price direction, provide target prices/ratings/rankings, or time trades.
- `tools.py` remains the sole definition and implementation source for built-in research tools; `chat.py`, `debate.py`, and `mcp_server.py` remain consumers of it.
- Every built-in LangChain tool acquires one process-wide `asyncio.Lock` before entering its worker thread. `ChatOpenAI` also sets `parallel_tool_calls=False`; neither defense may be removed.
- Agent settings, API keys, MCP headers, and MCP env values never enter thread metadata, Graph state, checkpoints, logs, frontend requests, or error responses.
- Tests set `VR_AGENT_SETTINGS`, `VR_DATA_DIR`, and `VR_REPORTS_DIR` before importing Agent or FastAPI modules. Tests never read or mutate `~/.vibe-research/`.
- LangGraph Server binds only to `127.0.0.1`; CORS blocks hostile browser reads but does not prevent simple-request blind writes. Tests must preserve that exact boundary.
- `backend/agent/ssrf.py`, `backend/tests/agent/test_ssrf.py`, `backend/tests/agent/fakes.py`, `backend/tests/agent/fake_mcp_server.py`, `frontend/src/lib/agents.ts`, `frontend/src/lib/ndjson.ts`, and shared assistant-ui Thread/Markdown/tool components are protected from cleanup.

## File Map

### Backend Production

- `backend/requirements.txt`: the complete, explicit Agent runtime version contract; no AG-UI packages.
- `backend/requirements-dev.txt`: pytest-only development dependencies; LangGraph CLI stays in runtime requirements.
- `backend/agent/settings.py`: static JSON location, Pydantic validation, secret-safe Chinese errors, POSIX permission warning, and MCP connection conversion.
- `backend/agent/tool_registry.py`: JSON-schema-to-LangChain conversion, JSON result encoding, worker-thread dispatch, and the single process-wide built-in lock.
- `backend/agent/graph.py`: one-time model/MCP/Skills/HITL assembly and exported compiled graph.
- `backend/agent/ssrf.py`: unchanged shared SSRF implementation for legacy chat only.
- `backend/langgraph.json`: production graph registration and local frontend CORS origin.
- `backend/app.py`: remove only Agent router and lifecycle wiring; all legacy routes remain.
- `backend/.env.example`, `backend/.gitignore`: settings override documentation and `.langgraph_api/` ignore.

### Backend Tests

- `backend/conftest.py`: create isolated Agent settings and Skills roots before test collection imports.
- `backend/tests/agent/test_dependency_compat.py`: retain mootdx checks and add exact installed Agent versions.
- `backend/tests/agent/test_dependency_install.py`: live-only clean-venv installation and resolver contract.
- `backend/tests/agent/test_settings.py`: valid/invalid settings, path errors, permission warnings, and secret redaction.
- `backend/tests/agent/test_tool_registry.py`: schema parity, structured errors, worker-thread execution, shared lock, and Eastmoney spacing.
- `backend/tests/agent/test_graph.py`: model parameters, middleware/tool composition, conflicts, neutral prompt, Skills read-only surface, and offline scripted-model behavior.
- `backend/tests/agent/test_langgraph_server.py`: real `langgraph dev` boot, native thread/run/HITL, process restart recovery, and layered CORS behavior.
- `backend/tests/agent/server_harness.py`: session-scoped isolated LangGraph subprocess lifecycle used by integration tests.
- `backend/tests/agent_e2e/server_graph.py` and `server_langgraph.json`: deterministic 16-reply graph/config used only by the backend server harness.
- `backend/tests/agent_e2e/graph.py`, `langgraph.json`, and `start_langgraph.py`: deterministic browser-test graph and isolated startup helper.
- `backend/tests/agent_e2e/skills/research/SKILL.md`: shared Skill fixture copied by both backend server and browser harnesses.
- `backend/tests/agent/fakes.py`, `fake_mcp_server.py`: retained and minimally adjusted fixtures; no second scripted model class.

### Frontend

- `frontend/package.json`, `frontend/package-lock.json`: replace AG-UI dependencies with the pinned LangChain runtime and SDK.
- `frontend/vite.config.ts`: add `/agent-api` proxy to `127.0.0.1:2024` with prefix removal; retain `/api` proxy.
- `frontend/src/lib/agent/thread-adapter.ts`: all eight required `RemoteThreadListAdapter` methods backed by LangGraph SDK.
- `frontend/src/lib/agent/thread-adapter.test.ts`: exact adapter contract, metadata merge, title generation, and archive mapping.
- `frontend/src/lib/agent/runtime.tsx`: minimal `useStreamRuntime` provider with fixed assistant/API and optional settled-ID observation.
- `frontend/src/lib/agent/runtime.test.tsx`: option contract, settled-ID callback forwarding, and no model-key transmission.
- `frontend/src/lib/agent/approval.ts`: parse native LangChain HITL payloads and build ordered decisions.
- `frontend/src/components/agent/ApprovalPanel.tsx`: approve/reject every action and resume once with `{ decisions }`.
- `frontend/src/components/agent/AgentThreadList.tsx`: assistant-ui thread-list primitives for create/switch/rename/delete.
- `frontend/src/components/agent/AgentWorkspace.tsx`: two-column thread/chat shell; remove settings and Inspector affordances.
- `frontend/src/components/agent/AgentThread.tsx`: retain shared Thread/tool rendering while removing Artifact/steer-away/retry contracts.
- `frontend/src/pages/Agent.tsx`: small composition root for runtime, thread list, chat, and approval.
- Matching focused `*.test.tsx` files and `frontend/src/pages/Agent.test.tsx`: native runtime interaction contracts.
- `frontend/playwright.config.ts`, `frontend/e2e/agent-workspace.spec.ts`: three isolated services and browser acceptance.

### Intentional Deletions

After the replacement path is green, delete the custom backend Agent control plane (`artifacts.py`, `capabilities.py`, `governance.py`, `mcp.py`, `models.py`, `policy.py`, `protocol.py`, `provenance.py`, `router.py`, `runs.py`, `runtime.py`, `skills.py`, `stores.py`, `tool_executor.py`) and their obsolete tests. Delete frontend Agent REST/history/workspace/model-config modules and management/Inspector/Artifact/source/steer-away components after the native page tests pass. Exact deletion commands are in Tasks 10 and 11 and deliberately preserve the protected files above.

## Commit Gates

Before each backend commit run the focused test named by the task, then:

```bash
cd backend && .venv/bin/pytest -m "not live"
```

Before each frontend commit run:

```bash
cd frontend && npm test && npm run test:unit && npm run build
```

At each phase boundary run `git diff --check`. Stage only files listed by the task; do not use `git add .` or directory-wide adds because the worktree contains unrelated untracked files.

## Phase 1: Dependency And Backend Contracts

### Task 1: Lock The Runtime Dependency Contract

**Files:**
- Modify: `backend/requirements.txt`
- Modify: `backend/tests/agent/test_dependency_compat.py`
- Create: `backend/tests/agent/test_dependency_install.py`

- [ ] **Step 1: Extend the installed-version test and add a live clean-install test**

Add these exact version assertions to `test_locked_mcp_stack_and_local_mootdx_are_importable`:

```python
EXPECTED_AGENT_VERSIONS = {
    "langgraph-cli": "0.4.31",
    "langgraph-api": "0.12.6",
    "langgraph-runtime-inmem": "0.32.6",
    "langgraph": "1.2.11",
    "httpx": "0.28.1",
    "langchain": "1.3.15",
    "langchain-core": "1.5.5",
    "langchain-openai": "1.5.1",
    "langchain-mcp-adapters": "0.3.2",
    "mcp": "1.26.0",
    "deepagents": "0.7.7",
    "langchain-anthropic": "1.5.4",
    "langchain-google-genai": "4.3.1",
}


def test_locked_mcp_stack_and_local_mootdx_are_importable():
    assert version("mootdx") == "0.11.7+vr1"
    assert {name: version(name) for name in EXPECTED_AGENT_VERSIONS} == EXPECTED_AGENT_VERSIONS
```

Create `test_dependency_install.py` as an explicitly networked test. It must invoke the current interpreter, not assume `python3.11` exists, and it must assert Python 3.11+ before creating the child venv:

```python
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
```

- [ ] **Step 2: Run the focused installed-version test and verify it fails**

Run: `cd backend && .venv/bin/pytest tests/agent/test_dependency_compat.py::test_locked_mcp_stack_and_local_mootdx_are_importable -q`

Expected: FAIL because CLI/API/runtime-inmem, Deep Agents, and the transitive provider pins are absent or differ.

- [ ] **Step 3: Dry-run the exact resolver change before editing requirements**

Run:

```bash
cd backend
.venv/bin/pip install --dry-run -r requirements.txt \
  'langgraph-cli[inmem]==0.4.31' langgraph-api==0.12.6 \
  langgraph-runtime-inmem==0.32.6 langchain-core==1.5.5 \
  deepagents==0.7.7 langchain-anthropic==1.5.4 \
  langchain-google-genai==4.3.1 \
  -i https://pypi.tuna.tsinghua.edu.cn/simple
```

Expected: exit 0; the report keeps `langchain==1.3.15`, `langchain-core==1.5.5`, `langgraph==1.2.11`, and `httpx==0.28.1`.

- [ ] **Step 4: Replace the Agent runtime group with the full explicit contract**

In `backend/requirements.txt`, keep core/data dependencies and the vendored mootdx line unchanged, delete the three `ag-ui-*` entries, and make the Agent group exactly:

```text
# Agent workspace: native LangGraph Server contract. Upgrade and test as one unit.
langgraph-cli[inmem]==0.4.31
langgraph-api==0.12.6
langgraph-runtime-inmem==0.32.6
langgraph==1.2.11
httpx==0.28.1
langchain==1.3.15
langchain-core==1.5.5
langchain-openai==1.5.1
langchain-mcp-adapters==0.3.2
mcp==1.26.0
deepagents==0.7.7
langchain-anthropic==1.5.4
langchain-google-genai==4.3.1
```

Keep `PyYAML`, `python-multipart`, and `commonmark` where existing legacy code still uses them; dependency cleanup for genuinely orphaned packages occurs in Task 13 after reference search.

- [ ] **Step 5: Install into the existing Python 3.13 venv and run contracts**

Run:

```bash
cd backend
.venv/bin/pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
.venv/bin/pip check
.venv/bin/pytest tests/agent/test_dependency_compat.py -q
.venv/bin/pytest -m "not live" -q
```

Expected: `pip check` and all offline tests PASS. Do not run the clean-venv test in the offline gate.

- [ ] **Step 6: Run the live clean resolver test once**

Run: `cd backend && .venv/bin/pytest tests/agent/test_dependency_install.py -m live -q`

Expected: PASS with an isolated install that reports every exact version above.

- [ ] **Step 7: Commit the dependency contract**

```bash
git add backend/requirements.txt backend/tests/agent/test_dependency_compat.py backend/tests/agent/test_dependency_install.py
git commit -m "build(agent): lock native LangGraph runtime"
```

### Task 2: Add Static Secret-Safe Agent Settings

**Files:**
- Create: `backend/agent/settings.py`
- Create: `backend/tests/agent/test_settings.py`
- Modify: `backend/conftest.py`

- [ ] **Step 1: Isolate Agent settings before any test module import**

Merge `json` and `Path` into the existing top-level import block in `backend/conftest.py`, then extend the module-level isolation immediately after `VR_REPORTS_DIR` is set:

```python
import json
import os
import sys
import tempfile
from pathlib import Path

# ... keep the existing sys.path and VR_DATA_DIR / VR_REPORTS_DIR setup ...

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
```

- [ ] **Step 2: Write failing settings tests**

Create tests for aliases, MCP conversion, path validation, malformed JSON, unsupported providers, permission warnings, and redaction. The redaction assertion must include the exact secret in the invalid input and prove it is absent from the public exception string:

```python
def test_load_settings_maps_aliases_and_mcp_transports(tmp_path, monkeypatch):
    skills = tmp_path / "skills"
    skills.mkdir()
    path = write_settings(tmp_path, skills=skills, mcp={
        "local": {"transport": "stdio", "command": "python", "args": ["server.py"], "env": {"TOKEN": "plain"}},
        "remote": {"transport": "http", "url": "https://example.test/mcp", "headers": {"Authorization": "Bearer plain"}},
    })
    monkeypatch.setenv("VR_AGENT_SETTINGS", str(path))
    settings = load_agent_settings()
    assert settings.model.api_key.get_secret_value() == "sk-test"
    assert settings.model.base_url == "https://example.test/v1"
    assert settings.mcp_connections()["remote"]["transport"] == "http"
    assert settings.mcp_connections()["local"]["env"]["TOKEN"] == "plain"


@pytest.mark.parametrize("payload, needle", [
    ("{", "不是合法 JSON"),
    (json.dumps({"model": {"provider": "anthropic"}}), "model.provider"),
])
def test_invalid_settings_report_path_and_field_without_secret(tmp_path, monkeypatch, payload, needle):
    path = tmp_path / "settings.json"
    path.write_text(payload.replace("anthropic", "anthropic-sk-private"), encoding="utf-8")
    monkeypatch.setenv("VR_AGENT_SETTINGS", str(path))
    with pytest.raises(AgentSettingsError) as caught:
        load_agent_settings()
    message = str(caught.value)
    assert str(path) in message
    assert needle in message
    assert "sk-private" not in message
```

Define `write_settings` in `test_settings.py` with defaults matching the assertions above; overrides must still produce a complete otherwise-valid document so each test has one reason to fail:

```python
def write_settings(tmp_path: Path, *, skills: Path, mcp: dict | None = None, model: dict | None = None) -> Path:
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({
        "model": model or {
            "provider": "openai",
            "name": "test-model",
            "apiKey": "sk-test",
            "baseURL": "https://example.test/v1",
            "temperature": 0.2,
        },
        "skills": {"path": str(skills)},
        "mcpServers": mcp or {},
    }), encoding="utf-8")
    path.chmod(0o600)
    return path
```

- [ ] **Step 3: Run the new tests and verify they fail**

Run: `cd backend && .venv/bin/pytest tests/agent/test_settings.py -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'agent.settings'`.

- [ ] **Step 4: Implement the strict Pydantic settings model and loader**

Implement these public types and functions in `backend/agent/settings.py`:

```python
from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError


class AgentSettingsError(RuntimeError):
    pass


class ModelSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    provider: Literal["openai"]
    name: str = Field(min_length=1)
    api_key: SecretStr = Field(alias="apiKey")
    base_url: str = Field(alias="baseURL", min_length=1)
    temperature: float = Field(default=0.2, ge=0, le=2)


class SkillsSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: Path


class StdioMcpSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    transport: Literal["stdio"]
    command: str = Field(min_length=1)
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)


class HttpMcpSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    transport: Literal["http"]
    url: str = Field(min_length=1)
    headers: dict[str, str] = Field(default_factory=dict)


McpSettings = Annotated[StdioMcpSettings | HttpMcpSettings, Field(discriminator="transport")]


class AgentSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: ModelSettings
    skills: SkillsSettings
    mcp_servers: dict[str, McpSettings] = Field(default_factory=dict, alias="mcpServers")

    def mcp_connections(self) -> dict[str, dict[str, object]]:
        return {name: config.model_dump(mode="python", exclude_none=True)
                for name, config in self.mcp_servers.items()}


def agent_settings_path() -> Path:
    override = os.environ.get("VR_AGENT_SETTINGS", "").strip()
    return Path(override).expanduser() if override else Path.home() / ".vibe-research" / "agent" / "settings.json"


def load_agent_settings(path: Path | None = None) -> AgentSettings:
    resolved = (path or agent_settings_path()).expanduser().resolve()
    try:
        raw = resolved.read_text(encoding="utf-8")
        payload = json.loads(raw)
        settings = AgentSettings.model_validate(payload)
    except FileNotFoundError as exc:
        raise AgentSettingsError(f"Agent 配置文件不存在：{resolved}") from exc
    except OSError as exc:
        raise AgentSettingsError(f"Agent 配置文件不可读：{resolved}（{exc.strerror or '未知错误'}）") from exc
    except json.JSONDecodeError as exc:
        raise AgentSettingsError(f"Agent 配置文件不是合法 JSON：{resolved}（第 {exc.lineno} 行第 {exc.colno} 列）") from exc
    except ValidationError as exc:
        locations = [".".join(str(part) for part in error["loc"]) for error in exc.errors(include_input=False)]
        raise AgentSettingsError(f"Agent 配置字段无效：{resolved}（{', '.join(locations)}）") from exc
    skill_root = settings.skills.path.expanduser().resolve()
    if not skill_root.is_dir() or not os.access(skill_root, os.R_OK):
        raise AgentSettingsError(f"Agent Skills 目录不存在、不可读或不是目录：{skill_root}")
    settings.skills.path = skill_root
    try:
        if resolved.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            print(f"警告：Agent 配置包含明文密钥，建议执行 chmod 600 {resolved}", file=sys.stderr)
    except OSError:
        pass
    return settings
```

Before committing, adjust `mcp_connections()` so the discriminator remains `"stdio"`/`"http"` and aliases are not emitted. Do not serialize a settings object in logs or exception details.

- [ ] **Step 5: Run settings and isolation tests**

Run:

```bash
cd backend
.venv/bin/pytest tests/agent/test_settings.py -q
.venv/bin/pytest tests/agent/test_ssrf.py -q
.venv/bin/pytest -m "not live" -q
```

Expected: PASS; no test opens the default settings path.

- [ ] **Step 6: Commit static settings**

```bash
git add backend/agent/settings.py backend/conftest.py backend/tests/agent/test_settings.py
git commit -m "feat(agent): load static local settings"
```

### Task 3: Replace Built-In Tool Governance With One LangChain Adapter

**Files:**
- Modify: `backend/agent/tool_registry.py`
- Modify: `backend/tests/agent/test_tool_registry.py`

- [ ] **Step 1: Replace obsolete governance tests with native adapter tests**

Rewrite `test_tool_registry.py` around these behaviors:

```python
@pytest.mark.asyncio
async def test_builtin_tools_preserve_schema_names_and_structured_errors(monkeypatch):
    monkeypatch.setattr(legacy_tools, "exec_tool", lambda name, args: {"error": f"{name} failed", "args": args})
    built = build_builtin_tools()
    assert [tool.name for tool in built] == legacy_tools.TOOL_NAMES
    result = json.loads(await built[0].ainvoke({"codes": ["600519"]}))
    assert result == {"error": "query_quote failed", "args": {"codes": ["600519"]}}


@pytest.mark.asyncio
async def test_builtin_tool_dispatch_runs_off_event_loop(monkeypatch):
    main_thread = threading.get_ident()
    seen = []
    monkeypatch.setattr(legacy_tools, "exec_tool", lambda name, args: seen.append(threading.get_ident()) or {"ok": True})
    await build_builtin_tools()[0].ainvoke({"codes": ["600519"]})
    assert seen and seen[0] != main_thread


@pytest.mark.asyncio
async def test_all_builtin_tools_share_one_process_lock():
    built = build_builtin_tools()
    assert BUILTIN_SERIAL_LOCK is builtin_serial_lock()
    assert all(tool.metadata == {"vr_origin": "builtin", "vr_serial_lock": "process"} for tool in built)
```

Add this Eastmoney timing test. Both selected tools go through `eastmoney_datacenter -> em_get`; no report API or real network path is reachable. Replace the module's `random` namespace instead of mutating the process-global `random.uniform` function:

```python
@pytest.mark.asyncio
async def test_builtin_lock_keeps_two_eastmoney_requests_one_second_apart(monkeypatch):
    starts: list[float] = []

    class FakeResponse:
        def json(self):
            return {"result": {"data": []}}

    class FakeSession:
        def get(self, *_args, **_kwargs):
            starts.append(time.monotonic())
            return FakeResponse()

    session = FakeSession()
    monkeypatch.setattr(astock, "_EM_MIN_INTERVAL", 1.0)
    monkeypatch.setattr(astock, "_em_last_call", [0.0])
    monkeypatch.setattr(astock, "_em_mode", ["direct"])
    monkeypatch.setattr(astock, "_em_session", lambda _direct: session)
    monkeypatch.setattr(astock, "random", SimpleNamespace(uniform=lambda *_args: 0.0))
    built = {tool.name: tool for tool in build_builtin_tools()}

    await asyncio.gather(
        built["query_margin"].ainvoke({"code": "600519"}),
        built["query_block_trade"].ainvoke({"code": "600519"}),
    )

    assert len(starts) == 2
    assert starts[1] - starts[0] >= 1.0
```

- [ ] **Step 2: Run the tool tests and verify the old implementation fails**

Run: `cd backend && .venv/bin/pytest tests/agent/test_tool_registry.py -q`

Expected: FAIL because the current adapter requires `ToolExecutionContext`/capacity leases and exposes no process lock.

- [ ] **Step 3: Rewrite `tool_registry.py` to the minimal native adapter**

Replace governance imports and Artifact helpers with:

```python
from __future__ import annotations

import asyncio
import json
from typing import Any

from langchain_core.tools import StructuredTool

import tools as legacy_tools

BUILTIN_RESULT_LIMIT = 6000
BUILTIN_SERIAL_LOCK = asyncio.Lock()


def builtin_serial_lock() -> asyncio.Lock:
    return BUILTIN_SERIAL_LOCK


def _encode_result(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, default=str)
    suffix = "...[truncated]"
    return encoded if len(encoded) <= BUILTIN_RESULT_LIMIT else encoded[:BUILTIN_RESULT_LIMIT - len(suffix)] + suffix


def _build_one(schema: dict[str, Any]) -> StructuredTool:
    function = schema["function"]
    name = function["name"]

    async def invoke(**kwargs: Any) -> str:
        async with BUILTIN_SERIAL_LOCK:
            result = await asyncio.to_thread(legacy_tools.exec_tool, name, kwargs)
        return _encode_result(result)

    return StructuredTool.from_function(
        coroutine=invoke,
        name=name,
        description=function["description"],
        args_schema=function["parameters"],
        metadata={"vr_origin": "builtin", "vr_serial_lock": "process"},
    )


def build_builtin_tools() -> list[StructuredTool]:
    return [_build_one(schema) for schema in legacy_tools.TOOLS]
```

Do not retain `compose_run_tools`, Artifact creation, execution context, deadlines, capacity leases, or a per-tool lock.

- [ ] **Step 4: Run focused and legacy tool contracts**

Run:

```bash
cd backend
.venv/bin/pytest tests/agent/test_tool_registry.py -q
.venv/bin/pytest tests/test_agents.py tests/test_fixes.py tests/test_mcp_stdio_encoding.py -q
```

Expected: PASS, including the real one-second mocked HTTP spacing assertion.

- [ ] **Step 5: Commit the tool adapter**

```bash
git add backend/agent/tool_registry.py backend/tests/agent/test_tool_registry.py
git commit -m "refactor(agent): adapt built-in tools with LangChain"
```

### Task 4: Assemble The Native LangChain Graph Once

**Files:**
- Create: `backend/agent/graph.py`
- Create: `backend/tests/agent/test_graph.py`
- Modify: `backend/tests/agent/fakes.py`

- [ ] **Step 1: Add graph tests with injected settings, MCP discovery, and scripted replies**

Define the settings and MCP-tool fixtures in `test_graph.py`; do not rely on a deleted or implicit `conftest.py` fixture:

```python
def make_settings(tmp_path: Path) -> AgentSettings:
    skills = tmp_path / "skills"
    skills.mkdir(exist_ok=True)
    return AgentSettings.model_validate({
        "model": {
            "provider": "openai",
            "name": "test-model",
            "apiKey": "test-secret-never-send",
            "baseURL": "https://example.invalid/v1",
            "temperature": 0.2,
        },
        "skills": {"path": str(skills)},
        "mcpServers": {},
    })


@pytest.fixture
def settings(tmp_path: Path) -> AgentSettings:
    return make_settings(tmp_path)


@tool("fixture_echo")
def fixture_echo(value: str) -> str:
    """Return deterministic fixture text."""
    return value


@tool("query_quote")
def duplicate_query_quote(codes: list[str]) -> str:
    """Deliberately collide with a built-in tool in one test."""
    return ",".join(codes)
```

Tests must inject `ScriptedChatModel`, monkeypatch MCP discovery, and never contact a provider. Cover these exact assertions:

```python
@pytest.mark.asyncio
async def test_build_graph_uses_fixed_prompt_and_complete_tool_surface(monkeypatch, settings):
    captured = {}
    compiled = object()
    monkeypatch.setattr(graph_module, "create_agent", lambda **kwargs: captured.update(kwargs) or compiled)
    monkeypatch.setattr(graph_module.MultiServerMCPClient, "get_tools", AsyncMock(return_value=[fixture_echo]))
    model = ScriptedChatModel([AIMessage(content="客观回复")])
    assert await graph_module.build_graph(model=model, settings=settings) is compiled
    assert captured["system_prompt"] == chat.SYSTEM_PROMPT.format(context="Agent 工作台")
    assert {tool.name for tool in captured["tools"]} == {*legacy_tools.TOOL_NAMES, "fixture_echo"}
    assert isinstance(captured["middleware"][0], SkillsMiddleware)
    assert isinstance(captured["middleware"][1], FilesystemMiddleware)
    assert [tool.name for tool in captured["middleware"][1].tools] == ["ls", "read_file"]
    hitl = captured["middleware"][2]
    assert hitl.interrupt_on == {"fixture_echo": {"allowed_decisions": ["approve", "reject"]}}


@pytest.mark.asyncio
async def test_duplicate_tool_names_fail_before_agent_creation(monkeypatch, settings):
    monkeypatch.setattr(graph_module.MultiServerMCPClient, "get_tools", AsyncMock(return_value=[duplicate_query_quote]))
    with pytest.raises(RuntimeError, match="query_quote"):
        await graph_module.build_graph(model=ScriptedChatModel([]), settings=settings)
```

Add a model-construction test that explicitly expects LangChain's warning about moving this non-default parameter into `model_kwargs`:

```python
def test_model_is_streaming_serial_and_secret_safe(settings):
    with pytest.warns(UserWarning, match="transferred to model_kwargs"):
        model = graph_module._build_model(settings)
    assert model.model_kwargs["parallel_tool_calls"] is False
    assert model.streaming is True
    assert isinstance(model.openai_api_key, SecretStr)
    assert "test-secret-never-send" not in repr(model)
```

Add an offline built-in tool-loop test. Patch the sole legacy dispatcher, script one built-in call followed by a final response, and prove the `ToolMessage` reaches the second model invocation without any network access:

```python
@pytest.mark.asyncio
async def test_builtin_tool_call_loops_back_into_model(monkeypatch, settings):
    monkeypatch.setattr(graph_module.MultiServerMCPClient, "get_tools", AsyncMock(return_value=[]))
    monkeypatch.setattr(legacy_tools, "exec_tool", lambda name, args: {"name": name, "codes": args["codes"]})
    model = ScriptedChatModel([
        AIMessage(content="", tool_calls=[{
            "id": "builtin-1", "name": "query_quote", "args": {"codes": ["600519"]},
        }]),
        AIMessage(content="已基于客观行情完成核验。"),
    ])
    graph = await graph_module.build_graph(model=model, settings=settings)
    result = await graph.ainvoke({"messages": [{"role": "user", "content": "查询行情"}]})

    assert result["messages"][-1].content == "已基于客观行情完成核验。"
    tool_messages = [message for message in model.invocations[1] if isinstance(message, ToolMessage)]
    assert json.loads(tool_messages[-1].content) == {"name": "query_quote", "codes": ["600519"]}
```

Add a separate offline HITL rejection regression. Import `create_agent as real_create_agent`,
`InMemorySaver`, and `Command` in the test module; inject the in-memory checkpointer only through
the test's monkeypatched `create_agent`, because the production graph remains server-checkpointed.
The test must prove rejection does not execute the tool, creates an error `ToolMessage`, and calls
the model a second time with that rejection result:

```python
@pytest.mark.asyncio
async def test_hitl_reject_resumes_without_executing_tool(monkeypatch, settings):
    executed: list[str] = []

    @tool("fixture_guarded")
    def fixture_guarded(value: str) -> str:
        """Record execution for the rejection contract."""
        executed.append(value)
        return value

    monkeypatch.setattr(
        graph_module.MultiServerMCPClient,
        "get_tools",
        AsyncMock(return_value=[fixture_guarded]),
    )
    monkeypatch.setattr(
        graph_module,
        "create_agent",
        lambda **kwargs: real_create_agent(checkpointer=InMemorySaver(), **kwargs),
    )
    model = ScriptedChatModel([
        AIMessage(content="", tool_calls=[{
            "id": "reject-1", "name": "fixture_guarded", "args": {"value": "不得执行"},
        }]),
        AIMessage(content="拒绝已记录，工具未执行。"),
    ])
    graph = await graph_module.build_graph(model=model, settings=settings)
    config = {"configurable": {"thread_id": "offline-reject"}}

    paused = await graph.ainvoke(
        {"messages": [{"role": "user", "content": "拒绝这个工具"}]},
        config=config,
    )
    assert paused["__interrupt__"]
    result = await graph.ainvoke(
        Command(resume={"decisions": [{"type": "reject", "message": "用户拒绝"}]}),
        config=config,
    )

    assert executed == []
    rejected = [
        message for message in result["messages"]
        if isinstance(message, ToolMessage) and message.tool_call_id == "reject-1"
    ]
    assert len(rejected) == 1
    assert rejected[0].status == "error"
    assert rejected[0].content == "用户拒绝"
    assert len(model.invocations) == 2
    assert any(
        isinstance(message, ToolMessage) and message.status == "error"
        for message in model.invocations[1]
    )
```

Do not assert that the original `AIMessage.tool_calls` array becomes empty. In the pinned
`langchain==1.3.15` middleware the rejected call is retained alongside the synthetic error
`ToolMessage`; the matching tool result is what prevents execution before the model's second call.

Add a fixture Skill with a reference file and markers that must not be eagerly injected. Let the scripted model explicitly call `read_file` for the Skill, then its reference, then attempt root escape. Assert the first model call receives only metadata, later calls receive content progressively, and traversal returns an error `ToolMessage` without exposing the outside secret:

```python
@pytest.mark.asyncio
async def test_skills_are_metadata_first_read_only_and_root_confined(monkeypatch, settings, tmp_path):
    skill = settings.skills.path / "research"
    references = skill / "references"
    references.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: research\ndescription: 客观核验步骤。\n---\nFULL_SKILL_BODY_MARKER\n",
        encoding="utf-8",
    )
    (references / "checklist.md").write_text("REFERENCE_MARKER", encoding="utf-8")
    (tmp_path / "outside-secret.txt").write_text("OUTSIDE_SECRET", encoding="utf-8")
    monkeypatch.setattr(graph_module.MultiServerMCPClient, "get_tools", AsyncMock(return_value=[]))
    model = ScriptedChatModel([
        AIMessage(content="", tool_calls=[{
            "id": "read-skill", "name": "read_file", "args": {"file_path": "/research/SKILL.md"},
        }]),
        AIMessage(content="", tool_calls=[{
            "id": "read-reference", "name": "read_file",
            "args": {"file_path": "/research/references/checklist.md"},
        }]),
        AIMessage(content="", tool_calls=[{
            "id": "escape-root", "name": "read_file", "args": {"file_path": "/../outside-secret.txt"},
        }]),
        AIMessage(content="完成"),
    ])
    graph = await graph_module.build_graph(model=model, settings=settings)
    result = await graph.ainvoke({"messages": [{"role": "user", "content": "读取核验技能"}]})

    system_text = "\n".join(str(message.content) for message in model.invocations[0] if isinstance(message, SystemMessage))
    assert "research" in system_text and "客观核验步骤" in system_text
    assert "FULL_SKILL_BODY_MARKER" not in system_text
    assert "REFERENCE_MARKER" not in system_text
    assert any(
        "FULL_SKILL_BODY_MARKER" in str(message.content)
        for message in model.invocations[1] if isinstance(message, ToolMessage)
    )
    assert any(
        "REFERENCE_MARKER" in str(message.content)
        for message in model.invocations[2] if isinstance(message, ToolMessage)
    )
    escape = [message for message in result["messages"] if isinstance(message, ToolMessage) and message.tool_call_id == "escape-root"]
    assert len(escape) == 1 and escape[0].status == "error"
    assert "Path traversal not allowed" in str(escape[0].content)
    assert "OUTSIDE_SECRET" not in str(result["messages"])
```

The composition test's exact assertion on `captured["middleware"][1].tools` proves only `ls` and `read_file` are exposed; additionally assert the resulting name set excludes `write_file`, `edit_file`, `delete`, `grep`, `glob`, and `execute`. Together these tests cover metadata discovery, progressive disclosure, the read-only tool surface, and virtual-root confinement without invoking a provider.

- [ ] **Step 2: Run graph tests and verify they fail**

Run: `cd backend && .venv/bin/pytest tests/agent/test_graph.py -q`

Expected: FAIL because `agent.graph` does not exist.

- [ ] **Step 3: Add only the fake capability needed by native tests**

Keep `ScriptedChatModel` as the single scripted model class. Remove AG-UI-specific wording from its comments, retain sync `_generate` and `_stream`, and add `invocations: list[list[BaseMessage]]`; its constructor must call `super().__init__(replies=deque(replies), invocations=[])`, and both generation paths append `list(messages)` before consuming each reply. Do not introduce a new fake model class.

- [ ] **Step 4: Implement the async graph builder and module export**

Create `backend/agent/graph.py` with these public contracts:

```python
from __future__ import annotations

import asyncio
from typing import Any

from deepagents.backends import FilesystemBackend
from deepagents.middleware.filesystem import FilesystemMiddleware
from deepagents.middleware.skills import SkillsMiddleware
from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI
from langchain_mcp_adapters.client import MultiServerMCPClient
from pydantic import SecretStr

import chat
from agent.settings import AgentSettings, load_agent_settings
from agent.tool_registry import build_builtin_tools


def _build_model(settings: AgentSettings) -> ChatOpenAI:
    model = settings.model
    return ChatOpenAI(
        model=model.name,
        base_url=model.base_url.rstrip("/"),
        api_key=SecretStr(model.api_key.get_secret_value()),
        temperature=model.temperature,
        streaming=True,
        parallel_tool_calls=False,
    )


def _require_unique_tool_names(tools: list[Any]) -> None:
    counts: dict[str, int] = {}
    for tool in tools:
        counts[tool.name] = counts.get(tool.name, 0) + 1
    duplicates = sorted(name for name, count in counts.items() if count > 1)
    if duplicates:
        raise RuntimeError(f"Agent 工具名冲突：{', '.join(duplicates)}")


async def build_graph(
    model: BaseChatModel | None = None,
    *,
    settings: AgentSettings | None = None,
):
    resolved = settings or load_agent_settings()
    backend = FilesystemBackend(root_dir=resolved.skills.path)
    client = MultiServerMCPClient(resolved.mcp_connections(), tool_name_prefix=True)
    builtin_tools = build_builtin_tools()
    mcp_tools = await client.get_tools()
    all_tools = [*builtin_tools, *mcp_tools]
    _require_unique_tool_names(all_tools)
    middleware = [
        SkillsMiddleware(backend=backend, sources=["/"]),
        FilesystemMiddleware(backend=backend, tools=["ls", "read_file"]),
        HumanInTheLoopMiddleware({
            tool.name: {"allowed_decisions": ["approve", "reject"]}
            for tool in mcp_tools
        }),
    ]
    return create_agent(
        model=model or _build_model(resolved),
        tools=all_tools,
        middleware=middleware,
        system_prompt=chat.SYSTEM_PROMPT.format(context="Agent 工作台"),
    )


graph = asyncio.run(build_graph())
```

Use the imports shown above, which are part of the pinned Deep Agents 0.7.7 contract and are covered by Task 1's import test. Do not add `MemorySaver`, a store, a graph factory, a request model override, or an E2E environment branch.

- [ ] **Step 5: Run graph tests and an isolated real import**

Run:

```bash
cd backend
.venv/bin/pytest tests/agent/test_graph.py -q
.venv/bin/python -c 'import json, os, tempfile; from pathlib import Path; root=Path(tempfile.mkdtemp(prefix="vr-graph-import-")); (root/"skills").mkdir(); settings=root/"settings.json"; settings.write_text(json.dumps({"model":{"provider":"openai","name":"test-model","apiKey":"test-only","baseURL":"https://example.invalid/v1","temperature":0.2},"skills":{"path":str(root/"skills")},"mcpServers":{}})); os.chmod(settings, 0o600); os.environ["VR_AGENT_SETTINGS"]=str(settings); from agent.graph import graph; print(type(graph).__name__)'
```

Expected: tests PASS and the import prints a compiled graph type without contacting the configured model. If the test settings have no MCP servers, import performs no external connection.

- [ ] **Step 6: Commit graph assembly**

```bash
git add backend/agent/graph.py backend/tests/agent/test_graph.py backend/tests/agent/fakes.py
git commit -m "feat(agent): assemble native LangChain graph"
```

### Task 5: Register LangGraph Server And Prove Persistence/CORS

**Files:**
- Create: `backend/langgraph.json`
- Modify: `backend/.gitignore`
- Create: `backend/tests/agent/server_harness.py`
- Create: `backend/tests/agent/test_langgraph_server.py`
- Create: `backend/tests/agent_e2e/__init__.py`
- Create: `backend/tests/agent_e2e/server_graph.py`
- Create: `backend/tests/agent_e2e/server_langgraph.json`
- Create: `backend/tests/agent_e2e/skills/research/SKILL.md`

- [ ] **Step 1: Write config and subprocess tests first**

The production config test is exact:

```python
def test_production_langgraph_config_is_local_and_persistent_ready():
    config = json.loads((BACKEND / "langgraph.json").read_text(encoding="utf-8"))
    assert config == {
        "dependencies": ["./"],
        "graphs": {"agent": "./agent/graph.py:graph"},
        "env": {"CORS_ALLOW_ORIGINS": "http://127.0.0.1:5899"},
    }
    assert ".langgraph_api/" in (BACKEND / ".gitignore").read_text(encoding="utf-8")


def test_server_fixture_config_uses_only_the_server_graph():
    config = json.loads(
        (BACKEND / "tests/agent_e2e/server_langgraph.json").read_text(encoding="utf-8")
    )
    assert config == {
        "dependencies": ["./"],
        "graphs": {"agent": "./server_graph.py:graph"},
        "env": {"CORS_ALLOW_ORIGINS": "http://127.0.0.1:5873"},
    }
```

Define the session-scoped fixture directly in `test_langgraph_server.py`, not in `tests/agent/conftest.py` (Task 10 deletes that legacy conftest):

```python
@pytest.fixture(scope="session")
def server(tmp_path_factory):
    harness = LangGraphServerHarness(tmp_path_factory.mktemp("langgraph-server"))
    harness.start()
    try:
        yield harness
    finally:
        harness.stop()
```

Add integration cases using that fixture. The first case must assert the real stdio MCP prefix and complete an approval resume, not stop at graph assembly:

```python
def test_native_thread_run_and_interrupt(server):
    client = get_sync_client(url=server.url, api_key=None)
    thread = client.threads.create()
    run = client.runs.create(thread["thread_id"], "agent", input={"messages": [{"role": "user", "content": "审批测试"}]})
    state = wait_until_interrupted(client, thread["thread_id"], run["run_id"])
    assert state["next"]
    assert len(state["tasks"][0]["interrupts"][0]["value"]["action_requests"]) == 1
    assert state["tasks"][0]["interrupts"][0]["value"]["action_requests"][0]["name"] == "fixture_echo"
    resume_with_decisions(server.url, thread["thread_id"], [{"type": "approve"}])
    terminal = wait_for_terminal_state(server.url, thread["thread_id"])
    assert terminal["next"] == []
    assert "approved fixture value" in str(terminal["values"])


def test_interrupt_survives_process_restart(server):
    thread_id = create_interrupted_thread(server.url)
    server.stop()
    assert list(server.cwd.glob(".langgraph_api/**/*.pckl")) or any((server.cwd / ".langgraph_api").iterdir())
    server.start()
    client = get_sync_client(url=server.url, api_key=None)
    assert thread_id in {item["thread_id"] for item in client.threads.search(limit=100)}
    restored = client.threads.get_state(thread_id)
    assert restored["next"]
    assert "审批测试" in str(restored["values"])
    resume_with_decisions(server.url, thread_id, [{"type": "approve"}])
    assert wait_for_terminal_state(server.url, thread_id)["next"] == []
```

The fixture graph must use `create_agent`, `HumanInTheLoopMiddleware`, `ScriptedChatModel`, and the real prefixed stdio MCP tool. Mark the discovered `fixture_echo` tool `return_direct=True` so approval completes without a second model call after restart. This exercises the pinned LangChain/core/MCP stack while keeping the scripted deque restart-safe.

Import `get_sync_client` from `langgraph_sdk`; `create_interrupted_thread()` always submits the user content `审批测试`, helper functions poll `client.threads.get_state(thread_id)`, and resume uses `client.runs.create(thread_id, "agent", command={"resume": {"decisions": decisions}})`.

- [ ] **Step 2: Add layered CORS tests with honest expectations**

Use raw `httpx` requests and assert all three layers:

```python
def test_cors_blocks_hostile_reads_but_accepts_known_local_origin(server):
    hostile = {"Origin": "https://evil.example.com"}
    preflight = httpx.options(f"{server.url}/threads", headers={**hostile,
        "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type"})
    assert preflight.status_code == 400
    actual = httpx.post(f"{server.url}/threads", headers=hostile, json={})
    assert actual.status_code == 200
    assert "access-control-allow-origin" not in actual.headers
    allowed = httpx.post(f"{server.url}/threads", headers={"Origin": server.frontend_origin}, json={})
    assert allowed.headers["access-control-allow-origin"] == server.frontend_origin


def test_text_plain_simple_posts_can_blind_write(server):
    headers = {"Origin": "https://evil.example.com", "Content-Type": "text/plain"}
    created = httpx.post(f"{server.url}/threads", headers=headers, content="{}")
    assert created.status_code == 200
    thread_id = created.json()["thread_id"]
    submitted = httpx.post(
        f"{server.url}/threads/{thread_id}/runs",
        headers=headers,
        content=json.dumps({"assistant_id": "agent", "input": {"messages": [{"role": "user", "content": "审批测试"}]}}),
    )
    assert submitted.status_code == 200
    assert wait_for_thread_status(server.url, thread_id) == "interrupted"
```

- [ ] **Step 3: Run config tests and verify they fail**

Run: `cd backend && .venv/bin/pytest tests/agent/test_langgraph_server.py -q`

Expected: FAIL because config, fixture graph, and harness do not exist.

- [ ] **Step 4: Add production and test LangGraph configs**

Create `backend/langgraph.json`:

```json
{
  "dependencies": ["./"],
  "graphs": {"agent": "./agent/graph.py:graph"},
  "env": {"CORS_ALLOW_ORIGINS": "http://127.0.0.1:5899"}
}
```

Add `.langgraph_api/` to `backend/.gitignore`. Create `server_langgraph.json` with the same shape but graph path `./server_graph.py:graph` and test origin `http://127.0.0.1:5873`. This config belongs only to `test_langgraph_server.py`; the browser graph/config arrive separately in Task 12.

- [ ] **Step 5: Implement the isolated server harness**

`LangGraphServerHarness(cwd)` receives the exact temporary directory created by the fixture above. It must copy `server_graph.py`, `server_langgraph.json`, and `skills/` into that directory without renaming them, choose a free loopback port with a bound socket, start this exact command from that cwd, poll `/ok` or `/docs` until ready, and terminate/kill with bounded timeouts:

```python
command = [
    str(BACKEND / ".venv/bin/langgraph"), "dev",
    "--config", str(cwd / "server_langgraph.json"),
    "--host", "127.0.0.1", "--port", str(port),
    "--no-browser", "--no-reload",
]
env = {
    **os.environ,
    "PYTHONPATH": str(BACKEND),
    "VR_AGENT_SETTINGS": str(cwd / "settings.json"),
}
```

The harness must expose `url`, `cwd`, `frontend_origin`, `start()`, and `stop()`. It must write an isolated valid settings file, a Skills root, and this stdio connection using absolute paths; never copy or read the real default settings:

```python
"mcpServers": {
    "fixture": {
        "transport": "stdio",
        "command": sys.executable,
        "args": [str(BACKEND / "tests/agent/fake_mcp_server.py")],
        "env": {},
    },
},
```

- [ ] **Step 6: Implement the deterministic integration graph**

In `backend/tests/agent_e2e/server_graph.py`, discover the tools from the isolated settings file. The module-level graph intentionally has 16 identical tool-call replies because the session-scoped server shares one compiled graph/deque across every run-creating test; each run consumes one reply, while a process restart reconstructs the deque. Sixteen is above the suite's run count and exhaustion must remain impossible unless a test explicitly adds more than sixteen pre-restart runs:

```python
from __future__ import annotations

import asyncio

from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langchain_core.messages import AIMessage
from langchain_mcp_adapters.client import MultiServerMCPClient

import chat
from agent.settings import load_agent_settings
from tests.agent.fakes import ScriptedChatModel


async def build_fixture_graph():
    settings = load_agent_settings()
    tools = await MultiServerMCPClient(
        settings.mcp_connections(), tool_name_prefix=True,
    ).get_tools()
    echo = next(tool for tool in tools if tool.name == "fixture_echo")
    echo.return_direct = True
    replies = [
        AIMessage(content="", tool_calls=[{
            "id": f"fixture-call-{index}",
            "name": "fixture_echo",
            "args": {"value": "approved fixture value"},
        }])
        for index in range(16)
    ]
    return create_agent(
        model=ScriptedChatModel(replies),
        tools=tools,
        middleware=[HumanInTheLoopMiddleware({
            tool.name: {"allowed_decisions": ["approve", "reject"]}
            for tool in tools
        })],
        system_prompt=chat.SYSTEM_PROMPT.format(context="Agent 工作台"),
    )


graph = asyncio.run(build_fixture_graph())
```

Do not define a local `@tool` and do not add a checkpointer; LangGraph Server injects persistence. This fixture proves real fake-MCP discovery, server-name prefixing, interrupt creation, approval resume, and restart recovery.

The Skill fixture frontmatter is:

```markdown
---
name: research
description: 提供客观投研核验步骤。
---

# Research

只整理客观事实、数据缺口与核验清单，不给买卖建议或价格预测。
```

- [ ] **Step 7: Run the real server suite twice**

Run:

```bash
cd backend
.venv/bin/pytest tests/agent/test_langgraph_server.py -q
.venv/bin/pytest tests/agent/test_langgraph_server.py -q
```

Expected: both runs PASS; no watchfiles reload occurs, the restart test reuses the same `.langgraph_api/`, and teardown leaves no process listening on its temporary port.

- [ ] **Step 8: Commit native server contracts**

```bash
git add backend/langgraph.json backend/.gitignore backend/tests/agent/server_harness.py backend/tests/agent/test_langgraph_server.py backend/tests/agent_e2e/__init__.py backend/tests/agent_e2e/server_graph.py backend/tests/agent_e2e/server_langgraph.json backend/tests/agent_e2e/skills/research/SKILL.md
git commit -m "feat(agent): register persistent LangGraph server"
```

## Phase 2: Frontend Native Runtime

### Task 6: Install The assistant-ui LangChain Runtime And Add The Vite Proxy

**Files:**
- Modify: `frontend/package.json`
- Modify: `frontend/package-lock.json`
- Modify: `frontend/vite.config.ts`
- Create: `frontend/tests/agent-proxy.test.mjs`

- [ ] **Step 1: Write a Vite proxy contract test**

Create a Node test that imports the config factory, resolves development config with controlled env, and asserts both targets and the rewrite:

```javascript
test("Agent API proxy targets LangGraph root while legacy API stays on FastAPI", async () => {
  process.env.VITE_API_URL = "http://127.0.0.1:18890";
  process.env.VITE_AGENT_API_URL = "http://127.0.0.1:12024";
  const config = await resolveConfig({ command: "serve", mode: "test" }, "serve");
  assert.equal(config.server.proxy["/api"].target, "http://127.0.0.1:18890");
  assert.equal(config.server.proxy["/agent-api"].target, "http://127.0.0.1:12024");
  assert.equal(config.server.proxy["/agent-api"].rewrite("/agent-api/threads"), "/threads");
});
```

- [ ] **Step 2: Run the proxy test and verify it fails**

Run: `cd frontend && node --test --test-name-pattern='Agent API proxy' tests/agent-proxy.test.mjs`

Expected: FAIL because `/agent-api` is not configured.

- [ ] **Step 3: Replace AG-UI packages with exact native dependencies**

Run:

```bash
cd frontend
npm uninstall @ag-ui/client @assistant-ui/react-ag-ui
npm install --save-exact @assistant-ui/react-langchain@0.0.27 @langchain/react@1.0.32 @langchain/langgraph-sdk@1.9.31 assistant-stream@0.3.39
```

Remove `overrides["@ag-ui/client"]` from `package.json`. Keep `@assistant-ui/react` and shared Markdown/Lexical packages.

- [ ] **Step 4: Add the second proxy without changing `/api`**

In the Vite config factory add:

```typescript
const agentApiTarget = env.VITE_AGENT_API_URL || "http://127.0.0.1:2024";

proxy: {
  "/agent-api": {
    target: agentApiTarget,
    changeOrigin: true,
    rewrite: (path) => path.replace(/^\/agent-api/, ""),
  },
  "/api": { target: apiTarget, changeOrigin: true },
},
```

Order `/agent-api` before `/api` for readability even though the prefixes do not overlap.

- [ ] **Step 5: Run install, proxy, and build gates**

Run:

```bash
cd frontend
npm ls @assistant-ui/react-langchain @langchain/react @langchain/langgraph-sdk assistant-stream
npm test
npm run build
```

Expected: exact requested versions are installed, proxy tests PASS, and TypeScript resolves the new packages.

- [ ] **Step 6: Commit frontend protocol dependencies**

```bash
git add frontend/package.json frontend/package-lock.json frontend/vite.config.ts frontend/tests/agent-proxy.test.mjs
git commit -m "build(agent): switch frontend to LangChain runtime"
```

### Task 7: Implement The Complete LangGraph Thread Adapter

**Files:**
- Create: `frontend/src/lib/agent/thread-adapter.ts`
- Create: `frontend/src/lib/agent/thread-adapter.test.ts`

- [ ] **Step 1: Write tests for all eight required adapter methods**

Use an injected SDK-shaped client with spies. Verify list/fetch translation, server-generated UUIDs on initialize, title generation from the first user message, metadata-preserving rename/archive/unarchive, and delete:

```typescript
it("implements the complete unstable adapter contract", async () => {
  const adapter: RemoteThreadListAdapter = createLangGraphThreadAdapter(client);
  expect(Object.keys(adapter).sort()).toEqual([
    "archive", "delete", "fetch", "generateTitle", "initialize", "list", "rename", "unarchive",
  ]);
});

it("merges metadata and maps archived status", async () => {
  client.threads.get.mockResolvedValue(thread({ metadata: { title: "标题", source: "studio" } }));
  await adapter.archive("th-1");
  expect(client.threads.update).toHaveBeenCalledWith("th-1", {
    metadata: { title: "标题", source: "studio", archived: true },
  });
  client.threads.search.mockResolvedValue([thread({ metadata: { title: "标题", archived: true } })]);
  expect((await adapter.list()).threads[0]).toMatchObject({ remoteId: "th-1", externalId: "th-1", status: "archived", title: "标题" });
});

it("writes and streams a deterministic first-user-message title", async () => {
  const stream = await adapter.generateTitle("th-1", [{
    id: "m1", role: "user", createdAt: new Date(), content: [{ type: "text", text: "  查询 600519 的客观数据  " }],
  }]);
  expect(client.threads.update).toHaveBeenCalledWith("th-1", { metadata: { title: "查询 600519 的客观数据" } });
  expect(await collectText(stream)).toBe("查询 600519 的客观数据");
});
```

Add this initialization contract. assistant-ui passes a temporary `__LOCALID_<7 chars>` identifier, but LangGraph Server rejects every non-UUID explicit `thread_id`; the adapter must ignore the local identifier and let the server allocate the canonical UUID:

```typescript
it("lets LangGraph allocate the canonical UUID for a local assistant-ui thread", async () => {
  client.threads.create.mockResolvedValue(thread({ thread_id: "018f4f4e-7b2d-7f2a-8000-123456789abc" }));
  await expect(adapter.initialize("__LOCALID_Ab3xY9z")).resolves.toEqual({
    remoteId: "018f4f4e-7b2d-7f2a-8000-123456789abc",
    externalId: "018f4f4e-7b2d-7f2a-8000-123456789abc",
  });
  expect(client.threads.create).toHaveBeenCalledWith();
});
```

- [ ] **Step 2: Run tests and verify they fail**

Run: `cd frontend && npx vitest run src/lib/agent/thread-adapter.test.ts`

Expected: FAIL because the adapter module does not exist.

- [ ] **Step 3: Implement the adapter against `Client.threads`**

Use `Client` from `@langchain/langgraph-sdk`, `RemoteThreadListAdapter`/`ThreadMessage` from `@assistant-ui/react`, and `createAssistantStream` from `assistant-stream`. Keep one client instance and expose a factory for tests:

```typescript
const titleOf = (metadata: Record<string, unknown>) =>
  typeof metadata.title === "string" ? metadata.title : undefined;

const toRemote = (thread: Thread): RemoteThreadMetadata => ({
  remoteId: thread.thread_id,
  externalId: thread.thread_id,
  status: thread.metadata.archived === true ? "archived" : "regular",
  title: titleOf(thread.metadata),
  lastMessageAt: new Date(thread.updated_at),
  custom: thread.metadata,
});

async function mergeMetadata(client: AgentThreadClient, id: string, patch: Record<string, unknown>) {
  const current = await client.threads.get(id);
  await client.threads.update(id, { metadata: { ...current.metadata, ...patch } });
}

export function createLangGraphThreadAdapter(client: AgentThreadClient): RemoteThreadListAdapter {
  return {
    async list() {
      const threads = await client.threads.search({ limit: 100, sortBy: "updated_at", sortOrder: "desc" });
      return { threads: threads.map(toRemote) };
    },
    async fetch(id) { return toRemote(await client.threads.get(id)); },
    async initialize() {
      const thread = await client.threads.create();
      return { remoteId: thread.thread_id, externalId: thread.thread_id };
    },
    async rename(id, title) { await mergeMetadata(client, id, { title }); },
    async archive(id) { await mergeMetadata(client, id, { archived: true }); },
    async unarchive(id) { await mergeMetadata(client, id, { archived: false }); },
    async delete(id) { await client.threads.delete(id); },
    async generateTitle(id, messages) {
      const user = messages.find((message) => message.role === "user");
      const title = (user?.content ?? [])
        .filter((part): part is Extract<typeof part, { type: "text" }> => part.type === "text")
        .map((part) => part.text).join(" ").trim().slice(0, 60) || "新会话";
      await mergeMetadata(client, id, { title });
      return createAssistantStream((controller) => controller.appendText(title));
    },
  };
}

export const langGraphClient = new Client({ apiUrl: "/agent-api" });
export const langGraphThreadAdapter = createLangGraphThreadAdapter(langGraphClient);
```

Use explicit local type aliases for the subset of `Client` accepted by the factory so Vitest mocks remain small; the production singleton must still be a real SDK `Client`.

- [ ] **Step 4: Run adapter tests and compile**

Run:

```bash
cd frontend
npx vitest run src/lib/agent/thread-adapter.test.ts
npm run build
```

Expected: all eight adapter methods pass runtime tests and satisfy the exact `RemoteThreadListAdapter` type.

- [ ] **Step 5: Commit the thread adapter**

```bash
git add frontend/src/lib/agent/thread-adapter.ts frontend/src/lib/agent/thread-adapter.test.ts
git commit -m "feat(agent): add LangGraph thread adapter"
```

### Task 8: Replace AG-UI Runtime And Approval With Native Hooks

**Files:**
- Modify: `frontend/src/lib/agent/runtime.tsx`
- Modify: `frontend/src/lib/agent/runtime.test.tsx`
- Modify: `frontend/src/lib/agent/approval.ts`
- Modify: `frontend/src/lib/agent/approval.contract.test.ts`
- Modify: `frontend/src/components/agent/ApprovalPanel.tsx`
- Modify: `frontend/src/components/agent/ApprovalPanel.test.tsx`

- [ ] **Step 1: Rewrite runtime tests around exact `useStreamRuntime` options**

Mock `@assistant-ui/react-langchain` and assert:

```typescript
expect(useStreamRuntime).toHaveBeenCalledWith({
  assistantId: "agent",
  apiUrl: "/agent-api",
  onThreadIdChange,
  unstable_threadListAdapter: langGraphThreadAdapter,
});
expect(JSON.stringify(useStreamRuntime.mock.calls[0][0])).not.toContain("apiKey");
expect(JSON.stringify(useStreamRuntime.mock.calls[0][0])).not.toContain("vr-agent-model");
```

Add a callback test that invokes the forwarded `onThreadIdChange` with a newly initialized canonical ID and proves the observer receives it. Assert `threadId`, `create`, and `delete` are absent from the top-level options. In 0.0.27, `useStreamThreadRuntime` overwrites any raw top-level `threadId` with the active thread-list item's `externalId`; switching is owned by assistant-ui's internal `runtime.threads.switchToThread(remoteId)`, not by a controlled provider prop.

- [ ] **Step 2: Rewrite approval tests for one aggregate interrupt**

Use this native payload shape and expected response:

```typescript
const interrupt = {
  id: "interrupt-1",
  value: {
    action_requests: [
      { name: "fixture_echo", args: { value: "a" }, description: "审批 A" },
      { name: "fixture_echo", args: { value: "b" }, description: "审批 B" },
    ],
    review_configs: [
      { action_name: "fixture_echo", allowed_decisions: ["approve", "reject"] },
      { action_name: "fixture_echo", allowed_decisions: ["approve", "reject"] },
    ],
  },
};

expect(respond).toHaveBeenCalledTimes(1);
expect(respond).toHaveBeenCalledWith({ decisions: [
  { type: "approve" },
  { type: "reject", message: "用户拒绝该工具调用。" },
] });
```

Add a case where one action has no choice and assert `respond` is not called. Remove session approval, bridge IDs, scopes, and `useLangChainRespondAll` expectations.

Keep the existing required `disabled: boolean` prop on `ApprovalPanel`; it disables every decision control and prevents submission while true. Keep a stable empty state for layout and E2E isolation: when there is no pending interrupt, `ApprovalPanel` renders `<section aria-label="MCP 工具审批">暂无待审批工具调用</section>` and no decision controls. Add unit tests for the disabled contract and that exact accessible region/text.

- [ ] **Step 3: Run runtime and approval tests and verify they fail**

Run: `cd frontend && npx vitest run src/lib/agent/runtime.test.tsx src/lib/agent/approval.contract.test.ts src/components/agent/ApprovalPanel.test.tsx`

Expected: FAIL because the current files still use AG-UI and custom approval wire models.

- [ ] **Step 4: Replace the runtime provider**

Make `runtime.tsx` a narrow provider:

```tsx
import { AssistantRuntimeProvider } from "@assistant-ui/react";
import { useStreamRuntime } from "@assistant-ui/react-langchain";
import { langGraphThreadAdapter } from "./thread-adapter";

export function AgentRuntimeProvider({ onThreadIdChange, children }: {
  onThreadIdChange?: (threadId: string | undefined) => void;
  children: React.ReactNode;
}) {
  const runtime = useStreamRuntime({
    assistantId: "agent",
    apiUrl: "/agent-api",
    onThreadIdChange,
    unstable_threadListAdapter: langGraphThreadAdapter,
  });
  return <AssistantRuntimeProvider runtime={runtime}>{children}</AssistantRuntimeProvider>;
}
```

Delete all AG-UI event translation, REST history hydration, model header, revision, watermark, stream convergence, conflict, Artifact invalidation, and custom cancellation code from this file.

- [ ] **Step 5: Replace approval parsing and panel submission**

In `approval.ts`, export a strict parser returning ordered actions only when `action_requests` and `review_configs` are same-length arrays. In `ApprovalPanel.tsx`, call `useLangChainInterrupts()` and `useLangChainRespond()`, render radio choices for `approve`/`reject`, and submit exactly once:

```tsx
const interrupts = useLangChainInterrupts();
const respond = useLangChainRespond();
const request = parseHitlRequest(interrupts[0]?.value);

const submit = async () => {
  if (disabled || !request || request.actions.some((_, index) => !choices[index])) return;
  await respond({
    decisions: request.actions.map((_, index) => choices[index] === "approve"
      ? { type: "approve" as const }
      : { type: "reject" as const, message: "用户拒绝该工具调用。" }),
  });
};
```

Do not expose edit/respond/session choices and do not use interrupt IDs in the resume payload.

- [ ] **Step 6: Run the native runtime tests and frontend gate**

Run:

```bash
cd frontend
npx vitest run src/lib/agent/runtime.test.tsx src/lib/agent/approval.contract.test.ts src/components/agent/ApprovalPanel.test.tsx
npm run build
```

Expected: PASS; `rg -n '@ag-ui|/api/agent|vr-agent-model' frontend/src/lib/agent/runtime.tsx frontend/src/lib/agent/approval.ts frontend/src/components/agent/ApprovalPanel.tsx` returns no matches.

- [ ] **Step 7: Commit native runtime and approval**

```bash
git add frontend/src/lib/agent/runtime.tsx frontend/src/lib/agent/runtime.test.tsx frontend/src/lib/agent/approval.ts frontend/src/lib/agent/approval.contract.test.ts frontend/src/components/agent/ApprovalPanel.tsx frontend/src/components/agent/ApprovalPanel.test.tsx
git commit -m "feat(agent): use native LangGraph runtime and HITL"
```

### Task 9: Simplify The Agent Workspace Around assistant-ui Primitives

**Files:**
- Modify: `frontend/src/components/agent/AgentThreadList.tsx`
- Modify: `frontend/src/components/agent/AgentThreadList.test.tsx`
- Modify: `frontend/src/components/agent/AgentWorkspace.tsx`
- Modify: `frontend/src/components/agent/AgentWorkspace.test.tsx`
- Modify: `frontend/src/components/agent/AgentThread.tsx`
- Modify: `frontend/src/components/agent/AgentThread.test.tsx`
- Modify: `frontend/src/pages/Agent.tsx`
- Modify: `frontend/src/pages/Agent.test.tsx`

- [ ] **Step 1: Replace page tests with the retained user workflow**

Mock only the native runtime boundary and verify: compact two-column workspace, new thread, thread switching via thread-list selection, rename, delete, aggregated approval, composer, and mobile thread drawer. Explicitly assert removed commands are absent:

```typescript
expect(screen.getByTestId("agent-workspace")).toBeVisible();
expect(screen.getByTestId("agent-threads-column")).toBeVisible();
expect(screen.getByTestId("agent-chat-column")).toBeVisible();
expect(screen.getByLabelText("Agent 消息")).toBeEnabled();
expect(screen.queryByText("Inspector")).not.toBeInTheDocument();
expect(screen.queryByText("管理 MCP")).not.toBeInTheDocument();
expect(screen.queryByText("管理 Skills")).not.toBeInTheDocument();
expect(screen.queryByText("预算")).not.toBeInTheDocument();
expect(screen.queryByRole("button", { name: "模型设置" })).not.toBeInTheDocument();
```

Use assistant-ui test runtime helpers rather than mocking the deleted REST `agentApi` or workspace zustand store.

- [ ] **Step 2: Run the four component/page tests and verify they fail**

Run: `cd frontend && npx vitest run src/components/agent/AgentThreadList.test.tsx src/components/agent/AgentWorkspace.test.tsx src/components/agent/AgentThread.test.tsx src/pages/Agent.test.tsx`

Expected: FAIL because current components depend on custom Agent types, Inspector, settings, and workspace state.

- [ ] **Step 3: Rebuild the thread list with assistant-ui primitives**

Use `ThreadListPrimitive.Root/New/Items` and `ThreadListItemPrimitive.Root/Trigger/Title/Delete`. Keep the current compact visual language and existing icons. Retain an accessible inline rename field; submit through `useAui().threadListItem.rename(newTitle)`, let Escape cancel, and disable confirmation for a blank trimmed title. Do not use `window.prompt` and do not render archive controls in v1, although adapter methods remain implemented.

The rename control and item skeleton are:

```tsx
function RenameThreadButton() {
  const aui = useAui();
  const currentTitle = useAuiState((state) => state.threadListItem.title ?? "");
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");

  if (!editing) {
    return <button type="button" aria-label="重命名会话" onClick={(event) => {
      event.stopPropagation();
      setDraft(currentTitle);
      setEditing(true);
    }}><Pencil className="size-4" /></button>;
  }

  return (
    <form onSubmit={async (event) => {
      event.preventDefault();
      event.stopPropagation();
      const title = draft.trim();
      if (!title) return;
      await aui.threadListItem.rename(title);
      setEditing(false);
    }}>
      <input
        autoFocus
        aria-label="会话标题"
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            event.preventDefault();
            setEditing(false);
          }
        }}
      />
      <button type="submit" aria-label="确认重命名" disabled={!draft.trim()}>确认</button>
    </form>
  );
}

function ThreadListItem() {
  const itemId = useAuiState((state) => state.threadListItem.id);
  return (
    <ThreadListItemPrimitive.Root
      data-testid={`agent-thread-${itemId}`}
      className="group relative flex items-center rounded-md data-[active=true]:bg-primary/10"
    >
      <ThreadListItemPrimitive.Trigger className="min-w-0 flex-1 px-3 py-2 text-left">
        <ThreadListItemPrimitive.Title fallback="新会话" />
      </ThreadListItemPrimitive.Trigger>
      <RenameThreadButton />
      <ThreadListItemPrimitive.Delete aria-label="删除会话" title="删除会话">
        <Trash2 className="size-4" />
      </ThreadListItemPrimitive.Delete>
    </ThreadListItemPrimitive.Root>
  );
}

export function AgentThreadList() {
  return (
    <ThreadListPrimitive.Root className="min-h-full p-2">
      <ThreadListPrimitive.New aria-label="新建会话" title="新建会话"><MessageSquarePlus className="size-4" /></ThreadListPrimitive.New>
      <ThreadListPrimitive.Items components={{ ThreadListItem }} />
    </ThreadListPrimitive.Root>
  );
}
```

- [ ] **Step 4: Reduce workspace and thread components to retained UI**

`AgentWorkspace` accepts only `desktop`, `threads`, `chat`, and optional `approval`. Desktop uses `grid-cols-[240px_minmax(0,1fr)]`; the chat column has `data-testid="agent-chat-column"` and renders `approval` immediately above `chat`. Mobile uses the existing accessible `WorkspaceDrawer` only for threads, with an `aria-label="打开会话列表"` trigger and drawer title `会话`; approval stays inline in the chat column above the composer and never moves into or behind the drawer. Remove model/capability labels, settings drawer, Inspector column, artifact selection, and alert band.

`AgentThread` keeps `Thread`, `ToolFallback`, `ToolGroup`, the standard send/cancel composer, and Chinese placeholder. Remove Artifact parsing/open buttons, `SteerAwayComposer`, custom Retry action, custom status note, and custom run-state props. The cancel button remains connected through assistant-ui and `useStreamRuntime`'s default cancellation support.

- [ ] **Step 5: Make `Agent.tsx` a thin composition root**

Let assistant-ui own the active thread and retain the page's existing inline `matchMedia` subscription; do not add dead `threadId` state and do not call a nonexistent `useDesktopViewport` helper:

```tsx
export function Agent() {
  const [desktop, setDesktop] = useState(() =>
    typeof window !== "undefined" && window.matchMedia("(min-width: 1280px)").matches,
  );
  useEffect(() => {
    const media = window.matchMedia("(min-width: 1280px)");
    const update = (event: MediaQueryListEvent) => setDesktop(event.matches);
    setDesktop(media.matches);
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  return (
    <AgentRuntimeProvider>
      <AgentWorkspace
        desktop={desktop}
        threads={<AgentThreadList />}
        approval={<ApprovalPanel disabled={false} />}
        chat={<AgentThread />}
      />
    </AgentRuntimeProvider>
  );
}
```

Do not load model config, Agent REST history, policy, Skills, MCP catalog, runs, sources, or artifacts.

- [ ] **Step 6: Run focused and full frontend gates**

Run:

```bash
cd frontend
npx vitest run src/components/agent/AgentThreadList.test.tsx src/components/agent/AgentWorkspace.test.tsx src/components/agent/AgentThread.test.tsx src/pages/Agent.test.tsx
npm test
npm run test:unit
npm run build
```

Expected: PASS with no unused TypeScript symbols and no visual shell depending on removed features.

- [ ] **Step 7: Commit the simplified workspace**

```bash
git add frontend/src/components/agent/AgentThreadList.tsx frontend/src/components/agent/AgentThreadList.test.tsx frontend/src/components/agent/AgentWorkspace.tsx frontend/src/components/agent/AgentWorkspace.test.tsx frontend/src/components/agent/AgentThread.tsx frontend/src/components/agent/AgentThread.test.tsx frontend/src/pages/Agent.tsx frontend/src/pages/Agent.test.tsx
git commit -m "refactor(agent): simplify workspace around assistant-ui"
```

## Phase 3: Remove The Replaced Control Plane

### Task 10: Detach And Delete The Custom Backend Agent Runtime

**Files:**
- Modify: `backend/app.py`
- Delete: `backend/agent/artifacts.py`
- Delete: `backend/agent/capabilities.py`
- Delete: `backend/agent/governance.py`
- Delete: `backend/agent/mcp.py`
- Delete: `backend/agent/models.py`
- Delete: `backend/agent/policy.py`
- Delete: `backend/agent/protocol.py`
- Delete: `backend/agent/provenance.py`
- Delete: `backend/agent/router.py`
- Delete: `backend/agent/runs.py`
- Delete: `backend/agent/runtime.py`
- Delete: `backend/agent/skills.py`
- Delete: `backend/agent/stores.py`
- Delete: `backend/agent/tool_executor.py`
- Delete: obsolete `backend/tests/agent/test_*.py` files enumerated below
- Delete: `backend/tests/agent/conftest.py`
- Delete: `backend/tests/agent_e2e_app.py`

- [ ] **Step 1: Add a legacy FastAPI contract test before detaching Agent routes**

In the existing app API test module add:

```python
def test_fastapi_keeps_legacy_ai_and_has_no_agent_control_plane():
    assert client.get("/api/health").status_code == 200
    assert client.post("/api/chat", json={}).status_code in {400, 422}
    assert client.post("/api/debate", json={}).status_code in {400, 422}
    assert client.post("/api/reflect", json={}).status_code in {400, 422}
    assert client.get("/api/agent/threads").status_code == 404
```

- [ ] **Step 2: Verify the new assertion fails on the old router**

Run: `cd backend && .venv/bin/pytest tests/test_api.py::test_fastapi_keeps_legacy_ai_and_has_no_agent_control_plane -q`

Expected: FAIL because `/api/agent/threads` is still registered and returns something other than 404.

- [ ] **Step 3: Remove only Agent router/lifecycle wiring from `app.py`**

Delete:

```python
from agent.router import router as agent_router
from agent.router import shutdown_agent_services, startup_agent_services
```

Remove the Agent startup/shutdown calls from lifespan and remove `app.include_router(agent_router)`. Keep scheduler startup/shutdown and every non-Agent route unchanged.

- [ ] **Step 4: Prove every deletion target has no protected consumer**

Run:

```bash
rg -n "agent\.(artifacts|capabilities|governance|mcp|models|policy|protocol|provenance|router|runs|runtime|skills|stores|tool_executor)" backend \
  -g '!tests/agent/test_*' -g '!tests/agent_e2e_app.py'
rg -n "agent\.ssrf|from agent import ssrf" backend/chat.py backend/tests/agent/test_ssrf.py
```

Expected: the first command reports only files scheduled for deletion or imports that this task removes; the second confirms the protected SSRF path remains live.

- [ ] **Step 5: Delete obsolete modules and their bound tests explicitly**

Use `apply_patch` deletions for the production modules above. Delete these old contract tests, retaining only `test_dependency_compat.py`, `test_dependency_install.py`, `test_settings.py`, `test_tool_registry.py`, `test_graph.py`, `test_langgraph_server.py`, `test_ssrf.py`, `fakes.py`, `fake_mcp_server.py`, and `server_harness.py`:

```text
test_agent_1d_integration.py
test_agent_vertical_slice.py
test_artifact_api.py
test_artifact_tool.py
test_artifacts.py
test_capabilities.py
test_context_governance.py
test_governance.py
test_governance_order.py
test_mcp_api.py
test_mcp_approval.py
test_mcp_config.py
test_mcp_registry.py
test_model_factory.py
test_models.py
test_policy.py
test_protocol_bridge.py
test_provenance.py
test_resume_contract.py
test_router.py
test_run_persistence.py
test_runtime_stream.py
test_skill_api.py
test_skill_import.py
test_skills.py
test_stores.py
test_thread_api.py
test_tool_executor.py
test_transition_limit.py
```

Delete `backend/tests/agent/conftest.py` after confirming no retained test imports `enter_single_loop_client`, and delete `backend/tests/agent_e2e_app.py` because Playwright will use production `app:app` plus the separate fixture graph.

- [ ] **Step 6: Run backend and protected legacy tests**

Run:

```bash
cd backend
.venv/bin/pytest tests/agent/test_ssrf.py tests/test_agents.py tests/test_fixes.py tests/test_mcp_stdio_encoding.py tests/test_reports_and_security.py -q
.venv/bin/pytest -m "not live" -q
rg -n "/api/agent|ag_ui|ag-ui" . -g '*.py' -g 'requirements*.txt'
```

Expected: all tests PASS; final search has no runtime matches. Mentions in historical docs/specs are allowed and should not be edited here.

- [ ] **Step 7: Commit backend control-plane deletion**

Stage `backend/app.py`, the explicit removed files, and the modified API test only. Verify `git diff --cached --name-status` before committing:

```bash
git commit -m "refactor(agent): remove custom FastAPI control plane"
```

### Task 11: Delete Obsolete Frontend Agent State And Panels

**Files:**
- Delete: `frontend/src/lib/agent/api.ts`
- Delete: `frontend/src/lib/agent/history.ts`
- Delete: `frontend/src/lib/agent/history.test.tsx`
- Delete: `frontend/src/lib/agent/model-config.ts`
- Delete: `frontend/src/lib/agent/types.ts`
- Delete: `frontend/src/lib/agent/workspace.ts`
- Delete: `frontend/src/lib/agent/workspace.test.ts`
- Delete: management/Inspector/Artifact/source/steer-away components and tests listed below

- [ ] **Step 1: Search for consumers after the native page is green**

Run:

```bash
rg -n "@/lib/agent/(api|history|model-config|types|workspace)|/api/agent|vr-agent-model" frontend/src frontend/e2e frontend/tests
rg -n "AgentInspector|AgentSettingsDrawer|ArtifactViewer|CapabilityBar|CapabilityManagerDialog|McpManager|RunInspector|SkillManager|SourceInspector|SteerAwayComposer" frontend/src
```

Expected: matches are confined to files in this deletion task. Any match in the retained runtime/page/thread files must be removed and covered by Task 8/9 tests before continuing.

- [ ] **Step 2: Delete the old frontend modules and components explicitly**

Delete the library files above plus:

```text
frontend/src/components/agent/AgentInspector.tsx
frontend/src/components/agent/AgentInspector.test.tsx
frontend/src/components/agent/AgentSettingsDrawer.tsx
frontend/src/components/agent/AgentSettingsDrawer.test.tsx
frontend/src/components/agent/ArtifactViewer.tsx
frontend/src/components/agent/ArtifactViewer.test.tsx
frontend/src/components/agent/CapabilityBar.tsx
frontend/src/components/agent/CapabilityBar.test.tsx
frontend/src/components/agent/CapabilityManagerDialog.tsx
frontend/src/components/agent/CapabilityManagerDialog.test.tsx
frontend/src/components/agent/McpManager.tsx
frontend/src/components/agent/McpManager.test.tsx
frontend/src/components/agent/RunInspector.tsx
frontend/src/components/agent/RunInspector.test.tsx
frontend/src/components/agent/SkillManager.tsx
frontend/src/components/agent/SourceInspector.tsx
frontend/src/components/agent/SourceInspector.test.tsx
frontend/src/components/agent/SteerAwayComposer.tsx
```

Keep `WorkspaceDrawer.tsx` and its test because Task 9 uses it for the mobile thread list. Keep all `frontend/src/components/assistant-ui/*`, `frontend/src/lib/agents.ts`, and `frontend/src/lib/ndjson.ts`.

- [ ] **Step 3: Prove old Agent model storage is gone while legacy storage works**

Add or retain a storage test that writes `vr-llm`, imports the legacy model loader, and asserts it still reads the value. Then assert:

```javascript
assert.equal(await sourceTreeContains("vr-agent-model"), false);
assert.equal(await sourceTreeContains("vr-llm"), true);
```

Do not remove existing user browser data at runtime; simply stop reading and writing the obsolete key.

- [ ] **Step 4: Run the full frontend gate and reference searches**

Run:

```bash
cd frontend
npm test
npm run test:unit
npm run build
rg -n "@ag-ui|react-ag-ui|/api/agent|vr-agent-model" src e2e tests package.json
```

Expected: all commands PASS and the search has no runtime match. Historical changelog text is outside this search scope.

- [ ] **Step 5: Commit frontend cleanup**

Stage only the explicit deleted files and the legacy storage test, inspect `git diff --cached --name-status`, then:

```bash
git commit -m "refactor(agent): remove custom workspace state and panels"
```

## Phase 4: Three-Service E2E And Documentation

### Task 12: Rebuild E2E Around FastAPI, LangGraph, And Vite

**Files:**
- Create: `backend/tests/agent_e2e/start_langgraph.py`
- Create: `backend/tests/agent_e2e/graph.py`
- Create: `backend/tests/agent_e2e/langgraph.json`
- Modify: `frontend/playwright.config.ts`
- Rewrite: `frontend/e2e/agent-workspace.spec.ts`

- [ ] **Step 1: Make the fixture graph support the browser sequence using `ScriptedChatModel`**

Use one scripted sequence long enough for the single serial browser scenario: ordinary text, MCP tool call plus final text after approval, another MCP call plus final text after rejection, and an MCP sleep call for cancellation. Backend tests already cover built-in tools; the browser fixture must not call a real market-data source. Reuse `tests.agent.fakes.ScriptedChatModel`; do not define another `BaseChatModel` subclass. This six-reply `graph.py` is browser-only and must not replace or import Task 5's 16-reply `server_graph.py`.

The E2E module must intentionally import production graph first, then build again with the injected model:

```python
from agent import graph as production_graph  # module-level production builder runs once
from langchain_core.messages import AIMessage
from tests.agent.fakes import ScriptedChatModel

graph = asyncio.run(production_graph.build_graph(
    model=ScriptedChatModel([
        AIMessage(content="客观测试回复完成。"),
        AIMessage(content="", tool_calls=[{
            "id": "call-approve", "name": "fixture_echo", "args": {"value": "客观 MCP 数据"},
        }]),
        AIMessage(content="MCP 客观结果已返回。"),
        AIMessage(content="", tool_calls=[{
            "id": "call-reject", "name": "fixture_echo", "args": {"value": "不应执行"},
        }]),
        AIMessage(content="MCP 调用已拒绝，本轮未执行工具。"),
        AIMessage(content="", tool_calls=[{
            "id": "call-stop", "name": "fixture_sleep", "args": {"seconds": 5.0},
        }]),
    ]),
))
```

Use the retained stdio `tests/agent/fake_mcp_server.py`; the settings generated by the helper names the server `fixture`, so its tool becomes `fixture_echo` and is covered by HITL.
Create the browser-only `langgraph.json` with graph path `./graph.py:graph` and
`CORS_ALLOW_ORIGINS=http://127.0.0.1:5873`; do not modify `server_langgraph.json`.

- [ ] **Step 2: Implement an isolated LangGraph startup helper**

`start_langgraph.py` must require `VR_E2E_ROOT`, reject overlap with `Path.home() / ".vibe-research"`, copy only the browser `graph.py`, `langgraph.json`, and shared `skills/` into that root, write a `0600` settings file with invalid model credentials plus an absolute stdio Python command, then `os.execve` the pinned CLI:

```python
args = [
    str(BACKEND / ".venv/bin/langgraph"), "dev",
    "--config", str(root / "langgraph.json"),
    "--host", "127.0.0.1", "--port", os.environ["VR_E2E_LANGGRAPH_PORT"],
    "--no-browser", "--no-reload",
]
env = {**os.environ, "PYTHONPATH": str(BACKEND), "VR_AGENT_SETTINGS": str(settings_path)}
os.execve(args[0], args, env)
```

The helper must not add a fourth network service for MCP.

- [ ] **Step 3: Replace Playwright's two-service fixture with three services**

Use fixed isolated ports `8873` (FastAPI), `2873` (LangGraph), and `5873` (Vite), `reuseExistingServer: false`, and an OS temp root. The entries are:

```typescript
webServer: [
  {
    command: `cd ../backend && exec .venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port ${BACKEND_PORT} --log-level warning`,
    url: `http://127.0.0.1:${BACKEND_PORT}/api/health`,
    env: { VR_DATA_DIR: dataRoot, VR_REPORTS_DIR: path.join(dataRoot, "myreports") },
    reuseExistingServer: false,
  },
  {
    command: `cd ../backend && exec .venv/bin/python tests/agent_e2e/start_langgraph.py`,
    url: `http://127.0.0.1:${LANGGRAPH_PORT}/docs`,
    env: { VR_E2E_ROOT: langGraphRoot, VR_E2E_LANGGRAPH_PORT: String(LANGGRAPH_PORT) },
    reuseExistingServer: false,
  },
  {
    command: `exec node node_modules/vite/bin/vite.js --port ${FRONTEND_PORT} --strictPort --host 127.0.0.1`,
    url: `http://127.0.0.1:${FRONTEND_PORT}`,
    env: {
      VITE_API_URL: `http://127.0.0.1:${BACKEND_PORT}`,
      VITE_AGENT_API_URL: `http://127.0.0.1:${LANGGRAPH_PORT}`,
    },
    reuseExistingServer: false,
  },
]
```

Remove shell `rm -rf`; the helper owns only its exact validated temp root and uses a fresh per-run directory.

- [ ] **Step 4: Rewrite browser acceptance to retained capabilities only**

Use this single serial scenario, which exactly matches the model queue:

```typescript
test("native Agent workspace persists threads and handles MCP approval", async ({ page }) => {
  await page.goto("/agent");
  await send(page, "给出客观测试回复");
  await expect(page.getByText("客观测试回复完成")).toBeVisible();
  await page.reload();
  const originalThread = page.getByRole("button", { name: "给出客观测试回复" });
  await expect(originalThread).toBeVisible();
  await originalThread.click();
  await expect(page.getByTestId("agent-chat-column")
    .getByText("给出客观测试回复", { exact: true })).toBeVisible();
  await expect(page.getByTestId("agent-chat-column")
    .getByText("客观测试回复完成。", { exact: true })).toBeVisible();

  await page.getByLabel("新建会话").click();
  await send(page, "调用 MCP 并批准");
  await expect(page.getByRole("region", { name: "MCP 工具审批" })).toBeVisible();
  await page.getByRole("radio", { name: /批准/ }).check();
  await page.getByRole("button", { name: "提交全部决定" }).click();
  await expect(page.getByText(/MCP 客观结果/)).toBeVisible();

  await send(page, "调用 MCP 并拒绝");
  await page.getByRole("radio", { name: /拒绝/ }).check();
  await page.getByRole("button", { name: "提交全部决定" }).click();
  await expect(page.getByText(/已拒绝/)).toBeVisible();

  await send(page, "启动慢速 MCP 后停止");
  await page.getByRole("radio", { name: /批准/ }).check();
  await page.getByRole("button", { name: "提交全部决定" }).click();
  const stop = page.getByTitle("停止", { exact: true });
  await expect(stop).toBeVisible();
  await stop.click();
  await expect(page.getByTitle("发送", { exact: true })).toBeVisible();

  const activeItem = page.locator('[data-testid^="agent-thread-"][data-active="true"]');
  await activeItem.getByRole("button", { name: "重命名会话" }).click();
  await activeItem.getByRole("textbox", { name: "会话标题" }).fill("E2E 审批会话");
  await activeItem.getByRole("button", { name: "确认重命名" }).click();
  await expect(page.getByRole("button", { name: "E2E 审批会话" })).toBeVisible();

  await originalThread.click();
  await expect(page.getByTestId("agent-chat-column")
    .getByText("客观测试回复完成。", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "E2E 审批会话" }).click();
  await expect(page.getByTestId("agent-chat-column").getByText(/MCP 客观结果/)).toBeVisible();
  const originalItem = page.locator('[data-testid^="agent-thread-"]').filter({ has: originalThread });
  await originalItem.getByRole("button", { name: "删除会话" }).click();
  await expect(page.getByRole("button", { name: "给出客观测试回复" })).toHaveCount(0);
});
```

The sequence above is the thread switch/rename/delete and cancel contract. Do not add an extra model turn. Do not test Inspector, artifacts, sources, budgets, management panels, session approval, retry buttons, or steer-away.

- [ ] **Step 5: Add browser CORS and layout assertions**

Add this helper and a separate test. It verifies the Vite rewrite through a real browser fetch, the exact hostile-Origin boundary, and stable desktop/mobile layout:

```typescript
test.describe.configure({ mode: "serial" });

const E2E_LANGGRAPH_URL = "http://127.0.0.1:2873";

async function expectNoWorkspaceOverlap(page: Page) {
  expect(await page.evaluate(() =>
    document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1,
  )).toBe(true);
  const composer = await page.getByLabel("Agent 消息").boundingBox();
  const approval = await page.getByRole("region", { name: "MCP 工具审批" }).boundingBox();
  expect(composer).not.toBeNull();
  expect(approval).not.toBeNull();
  const intersects = !(
    approval!.x + approval!.width <= composer!.x ||
    composer!.x + composer!.width <= approval!.x ||
    approval!.y + approval!.height <= composer!.y ||
    composer!.y + composer!.height <= approval!.y
  );
  expect(intersects).toBe(false);
}

test("Agent proxy, CORS boundary, and responsive layout", async ({ page, request }, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/agent");
  await page.getByLabel("新建会话").click();
  await expect(page.getByRole("region", { name: "MCP 工具审批" }))
    .toContainText("暂无待审批工具调用");
  const proxyResult = await page.evaluate(async () => {
    const response = await fetch("/agent-api/threads/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ limit: 1 }),
    });
    return { ok: response.ok, body: await response.json() };
  });
  expect(proxyResult.ok).toBe(true);
  expect(Array.isArray(proxyResult.body)).toBe(true);
  await expectNoWorkspaceOverlap(page);
  await page.screenshot({ path: testInfo.outputPath("agent-desktop.png"), fullPage: true });

  const hostile = { Origin: "https://evil.example.com" };
  const preflight = await request.fetch(`${E2E_LANGGRAPH_URL}/threads`, {
    method: "OPTIONS",
    headers: {
      ...hostile,
      "Access-Control-Request-Method": "POST",
      "Access-Control-Request-Headers": "content-type",
    },
  });
  expect(preflight.status()).toBe(400);
  const actual = await request.post(`${E2E_LANGGRAPH_URL}/threads`, {
    headers: hostile,
    data: {},
  });
  expect(actual.status()).toBe(200);
  expect(actual.headers()["access-control-allow-origin"]).toBeUndefined();

  await page.setViewportSize({ width: 390, height: 844 });
  await page.reload();
  await expect(page.getByRole("button", { name: "打开会话列表" })).toBeVisible();
  await page.getByRole("button", { name: "打开会话列表" }).click();
  await expect(page.getByRole("dialog", { name: "会话" })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("agent-mobile.png"), fullPage: true });
  await page.getByRole("button", { name: "关闭" }).click();
  await expectNoWorkspaceOverlap(page);
});
```

Import `type Page` from `@playwright/test`, and place the `test.describe.configure({ mode: "serial" })` call immediately after imports, before constants, helpers, or either test declaration. Do not assert that the hostile actual request is rejected: CORS prevents the response from being browser-readable but leaves simple-request blind writes possible.
The fresh-thread click and stable empty approval region make this test independent of the prior test's cancelled run/checkpoint; do not hydrate or assert a stale interrupt merely to obtain layout geometry.

- [ ] **Step 6: Re-run the server harness, then run E2E twice and inspect screenshots**

Run:

```bash
cd backend && .venv/bin/pytest tests/agent/test_langgraph_server.py -q
```

Expected: PASS after the browser graph/config are added, proving Task 12 did not change the backend harness's `server_graph.py` or `server_langgraph.json` terminal behavior.

Then run:

```bash
cd frontend
npm run test:e2e
npm run test:e2e
```

Expected: both runs PASS without port reuse or real-user data; trace/screenshot artifacts show nonblank desktop and mobile workspace, stable thread column/drawer, readable approval controls, and no overlap.

- [ ] **Step 7: Commit E2E migration**

```bash
git add backend/tests/agent_e2e/start_langgraph.py backend/tests/agent_e2e/graph.py backend/tests/agent_e2e/langgraph.json frontend/playwright.config.ts frontend/e2e/agent-workspace.spec.ts
git commit -m "test(agent): migrate browser suite to LangGraph server"
```

### Task 13: Update Operator Docs, Privacy Boundaries, And Final Gates

**Files:**
- Modify: `README.md`
- Modify: `README_en.md`
- Modify: `backend/README.md`
- Modify: `AGENTS.md`
- Modify: `backend/.env.example`
- Modify: `CHANGELOG.md`
- Possibly modify: `backend/requirements.txt` only for packages proven orphaned by the search below

- [ ] **Step 1: Update environment and start commands**

Add `VR_AGENT_SETTINGS` to `backend/.env.example`:

```dotenv
# Agent 工作台静态配置；默认 ~/.vibe-research/agent/settings.json，修改后需重启 LangGraph Server
VR_AGENT_SETTINGS=
```

Document the three local commands in Chinese and English READMEs:

```bash
cd backend
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8900
.venv/bin/langgraph dev --host 127.0.0.1 --port 2024 --no-browser

cd ../frontend
npm install
npm run dev
```

State that Python 3.11+ is required, `langgraph dev` does not install requirements, and the Agent server must never bind to LAN/public addresses.

- [ ] **Step 2: Document settings, permissions, and the precise CORS limit**

Include the full example from the design spec, explain `chmod 600 ~/.vibe-research/agent/settings.json`, and state:

```text
The allowlist prevents an unrelated website from reading LangGraph responses. It is not authentication or CSRF protection: browser simple requests can still blindly create threads or submit runs and consume model/data-source quota. This residual risk is accepted only for loopback, single-user operation.
```

Update the root `AGENTS.md` privacy section: Agent workspace keys are now plaintext only in the local settings file, while legacy `vr-llm` remains browser localStorage. Do not claim all API keys remain in frontend localStorage. In both the layout description and "AI layer" section, replace the stale "23 tools" count with the verified current count of 24.

- [ ] **Step 3: Record migration behavior in the changelog**

Add one dated entry covering: native LangGraph threads/checkpoints/HITL; static model/MCP/Skills config; separate `:2024` process; no migration of old Agent JSON sessions; removal of Inspector/artifacts/sources/budgets/management and request-level Agent model settings; unchanged legacy chat/debate/reflection.

- [ ] **Step 4: Perform dependency and reference cleanup based on evidence**

Run:

```bash
rg -n "ag_ui|ag-ui|@ag-ui/client|react-ag-ui|/api/agent|vr-agent-model" backend frontend \
  -g '!node_modules/**' -g '!.venv/**'
rg -n "python-multipart|multipart|yaml|commonmark" backend -g '*.py' -g 'requirements*.txt'
rg -n "agent\.(artifacts|capabilities|governance|mcp|models|policy|protocol|provenance|router|runs|runtime|skills|stores|tool_executor)" backend -g '*.py'
```

Expected: no removed runtime paths. Remove `python-multipart`, `PyYAML`, or `commonmark` only if the second search proves there is no remaining production or test import; do not remove packages merely because the old Agent used them.

- [ ] **Step 5: Run the full acceptance matrix**

Run:

```bash
(cd backend && .venv/bin/pip check && .venv/bin/pytest -m "not live")
(cd frontend && npm test && npm run test:unit && npm run build && npm run test:e2e)
git diff --check
git status --short
```

Expected: every command PASS. `git status --short` lists only this migration's intended changes plus the user's pre-existing untracked `.superpowers/`, `.vr-dev/`, `.zcode/`, `AGENTS.md`, old 1C plan, and `scripts/`; no settings file, `.langgraph_api/`, API key, user data, test result, or screenshot is staged.

- [ ] **Step 6: Manually smoke the documented local startup**

Start FastAPI, LangGraph Server, and Vite in separate managed terminals using the documented commands. Verify:

```bash
curl -fsS http://127.0.0.1:8900/api/health
curl -fsS http://127.0.0.1:2024/assistants/search -H 'Content-Type: application/json' -d '{}'
curl -fsS http://127.0.0.1:5899/agent >/dev/null
```

Expected: health JSON, an assistant list containing graph ID `agent`, and a successful frontend response. Stop only the processes started for this smoke; do not kill a user's existing server on the same port.

- [ ] **Step 7: Commit documentation and any proven dependency cleanup**

```bash
git add README.md README_en.md backend/README.md AGENTS.md backend/.env.example CHANGELOG.md
git add backend/requirements.txt  # only when Step 4 made evidence-backed removals
git commit -m "docs(agent): document native LangGraph workspace"
```

## Final Acceptance Checklist

- [ ] Agent page sends no request-level model secret and calls no `/api/agent/*` endpoint.
- [ ] Native LangGraph API owns thread creation, streaming, stop, edit/fork, retry, checkpoint, and interrupt resume.
- [ ] Process restart restores both ordinary thread messages and a pending HITL checkpoint under the full pinned LangChain/core 1.5.5 contract.
- [ ] All 24 current `tools.py` definitions are available as LangChain tools, preserve JSON error results/output truncation, and execute under one process-wide lock.
- [ ] MCP names are server-prefixed, duplicate names fail startup, and every MCP call permits only approve/reject decisions.
- [ ] Skills use Deep Agents discovery plus only `ls`/`read_file`, with traversal blocked by the single configured filesystem root.
- [ ] `backend/app.py` has no Agent router/lifecycle side effects; legacy chat/debate/reflection and SSRF tests remain green.
- [ ] LangGraph CORS permits the configured local frontend origin, blocks hostile response reads, and explicitly tests/accepts simple-request blind writes.
- [ ] Old Agent JSON files are neither read nor deleted; old custom session/run/checkpoint code is absent from the repository runtime.
- [ ] Desktop/mobile Playwright flows cover stream, refresh then explicit-list-selection hydration, thread create/switch/rename/delete, MCP approve/reject, cancel, proxy rewriting, and non-overlapping layout; automatic restoration of the previously active thread is not promised on 0.0.27.
- [ ] Documentation consistently says Python 3.11+, requirements must be preinstalled, settings contain plaintext secrets with `0600`, and Agent Server is loopback-only.
