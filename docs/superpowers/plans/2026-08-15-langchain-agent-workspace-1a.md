# LangChain Agent Workspace 1A Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and de-risk the first vertical Agent loop: a new `/agent` entry streams LangChain `create_agent` text and built-in tool activity over AG-UI, while the compatibility bridge proves interrupt/resume, per-request graph reconstruction, cancellation, and secret isolation on the locked dependency set.

**Architecture:** Add a focused `backend/agent/` package without changing the existing AI routes. A custom FastAPI endpoint creates a fresh `LangGraphAgent` for each HTTP request and delegates version-sensitive event and resume conversion to `AgentProtocolBridge`; active Spike state stays in memory until milestone 1B adds authoritative JSON thread/run storage. The existing React/Vite app gets only a minimal `/agent` page using `assistant-ui`, `@assistant-ui/react-ag-ui`, and a small `HttpAgent` wrapper; the full three-column workspace, durable history, Skills, MCP, approvals, artifacts, and policy UI remain later milestones.

**Tech Stack:** Python 3, FastAPI, Pydantic, LangChain 1.3.15, LangGraph 1.2.11, `ag-ui-langgraph` 0.0.42, `ag-ui-protocol` 0.1.15, React 19, TypeScript, assistant-ui 0.15.14, AG-UI client 0.0.58, Vitest, React Testing Library

---

## Scope And Stop Rule

This plan implements milestone 1A only. It intentionally uses an in-memory `RunCoordinator` and one in-memory thread per browser runtime. Do not add JSON stores, thread CRUD, retry persistence, Skill loading, MCP clients, artifact storage, budget settings, or the final three-column layout here; those belong to 1B-1D.

Tasks 1-7 are the protocol Spike. If Task 7 cannot prove all of the following on the exact locked versions, stop, record the failing contract in the task commit, and revise the dependency/design decision before proceeding to Task 8:

- legacy `CUSTOM/on_interrupt` becomes a standard `RUN_FINISHED.outcome.type="interrupt"`;
- standard `resume[]` is fully validated and converted to ordered HITL decisions;
- resume sends `messages=[]` and does not enter the adapter regenerate path;
- the old Graph and `LangGraphAgent` can be discarded, then an equivalent Graph plus a new adapter can resume from the same `MemorySaver`;
- no model key enters events, checkpoint state, callback/config metadata, or coordinator state.

## File Map

- `backend/requirements.txt`: exact backend runtime pins.
- `backend/requirements-dev.txt`: async test runner pin.
- `backend/agent/models.py`: immutable model/secrets/run request value objects.
- `backend/agent/tool_registry.py`: conversion of the 24 existing schemas and `tools.exec_tool` to LangChain tools.
- `backend/agent/runtime.py`: model construction, `create_agent`, checkpointer reuse, and request-scoped adapter creation.
- `backend/agent/protocol.py`: legacy interrupt capture, bridge IDs, standard outcomes, and ordered resume conversion.
- `backend/agent/runs.py`: 1A-only in-memory active handle/coordinator and cancellation state.
- `backend/agent/router.py`: custom `/api/agent/run` streaming route.
- `backend/tests/agent/`: offline unit, protocol, and endpoint contract tests.
- `frontend/src/lib/agent/model-config.ts`: independent local Agent model configuration.
- `frontend/src/lib/agent/runtime.tsx`: version-sensitive AG-UI runtime and HTTP wrapper.
- `frontend/src/lib/agent/approval.ts`: sole wrapper around version-sensitive interrupt hooks.
- `frontend/src/components/agent/AgentThread.tsx`: minimal assistant-ui thread/composer surface.
- `frontend/src/pages/Agent.tsx`: model settings plus runtime page assembly.
- `frontend/src/pages/Agent.test.tsx`: browser-state and interaction contracts.

### Task 1: Lock Dependencies And Establish Test Baselines

**Files:**
- Modify: `backend/requirements.txt`
- Modify: `backend/requirements-dev.txt`
- Modify: `frontend/package.json`
- Modify: `frontend/package-lock.json`

- [ ] **Step 1: Capture the pre-change regression baseline**

Run:

```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/pytest -m "not live"

cd ../frontend
npm ci
npm test
npm run build
```

Expected: all existing non-live backend tests and Node frontend tests pass; the production build completes. If an existing failure occurs, record it before changing dependencies rather than weakening an assertion.

- [ ] **Step 2: Add the exact backend protocol/runtime pins**

Append this block to `backend/requirements.txt`:

```text

# Agent workspace protocol/runtime group. Upgrade and contract-test as one unit.
langchain==1.3.15
langchain-openai==1.5.1
langgraph==1.2.11  # 1.2.9 与 langchain 1.3.15 冲突（要求 >=1.2.11），经用户确认上调
ag-ui-langgraph==0.0.42
ag-ui-protocol==0.1.15
ag-ui-a2ui-toolkit==0.0.4
# langchain-mcp-adapters 与 mootdx 的 httpx 上下限互斥（mcp 需 >=0.27，mootdx 需 <0.26），
# 经用户确认 1A 移除、待 1C 引入 MCP 时再评估拆分
```

Append this exact test dependency to `backend/requirements-dev.txt`:

```text
pytest-asyncio==0.25.3
```

- [ ] **Step 3: Install the exact frontend runtime and test dependencies**

Run:

```bash
cd frontend
npm install --save-exact @assistant-ui/react@0.15.14 @assistant-ui/react-ag-ui@0.0.54 @ag-ui/client@0.0.58
npm install --save-dev --save-exact vitest@3.2.4 jsdom@26.1.0 @testing-library/react@16.3.0 @testing-library/jest-dom@6.6.3 @testing-library/user-event@14.6.1
```

Expected: `package.json` contains exact versions without `^` or `~`, and `package-lock.json` changes.

- [ ] **Step 4: Verify imports and the locked compatibility surface**

Run:

```bash
cd backend
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/python - <<'PY'
from ag_ui.core.events import RunFinishedEvent
from ag_ui.core.types import RunAgentInput
from ag_ui_langgraph import LangGraphAgent
from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.memory import MemorySaver


async def probe_tool(**kwargs):
    return kwargs


schema = {
    "type": "object",
    "properties": {"code": {"type": "string"}},
    "required": ["code"],
}
tool = StructuredTool.from_function(
    coroutine=probe_tool,
    name="probe_tool",
    description="Probe raw JSON Schema support.",
    args_schema=schema,
)
assert tool.args_schema == schema
finished = RunFinishedEvent(
    thread_id="thread-probe",
    run_id="run-probe",
    outcome={"type": "interrupt", "interrupts": []},
)
assert finished.model_dump()["outcome"]["type"] == "interrupt"
assert callable(getattr(LangGraphAgent, "prepare_regenerate_stream", None))
print("backend agent compatibility ok")
PY

cd ../frontend
node --input-type=module <<'JS'
import { ComposerPrimitive, MessagePrimitive, ThreadPrimitive } from "@assistant-ui/react";
import {
  useAgUiInterrupts,
  useAgUiSteerAway,
  useAgUiSubmitInterruptResponses,
} from "@assistant-ui/react-ag-ui";
import { HttpAgent } from "@ag-ui/client";

const agent = new HttpAgent({
  url: "/api/agent/run",
  headers: { "X-Probe": "1" },
  fetch: async () => new Response(null, { status: 200 }),
});
if (typeof agent.requestInit !== "function") throw new Error("HttpAgent.requestInit is unavailable");
if (!ThreadPrimitive.If || !ComposerPrimitive.Cancel || !MessagePrimitive.Content) {
  throw new Error("required assistant-ui primitives are unavailable");
}
for (const hook of [useAgUiInterrupts, useAgUiSteerAway, useAgUiSubmitInterruptResponses]) {
  if (typeof hook !== "function") throw new Error("required AG-UI interrupt hook is unavailable");
}
console.log("frontend agent compatibility ok");
JS
```

Expected: the commands print `backend agent compatibility ok` and `frontend agent compatibility ok`. Treat any failed assertion as a locked-version incompatibility and stop before Task 2; do not defer it to the later Spike tasks.

- [ ] **Step 5: Re-run the baseline and commit**

```bash
cd backend && .venv/bin/pytest -m "not live"
cd ../frontend && npm test && npm run build
git add backend/requirements.txt backend/requirements-dev.txt frontend/package.json frontend/package-lock.json
git commit -m "build: lock agent workspace dependencies"
```

Expected: all baseline commands pass before the commit.

### Task 2: Define The Request-Scoped Configuration Boundary

**Files:**
- Create: `backend/agent/__init__.py`
- Create: `backend/agent/models.py`
- Create: `backend/tests/agent/__init__.py`
- Create: `backend/tests/agent/test_models.py`

- [ ] **Step 1: Write failing tests for sanitized and secret values**

Create `backend/tests/agent/test_models.py`:

```python
from pydantic import SecretStr, ValidationError
import pytest

from agent.models import ModelRef, RunSecrets, RuntimeForwardedProps


def test_model_ref_uses_frontend_field_names_and_contains_no_secret():
    ref = ModelRef.model_validate({
        "provider": "deepseek",
        "baseURL": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
    })
    assert ref.base_url == "https://api.deepseek.com/v1"
    assert set(ref.model_dump()) == {"provider", "base_url", "model"}
    assert "model_api_key" not in ref.model_dump_json()


def test_run_secret_masks_key():
    secrets = RunSecrets(model_api_key=SecretStr("spike-secret"))
    assert "spike-secret" not in repr(secrets)
    assert secrets.model_api_key.get_secret_value() == "spike-secret"


def test_runtime_props_require_model_and_reject_retry_in_1a():
    with pytest.raises(ValidationError):
        RuntimeForwardedProps.model_validate({
            "model": {"provider": "openai", "baseURL": "https://api.openai.com/v1", "model": "gpt-5-mini"},
            "retryOf": "run-old",
        })
```

- [ ] **Step 2: Run the tests and verify the import failure**

Run: `cd backend && .venv/bin/pytest tests/agent/test_models.py -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'agent'`.

- [ ] **Step 3: Implement immutable request models**

Create empty `backend/agent/__init__.py` and `backend/tests/agent/__init__.py` files, then create `backend/agent/models.py`:

```python
from __future__ import annotations

from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, SecretStr, model_validator


class ModelRef(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")

    provider: str = Field(min_length=1)
    base_url: str = Field(
        min_length=1,
        validation_alias=AliasChoices("baseURL", "base_url"),
        serialization_alias="baseURL",
    )
    model: str = Field(min_length=1)


class RunSecrets(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    model_api_key: SecretStr


class RuntimeForwardedProps(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    model: ModelRef
    command: dict | None = None
    retry_of: str | None = Field(default=None, validation_alias="retryOf")

    @model_validator(mode="after")
    def reject_later_milestone_retry(self) -> "RuntimeForwardedProps":
        if self.retry_of is not None:
            raise ValueError("retry is introduced with durable run history in milestone 1B")
        return self


class RunPhase(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: Literal["running", "awaiting_approval", "completed", "failed", "cancelled"]
```

- [ ] **Step 4: Run tests and commit**

```bash
cd backend && .venv/bin/pytest tests/agent/test_models.py -q
git add backend/agent/__init__.py backend/agent/models.py backend/tests/agent/__init__.py backend/tests/agent/test_models.py
git commit -m "feat(agent): define request scoped model boundary"
```

Expected: 3 tests pass.

### Task 3: Adapt All Existing Tools Once

**Files:**
- Create: `backend/agent/tool_registry.py`
- Create: `backend/tests/agent/test_tool_registry.py`

- [ ] **Step 1: Write failing conversion and result-boundary tests**

Create `backend/tests/agent/test_tool_registry.py`:

```python
import asyncio
import json

import pytest
import tools
from agent.tool_registry import BUILTIN_RESULT_LIMIT, build_builtin_tools

pytestmark = pytest.mark.asyncio


def test_all_existing_tools_are_converted_exactly_once():
    converted = build_builtin_tools()
    assert len(converted) == 24
    assert [tool.name for tool in converted] == tools.TOOL_NAMES
    assert len({tool.name for tool in converted}) == len(converted)
    for source, converted_tool in zip(tools.TOOLS, converted, strict=True):
        assert converted_tool.description == source["function"]["description"]
        assert converted_tool.args_schema == source["function"]["parameters"]


async def test_error_results_are_json_tool_results(monkeypatch):
    monkeypatch.setattr(tools, "exec_tool", lambda name, args: {"error": f"{name} failed"})
    result = await build_builtin_tools()[0].ainvoke({"codes": ["600519"]})
    assert json.loads(result) == {"error": "query_quote failed"}


async def test_large_results_are_trimmed_before_leaving_registry(monkeypatch):
    monkeypatch.setattr(tools, "exec_tool", lambda name, args: {"text": "x" * 7000})
    result = await build_builtin_tools()[0].ainvoke({"codes": ["600519"]})
    assert len(result) <= BUILTIN_RESULT_LIMIT
    assert result.endswith("...[truncated]")


async def test_builtins_share_one_per_run_execution_lock(monkeypatch):
    active = 0
    maximum = 0

    def fake(name, args):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        import time
        time.sleep(0.02)
        active -= 1
        return {"name": name}

    monkeypatch.setattr(tools, "exec_tool", fake)
    converted = build_builtin_tools()
    await asyncio.gather(
        converted[0].ainvoke({"codes": ["600519"]}),
        converted[1].ainvoke({"code": "600519"}),
    )
    assert maximum == 1
```

- [ ] **Step 2: Run tests and verify the missing module**

Run: `cd backend && .venv/bin/pytest tests/agent/test_tool_registry.py -q`

Expected: FAIL because `agent.tool_registry` does not exist.

- [ ] **Step 3: Implement the minimal registry adapter**

Create `backend/agent/tool_registry.py`:

```python
from __future__ import annotations

import asyncio
import json
from typing import Any

from langchain_core.tools import StructuredTool

import tools as legacy_tools

BUILTIN_RESULT_LIMIT = 6000


def _encode_result(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, default=str)
    if len(encoded) <= BUILTIN_RESULT_LIMIT:
        return encoded
    return encoded[: BUILTIN_RESULT_LIMIT - len("...[truncated]")] + "...[truncated]"


def _build_one(schema: dict[str, Any], execution_lock: asyncio.Lock) -> StructuredTool:
    function = schema["function"]
    name = function["name"]

    async def invoke(**kwargs: Any) -> str:
        async with execution_lock:
            result = await asyncio.to_thread(legacy_tools.exec_tool, name, kwargs)
        return _encode_result(result)

    return StructuredTool.from_function(
        coroutine=invoke,
        name=name,
        description=function["description"],
        args_schema=function["parameters"],
    )


def build_builtin_tools() -> list[StructuredTool]:
    execution_lock = asyncio.Lock()
    return [_build_one(schema, execution_lock) for schema in legacy_tools.TOOLS]
```

- [ ] **Step 4: Run focused and legacy tool tests**

```bash
cd backend
.venv/bin/pytest tests/agent/test_tool_registry.py tests/test_agents.py::test_every_tool_has_handler tests/test_agents.py::test_exec_tool_never_raises -q
```

Expected: all focused and selected legacy tests pass without network calls.

- [ ] **Step 5: Commit**

```bash
git add backend/agent/tool_registry.py backend/tests/agent/test_tool_registry.py
git commit -m "feat(agent): adapt built in tools for langchain"
```

### Task 4: Add A Deterministic Offline Agent Fixture

**Files:**
- Create: `backend/tests/agent/fakes.py`
- Create: `backend/tests/agent/test_runtime_stream.py`
- Create: `backend/agent/runtime.py`

- [ ] **Step 1: Create the scripted fake chat model used by all protocol tests**

Create `backend/tests/agent/fakes.py`:

```python
from collections import deque
from typing import Any, Sequence

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import BaseTool


class ScriptedChatModel(BaseChatModel):
    replies: deque[AIMessage]

    def __init__(self, replies: Sequence[AIMessage]):
        super().__init__(replies=deque(replies))

    @property
    def _llm_type(self) -> str:
        return "scripted-agent-spike"

    def bind_tools(self, tools: Sequence[BaseTool | dict[str, Any]], **kwargs: Any):
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self.replies.popleft())])
```

- [ ] **Step 2: Write the failing multi-step runtime contract**

Create `backend/tests/agent/test_runtime_stream.py`:

```python
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
import pytest

from agent.runtime import AgentFactory
from tests.agent.fakes import ScriptedChatModel

pytestmark = pytest.mark.asyncio


async def test_create_agent_completes_a_tool_then_text_run():
    calls: list[str] = []

    @tool
    def lookup(code: str) -> str:
        """Look up one test code."""
        calls.append(code)
        return "price=10"

    model = ScriptedChatModel([
        AIMessage(content="", tool_calls=[{"id": "call-1", "name": "lookup", "args": {"code": "600519"}}]),
        AIMessage(content="fixture answer"),
    ])
    handle = AgentFactory().create(model=model, tools=[lookup], thread_id="thread-1")
    events = [event async for event in handle.new_adapter("protocol-1").run(handle.start_input("hello"))]

    assert calls == ["600519"]
    assert [event.type for event in events][0].value == "RUN_STARTED"
    assert [event.type for event in events][-1].value == "RUN_FINISHED"
    text = "".join(
        getattr(event, "delta", "")
        for event in events
        if getattr(event.type, "value", event.type) == "TEXT_MESSAGE_CONTENT"
    )
    assert text == "fixture answer"
```

- [ ] **Step 3: Run the test and verify the missing factory**

Run: `cd backend && .venv/bin/pytest tests/agent/test_runtime_stream.py -q`

Expected: FAIL because `AgentFactory` is not defined.

- [ ] **Step 4: Implement the in-memory runtime handle and fresh adapter factory**

Create `backend/agent/runtime.py` with these public contracts:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from ag_ui.core.types import RunAgentInput, UserMessage
from ag_ui_langgraph import LangGraphAgent
from langchain.agents import create_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.checkpoint.memory import MemorySaver

import chat


@dataclass
class RuntimeHandle:
    thread_id: str
    graph: Any
    checkpointer: MemorySaver
    model: BaseChatModel
    tools: Sequence[BaseTool]

    def start_input(self, content: str, run_id: str = "protocol-1") -> RunAgentInput:
        return RunAgentInput(
            thread_id=self.thread_id,
            run_id=run_id,
            state={},
            messages=[UserMessage(id=f"{run_id}-user", content=content)],
            tools=[],
            context=[],
            forwarded_props={},
        )

    def new_adapter(self, run_id: str) -> LangGraphAgent:
        return LangGraphAgent(name=f"vibe-research-{run_id}", graph=self.graph)


class AgentFactory:
    def create(
        self,
        *,
        model: BaseChatModel,
        tools: Sequence[BaseTool],
        thread_id: str,
        checkpointer: MemorySaver | None = None,
        middleware: Sequence[Any] = (),
    ) -> RuntimeHandle:
        saver = checkpointer or MemorySaver()
        graph = create_agent(
            model,
            tools=list(tools),
            system_prompt=chat.SYSTEM_PROMPT.format(context="Agent 工作台"),
            middleware=list(middleware),
            checkpointer=saver,
        )
        return RuntimeHandle(thread_id, graph, saver, model, tuple(tools))
```

The executor must keep `new_adapter()` as a constructor call. Do not cache or clone a `LangGraphAgent`.

- [ ] **Step 5: Run the focused test and commit**

```bash
cd backend && .venv/bin/pytest tests/agent/test_runtime_stream.py -q
git add backend/agent/runtime.py backend/tests/agent/fakes.py backend/tests/agent/test_runtime_stream.py
git commit -m "test(agent): prove offline langchain tool loop"
```

Expected: the deterministic model completes one tool call and one text response with no network access.

### Task 5: Convert Legacy Interrupts To Standard AG-UI Outcomes

**Files:**
- Create: `backend/agent/protocol.py`
- Create: `backend/tests/agent/test_protocol_bridge.py`

- [ ] **Step 1: Write failing bridge-ID and outcome tests**

Create `backend/tests/agent/test_protocol_bridge.py`:

```python
import json

from ag_ui.core.events import (
    CustomEvent, EventType, RunFinishedEvent,
    ToolCallArgsEvent, ToolCallEndEvent, ToolCallStartEvent,
)
from agent.protocol import AgentProtocolBridge


def legacy_interrupt(tool_call_id: str = "call-1") -> CustomEvent:
    return CustomEvent(
        type=EventType.CUSTOM,
        name="on_interrupt",
        value={
            "action_requests": [{"name": "mcp__demo__quote", "args": {"code": "600519"}, "description": "review"}],
            "review_configs": [{"action_name": "mcp__demo__quote", "allowed_decisions": ["approve", "reject"]}],
        },
    )


def observe_tool_call(bridge: AgentProtocolBridge, tool_call_id: str = "call-1") -> None:
    bridge.convert(ToolCallStartEvent(
        tool_call_id=tool_call_id,
        tool_call_name="mcp__demo__quote",
        parent_message_id="assistant-1",
    ))
    bridge.convert(ToolCallArgsEvent(
        tool_call_id=tool_call_id,
        delta=json.dumps({"code": "600519"}),
    ))
    bridge.convert(ToolCallEndEvent(tool_call_id=tool_call_id))


def test_legacy_interrupt_is_suppressed_and_finishes_with_standard_outcome():
    bridge = AgentProtocolBridge("thread-1", "run-1")
    observe_tool_call(bridge)
    assert bridge.convert(legacy_interrupt()) == []
    converted = bridge.convert(RunFinishedEvent(thread_id="thread-1", run_id="run-1"))
    assert len(converted) == 1
    payload = converted[0].model_dump(by_alias=True, mode="json")
    assert payload["outcome"]["type"] == "interrupt"
    assert payload["outcome"]["interrupts"][0]["reason"] == "tool_call"
    assert "expiresAt" not in payload["outcome"]["interrupts"][0]


def test_repeated_observation_reuses_bridge_id():
    bridge = AgentProtocolBridge("thread-1", "run-1")
    observe_tool_call(bridge)
    bridge.convert(legacy_interrupt())
    first = bridge.pending[0].bridge_interrupt_id
    bridge.convert(legacy_interrupt())
    assert bridge.pending[0].bridge_interrupt_id == first


def test_unknown_custom_event_fails_closed():
    bridge = AgentProtocolBridge("thread-1", "run-1")
    event = CustomEvent(type=EventType.CUSTOM, name="surprise", value={})
    assert bridge.convert(event)[0].code == "UNSUPPORTED_CUSTOM_EVENT"


def test_cancelled_event_uses_the_standard_client_event_name():
    payload = AgentProtocolBridge("thread-1", "run-1").cancelled().model_dump(by_alias=True)
    assert payload == {"type": "RUN_CANCELLED", "threadId": "thread-1", "runId": "run-1"}


def test_interleaved_tool_fragments_keep_call_ids_and_order():
    bridge = AgentProtocolBridge("thread-1", "run-1")
    bridge.convert(ToolCallStartEvent(tool_call_id="call-a", tool_call_name="tool_a"))
    bridge.convert(ToolCallStartEvent(tool_call_id="call-b", tool_call_name="tool_b"))
    bridge.convert(ToolCallArgsEvent(tool_call_id="call-a", delta='{"code":'))
    bridge.convert(ToolCallArgsEvent(tool_call_id="call-b", delta='{"symbol":'))
    bridge.convert(ToolCallArgsEvent(tool_call_id="call-a", delta='"600519"}'))
    bridge.convert(ToolCallArgsEvent(tool_call_id="call-b", delta='"AAPL"}'))
    bridge.convert(ToolCallEndEvent(tool_call_id="call-b"))
    bridge.convert(ToolCallEndEvent(tool_call_id="call-a"))
    bridge.convert(CustomEvent(type=EventType.CUSTOM, name="on_interrupt", value={
        "action_requests": [
            {"name": "tool_a", "args": {"code": "600519"}},
            {"name": "tool_b", "args": {"symbol": "AAPL"}},
        ],
        "review_configs": [
            {"action_name": "tool_a", "allowed_decisions": ["approve", "reject"]},
            {"action_name": "tool_b", "allowed_decisions": ["approve", "reject"]},
        ],
    }))
    assert [item.tool_call_id for item in bridge.pending] == ["call-a", "call-b"]
```

- [ ] **Step 2: Run and verify failure**

Run: `cd backend && .venv/bin/pytest tests/agent/test_protocol_bridge.py -q`

Expected: FAIL because `agent.protocol` does not exist.

- [ ] **Step 3: Implement stable IDs and standard outcome conversion**

Create `backend/agent/protocol.py` with these exact public types and behavior:

```python
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Literal
from uuid import uuid4

from ag_ui.core.events import (
    CustomEvent,
    RunErrorEvent,
    RunFinishedEvent,
    ToolCallArgsEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
)
from ag_ui.core.types import ConfiguredBaseModel


class RunCancelledEvent(ConfiguredBaseModel):
    type: Literal["RUN_CANCELLED"] = "RUN_CANCELLED"
    thread_id: str
    run_id: str


@dataclass(frozen=True)
class PendingInterrupt:
    bridge_interrupt_id: str
    order: int
    tool_call_id: str
    value: dict[str, Any]


class AgentProtocolBridge:
    def __init__(
        self,
        thread_id: str,
        run_id: str,
        pending: list[PendingInterrupt] | None = None,
    ):
        self.thread_id = thread_id
        self.run_id = run_id
        self.pending = pending if pending is not None else []
        self._tool_calls: dict[str, dict[str, Any]] = {}
        self._tool_call_order: list[str] = []

    def convert(self, event: Any) -> list[Any]:
        if isinstance(event, ToolCallStartEvent):
            self._tool_calls[event.tool_call_id] = {"name": event.tool_call_name, "args_text": ""}
            self._tool_call_order.append(event.tool_call_id)
            return [event]
        if isinstance(event, ToolCallArgsEvent):
            if event.tool_call_id not in self._tool_calls:
                raise ValueError("tool args arrived before tool start")
            self._tool_calls[event.tool_call_id]["args_text"] += event.delta
            return [event]
        if isinstance(event, ToolCallEndEvent):
            if event.tool_call_id not in self._tool_calls:
                raise ValueError("tool end arrived before tool start")
            return [event]
        if isinstance(event, CustomEvent):
            if event.name != "on_interrupt":
                return [RunErrorEvent(message=f"Unsupported custom event: {event.name}", code="UNSUPPORTED_CUSTOM_EVENT")]
            self._capture(event.value)
            return []
        if isinstance(event, RunFinishedEvent) and self.pending:
            interrupts = [{
                "id": item.bridge_interrupt_id,
                "reason": "tool_call",
                "message": item.value["action_requests"][0].get("description", "Tool approval required"),
                "toolCallId": item.tool_call_id,
                "responseSchema": {
                    "type": "object",
                    "properties": {
                        "decision": {"enum": ["approve", "reject"]},
                        "scope": {"enum": ["once", "thread_session"]},
                    },
                    "required": ["decision", "scope"],
                },
            } for item in self.pending]
            return [RunFinishedEvent(
                thread_id=self.thread_id,
                run_id=self.run_id,
                outcome={"type": "interrupt", "interrupts": interrupts},
            )]
        return [event]

    def cancelled(self) -> RunCancelledEvent:
        return RunCancelledEvent(thread_id=self.thread_id, run_id=self.run_id)

    def _capture(self, value: dict[str, Any]) -> None:
        actions = value.get("action_requests") or []
        reviews = value.get("review_configs") or []
        if not actions or len(actions) != len(reviews):
            raise ValueError("legacy interrupt has an invalid HITL request")
        candidates = [
            (tool_call_id, self._tool_calls[tool_call_id])
            for tool_call_id in self._tool_call_order
            if not any(item.tool_call_id == tool_call_id for item in self.pending)
        ]
        if len(candidates) < len(actions):
            # A repeated observation reuses the already captured mapping.
            if len(self.pending) == len(actions):
                for item, action, review in zip(
                    sorted(self.pending, key=lambda pending: pending.order),
                    actions,
                    reviews,
                    strict=True,
                ):
                    if item.value != {"action_requests": [action], "review_configs": [review]}:
                        raise ValueError("reloaded interrupt differs from the pending mapping")
                return
            raise ValueError("interrupt cannot be matched to observed tool calls")
        for action, review, (tool_call_id, observed) in zip(actions, reviews, candidates, strict=True):
            observed_args = json.loads(observed["args_text"] or "{}")
            if action.get("name") != observed["name"] or action.get("args") != observed_args:
                raise ValueError("interrupt action does not match the streamed tool call")
            self.pending.append(PendingInterrupt(
                str(uuid4()), len(self.pending), tool_call_id,
                {"action_requests": [action], "review_configs": [review]},
            ))
```

- [ ] **Step 4: Run tests and commit**

```bash
cd backend && .venv/bin/pytest tests/agent/test_protocol_bridge.py -q
git add backend/agent/protocol.py backend/tests/agent/test_protocol_bridge.py
git commit -m "feat(agent): bridge legacy interrupts to ag-ui outcomes"
```

Expected: stable-ID, outcome, no-`expiresAt`, and unknown-event tests pass.

### Task 6: Validate Resume And Steer-Away Shapes Fail-Closed

**Files:**
- Modify: `backend/agent/protocol.py`
- Modify: `backend/tests/agent/test_protocol_bridge.py`

- [ ] **Step 1: Add failing ordered-resume and cancellation tests**

Append to `backend/tests/agent/test_protocol_bridge.py`:

```python
import pytest


def test_resolved_entries_become_ordered_hitl_decisions():
    bridge = AgentProtocolBridge("thread-1", "run-1")
    observe_tool_call(bridge, "call-1")
    bridge.convert(legacy_interrupt("call-1"))
    observe_tool_call(bridge, "call-2")
    bridge.convert(legacy_interrupt("call-2"))
    entries = [
        {"interruptId": bridge.pending[1].bridge_interrupt_id, "status": "resolved", "payload": {"decision": "reject", "scope": "once"}},
        {"interruptId": bridge.pending[0].bridge_interrupt_id, "status": "resolved", "payload": {"decision": "approve", "scope": "once"}},
    ]
    assert bridge.resume_value(entries) == {
        "decisions": [{"type": "approve"}, {"type": "reject", "message": "User rejected the tool call"}]
    }


@pytest.mark.parametrize("entries", [[], [{"interruptId": "unknown", "status": "resolved", "payload": {"decision": "approve", "scope": "once"}}]])
def test_incomplete_or_unknown_resume_fails_closed(entries):
    bridge = AgentProtocolBridge("thread-1", "run-1")
    observe_tool_call(bridge)
    bridge.convert(legacy_interrupt())
    with pytest.raises(ValueError):
        bridge.resume_value(entries)


def test_all_cancelled_is_steer_away_and_never_a_hitl_decision():
    bridge = AgentProtocolBridge("thread-1", "run-1")
    observe_tool_call(bridge)
    bridge.convert(legacy_interrupt())
    entries = [{"interruptId": bridge.pending[0].bridge_interrupt_id, "status": "cancelled"}]
    assert bridge.is_steer_away(entries) is True
    with pytest.raises(ValueError):
        bridge.resume_value(entries)
```

- [ ] **Step 2: Run and verify the missing methods**

Run: `cd backend && .venv/bin/pytest tests/agent/test_protocol_bridge.py -q`

Expected: FAIL with missing `resume_value` and `is_steer_away`.

- [ ] **Step 3: Implement full-set validation and ordered conversion**

Add these methods to `AgentProtocolBridge`:

```python
    def _ordered_entries(self, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        by_id: dict[str, dict[str, Any]] = {}
        for entry in entries:
            interrupt_id = entry.get("interruptId")
            if not interrupt_id or interrupt_id in by_id:
                raise ValueError("resume contains a missing or duplicate interrupt ID")
            by_id[interrupt_id] = entry
        expected = {item.bridge_interrupt_id for item in self.pending}
        if set(by_id) != expected:
            raise ValueError("resume must answer every pending bridge interrupt exactly once")
        return [by_id[item.bridge_interrupt_id] for item in sorted(self.pending, key=lambda item: item.order)]

    def is_steer_away(self, entries: list[dict[str, Any]]) -> bool:
        ordered = self._ordered_entries(entries)
        return bool(ordered) and all(entry.get("status") == "cancelled" for entry in ordered)

    def resume_value(self, entries: list[dict[str, Any]]) -> dict[str, Any]:
        ordered = self._ordered_entries(entries)
        decisions = []
        for entry in ordered:
            if entry.get("status") != "resolved":
                raise ValueError("cancelled interrupts are transport-level steer-away, not HITL decisions")
            payload = entry.get("payload") or {}
            if payload == {"decision": "approve", "scope": "once"}:
                decisions.append({"type": "approve"})
            elif payload == {"decision": "reject", "scope": "once"}:
                decisions.append({"type": "reject", "message": "User rejected the tool call"})
            else:
                raise ValueError("unsupported approval payload")
        return {"decisions": decisions}
```

Do not add `thread_session` behavior here. It requires the MCP/session allowance registry from 1C; the 1A schema may display the future option, but the Spike must reject it rather than pretend it is persisted.

- [ ] **Step 4: Run tests and commit**

```bash
cd backend && .venv/bin/pytest tests/agent/test_protocol_bridge.py -q
git add backend/agent/protocol.py backend/tests/agent/test_protocol_bridge.py
git commit -m "feat(agent): validate ordered interrupt resumes"
```

### Task 7: Prove Cross-Graph Resume And Secret Exclusion

**Files:**
- Modify: `backend/agent/runtime.py`
- Modify: `backend/tests/agent/test_runtime_stream.py`
- Create: `backend/tests/agent/test_resume_contract.py`

- [ ] **Step 1: Write the fail-stop cross-Graph resume test**

Create `backend/tests/agent/test_resume_contract.py`:

```python
import gc
import weakref

import pytest
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from agent.models import ModelRef, RunSecrets
from agent.protocol import AgentProtocolBridge
from agent.runtime import AgentFactory, RunConfigMismatch
from tests.agent.fakes import ScriptedChatModel

pytestmark = pytest.mark.asyncio
SENTINEL = "sk-agent-spike-do-not-persist"


def assert_secret_absent(secret: str, *values: object) -> None:
    for value in values:
        rendered = repr(value)
        if hasattr(value, "model_dump_json"):
            rendered += value.model_dump_json()
        assert secret not in rendered


class MetadataRecorder(BaseCallbackHandler):
    def __init__(self):
        self.metadata: list[dict] = []

    def on_chain_start(self, serialized, inputs, **kwargs):
        self.metadata.append(dict(kwargs.get("metadata") or {}))


async def test_resume_uses_empty_messages_new_graph_and_new_adapter():
    executed: list[str] = []

    @tool
    def approval_tool(code: str) -> str:
        """Read one protected fixture value."""
        executed.append(code)
        return "approved-result"

    ref = ModelRef(provider="fixture", base_url="https://example.com/v1", model="fixture-model")
    first_model = ScriptedChatModel([AIMessage(
        content="",
        tool_calls=[{"id": "call-approval", "name": "approval_tool", "args": {"code": "600519"}}],
    )])
    factory = AgentFactory()
    handle = factory.create(
        model_ref=ref,
        secrets=RunSecrets(model_api_key=SENTINEL),
        model_builder=lambda model_ref, secrets: first_model,
        tools=[approval_tool],
        thread_id="thread-resume",
        middleware=[HumanInTheLoopMiddleware(interrupt_on={
            "approval_tool": {"allowed_decisions": ["approve", "reject"]},
        })],
    )
    bridge = AgentProtocolBridge("thread-resume", "protocol-start")
    recorder = MetadataRecorder()
    first_adapter = handle.new_adapter("protocol-start", callbacks=[recorder])
    first_events = []
    async for event in first_adapter.run(handle.start_input("approve fixture", "protocol-start")):
        first_events.extend(bridge.convert(event))

    assert executed == []
    assert len(bridge.pending) == 1
    assert first_events[-1].model_dump()["outcome"]["type"] == "interrupt"
    first_adapter_ref = weakref.ref(first_adapter)
    saver = handle.checkpointer
    first_state = await handle.graph.aget_state({"configurable": {"thread_id": handle.thread_id}})
    assert_secret_absent(SENTINEL, first_events, saver.storage, handle.snapshot, first_state.values)

    handle.release_graph()
    del first_adapter, first_model
    gc.collect()
    assert first_adapter_ref() is None
    assert handle.graph is None and handle.model is None

    final_model = ScriptedChatModel([AIMessage(content="fixture complete")])
    factory.resume(
        handle=handle,
        model_ref=ref,
        secrets=RunSecrets(model_api_key="second-secret"),
        model_builder=lambda model_ref, secrets: final_model,
    )
    second_adapter = handle.new_adapter("protocol-resume", callbacks=[recorder])

    async def fail_regenerate(*args, **kwargs):
        raise AssertionError("resume entered regenerate path")

    assert callable(getattr(second_adapter, "prepare_regenerate_stream", None))
    second_adapter.prepare_regenerate_stream = fail_regenerate
    resume_value = bridge.resume_value([{
        "interruptId": bridge.pending[0].bridge_interrupt_id,
        "status": "resolved",
        "payload": {"decision": "approve", "scope": "once"},
    }])
    resume_input = handle.resume_input("protocol-resume", resume_value)
    assert resume_input.messages == []

    resumed_events = [event async for event in second_adapter.run(resume_input)]
    assert handle.checkpointer is saver
    assert executed == ["600519"]
    text = "".join(
        getattr(event, "delta", "")
        for event in resumed_events
        if getattr(event.type, "value", event.type) == "TEXT_MESSAGE_CONTENT"
    )
    assert text == "fixture complete"
    resumed_state = await handle.graph.aget_state({"configurable": {"thread_id": handle.thread_id}})
    assert_secret_absent(SENTINEL, resumed_events, saver.storage, handle.snapshot, resumed_state.values)
    assert_secret_absent(SENTINEL, recorder.metadata, repr(handle))

    handle.release_graph()
    builder_called = False

    def builder_must_not_run(model_ref, secrets):
        nonlocal builder_called
        builder_called = True
        raise AssertionError("model builder ran before config validation")

    with pytest.raises(RunConfigMismatch) as exc:
        factory.resume(
            handle=handle,
            model_ref=ref.model_copy(update={"model": "changed-model"}),
            secrets=RunSecrets(model_api_key="third-secret"),
            model_builder=builder_must_not_run,
        )
    assert exc.value.code == "RUN_CONFIG_MISMATCH"
    assert builder_called is False
    assert_secret_absent(SENTINEL, exc.value, saver.storage, handle.snapshot)
```

- [ ] **Step 2: Add callback metadata to the same leak scan**

The `MetadataRecorder` and assertions in Step 1 are the executable callback scan. Add this structural assertion immediately after creating the handle:

```python
    assert "secrets" not in handle.__dataclass_fields__
    assert "model_api_key" not in handle.__dataclass_fields__
```

- [ ] **Step 3: Run the new tests and verify they fail for missing reconstruction APIs**

Run:

```bash
cd backend
.venv/bin/pytest tests/agent/test_resume_contract.py -q
```

Expected: FAIL because `RuntimeHandle.release_graph`, `AgentFactory.resume`, and sanitized snapshot/config checks are not implemented.

- [ ] **Step 4: Extend the runtime boundary minimally**

Refactor `RuntimeHandle` so it stores `model_ref`, `checkpointer`, tool tuple, middleware tuple, and counters independently from request-scoped `graph`/`model`. Define `RunSnapshot` as a frozen dataclass containing only `model_ref`, `thread_id`, `model_calls`, `tool_calls`, and `transitions`. Define `RunConfigMismatch(RuntimeError)` with class attribute `code = "RUN_CONFIG_MISMATCH"`. Add:

```python
    def release_graph(self) -> None:
        self.graph = None
        self.model = None

    def resume_input(self, run_id: str, resume_value: dict[str, Any]) -> RunAgentInput:
        return RunAgentInput(
            thread_id=self.thread_id,
            run_id=run_id,
            state={},
            messages=[],
            tools=[],
            context=[],
            forwarded_props={"command": {"resume": resume_value}},
        )

    def new_adapter(self, run_id: str, callbacks: Sequence[Any] = ()) -> LangGraphAgent:
        return LangGraphAgent(
            name=f"vibe-research-{run_id}",
            graph=self.graph,
            config={"callbacks": list(callbacks)},
        )
```

Change `AgentFactory.create` to accept `model_ref`, `secrets`, `model_builder`, tools, thread ID, optional checkpointer, and middleware. It calls the builder, creates the Graph, and stores only `RunSnapshot`, tool/middleware tuples, Graph, and model on the handle. Add `AgentFactory.resume(handle, model_ref, secrets, model_builder)` that first compares `model_ref` to `handle.model_ref`, then calls `model_builder(model_ref, secrets)` and rebuilds `create_agent(..., checkpointer=handle.checkpointer)` from the immutable tool/middleware snapshot. Keep `RunSecrets` out of the handle and all `RunnableConfig` values.

Add `from agent.models import ModelRef, RunSecrets` with the test imports. Then update the Task 4 call site in `test_runtime_stream.py` from `create(model=model, ...)` to:

```python
    handle = AgentFactory().create(
        model_ref=ModelRef(provider="fixture", base_url="https://example.com/v1", model="fixture"),
        secrets=RunSecrets(model_api_key="fixture-key"),
        model_builder=lambda model_ref, secrets: model,
        tools=[lookup],
        thread_id="thread-1",
    )
```

- [ ] **Step 5: Run the complete Spike test set**

```bash
cd backend
.venv/bin/pytest tests/agent/test_models.py tests/agent/test_tool_registry.py tests/agent/test_runtime_stream.py tests/agent/test_protocol_bridge.py tests/agent/test_resume_contract.py -q
```

Expected: all tests pass. If cross-Graph resume, `messages=[]`, fresh adapter construction, or secret exclusion does not pass on the locked versions, stop here under the Scope And Stop Rule.

- [ ] **Step 6: Record recursion-limit evidence without productizing an unproven limit**

Add `backend/tests/agent/test_transition_limit.py`. Run a two-step interrupted fixture once with `recursion_limit=3` and resume it with the same checkpointer. If the resume is reliably rejected before a fourth product-run transition, encode that observed count in `test_recursion_limit_is_cumulative_across_resume`. Otherwise add this exact fallback contract and keep 1A free of a claimed transition policy:

```python
from agent.runtime import PRODUCT_TRANSITION_LIMIT


def test_transition_limit_is_not_advertised_when_cross_resume_is_unproven():
    assert PRODUCT_TRANSITION_LIMIT is None
```

- [ ] **Step 7: Commit the passing Spike**

```bash
git add backend/agent/runtime.py backend/tests/agent/test_runtime_stream.py backend/tests/agent/test_resume_contract.py backend/tests/agent/test_transition_limit.py
git commit -m "test(agent): prove interrupt resume across rebuilt graphs"
```

### Task 8: Add Request-Scoped OpenAI-Compatible Model Construction

**Files:**
- Modify: `backend/agent/runtime.py`
- Create: `backend/tests/agent/test_model_factory.py`

- [ ] **Step 1: Write failing construction and SSRF tests**

Create `backend/tests/agent/test_model_factory.py`:

```python
import pytest
from langchain_openai import ChatOpenAI

import chat
from agent.models import ModelRef, RunSecrets
from agent.runtime import build_chat_model


@pytest.mark.parametrize("provider,base_url,model", [
    ("openai", "https://api.openai.com/v1", "gpt-5-mini"),
    ("deepseek", "https://api.deepseek.com/v1", "deepseek-chat"),
])
def test_builds_openai_compatible_model_after_ssrf_check(monkeypatch, provider, base_url, model):
    checked = []
    monkeypatch.setattr(chat, "_check_base_url", checked.append)
    built = build_chat_model(
        ModelRef(provider=provider, base_url=base_url, model=model),
        RunSecrets(model_api_key="request-key"),
    )
    assert isinstance(built, ChatOpenAI)
    assert checked == [base_url]
    assert built.model_name == model
    assert str(built.openai_api_base).rstrip("/") == base_url.rstrip("/")
    assert "request-key" not in repr(built)


def test_blank_key_is_rejected_before_model_construction(monkeypatch):
    monkeypatch.setattr(chat, "_check_base_url", lambda value: None)
    ref = ModelRef(provider="openai", base_url="https://api.openai.com/v1", model="gpt-5-mini")
    with pytest.raises(ValueError, match="X-VR-Agent-Model-Key"):
        build_chat_model(ref, RunSecrets(model_api_key="   "))
```

- [ ] **Step 2: Run and verify the missing function**

Run: `cd backend && .venv/bin/pytest tests/agent/test_model_factory.py -q`

Expected: FAIL because `build_chat_model` does not exist.

- [ ] **Step 3: Implement the request-scoped builder**

Add to `backend/agent/runtime.py`:

```python
from langchain_openai import ChatOpenAI
from pydantic import SecretStr
import chat


def build_chat_model(model_ref: ModelRef, secrets: RunSecrets) -> ChatOpenAI:
    chat._check_base_url(model_ref.base_url)
    key = secrets.model_api_key.get_secret_value().strip()
    if not key:
        raise ValueError("X-VR-Agent-Model-Key is required")
    return ChatOpenAI(
        model=model_ref.model,
        base_url=model_ref.base_url.rstrip("/"),
        api_key=SecretStr(key),
        temperature=0.2,
        streaming=True,
    )
```

The later route must call this function inside the request task. Do not log its arguments or place `RunSecrets` in callbacks/config.

- [ ] **Step 4: Run tests and commit**

```bash
cd backend && .venv/bin/pytest tests/agent/test_model_factory.py tests/agent/test_resume_contract.py -q
git add backend/agent/runtime.py backend/tests/agent/test_model_factory.py
git commit -m "feat(agent): build request scoped compatible models"
```

### Task 9: Stream Through A Custom FastAPI Endpoint

**Files:**
- Create: `backend/agent/runs.py`
- Create: `backend/agent/router.py`
- Modify: `backend/app.py`
- Create: `backend/tests/agent/test_router.py`
- Modify: `backend/tests/test_api.py`

- [ ] **Step 1: Write failing endpoint contracts**

Start `backend/tests/agent/test_router.py` with this real request contract, using `monkeypatch.setattr("agent.router.build_chat_model", ...)` to return `ScriptedChatModel([AIMessage(content="endpoint answer")])`:

```python
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

import app as app_module
from tests.agent.fakes import ScriptedChatModel

def make_client(host: str = "127.0.0.1") -> TestClient:
    return TestClient(app_module.app, client=(host, 50000))


client = make_client()

START = {
    "threadId": "thread-endpoint",
    "runId": "protocol-endpoint",
    "state": {},
    "messages": [{"id": "user-endpoint", "role": "user", "content": "hello"}],
    "tools": [],
    "context": [],
    "forwardedProps": {
        "runtime": {
            "model": {
                "provider": "fixture",
                "baseURL": "https://example.com/v1",
                "model": "fixture-model",
            }
        }
    },
}


def test_start_streams_standard_ag_ui_events(monkeypatch):
    monkeypatch.setattr(
        "agent.router.build_chat_model",
        lambda model_ref, secrets: ScriptedChatModel([AIMessage(content="endpoint answer")]),
    )
    response = client.post(
        "/api/agent/run",
        json=START,
        headers={"X-VR-Agent-Model-Key": "request-only-key", "Accept": "text/event-stream"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "RUN_STARTED" in response.text
    assert "endpoint answer" in response.text
    assert "RUN_FINISHED" in response.text
    assert "request-only-key" not in response.text


def test_missing_model_key_fails_before_model_build(monkeypatch):
    called = False

    def fail_if_called(*args):
        nonlocal called
        called = True
        raise AssertionError("model builder ran")

    monkeypatch.setattr("agent.router.build_chat_model", fail_if_called)
    response = client.post("/api/agent/run", json=START)
    assert response.status_code == 400
    assert called is False
```

Add parameterized cases around the same `START` fixture for:

```text
valid start -> text/event-stream with RUN_STARTED, tool-call events, text, RUN_FINISHED
second start while thread is running -> 409 THREAD_BUSY
valid full resume -> same product handle, new LangGraphAgent, messages=[]
all-cancelled resume plus one new user message -> old handle closed and fresh run streamed
partial/mixed/unknown resume -> RUN_ERROR and pending tool is not called
bridge cancellation fixture -> standard RUN_CANCELLED event; real client disconnect -> coordinator status cancelled and no later event
model exception -> redacted RUN_ERROR
non-loopback HTTP with model key -> 400 INSECURE_MODEL_KEY_TRANSPORT before Graph construction
loopback HTTP -> allowed; trusted proxy IP plus VR_TRUST_PROXY_HEADERS=1 and X-Forwarded-Proto=https -> allowed
untrusted X-Forwarded-Proto=https -> rejected
```

Use `make_client("203.0.113.9")` for the non-loopback rejection case and `make_client()` for every ordinary HTTP success case. Do not depend on Starlette's default `("testclient", 50000)` address because it is intentionally not treated as loopback by `require_secure_model_key_transport`.

Build the steer-away request from the bridge ID returned by an interrupted fixture using this exact wire shape; define the fixture to return `pending_id`, the old `ActiveRunHandle`, and the protected tool's call list so the test can assert cancellation and release before the replacement run starts:

```python
from copy import deepcopy


steer = deepcopy(START)
steer["runId"] = "protocol-steer-away"
steer["messages"] = [{"id": "user-steer", "role": "user", "content": "use a different approach"}]
steer["forwardedProps"]["command"] = {
    "resume": [{"interruptId": pending_id, "status": "cancelled"}],
}
response = client.post(
    "/api/agent/run",
    json=steer,
    headers={"X-VR-Agent-Model-Key": "request-only-key", "Accept": "text/event-stream"},
)
assert response.status_code == 200
assert old_handle.phase == "cancelled"
assert old_handle.runtime.graph is None and old_handle.runtime.model is None
assert "RUN_STARTED" in response.text and "RUN_FINISHED" in response.text
assert protected_tool_calls == []
```

Test disconnect delivery through the ASGI receive channel instead of relying on `TestClient`, which buffers normal responses. Add this helper and use a spy around `coordinator.cancel`; the scripted adapter must pause after its first event so `http.disconnect` wins, then the test releases it and asserts that no later event was sent:

```python
import json


async def post_then_disconnect(app, payload: dict) -> list[dict]:
    body = json.dumps(payload).encode()
    incoming = iter([
        {"type": "http.request", "body": body, "more_body": False},
        {"type": "http.disconnect"},
    ])
    sent: list[dict] = []

    async def receive() -> dict:
        try:
            return next(incoming)
        except StopIteration:
            return {"type": "http.disconnect"}

    async def send(message: dict) -> None:
        sent.append(message)

    await app({
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/agent/run",
        "raw_path": b"/api/agent/run",
        "query_string": b"",
        "root_path": "",
        "state": {},
        "extensions": {},
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
        "headers": [
            (b"host", b"testserver"),
            (b"content-type", b"application/json"),
            (b"accept", b"text/event-stream"),
            (b"x-vr-agent-model-key", b"request-only-key"),
            (b"content-length", str(len(body)).encode()),
        ],
    }, receive, send)
    return sent
```

The disconnect assertion must verify one `coordinator.cancel(thread_id)` call, the captured handle's `phase == "cancelled"`, `handle.runtime.graph is None`, `handle.runtime.model is None`, and absence of any response body frame containing a post-cancellation fixture marker.

Add a CORS test to `backend/tests/test_api.py` that sends an `OPTIONS` preflight with `Access-Control-Request-Method: PATCH` and expects `access-control-allow-methods` to include `PATCH`.

- [ ] **Step 2: Run and verify route failures**

Run: `cd backend && .venv/bin/pytest tests/agent/test_router.py tests/test_api.py -q`

Expected: endpoint tests return 404 and the PATCH preflight assertion fails.

- [ ] **Step 3: Implement the 1A in-memory coordinator**

In `backend/agent/runs.py`, define an `ActiveRunHandle` dataclass containing `runtime: RuntimeHandle`, `phase: Literal["running", "awaiting_approval", "completed", "failed", "cancelled"]`, and `pending_interrupts: list[PendingInterrupt]`. Define one `asyncio.Lock` per thread, one `ActiveRunHandle` per thread, and explicit `acquire_start`, `acquire_resume`, `mark_awaiting_approval`, `steer_away`, `cancel`, and `release` methods. Each protocol request creates its new bridge with the active handle's same pending list, so bridge IDs survive adapter/Graph reconstruction. `mark_awaiting_approval` must set the phase and call `runtime.release_graph()` before the standard interrupt outcome is emitted. Terminal release must remove the handle. `steer_away` must validate every pending bridge ID under the lock, set the old phase to `cancelled`, call `runtime.release_graph()`, and only then create the replacement handle. `cancel` must likewise set `phase="cancelled"` and release the runtime before terminal removal, allowing the caller that captured the handle reference to verify the transition. Do not add disk I/O or retry state.

- [ ] **Step 4: Implement the custom streaming route**

In `backend/agent/router.py`, define `router = APIRouter(prefix="/api/agent")` and `POST /run` accepting `RunAgentInput`, `Request`, and `X-VR-Agent-Model-Key`. Bind the header to a local `model_key: str`, reject a missing or blank value, and construct `RunSecrets` from it inside the request task. Before parsing `RuntimeForwardedProps` or constructing the Graph, call `require_secure_model_key_transport(request)`: allow plain HTTP only when `request.client.host` parses as loopback; otherwise require `request.url.scheme == "https"`. Consult `X-Forwarded-Proto` only when `VR_TRUST_PROXY_HEADERS=1` and the direct client IP is a member of the comma-separated `VR_TRUSTED_PROXY_IPS`; all other forwarded headers are ignored.

Use `EventEncoder(accept=request.headers.get("accept"))` and call `selected_handle.runtime.new_adapter(...)` to create a fresh `LangGraphAgent` for this request. For resume, first compute `resume_value = bridge.resume_value(entries)`, construct the `messages=[]` adapter input, transition the active handle to `running`, and only then clear `selected_handle.pending_interrupts`; malformed resume leaves the list untouched. For steer-away, coordinator validation consumes and closes the old handle rather than turning cancellations into a resume value. Stream with:

```python
async def event_generator():
    try:
        async for event in adapter.run(adapter_input):
            if await request.is_disconnected():
                await coordinator.cancel(input_data.thread_id)
                return
            converted_events = bridge.convert(event)
            if isinstance(event, RunFinishedEvent) and bridge.pending:
                await coordinator.mark_awaiting_approval(input_data.thread_id)
            for converted in converted_events:
                yield encoder.encode(converted)
    except asyncio.CancelledError:
        await coordinator.cancel(input_data.thread_id)
        raise
    except Exception as exc:
        message = str(exc).replace(model_key, "[redacted]")[:1000]
        yield encoder.encode(RunErrorEvent(message=message, code="AGENT_RUN_FAILED"))
    finally:
        await coordinator.finish_if_terminal(input_data.thread_id)
```

Return `StreamingResponse(event_generator(), media_type=encoder.get_content_type())`. Classify only start, resume, and steer-away in 1A; reject retry with a structured `RETRY_REQUIRES_DURABLE_HISTORY` error until 1B. Never register `add_langgraph_fastapi_endpoint`.

- [ ] **Step 5: Register only the new router and PATCH CORS method**

In `backend/app.py`, import and include `agent.router.router`, and change only:

```python
allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"]
```

- [ ] **Step 6: Run endpoint and legacy route tests**

```bash
cd backend
.venv/bin/pytest tests/agent/test_router.py tests/test_api.py -q
.venv/bin/pytest tests/test_agents.py -q
```

Expected: endpoint/CORS tests pass; legacy `/api/chat`, `/api/debate`, and `/api/reflect` coverage remains green.

- [ ] **Step 7: Commit**

```bash
git add backend/agent/runs.py backend/agent/router.py backend/app.py backend/tests/agent/test_router.py backend/tests/test_api.py
git commit -m "feat(agent): add custom ag-ui streaming endpoint"
```

### Task 10: Add Frontend Test Infrastructure And Runtime Contracts

**Files:**
- Create: `frontend/vitest.config.ts`
- Create: `frontend/src/test/setup.ts`
- Modify: `frontend/package.json`
- Create: `frontend/src/lib/agent/model-config.ts`
- Create: `frontend/src/lib/agent/runtime.tsx`
- Create: `frontend/src/lib/agent/approval.ts`
- Create: `frontend/src/lib/agent/runtime.test.tsx`

- [ ] **Step 1: Configure Vitest without replacing existing Node tests**

Add `"test:unit": "vitest run"` to `frontend/package.json`; keep the existing `npm test` command unchanged. Create `frontend/vitest.config.ts`:

```typescript
import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    restoreMocks: true,
  },
});
```

Create `frontend/src/test/setup.ts`:

```typescript
import "@testing-library/jest-dom/vitest";

if (!globalThis.crypto) {
  Object.defineProperty(globalThis, "crypto", { value: {} });
}
if (!globalThis.crypto.randomUUID) {
  Object.defineProperty(globalThis.crypto, "randomUUID", {
    value: () => "00000000-0000-4000-8000-000000000001",
  });
}
```

- [ ] **Step 2: Write failing runtime boundary tests**

In `frontend/src/lib/agent/runtime.test.tsx`, use this shared input and request capture:

```typescript
import { afterEach, expect, it, vi } from "vitest";
import type { RunAgentInput } from "@ag-ui/client";

import { AgentHttpAgent } from "./runtime";

const INPUT: RunAgentInput = {
  threadId: "thread-1",
  runId: "run-1",
  state: {},
  messages: [{ id: "user-1", role: "user", content: "hello" }],
  tools: [],
  context: [],
  forwardedProps: {},
};

const CONFIG = {
  provider: "deepseek",
  baseURL: "https://api.deepseek.com/v1",
  model: "deepseek-chat",
  apiKey: "model-secret",
};

afterEach(() => {
  localStorage.clear();
  vi.unstubAllGlobals();
});

it("puts the model key only in headers and sends a sanitized model ref", async () => {
  localStorage.setItem("vr-access-key", "backend-access");
  let captured: RequestInit | undefined;
  vi.stubGlobal("fetch", vi.fn(async (_url, init) => {
    captured = init;
    return new Response('data: {"type":"RUN_FINISHED","threadId":"thread-1","runId":"run-1"}\n\n', {
      status: 200,
      headers: { "content-type": "text/event-stream" },
    });
  }));
  const agent = new AgentHttpAgent(CONFIG, vi.fn());
  await new Promise<void>((resolve, reject) => {
    agent.run(INPUT).subscribe({ complete: resolve, error: reject });
  });
  const headers = new Headers(captured?.headers);
  expect(headers.get("Authorization")).toBe("Bearer backend-access");
  expect(headers.get("X-VR-Agent-Model-Key")).toBe("model-secret");
  const body = JSON.parse(String(captured?.body));
  expect(body.forwardedProps.runtime.model).toEqual({
    provider: "deepseek",
    baseURL: "https://api.deepseek.com/v1",
    model: "deepseek-chat",
  });
  expect(JSON.stringify(body)).not.toContain("model-secret");
  expect(body.forwardedProps.runtime.retryOf).toBeUndefined();
});
```

Add a second test whose mocked response is `409` with `{"code":"THREAD_BUSY"}` and assert `onConflict` receives that object exactly once. Add storage tests that call `saveAgentModelConfig`/`loadAgentModelConfig`, assert the key is `vr-agent-model`, and assert `localStorage.getItem("vr-llm")` remains null. Together these tests lock:

```text
AgentHttpAgent sends Authorization from authHeaders()
AgentHttpAgent sends X-VR-Agent-Model-Key only as a header
request body forwardedProps.runtime.model contains provider/baseURL/model but no key
retryOf is absent in 1A
structured 409 invokes onConflict instead of attempting stream reattachment
model config uses the vr-agent-model key and never the legacy vr-llm key
```

Mock `fetch`, call `agent.run(...)`, subscribe until completion/error, and inspect both `RequestInit.headers` and parsed body. Use the actual `HttpAgent` transport rather than source-text assertions.

- [ ] **Step 3: Run and verify missing modules**

Run: `cd frontend && npm run test:unit -- src/lib/agent/runtime.test.tsx`

Expected: FAIL because the Agent runtime modules do not exist.

- [ ] **Step 4: Implement independent model storage and the HTTP wrapper**

Create `model-config.ts` with this storage boundary:

```typescript
import { storageGet, storageRemove, storageSet } from "@/lib/storage";

const AGENT_MODEL_KEY = "vr-agent-model";

export type AgentModelConfig = {
  provider: string;
  baseURL: string;
  model: string;
  apiKey: string;
};

export function loadAgentModelConfig(): AgentModelConfig | null {
  const raw = storageGet(AGENT_MODEL_KEY);
  if (!raw) return null;
  try {
    const value = JSON.parse(raw) as Partial<AgentModelConfig>;
    if ([value.provider, value.baseURL, value.model, value.apiKey].every((item) => typeof item === "string")) {
      return value as AgentModelConfig;
    }
  } catch {
    return null;
  }
  return null;
}

export function saveAgentModelConfig(config: AgentModelConfig): void {
  if (!config.provider && !config.baseURL && !config.model && !config.apiKey) {
    storageRemove(AGENT_MODEL_KEY);
    return;
  }
  storageSet(AGENT_MODEL_KEY, JSON.stringify(config));
}
```

Create `runtime.tsx` with the following public boundary; retain the same merge order so callers cannot overwrite the sanitized model with an API key:

```tsx
import { useMemo, type ReactNode } from "react";
import { AssistantRuntimeProvider } from "@assistant-ui/react";
import { useAgUiRuntime } from "@assistant-ui/react-ag-ui";
import { HttpAgent, type RunAgentInput } from "@ag-ui/client";

import { authHeaders } from "@/lib/api";
import type { AgentModelConfig } from "./model-config";

type Conflict = { code?: string; detail?: string };

export class AgentHttpAgent extends HttpAgent {
  private readonly modelConfig: AgentModelConfig;

  constructor(config: AgentModelConfig, onConflict: (value: Conflict) => void) {
    const transportFetch = async (url: string, init: RequestInit) => {
      const response = await fetch(url, init);
      if (response.status === 409) {
        const payload = await response.clone().json().catch(() => ({})) as Conflict;
        onConflict(payload);
      }
      return response;
    };
    super({
      url: "/api/agent/run",
      headers: {
        ...authHeaders(),
        "X-VR-Agent-Model-Key": config.apiKey,
      },
      fetch: transportFetch,
    });
    this.modelConfig = config;
  }

  protected requestInit(input: RunAgentInput): RequestInit {
    const forwardedProps = input.forwardedProps ?? {};
    const runtime = typeof forwardedProps.runtime === "object" && forwardedProps.runtime
      ? forwardedProps.runtime as Record<string, unknown>
      : {};
    return super.requestInit({
      ...input,
      forwardedProps: {
        ...forwardedProps,
        runtime: {
          ...runtime,
          model: {
            provider: this.modelConfig.provider,
            baseURL: this.modelConfig.baseURL,
            model: this.modelConfig.model,
          },
        },
      },
    });
  }
}

export function AgentRuntimeProvider({
  config,
  onConflict,
  onError,
  children,
}: {
  config: AgentModelConfig;
  onConflict: (value: Conflict) => void;
  onError: (error: Error) => void;
  children: ReactNode;
}) {
  const agent = useMemo(
    () => new AgentHttpAgent(config, onConflict),
    [config, onConflict],
  );
  const runtime = useAgUiRuntime({
    agent,
    autoCancelPendingToolCalls: false,
    unstable_enableMessageQueue: false,
    onError,
  });
  return <AssistantRuntimeProvider runtime={runtime}>{children}</AssistantRuntimeProvider>;
}
```

Create `approval.ts` as the only production module that imports the three version-sensitive hooks:

```typescript
import {
  useAgUiInterrupts,
  useAgUiSteerAway,
  useAgUiSubmitInterruptResponses,
} from "@assistant-ui/react-ag-ui";

export function useApprovalBridge() {
  const interrupts = useAgUiInterrupts();
  const submit = useAgUiSubmitInterruptResponses();
  const steerAway = useAgUiSteerAway();
  return {
    pending: interrupts.map((item) => ({
      id: item.id,
      toolCallId: item.toolCallId,
      message: item.message ?? "Tool approval required",
    })),
    resolveAll: (decisions: readonly { id: string; decision: "approve" | "reject" }[]) =>
      submit(decisions.map((item) => ({
        interruptId: item.id,
        status: "resolved" as const,
        payload: { decision: item.decision, scope: "once" },
      }))),
    steerAway,
  };
}
```

No component calls this hook in 1A. Add a contract test that scans production TypeScript sources and asserts only `approval.ts` imports these three hook names; actual approval UI and session allowance behavior arrive in 1C.

- [ ] **Step 5: Run unit tests and TypeScript build**

```bash
cd frontend
npm run test:unit -- src/lib/agent/runtime.test.tsx
npm run build
```

Expected: runtime tests pass and strict TypeScript compilation succeeds.

- [ ] **Step 6: Commit**

```bash
git add frontend/vitest.config.ts frontend/src/test/setup.ts frontend/package.json frontend/src/lib/agent/model-config.ts frontend/src/lib/agent/runtime.tsx frontend/src/lib/agent/approval.ts frontend/src/lib/agent/runtime.test.tsx
git commit -m "feat(agent): add assistant ui ag-ui runtime"
```

### Task 11: Add The Minimal `/agent` Page And Running-State Guard

**Files:**
- Create: `frontend/src/components/agent/AgentThread.tsx`
- Create: `frontend/src/pages/Agent.tsx`
- Create: `frontend/src/pages/Agent.test.tsx`
- Modify: `frontend/src/router.tsx`
- Modify: `frontend/src/components/layout/Layout.tsx`

- [ ] **Step 1: Write failing user-visible page tests**

Using React Testing Library and a mocked `AgentRuntimeProvider`, verify:

```text
/agent page renders the Agent model form and thread composer
save writes only vr-agent-model
Composer input and Send are disabled while runtime is running
Stop remains enabled while running
Composer becomes enabled only after cancel settles
streamed assistant text and tool-call name are visible
runtime error is visible without exposing a supplied sentinel API key
```

- [ ] **Step 2: Run and verify missing components**

Run: `cd frontend && npm run test:unit -- src/pages/Agent.test.tsx`

Expected: FAIL because `pages/Agent.tsx` does not exist.

- [ ] **Step 3: Build the minimal assistant-ui thread**

Create `frontend/src/components/agent/AgentThread.tsx`:

```tsx
import {
  ComposerPrimitive,
  MessagePrimitive,
  ThreadPrimitive,
} from "@assistant-ui/react";
import { Send, Square } from "lucide-react";

function UserMessage() {
  return (
    <MessagePrimitive.Root className="ml-auto max-w-[80%] rounded-md bg-primary px-3 py-2 text-sm text-primary-foreground">
      <MessagePrimitive.Content />
    </MessagePrimitive.Root>
  );
}

function AssistantMessage() {
  return (
    <MessagePrimitive.Root className="max-w-[88%] text-sm leading-6 text-foreground">
      <MessagePrimitive.Content />
    </MessagePrimitive.Root>
  );
}

export function AgentThread() {
  return (
    <ThreadPrimitive.Root className="flex min-h-[560px] flex-col">
      <ThreadPrimitive.Viewport className="flex flex-1 flex-col gap-4 overflow-y-auto px-1 py-4">
        <ThreadPrimitive.Empty>
          <p className="m-auto text-sm text-muted-foreground">开始一项投研任务</p>
        </ThreadPrimitive.Empty>
        <ThreadPrimitive.Messages components={{ UserMessage, AssistantMessage }} />
        <ThreadPrimitive.ViewportFooter className="sticky bottom-0 mt-auto bg-background pt-3">
          <ComposerPrimitive.Root className="flex min-h-12 items-end gap-2 rounded-md border border-border bg-background p-2">
            <ThreadPrimitive.If running={false}>
              <ComposerPrimitive.Input
                aria-label="Agent 消息"
                placeholder="输入投研问题"
                className="max-h-40 min-h-8 flex-1 resize-none bg-transparent px-1 py-1 text-sm outline-none"
              />
              <ComposerPrimitive.Send className="grid h-9 w-9 place-items-center rounded-md bg-primary text-primary-foreground" title="发送">
                <Send className="h-4 w-4" />
              </ComposerPrimitive.Send>
            </ThreadPrimitive.If>
            <ThreadPrimitive.If running>
              <ComposerPrimitive.Input
                aria-label="Agent 消息"
                disabled
                className="max-h-40 min-h-8 flex-1 resize-none bg-transparent px-1 py-1 text-sm outline-none"
              />
              <ComposerPrimitive.Cancel className="grid h-9 w-9 place-items-center rounded-md border border-border" title="停止">
                <Square className="h-4 w-4" />
              </ComposerPrimitive.Cancel>
            </ThreadPrimitive.If>
          </ComposerPrimitive.Root>
        </ThreadPrimitive.ViewportFooter>
      </ThreadPrimitive.Viewport>
    </ThreadPrimitive.Root>
  );
}
```

The two composer branches have the same fixed geometry, so the running state cannot resize the page. Queueing remains disabled in `AgentRuntimeProvider`; the disabled branch cannot submit a second start request.

- [ ] **Step 4: Assemble independent model settings and runtime**

`Agent.tsx` loads/saves provider, Base URL, model, and API key through `model-config.ts`, passes the saved config to `AgentRuntimeProvider`, and renders `AgentThread`. The API key input uses `type="password"`; no visible text prints the key. Use restrained form sizing consistent with the existing Settings page.

- [ ] **Step 5: Add the route and navigation entry**

Import `Agent` in `frontend/src/router.tsx` and add `{ path: "/agent", element: <Agent /> }`. Add one `Bot`-icon NAV item `{ to: "/agent", icon: Bot, label: "Agent 工作台" }` in `Layout.tsx`; do not modify existing AI entries.

- [ ] **Step 6: Run tests and build**

```bash
cd frontend
npm run test:unit -- src/pages/Agent.test.tsx src/lib/agent/runtime.test.tsx
npm test
npm run build
```

Expected: component/runtime tests, existing source-contract tests, and the production build all pass.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/agent/AgentThread.tsx frontend/src/pages/Agent.tsx frontend/src/pages/Agent.test.tsx frontend/src/router.tsx frontend/src/components/layout/Layout.tsx
git commit -m "feat(agent): add minimal agent workspace entry"
```

### Task 12: Verify End-To-End 1A Contracts And Document Manual Provider Checks

**Files:**
- Create: `backend/tests/agent/test_agent_vertical_slice.py`
- Create: `docs/superpowers/verification/2026-08-15-langchain-agent-workspace-1a.md`

- [ ] **Step 1: Add one offline vertical-slice test**

Drive `POST /api/agent/run` through `TestClient(app_module.app, client=("127.0.0.1", 50000))` with the scripted model and a monkeypatched built-in tool. Parse the actual encoded event stream, filter out state/step events that are not part of this contract, and assert this order among the listed event types:

```text
RUN_STARTED
TOOL_CALL_START
TOOL_CALL_ARGS
TOOL_CALL_END
TOOL_CALL_RESULT
TEXT_MESSAGE_START
TEXT_MESSAGE_CONTENT
TEXT_MESSAGE_END
RUN_FINISHED
```

Add companion cases for tool failure returning structured content, model failure returning `RUN_ERROR`, and cancellation preventing late tool/model events. The test must remain offline.

- [ ] **Step 2: Run the entire backend regression suite**

Run:

```bash
cd backend
.venv/bin/pytest -m "not live"
```

Expected: all tests pass, including explicit smoke coverage for `/api/chat`, `/api/debate`, `/api/reflect`, `/api/agent/run`, and PATCH CORS preflight.

- [ ] **Step 3: Run the entire frontend regression suite**

Run:

```bash
cd frontend
npm test
npm run test:unit
npm run build
```

Expected: all Node tests and Vitest tests pass; TypeScript/Vite production build succeeds.

- [ ] **Step 4: Perform the two allowed live provider checks**

Start the backend and frontend in separate terminals:

```bash
cd backend && .venv/bin/uvicorn app:app --host 127.0.0.1 --port 8900
cd frontend && npm run dev -- --host 127.0.0.1
```

At `http://127.0.0.1:5899/agent`, run one tool-using prompt with an OpenAI configuration and one with a DeepSeek-compatible configuration. For each, record provider/model (never the key), text streaming, tool-call rendering, Stop behavior, terminal state, and browser-console/network errors in the verification document. These are manual exit checks because paid/public providers are forbidden in automated tests.

- [ ] **Step 5: Record the milestone decision**

Create `docs/superpowers/verification/2026-08-15-langchain-agent-workspace-1a.md` containing:

```markdown
# LangChain Agent Workspace 1A Verification

- Locked protocol contract suite: PASS
- Cross-equivalent-Graph MemorySaver resume: PASS
- Resume `messages=[]` regenerate guard: PASS
- Fresh `LangGraphAgent` per request: PASS
- API key leak scan across events/checkpoint/config/errors: PASS
- Disconnect/cancel terminal behavior: PASS
- OpenAI manual check: PASS
- DeepSeek-compatible manual check: PASS
- Legacy backend/frontend regression: PASS
- Transition limit decision: enabled only if the cross-resume hard-limit test passed; otherwise omitted from 1A

Decision: 1A exit criteria passed; implementation planning for 1B may begin.
```

If any item fails, write `FAIL` with the exact test/observed behavior and set `Decision: Stop before 1B and revise the locked runtime/protocol choice.` Do not mark a provider check PASS without actually running it.

- [ ] **Step 6: Commit verification artifacts**

```bash
git add backend/tests/agent/test_agent_vertical_slice.py docs/superpowers/verification/2026-08-15-langchain-agent-workspace-1a.md
git commit -m "test(agent): verify milestone 1a vertical slice"
```

## Final Self-Review Gate

Before declaring this plan executed, run:

```bash
rg -n 'T[B]D|T[O]DO|implement l[a]ter|similar t[o]|add appropriat[e]|handle edge case[s]' docs/superpowers/plans/2026-08-15-langchain-agent-workspace-1a.md
git status --short
```

Expected: the placeholder scan has no matches. `git status --short` shows no uncommitted 1A files; the pre-existing untracked `.superpowers/` directory may remain and must not be added or modified.

Confirm against the approved spec:

- Existing AI routes and UI actions were not migrated or changed.
- The endpoint is custom and never calls `add_langgraph_fastapi_endpoint`.
- Every request constructs a new `LangGraphAgent`.
- Built-in schemas map one-to-one to all 24 tools and execute serially within a run.
- Interrupt IDs are bridge-owned, stable, fully validated, and ordered independently of LangGraph internal IDs.
- Pure resume uses `messages=[]`, reuses only `MemorySaver` plus sanitized snapshot, and reconstructs the Graph/model from the new request key.
- The model key exists only in `X-VR-Agent-Model-Key` and request-scoped secret/model objects.
- Running Composer is disabled; Stop remains available.
- 1A automated tests are offline, while OpenAI and DeepSeek checks are explicitly manual.
- JSON persistence/revision/retry, Skills, MCP/session allowances, Artifacts, budgets, history adapter, and full workspace remain unimplemented until their own plans.
