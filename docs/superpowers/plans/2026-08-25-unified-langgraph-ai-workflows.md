# Unified LangGraph AI Workflows Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move every model conversation and analysis workflow from the legacy FastAPI AI exits to the existing local LangGraph Server, while retaining all objective-data FastAPI APIs, isolated histories, deterministic debate data collection, and the product's neutral research boundary.

**Architecture:** One loopback-only LangGraph Server registers the existing workspace Agent plus an embedded chat Graph and four fixed workflow Graphs. Python owns model construction, neutral policy, built-in Skills, typed workflow state, deterministic YAML compilation, checkpoint recovery, and process-wide tool concurrency; React keeps the existing business-page experiences but replaces NDJSON/model-key calls with the LangGraph SDK. The legacy AI routes remain operational until all new Graph and UI acceptance tests pass, then are removed in one final cutover task.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic 2, PyYAML 6, LangChain 1.3.15, LangGraph 1.2.11 / API 0.12.6 / SDK 0.4.2, Deep Agents 0.7.7, React 19, TypeScript 5.7, Zustand 5, LangGraph JS SDK 1.9.31, assistant-ui 0.15.16, pytest, Vitest, Playwright

---

## Execution Assumptions

- The source of truth is `docs/superpowers/specs/2026-08-25-unified-langgraph-ai-workflows-design.md`, including both adversarial-review revisions currently present in the worktree.
- Before implementation starts, commit the reviewed design and this plan together or in two documentation-only commits. Do not mix the existing uncommitted design changes into the first production-code commit.
- This is an ordered migration, not a flag-day rewrite. Tasks 1-12 leave `/api/chat`, `/api/debate`, and `/api/reflect` usable; Task 13 removes them only after browser acceptance passes.
- FastAPI objective-data routes and their response contracts are outside this migration. A diff touching those route bodies requires a separate justification and review.
- The LangGraph and Vite processes remain bound to `127.0.0.1`. `VR_API_KEY` protects FastAPI only and must never be described as LangGraph authentication.
- All tests use temporary settings, data, reports, Skills, threads, and ports. No test may read or mutate `~/.vibe-research/`.

## Product And Security Invariants

- All six Graphs prepend a code-owned neutral policy: no buy/sell recommendation, price prediction, target price, rating, ranking, timing, backtest, stock selection, or automated trading.
- Debate ends with disagreements, missing evidence, and a verification checklist. Reflection audits supplied reasoning and does not generate a new investment view.
- `backend/tools.py` remains the only built-in tool schema/handler source.
- The seven explicitly safe dossier tools have one process-wide maximum of four in-flight handlers. The six Eastmoney-throttled dossier tools and all unknown tools are serialized process-wide.
- Settings secrets never enter metadata, state, checkpoints, custom events, traces, logs, frontend requests, or error text.
- Workspace, embedded, and workflow threads remain isolated. Pre-migration threads without `metadata.channel` continue to appear in the workspace list.
- Custom-event deltas are transient. Checkpoint state is authoritative after completion, reconnect, cancellation, crash, or Server restart.

## File Map

### Backend Production

- `backend/agent/policy.py`: fixed neutral policy and five-dimension framework shared by every Graph and temporarily re-exported by legacy chat.
- `backend/agent/model_factory.py`: the only `ChatOpenAI` / `ReasoningChatOpenAI` construction path.
- `backend/agent/skill_backends.py`: read-only `/builtin/` and `/user/` CompositeBackend construction.
- `backend/agent/tool_executor.py`: policy table, process-wide lock/semaphore, worker-thread dispatch, batch dossier execution, and result encoding.
- `backend/agent/tool_registry.py`: thin `StructuredTool` adapter over `tool_executor.py`.
- `backend/agent/workflow_state.py`: Graph `TypedDict` state, Pydantic boundary models, reducers, terminal status types, and checkpointed event sequence cursor.
- `backend/agent/workflow_loader.py`: strict YAML models and startup-time cross-reference validation.
- `backend/agent/workflow_events.py`: Pydantic discriminated union and per-run monotonic event emitter.
- `backend/agent/workflow_runtime.py`: deterministic dossier shaping, context budgeting, Skill prompt assembly, model streaming, and error redaction.
- `backend/agent/workflow_builder.py`: fixed `staged_research` and `single_pass` StateGraph compilers.
- `backend/agent/workflows_graph.py`: one-time loading and export of `debate_graph`, `reflection_graph`, `daily_review_graph`, and `news_digest_graph`.
- `backend/agent/embedded_graph.py`: isolated page-chat Agent state, snapshot reducer, dynamic context middleware, and exported Graph.
- `backend/agent/workflows/*.yaml`: repository-controlled workflow definitions.
- `backend/agent/builtin_skills/**`: repository-controlled analysis methods and debate role instructions.
- `backend/agent/settings.py`: secret-safe status summary and minimal configuration template.
- `backend/agent/graph.py`: workspace Agent rebuilt from shared model, policy, Skill backends, and tool executor.
- `backend/langgraph.json`: six Graph registrations.
- `backend/app.py`: read-only `/api/agent/status`, then legacy AI route removal in Task 13.

### Backend Tests And Contracts

- `backend/tests/agent/test_policy.py`
- `backend/tests/agent/test_tool_executor.py`
- `backend/tests/agent/test_workflow_state.py`
- `backend/tests/agent/test_workflow_loader.py`
- `backend/tests/agent/test_workflow_runtime.py`
- `backend/tests/agent/test_workflow_builder.py`
- `backend/tests/agent/test_embedded_graph.py`
- `backend/tests/agent/test_workflow_events.py`
- `backend/tests/agent/test_agent_status.py`
- `backend/tests/agent/test_langgraph_server.py`
- `backend/tests/agent/server_harness.py`
- `backend/tests/agent_e2e/server_graph.py`
- `backend/tests/agent_e2e/server_langgraph.json`
- `docs/contracts/workflow-custom-events.json`: secret-free examples validated by both Python and TypeScript.

### Frontend Production

- `frontend/src/lib/agent/thread-adapter.ts`: workspace-only filtering and workspace metadata initialization.
- `frontend/src/lib/agent/workflow-types.ts`: state, status, thread projection, and custom-event unions.
- `frontend/src/lib/agent/workflow-client.ts`: workflow thread CRUD/search, run lifecycle, cancellation, retry, and checkpoint reads.
- `frontend/src/lib/agent/workflow-stream.ts`: sequence validation, transient buffers, reconnect, and checkpoint reconciliation.
- `frontend/src/lib/agent/embedded-client.ts`: `(route, scopeKey)` thread reuse, messages, context snapshots, and explicit deletion.
- `frontend/src/hooks/useWorkflowRun.ts`: small React-state controller shared by four workflow pages.
- `frontend/src/components/workflow/WorkflowHistory.tsx`: filtered history list and explicit deletion.
- `frontend/src/components/ui/AskAiButton.tsx`: existing drawer UI backed by embedded LangGraph threads.
- `frontend/src/pages/Debate.tsx`, `DailyReview.tsx`, `Intel.tsx`, `Notes.tsx`: existing experiences backed by workflow Graphs and isolated history.
- `frontend/src/pages/Settings.tsx`: read-only Agent status/configuration guidance while retaining FastAPI access-key configuration.
- `frontend/src/lib/api.ts`: typed `/api/agent/status` client only; objective-data methods remain unchanged.

### Intentional Deletions In Task 13

- `backend/chat.py`, `backend/debate.py`, `backend/reflection.py`, `backend/cli_runtime.py`
- `backend/agent/ssrf.py`, `backend/tests/agent/test_ssrf.py`, `backend/tests/test_agents.py`
- `frontend/src/lib/llm.ts`, `frontend/src/lib/agents.ts`, `frontend/src/lib/ndjson.ts`, `frontend/src/lib/ai-models.ts`
- `frontend/src/lib/agent/storage-keys.test.ts`; update `frontend/tests/agent-storage.test.mjs` to assert the old key is no longer read

Do not delete old browser keys. New code stops reading `vr-llm` and `vr-askai-chat:*`, but leaves stored values untouched.

## Commit Gates

After every backend task, run its focused test and then:

```bash
cd backend && .venv/bin/pytest -m "not live"
```

After every frontend task, run its focused test and then:

```bash
cd frontend && npm test && npm run test:unit && npm run build
```

At every commit boundary run `git diff --check`. Stage only the files named by that task; never use `git add .` in this dirty worktree.

## Phase 1: Shared Runtime And Configuration Contracts

### Task 1: Extract The Shared Model, Policy, And Skill Namespaces

**Files:**
- Create: `backend/agent/policy.py`
- Create: `backend/agent/model_factory.py`
- Create: `backend/agent/skill_backends.py`
- Create: `backend/agent/builtin_skills/stock-analysis/SKILL.md`
- Create: `backend/tests/agent/test_policy.py`
- Modify: `backend/agent/graph.py`
- Modify: `backend/tests/agent/test_graph.py`

- [ ] **Step 1: Write failing tests for one model factory, policy precedence, and Skill namespaces**

Add tests that assert:

```python
def test_policy_keeps_every_product_red_line() -> None:
    text = fixed_system_policy("Agent 工作台")
    for phrase in ("不推荐买卖", "不预测涨跌", "不给目标价", "不评级", "不排名", "不给交易时机"):
        assert phrase in text


def test_skill_backend_exposes_separate_read_only_namespaces(tmp_path: Path) -> None:
    builtin = tmp_path / "builtin"
    user = tmp_path / "user"
    builtin.mkdir()
    user.mkdir()
    backend = build_skill_backend(builtin, user)
    assert set(backend.routes) == {"/builtin/", "/user/"}
    assert backend.routes["/builtin/"].virtual_mode is True
    assert backend.routes["/user/"].virtual_mode is True
```

Extend `test_build_graph_uses_fixed_prompt_and_complete_tool_surface` to monkeypatch `build_model`, assert it is the only model constructor, assert `SkillsMiddleware.sources == ["/builtin/", "/user/"]`, and assert the fixed policy precedes any Skill content. Assert `stock-analysis/SKILL.md` contains all five existing dimensions and the no-conclusion boundary.

- [ ] **Step 2: Run the focused tests and verify failure**

Run:

```bash
cd backend && .venv/bin/pytest tests/agent/test_policy.py tests/agent/test_graph.py -q
```

Expected: FAIL because `agent.policy`, `agent.model_factory`, `agent.skill_backends`, and the built-in stock-analysis Skill do not exist and `graph.py` still imports `chat` and constructs the model locally.

- [ ] **Step 3: Separate fixed policy from the analysis method and move model construction**

Create a concise code-owned policy containing the existing universal tool-grounding and neutrality rules, but not the five-dimension method. Expose this stable API:

```python
def fixed_system_policy(context: str) -> str:
    return FIXED_SYSTEM_POLICY.format(context=context or "（无）")
```

`FIXED_SYSTEM_POLICY` must explicitly say: use objective tools before stating unavailable facts; identify missing data; do not recommend buy/sell, predict direction/price, give timing/target/rating/ranking, or promise returns; present multiple interpretations and let the user decide. Put the current `chat.ANALYSIS_FRAMEWORK` five dimensions and output organization into `builtin_skills/stock-analysis/SKILL.md` with valid `name`/`description` frontmatter. This avoids duplicating the analysis method in the permanent system prompt.

Move `_build_model` from `graph.py` into `model_factory.py` as:

```python
def build_model(settings: AgentSettings) -> BaseChatModel:
    model = settings.model
    model_cls = ReasoningChatOpenAI if model.thinking else ChatOpenAI
    extra_kwargs: dict[str, Any] = {}
    if model.thinking:
        extra_kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
    return model_cls(
        model=model.name,
        base_url=model.base_url.rstrip("/"),
        api_key=SecretStr(model.api_key.get_secret_value()),
        temperature=model.temperature,
        streaming=True,
        parallel_tool_calls=False,
        **extra_kwargs,
    )
```

Build the namespaced backend with:

```python
CompositeBackend(
    default=StateBackend(),
    routes={
        "/builtin/": FilesystemBackend(root_dir=builtin_root, virtual_mode=True),
        "/user/": FilesystemBackend(root_dir=user_root, virtual_mode=True),
    },
)
```

Workspace sources are `['/builtin/', '/user/']`; embedded/workflow callers request only `['/builtin/']`. Keep `FilesystemMiddleware` read-only with `ls` and `read_file` only.

Update `graph.py` to call `build_model(resolved)`, `fixed_system_policy("Agent 工作台")`, and the shared Skill backend. Do not alter MCP discovery or HITL behavior. Leave legacy `chat.py` unchanged until Task 13 so `/api/chat` keeps its exact prompt during the staged migration.

- [ ] **Step 4: Run tests and the full backend gate**

Run:

```bash
cd backend && .venv/bin/pytest tests/agent/test_policy.py tests/agent/test_graph.py -q
cd backend && .venv/bin/pytest -m "not live"
```

Expected: PASS; existing workspace Agent behavior remains unchanged and no secret appears in model repr or prompt fixtures.

- [ ] **Step 5: Commit**

```bash
git add backend/agent/policy.py backend/agent/model_factory.py backend/agent/skill_backends.py backend/agent/builtin_skills/stock-analysis/SKILL.md backend/agent/graph.py backend/tests/agent/test_policy.py backend/tests/agent/test_graph.py
git commit -m "refactor: share agent model policy and skills"
```

### Task 2: Enforce Process-Wide Tool Execution Policies

**Files:**
- Create: `backend/agent/tool_executor.py`
- Create: `backend/tests/agent/test_tool_executor.py`
- Modify: `backend/agent/tool_registry.py`
- Modify: `backend/tests/agent/test_tool_registry.py`

- [ ] **Step 1: Write failing policy, concurrency, cancellation, and cross-loop tests**

Use these exact policy sets in the test:

```python
PARALLEL_SAFE = {
    "query_quote", "query_valuation_percentile", "query_financials",
    "query_kline", "query_announcements", "query_reports", "query_news",
}
EASTMONEY_SERIAL = {
    "query_valuation", "query_fund_flow", "query_margin", "query_holders",
    "query_lockup", "query_concepts",
}
```

Tests must prove:

- all 13 names have the expected policy and every other `tools.TOOL_NAMES` entry defaults to serial;
- five parallel-safe invocations never exceed an active-handler count of four;
- serial invocations launched from two OS threads, each running its own event loop, never overlap;
- cancelling the awaiting coroutine does not release the worker-held lock/semaphore before the synchronous handler returns;
- encoded results retain the existing 6,000-character cap and preserve a structured object whose `error` field contains the safe tool failure.

- [ ] **Step 2: Run the focused tests and verify failure**

Run: `cd backend && .venv/bin/pytest tests/agent/test_tool_executor.py tests/agent/test_tool_registry.py -q`

Expected: FAIL because the current adapter has one loop-bound `asyncio.Lock`, no policy table, and no global parallel capacity.

- [ ] **Step 3: Implement the minimal shared executor**

Create these code-owned primitives:

```python
class ToolExecutionPolicy(StrEnum):
    EASTMONEY_SERIAL = "eastmoney_serial"
    PARALLEL_SAFE = "parallel_safe"


EASTMONEY_SERIAL_TOOLS = frozenset({
    "query_valuation", "query_fund_flow", "query_margin", "query_holders",
    "query_lockup", "query_concepts",
})
PARALLEL_SAFE_TOOLS = frozenset({
    "query_quote", "query_valuation_percentile", "query_financials",
    "query_kline", "query_announcements", "query_reports", "query_news",
})
_SERIAL_LOCK = threading.Lock()
_PARALLEL_CAPACITY = threading.BoundedSemaphore(4)


def tool_policy(name: str) -> ToolExecutionPolicy:
    if name in PARALLEL_SAFE_TOOLS:
        return ToolExecutionPolicy.PARALLEL_SAFE
    return ToolExecutionPolicy.EASTMONEY_SERIAL


def _dispatch(name: str, args: dict[str, object]) -> object:
    guard = _PARALLEL_CAPACITY if tool_policy(name) is ToolExecutionPolicy.PARALLEL_SAFE else _SERIAL_LOCK
    with guard:
        return legacy_tools.exec_tool(name, args)


async def execute_tool(name: str, args: dict[str, object]) -> object:
    return await asyncio.to_thread(_dispatch, name, args)
```

Keep semaphore acquisition inside `_dispatch`; do not acquire capacity in the event loop. Make `tool_registry.py` a thin `StructuredTool` wrapper around `execute_tool` and keep metadata fields `vr_origin` plus `vr_execution_policy`.

- [ ] **Step 4: Run tests and backend gate**

Run:

```bash
cd backend && .venv/bin/pytest tests/agent/test_tool_executor.py tests/agent/test_tool_registry.py -q
cd backend && .venv/bin/pytest -m "not live"
```

Expected: PASS with observed peak parallelism exactly four and serial peak exactly one.

- [ ] **Step 5: Commit**

```bash
git add backend/agent/tool_executor.py backend/agent/tool_registry.py backend/tests/agent/test_tool_executor.py backend/tests/agent/test_tool_registry.py
git commit -m "refactor: centralize agent tool concurrency"
```

### Task 3: Define Workflow State And Strict YAML Loading

**Files:**
- Modify: `backend/requirements.txt`
- Modify: `backend/tests/agent/test_dependency_compat.py`
- Create: `backend/agent/workflow_state.py`
- Create: `backend/agent/workflow_loader.py`
- Create: `backend/tests/agent/test_workflow_state.py`
- Create: `backend/tests/agent/test_workflow_loader.py`
- Create: `backend/tests/agent/fixtures/workflows/*.yaml`

- [ ] **Step 1: Add failing state/reducer and loader matrix tests**

Cover all of these cases explicitly: valid staged and single-pass YAML; unknown field; unsupported schema version; non-positive config version; filename/ID mismatch; unknown kind/tool/Skill; duplicate/missing stage; forward stage reference; invalid input reference; path escape; invalid `empty_policy`/`on_error`; soft limit above hard limit; and forbidden `result.field`.

Add reducer tests equivalent to:

```python
def test_merge_stage_results_updates_only_named_stage() -> None:
    old = {"bull": stage("bull", "running"), "bear": stage("bear", "pending")}
    new = {"bull": stage("bull", "completed", content="多方文本")}
    merged = merge_stage_results(old, new)
    assert merged["bull"].status == "completed"
    assert merged["bear"].status == "pending"
    assert merged is not old


def test_failed_stage_cannot_retain_content() -> None:
    failed = stage("bull", "failed", error=workflow_error("MODEL_ERROR", "模型不可用", "bull"))
    assert failed.content is None
    assert failed.error.code == "MODEL_ERROR"
```

- [ ] **Step 2: Run tests and verify failure**

Run: `cd backend && .venv/bin/pytest tests/agent/test_workflow_state.py tests/agent/test_workflow_loader.py -q`

Expected: FAIL because the workflow state and loader modules do not exist and PyYAML is absent.

- [ ] **Step 3: Pin YAML support and implement exact state boundaries**

Add `PyYAML==6.0.2` to `requirements.txt` and its exact installed-version assertion. Install with:

```bash
cd backend && .venv/bin/pip install PyYAML==6.0.2
```

Define Pydantic models with `ConfigDict(extra="forbid")` for `WorkflowError`, `StageResult`, `DossierSection`, and `DossierResult`. Define `WorkflowState` with the exact public fields from design section 6.5 and these status literals:

```python
WorkflowStatus = Literal[
    "pending", "running", "completed", "partial", "failed", "cancelled", "interrupted",
]
StageStatus = Literal[
    "pending", "running", "completed", "failed", "skipped", "cancelled", "interrupted",
]
```

Add internal `event_seq: int` to `WorkflowState`; initialize it to zero and checkpoint its last emitted value at every node boundary. It is protocol bookkeeping, not a history-list projection. Implement immutable `merge_stage_results` and append-only `append_workflow_errors`. A failed/skipped/cancelled/interrupted stage always serializes as `【阶段 <id> 未产出】`; `content` must be `None` for failed stages.

Define strict loader models for the two whitelisted kinds, exact `${input.<field>}` whole-value references, `gap_if_empty | allow_no_record`, and `continue | fail`. Keep code hard limits in `HARD_LIMITS`; reject rather than clamp invalid config. Resolve Skill instructions and YAML files below fixed repository roots before calling `Path.relative_to` to block escape.

- [ ] **Step 4: Run tests and backend gate**

Run:

```bash
cd backend && .venv/bin/pytest tests/agent/test_workflow_state.py tests/agent/test_workflow_loader.py tests/agent/test_dependency_compat.py -q
cd backend && .venv/bin/pytest -m "not live"
```

Expected: PASS; loader errors name only file/field/reason and never echo YAML values that could contain secrets.

- [ ] **Step 5: Commit**

```bash
git add backend/requirements.txt backend/agent/workflow_state.py backend/agent/workflow_loader.py backend/tests/agent/test_dependency_compat.py backend/tests/agent/test_workflow_state.py backend/tests/agent/test_workflow_loader.py backend/tests/agent/fixtures/workflows
git commit -m "feat: add typed workflow configuration"
```

### Task 4: Add Built-In Skills And Production Workflow YAML

**Files:**
- Create: `backend/agent/workflows/debate.yaml`
- Create: `backend/agent/workflows/reflection.yaml`
- Create: `backend/agent/workflows/daily_review.yaml`
- Create: `backend/agent/workflows/news_digest.yaml`
- Create: `backend/agent/builtin_skills/debate/SKILL.md`
- Create: `backend/agent/builtin_skills/debate/references/bull.md`
- Create: `backend/agent/builtin_skills/debate/references/bear.md`
- Create: `backend/agent/builtin_skills/debate/references/bull-rebut.md`
- Create: `backend/agent/builtin_skills/debate/references/bear-rebut.md`
- Create: `backend/agent/builtin_skills/debate/references/referee.md`
- Create: `backend/agent/builtin_skills/reflection-audit/SKILL.md`
- Create: `backend/agent/builtin_skills/market-review/SKILL.md`
- Create: `backend/agent/builtin_skills/news-digest/SKILL.md`
- Modify: `backend/tests/agent/test_workflow_loader.py`

- [ ] **Step 1: Write failing production-config and content-boundary tests**

Assert production loading returns four configs, a malformed user Skill does not affect production workflow loading, a malformed built-in Skill fails production loading, and debate contains exactly this ordered mapping:

| ID | Tool | Args beyond code | Empty policy | Execution policy |
|---|---|---|---|---|
| `quote` | `query_quote` | `codes: [code]` | `gap_if_empty` | parallel |
| `valuation` | `query_valuation` | none | `gap_if_empty` | serial |
| `valuation_percentile` | `query_valuation_percentile` | none | `gap_if_empty` | parallel |
| `financials` | `query_financials` | none | `gap_if_empty` | parallel |
| `kline` | `query_kline` | `count: 60` | `gap_if_empty` | parallel |
| `fund_flow` | `query_fund_flow` | `days: 5` | `gap_if_empty` | serial |
| `margin` | `query_margin` | none | `allow_no_record` | serial |
| `holders` | `query_holders` | none | `allow_no_record` | serial |
| `announcements` | `query_announcements` | none | `allow_no_record` | parallel |
| `lockup` | `query_lockup` | none | `allow_no_record` | serial |
| `concepts` | `query_concepts` | none | `gap_if_empty` | serial |
| `reports` | `query_reports` | none | `allow_no_record` | parallel |
| `news` | `query_news` | none | `allow_no_record` | parallel |

Assert variants are exactly `standard=[bull,bear,referee]` and `cross_exam=[bull,bear,bull_rebut,bear_rebut,referee]`. Assert all Skills contain the neutral forbidden-output rules; referee contains “分歧点” and “验证清单” but no winner language; reflection contains “审计已有文本” and forbids new conclusions.

- [ ] **Step 2: Run tests and verify failure**

Run: `cd backend && .venv/bin/pytest tests/agent/test_workflow_loader.py -q`

Expected: FAIL because production YAML and built-in Skills do not exist.

- [ ] **Step 3: Write the production configurations and Skills**

Translate the exact 13-item table above into `debate.yaml`; set limits to `section_chars: 1800`, `dossier_summary_chars: 6000`, `stage_output_chars: 1200`, and `stage_context_chars: 24000`. Use the stage/context/error policy from design section 6.2 exactly.

Use these fixed single-pass inputs:

```yaml
# reflection.yaml
schema_version: 1
config_version: 1
id: reflection
kind: single_pass
skill: builtin/reflection-audit
instruction: SKILL.md
input:
  text_field: source
  max_chars: 12000
```

`daily_review.yaml` uses `input.text_field: market_snapshot`; `news_digest.yaml` uses `input.text_field: news_snapshot`; both use a 24,000-character input limit and do not declare tools. Every Skill starts with valid `name`/`description` frontmatter. Keep the Task 1 stock-analysis Skill unchanged; move role-specific rules from `debate.py` into the five debate references and move `REFLECT_PROMPT` into `reflection-audit`. Preserve the wording that disallows invented data and investment conclusions.

- [ ] **Step 4: Run loader/content tests and backend gate**

Run:

```bash
cd backend && .venv/bin/pytest tests/agent/test_workflow_loader.py -q
cd backend && .venv/bin/pytest -m "not live"
```

Expected: PASS with exactly four configs, thirteen debate sections, seven parallel-safe names, six serial names, and no dynamic result field.

- [ ] **Step 5: Commit**

```bash
git add backend/agent/workflows backend/agent/builtin_skills backend/tests/agent/test_workflow_loader.py
git commit -m "feat: add built-in research workflows and skills"
```

## Phase 2: Workflow And Embedded Graphs

### Task 5: Implement Typed Events And Deterministic Workflow Runtime

**Files:**
- Create: `backend/agent/workflow_events.py`
- Create: `backend/agent/workflow_runtime.py`
- Create: `backend/tests/agent/test_workflow_events.py`
- Create: `backend/tests/agent/test_workflow_runtime.py`
- Create: `docs/contracts/workflow-custom-events.json`

- [ ] **Step 1: Write failing event-contract and runtime tests**

Create one valid example for each of the nine event types in `docs/contracts/workflow-custom-events.json`. Python tests validate every example through the discriminated union and reject missing required fields.

Runtime tests use `ScriptedChatModel` and fake tools to prove:

- dossier completion order does not change configured display order;
- `allow_no_record` produces `no_record`, while `gap_if_empty` produces `gap` and enters `missing`;
- no substantive section stops before any model invocation;
- deterministic summary includes section source/time/key fields and is capped at 6,000 characters;
- failed/skipped/cancelled/interrupted stages serialize only as `【阶段 <id> 未产出】` and never expose the error message as research context;
- failed-stage context is only `【阶段 <id> 未产出】`;
- referee context includes summary/missing/bounded stages and excludes complete dossier section bodies;
- stage output stops at 1,200 characters and state/event both report `truncated=true`;
- context budgeting sets `context_truncated=true` and never truncates fixed policy, current Skill, or user input;
- errors contain a stable code and redacted Chinese message without headers, keys, or upstream body.
- event sequence is continuous across `collect_dossier`, `start_<id>`, `run_<id>`, and `finalize`, with the node's last value written back to `state.event_seq`.

- [ ] **Step 2: Run tests and verify failure**

Run: `cd backend && .venv/bin/pytest tests/agent/test_workflow_events.py tests/agent/test_workflow_runtime.py -q`

Expected: FAIL because the event and runtime modules do not exist.

- [ ] **Step 3: Implement the fixed event union and emitter**

Define a common Pydantic base with `type`, `workflow_id`, `run_id`, `seq`, and `emitted_at`, then nine `Literal[type]` subclasses matching design section 11.2. Export a discriminated `WorkflowEvent` type and only this construction path:

```python
class WorkflowEventEmitter:
    def __init__(self, workflow_id: str, run_id: str, starting_seq: int, config: RunnableConfig) -> None:
        self.workflow_id = workflow_id
        self.run_id = run_id
        self._seq = starting_seq
        self._config = config

    @classmethod
    def from_config(
        cls,
        workflow_id: str,
        starting_seq: int,
        config: RunnableConfig,
    ) -> "WorkflowEventEmitter":
        run_id = config.get("configurable", {}).get("run_id")
        if not isinstance(run_id, str) or not run_id:
            raise RuntimeError("LangGraph run_id 缺失")
        return cls(workflow_id, run_id, starting_seq, config)

    @property
    def last_seq(self) -> int:
        return self._seq

    async def emit(self, event_type: str, **payload: object) -> None:
        self._seq += 1
        event = validate_workflow_event({
            "type": event_type,
            "workflow_id": self.workflow_id,
            "run_id": self.run_id,
            "seq": self._seq,
            "emitted_at": utc_now(),
            **payload,
        })
        await adispatch_custom_event(
            "workflow",
            event.model_dump(mode="json"),
            config=self._config,
        )
```

LangGraph API 0.12.6 injects the Server run ID at `config.configurable.run_id`; the integration test must prove it equals the run returned to the client. Business nodes create an emitter from the checkpointed `state.event_seq`, call `emitter.emit`, return `event_seq=emitter.last_seq`, and never dispatch a raw dictionary. One emitter instance is shared by all concurrent dossier progress callbacks within a node so sequence allocation cannot fork.

- [ ] **Step 4: Implement deterministic dossier/context/model helpers**

Port `_payload_empty` and the explicit “可能无记录，也可能数据源不可用，不得据此推断” wording from `debate.py`. Execute all configured section calls through `tool_executor.execute_tool`, with `asyncio.gather` only for names classified parallel-safe and a sequential loop for serial names. Restore configured order after completion.

Expose focused functions: `collect_dossier`, `summarize_dossier`, `serialize_stage_context`, `build_stage_messages`, `run_stage`, `run_single_pass`, `redact_workflow_error`, and `finalize_workflow`. `result_summary` is deterministic and at most 80 characters; it reports status/stage/missing counts only, never model-generated sentiment or a bull/bear winner.

- [ ] **Step 5: Run tests and backend gate**

Run:

```bash
cd backend && .venv/bin/pytest tests/agent/test_workflow_events.py tests/agent/test_workflow_runtime.py -q
cd backend && .venv/bin/pytest -m "not live"
```

Expected: PASS; event sequence starts at one per emitter and runtime tests invoke no network service.

- [ ] **Step 6: Commit**

```bash
git add backend/agent/workflow_events.py backend/agent/workflow_runtime.py backend/tests/agent/test_workflow_events.py backend/tests/agent/test_workflow_runtime.py docs/contracts/workflow-custom-events.json
git commit -m "feat: add deterministic workflow runtime"
```

### Task 6: Compile And Export The Four Workflow Graphs

**Files:**
- Create: `backend/agent/workflow_builder.py`
- Create: `backend/agent/workflows_graph.py`
- Create: `backend/tests/agent/test_workflow_builder.py`

- [ ] **Step 1: Write failing scripted-Graph tests**

Test both debate variants, all three single-pass Graphs, and these checkpoint/error contracts:

```python
async def test_start_node_commits_running_before_model_node(graph, checkpointer) -> None:
    config = {"configurable": {"thread_id": "checkpoint-test"}}
    await graph.aupdate_state(config, valid_debate_input(), as_node="collect_dossier")
    await graph.ainvoke(None, config, interrupt_after=["start_bull"])
    state = await graph.aget_state(config)
    assert state.values["current_stage"] == "bull"
    assert state.values["stages"]["bull"].status == "running"
    assert "run_bull" in state.next
```

Also assert invalid variants fail before tools/models, `continue` routes to the next configured stage with an error sentinel, `fail` routes directly to `finalize`, completed stages are not rerun on resume, and single-pass output exists only at `state.result`.

- [ ] **Step 2: Run tests and verify failure**

Run: `cd backend && .venv/bin/pytest tests/agent/test_workflow_builder.py -q`

Expected: FAIL because builder and exported Graphs do not exist.

- [ ] **Step 3: Compile fixed graph skeletons once**

Implement `build_workflow_graph(config, *, model, tool_runner)` with only two code-owned kinds. For staged research, add `validate_input`, `collect_dossier`, every configured `start_<id>` and `run_<id>`, and `finalize`. The conditional router reads the validated variant list; it does not evaluate YAML expressions or create nodes per request.

Each `start_<id>` returns only a new `current_stage` and that stage's `running` update. Each `run_<id>` returns only its stage terminal update plus new errors. Nodes must not mutate state containers in place.

For single pass, compile `validate_input -> start -> run -> finalize`; always write `result`. In `workflows_graph.py`, load all production configs once, build one shared model once, compile four Graphs, and export names matching `langgraph.json`.

- [ ] **Step 4: Run tests and backend gate**

Run:

```bash
cd backend && .venv/bin/pytest tests/agent/test_workflow_builder.py -q
cd backend && .venv/bin/pytest -m "not live"
```

Expected: PASS; exact node traces match each YAML variant and all long model calls are preceded by a committed start node.

- [ ] **Step 5: Commit**

```bash
git add backend/agent/workflow_builder.py backend/agent/workflows_graph.py backend/tests/agent/test_workflow_builder.py
git commit -m "feat: compile dedicated workflow graphs"
```

### Task 7: Build The Isolated Embedded Agent Graph

**Files:**
- Create: `backend/agent/embedded_graph.py`
- Create: `backend/tests/agent/test_embedded_graph.py`
- Modify: `backend/agent/graph.py`
- Modify: `backend/tests/agent/test_graph.py`

- [ ] **Step 1: Write failing snapshot, isolation, and history-attribution tests**

Define tests for first-turn empty rejection, later omitted/null/empty preservation, valid replacement/version increment, route/scope mismatch rejection, content cap, timestamp/source attribution, and policy precedence. Assert embedded tools contain built-ins only; middleware contains built-in Skills only; MCP tools, user Skills, and HITL are absent.

Use this reducer matrix:

| Previous | Incoming | Result |
|---|---|---|
| none | missing/null/empty | validation error on first run |
| v1 | missing/null/empty | keep v1 unchanged |
| v1 | valid non-empty same scope | Server-stamped v2 |
| any | different route/scope | validation error |

- [ ] **Step 2: Run tests and verify failure**

Run: `cd backend && .venv/bin/pytest tests/agent/test_embedded_graph.py tests/agent/test_graph.py -q`

Expected: FAIL because `embedded_graph.py` and the reducer do not exist.

- [ ] **Step 3: Implement typed embedded state and middleware**

Define `PageContextInput`, `PageContextSnapshot`, and `AssistantContextRef`. Extend `AgentState` with:

```python
class EmbeddedAgentState(AgentState):
    page_context: Annotated[PageContextSnapshot | None, keep_latest_nonempty_context]
    assistant_context_refs: Annotated[list[AssistantContextRef], append_context_refs]
```

The reducer preserves a prior snapshot for omitted/null/blank content; a validated non-empty same-scope input is stamped with `captured_at=utc_now()` and `version=previous.version + 1`. The first model call validates that a snapshot exists.

Dynamic prompt middleware injects exactly one full current snapshot with `【当前页面快照 vN · captured_at】`. Historical assistant messages receive only compact version/time markers from `assistant_context_refs`, plus the instruction that old answers may use old snapshots and are not current facts. Build with shared model, fixed policy, built-in tools, `/builtin/` Skills, and no MCP/HITL/user Skill.

Update workspace `graph.py` to use the same namespaced backend but retain `/user/` Skills and MCP/HITL.

- [ ] **Step 4: Run tests and backend gate**

Run:

```bash
cd backend && .venv/bin/pytest tests/agent/test_embedded_graph.py tests/agent/test_graph.py -q
cd backend && .venv/bin/pytest -m "not live"
```

Expected: PASS; old snapshot bodies are not duplicated in later model inputs and embedded invocations cannot discover user MCP or user Skills.

- [ ] **Step 5: Commit**

```bash
git add backend/agent/embedded_graph.py backend/agent/graph.py backend/tests/agent/test_embedded_graph.py backend/tests/agent/test_graph.py
git commit -m "feat: add isolated embedded agent graph"
```

### Task 8: Register Six Graphs And Prove Server Contracts

**Files:**
- Modify: `backend/langgraph.json`
- Modify: `backend/tests/agent_e2e/server_graph.py`
- Modify: `backend/tests/agent_e2e/server_langgraph.json`
- Modify: `backend/tests/agent/server_harness.py`
- Modify: `backend/tests/agent/test_langgraph_server.py`

- [ ] **Step 1: Add failing real-Server integration tests**

Extend the isolated fixture server to expose `agent`, `embedded_agent`, `debate`, `reflection`, `daily_review`, and `news_digest`. Tests must verify discovery/runs, one `threads.search` projection of `values.workflow_status` and `values.result_summary`, custom-event schema, every event `run_id` matching the Server-created run, metadata/state secret absence, resumable stream replay, cancel state, process restart, incompatible `config_version`, and orphan state derivation inputs (`idle`, `interrupted`, `error`). The cancellation contract test must cancel at a committed `start_<id>` checkpoint, call state update without `as_node`, assert the cancelled status is committed, and assert the original `run_<id>` remains pending for explicit retry. This proves the fixed serial Graph has an unambiguous last node and prevents a fabricated node name from entering the client contract.

Add a bind-contract assertion over the spawned command:

```python
assert command[command.index("--host") + 1] == "127.0.0.1"
assert "0.0.0.0" not in command
```

- [ ] **Step 2: Run integration tests and verify failure**

Run: `cd backend && .venv/bin/pytest tests/agent/test_langgraph_server.py -q`

Expected: FAIL because production and fixture configs expose only `agent`.

- [ ] **Step 3: Register the Graphs and extend the deterministic harness**

Use these production registrations:

```json
{
  "graphs": {
    "agent": "./agent/graph.py:graph",
    "embedded_agent": "./agent/embedded_graph.py:graph",
    "debate": "./agent/workflows_graph.py:debate_graph",
    "reflection": "./agent/workflows_graph.py:reflection_graph",
    "daily_review": "./agent/workflows_graph.py:daily_review_graph",
    "news_digest": "./agent/workflows_graph.py:news_digest_graph"
  }
}
```

Retain existing dependency and CORS fields from `backend/langgraph.json`. The fixture Graphs are deterministic and network-free but must use the same metadata/state/custom-event shapes. Harness settings remain mode `0600`, use temporary Skills, and never import a real API key.

- [ ] **Step 4: Run integration and backend gates**

Run:

```bash
cd backend && .venv/bin/pytest tests/agent/test_langgraph_server.py -q
cd backend && .venv/bin/pytest -m "not live"
```

Expected: PASS across a server stop/start cycle; list projection is one search request and no per-row state request appears in the HTTP capture.

- [ ] **Step 5: Commit**

```bash
git add backend/langgraph.json backend/tests/agent_e2e/server_graph.py backend/tests/agent_e2e/server_langgraph.json backend/tests/agent/server_harness.py backend/tests/agent/test_langgraph_server.py
git commit -m "feat: register unified langgraph workflows"
```

## Phase 3: Frontend SDK Migration

### Task 9: Add Typed Workflow Client, Recovery, And Thread Filtering

**Files:**
- Create: `frontend/src/lib/agent/workflow-types.ts`
- Create: `frontend/src/lib/agent/workflow-client.ts`
- Create: `frontend/src/lib/agent/workflow-stream.ts`
- Create: `frontend/src/lib/agent/workflow-client.test.ts`
- Create: `frontend/src/lib/agent/workflow-stream.test.ts`
- Create: `frontend/src/lib/agent/workflow-types.test.ts`
- Modify: `frontend/src/lib/agent/thread-adapter.ts`
- Modify: `frontend/src/lib/agent/thread-adapter.test.ts`
- Modify: `frontend/src/lib/storage.ts`

- [ ] **Step 1: Write failing client/status/sequence/thread tests**

Define the shared status function and test the complete matrix:

```ts
expect(effectiveWorkflowStatus("busy", "pending")).toBe("running");
expect(effectiveWorkflowStatus("idle", "completed")).toBe("completed");
expect(effectiveWorkflowStatus("idle", "running")).toBe("interrupted");
expect(effectiveWorkflowStatus("interrupted", "running")).toBe("interrupted");
expect(effectiveWorkflowStatus("error", "running")).toBe("failed");
expect(effectiveWorkflowStatus("idle", "cancelled")).toBe("cancelled");
```

Assert workflow history calls `threads.search` once with metadata and two extract paths, reads list fields from `thread.extracted` before `thread.values`, never calls `getState` per row, and direct detail loading calls `getState` once for the selected thread. Include one search fixture whose `values` is empty and whose `extracted` contains both projected fields, plus a compatibility fixture that falls back to `values`. Assert workspace adapter locally keeps `channel=workspace` and missing-channel threads, while filtering `embedded` and `workflow`; initialize must create metadata `{channel: "workspace"}`.

Sequence tests cover expected, duplicate, older, and gap events. A gap must clear the current delta, mark it dirty, ignore later deltas for that run/stage, and wait for checkpoint replacement. `workflow-types.test.ts` reads `docs/contracts/workflow-custom-events.json` and validates all nine Python-produced examples against the TypeScript runtime guards.

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
cd frontend && npx vitest run src/lib/agent/workflow-types.test.ts src/lib/agent/workflow-client.test.ts src/lib/agent/workflow-stream.test.ts src/lib/agent/thread-adapter.test.ts
```

Expected: FAIL because workflow modules do not exist and workspace adapter currently lists every thread.

- [ ] **Step 3: Implement types and minimal SDK client**

Mirror the Python state/event literals as TypeScript discriminated unions. Implement `searchWorkflowHistory(type)` with:

```ts
return client.threads.search({
  metadata: { channel: "workflow", workflow_type: type },
  limit: 100,
  sortBy: "updated_at",
  sortOrder: "desc",
  extract: {
    workflow_status: "values.workflow_status",
    result_summary: "values.result_summary",
  },
});
```

Define a typed `WorkflowThreadProjection` and narrow the SDK `Thread.extracted` record at this boundary. History rendering must use `thread.extracted?.workflow_status ?? thread.values?.workflow_status` and the equivalent summary lookup. `extract` does not merge projected fields into `values`; the `values` fallback exists only for direct thread responses, compatibility, and focused test doubles.

Implement create/run with `streamMode: ["custom", "updates"]`, `streamResumable: true`, `onDisconnect: "continue"`, `durability: "sync"`, and `onRunCreated` to capture the Server-assigned run ID before consuming events. Workflow thread metadata is exactly `channel`, `workflow_type`, `title`, `subject`, and `config_version`; it contains no input or result.

Use the installed JS SDK 1.9.31 signatures exactly:

```ts
await client.runs.cancel(threadId, runId, true, "interrupt");
await client.threads.updateState(threadId, {
  values: cancelledStatePatch,
});
```

Write the cancelled state patch only after cancellation confirmation. Deliberately omit `asNode`: `cancel_workflow` is not a compiled Graph node, while the fixed serial Graph contract makes the last committed node unambiguous and preserves its pending successor for explicit retry. Do not describe this as a root-state update. Implement retry by first writing an orphan running stage as `interrupted`, then starting a new run from the checkpoint without replaying completed stages.

- [ ] **Step 4: Implement reconnect and safe cursor storage**

Persist `{runId,eventId,lastSeq}` through new `storage.ts` wrapper functions under `vr-workflow-stream:<threadId>`. Reconnect order is state/run read, completed checkpoint render, transient buffer clear, `joinStream` with saved event ID or `-1`, then final `getState`. `stage.completed` triggers checkpoint polling and atomic replacement; it never promotes delta text to authoritative content.

Update workspace adapter filtering and metadata creation. Do not add Authorization headers to LangGraph requests as a security claim.

- [ ] **Step 5: Run frontend gates**

Run:

```bash
cd frontend && npx vitest run src/lib/agent/workflow-types.test.ts src/lib/agent/workflow-client.test.ts src/lib/agent/workflow-stream.test.ts src/lib/agent/thread-adapter.test.ts
cd frontend && npm test && npm run test:unit && npm run build
```

Expected: PASS; TypeScript exhaustiveness rejects an unknown event at compile time, runtime logs/ignores unknown events, and missing required fields enter recoverable error state.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/agent/workflow-types.ts frontend/src/lib/agent/workflow-client.ts frontend/src/lib/agent/workflow-stream.ts frontend/src/lib/agent/workflow-types.test.ts frontend/src/lib/agent/workflow-client.test.ts frontend/src/lib/agent/workflow-stream.test.ts frontend/src/lib/agent/thread-adapter.ts frontend/src/lib/agent/thread-adapter.test.ts frontend/src/lib/storage.ts
git commit -m "feat: add typed langgraph workflow client"
```

### Task 10: Move Page Ask-AI To Embedded Threads

**Files:**
- Create: `frontend/src/lib/agent/embedded-client.ts`
- Create: `frontend/src/lib/agent/embedded-client.test.ts`
- Modify: `frontend/src/components/ui/AskAiButton.tsx`
- Create: `frontend/src/components/ui/AskAiButton.test.tsx`
- Modify: `frontend/tests/ask-ai-persistence.test.mjs`
- Modify: `frontend/tests/ask-ai-markdown.test.mjs`

- [ ] **Step 1: Write failing thread-reuse and drawer behavior tests**

Assert opening does not create an empty thread; first send searches latest `{channel:"embedded",route,scope_key}` and creates only when missing; repeated sends and reopen reuse it; a different stock/route never sees its messages; clear deletes exactly that thread; old `vr-askai-chat:*` entries are neither read nor removed.

Assert each send includes a non-empty page-context input `{route, scope_key, source_as_of, content}` and model/API keys never appear. Closing/route switching stops local streaming display but uses disconnect-continue semantics rather than cancelling the Server run.

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
cd frontend && npx vitest run src/lib/agent/embedded-client.test.ts src/components/ui/AskAiButton.test.tsx
```

Expected: FAIL because Ask-AI still reads `vr-llm`, stores messages in localStorage, and calls `/api/chat`.

- [ ] **Step 3: Implement embedded thread operations**

Create `findEmbeddedThread(route, scopeKey)`, `loadEmbeddedState`, `sendEmbeddedMessage`, and `deleteEmbeddedThread`. Search metadata is exactly `channel`, `route`, and `scope_key`; creation metadata additionally stores a deterministic title. Start `embedded_agent` runs with message input plus page context and consume native message/custom stream parts.

Refactor `AskAiButton` without changing its visible drawer, Markdown, tool chips, suggestions, save-note action, or clear button. Replace `hasLlm()` with LangGraph readiness/error state. Render checkpoint messages after reopen; mark the active streamed response temporary until checkpoint reconciliation.

- [ ] **Step 4: Run frontend gates**

Run:

```bash
cd frontend && npx vitest run src/lib/agent/embedded-client.test.ts src/components/ui/AskAiButton.test.tsx
cd frontend && npm test && npm run test:unit && npm run build
```

Expected: PASS; no source import from `@/lib/llm` remains in `AskAiButton.tsx` and old browser chat entries remain untouched in persistence tests.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/agent/embedded-client.ts frontend/src/lib/agent/embedded-client.test.ts frontend/src/components/ui/AskAiButton.tsx frontend/src/components/ui/AskAiButton.test.tsx frontend/tests/ask-ai-persistence.test.mjs frontend/tests/ask-ai-markdown.test.mjs
git commit -m "feat: persist page chat in langgraph threads"
```

### Task 11: Migrate Debate, Reflection, Review, And Digest Pages

**Files:**
- Create: `frontend/src/hooks/useWorkflowRun.ts`
- Create: `frontend/src/hooks/useWorkflowRun.test.ts`
- Create: `frontend/src/components/workflow/WorkflowHistory.tsx`
- Create: `frontend/src/components/workflow/WorkflowHistory.test.tsx`
- Modify: `frontend/src/pages/Debate.tsx`
- Create: `frontend/src/pages/Debate.test.tsx`
- Modify: `frontend/src/pages/Notes.tsx`
- Create: `frontend/src/pages/Notes.test.tsx`
- Modify: `frontend/src/pages/DailyReview.tsx`
- Create: `frontend/src/pages/DailyReview.test.tsx`
- Modify: `frontend/src/pages/Intel.tsx`
- Create: `frontend/src/pages/Intel.test.tsx`

- [ ] **Step 1: Write failing page/history/recovery tests**

For every page, assert create/run metadata, existing entry/button text, streaming state, stop, retry, save-note behavior, filtered history, history detail restore, explicit delete, failure terminal state, refresh recovery, and no N+1 detail calls. Specific contracts:

- debate maps one round to `standard`, two rounds to `cross_exam`, displays dossier progress and stage IDs, and never labels a winner;
- reflection metadata includes `subject=<note.id>` so Notes can filter source history, while result is still stored at `state.result`;
- daily review sends the already-rendered objective market snapshot and does not ask the Graph to fetch it again;
- news digest sends the selected industry's already-loaded news snapshot and keeps industry-specific history;
- rerun always creates a new workflow thread and preserves the old result.

- [ ] **Step 2: Run focused tests and verify failure**

Run:

```bash
cd frontend && npx vitest run src/hooks/useWorkflowRun.test.ts src/components/workflow/WorkflowHistory.test.tsx src/pages/Debate.test.tsx src/pages/Notes.test.tsx src/pages/DailyReview.test.tsx src/pages/Intel.test.tsx
```

Expected: FAIL because pages still call `/api/debate`, `/api/reflect`, or `/api/chat` and have no workflow history.

- [ ] **Step 3: Implement the shared controller and history component**

`useWorkflowRun` owns only `thread`, `run`, authoritative state, transient per-stage text, error, and operations `start`, `stop`, `retry`, `restore`, `remove`. It delegates protocol behavior to `workflow-client`/`workflow-stream`; it does not duplicate SDK calls.

`WorkflowHistory` receives a fixed `workflowType` plus optional subject filter. It renders `effectiveWorkflowStatus`, 80-character summary, update time, open, rerun, and delete. Opening a row calls `getState` once; listing never does.

- [ ] **Step 4: Replace each legacy call while preserving page composition**

Use these exact Graph IDs and inputs:

```ts
const WORKFLOW_INPUTS = {
  debate: { assistantId: "debate", inputKey: "code" },
  reflection: { assistantId: "reflection", inputKey: "source" },
  daily_review: { assistantId: "daily_review", inputKey: "market_snapshot" },
  news_digest: { assistantId: "news_digest", inputKey: "news_snapshot" },
} as const;
```

Keep existing Markdown result rendering and note-saving. Add history to each corresponding business page only; do not create a global workflow-history route. When a stage is dirty or uncommitted after reconnect, show the existing loading treatment and never partial text.

- [ ] **Step 5: Run frontend gates**

Run:

```bash
cd frontend && npx vitest run src/hooks/useWorkflowRun.test.ts src/components/workflow/WorkflowHistory.test.tsx src/pages/Debate.test.tsx src/pages/Notes.test.tsx src/pages/DailyReview.test.tsx src/pages/Intel.test.tsx
cd frontend && npm test && npm run test:unit && npm run build
```

Expected: PASS; `rg -n 'debateStream|reflectStream|chatStream|hasLlm' frontend/src/pages frontend/src/components/ui/AskAiButton.tsx` returns no matches.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/hooks/useWorkflowRun.ts frontend/src/hooks/useWorkflowRun.test.ts frontend/src/components/workflow/WorkflowHistory.tsx frontend/src/components/workflow/WorkflowHistory.test.tsx frontend/src/pages/Debate.tsx frontend/src/pages/Debate.test.tsx frontend/src/pages/Notes.tsx frontend/src/pages/Notes.test.tsx frontend/src/pages/DailyReview.tsx frontend/src/pages/DailyReview.test.tsx frontend/src/pages/Intel.tsx frontend/src/pages/Intel.test.tsx
git commit -m "feat: migrate analysis pages to langgraph workflows"
```

### Task 12: Replace Browser Model Setup With Read-Only Agent Status

**Files:**
- Modify: `backend/agent/settings.py`
- Create: `backend/tests/agent/test_agent_status.py`
- Modify: `backend/app.py`
- Modify: `backend/tests/test_api.py`
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/pages/Settings.tsx`
- Create: `frontend/src/pages/Settings.test.tsx`

- [ ] **Step 1: Write failing secret-redaction and settings-page tests**

Backend tests cover configured, missing, invalid, and protected endpoint cases. The payload is exactly:

```json
{
  "configured": true,
  "settings_path": "/absolute/path/settings.json",
  "model_name": "test-model",
  "base_url_host": "example.invalid",
  "builtin_skill_count": 5,
  "mcp_server_count": 0,
  "restart_required": true,
  "config_template": "{\n  \"model\": {\n    \"provider\": \"openai\",\n    \"name\": \"your-model\",\n    \"apiKey\": \"YOUR_API_KEY\",\n    \"baseURL\": \"https://your-provider.example/v1\"\n  },\n  \"skills\": {\n    \"path\": \"~/.vibe-research/agent/skills\"\n  },\n  \"mcpServers\": {}\n}"
}
```

For missing/invalid settings, `configured=false`, safe counts are zero, `model_name`/host are null, and a redacted Chinese reason is included. Assert neither real API key nor MCP header/env values appear anywhere. The unconfigured page must show `mkdir -p ~/.vibe-research/agent/skills`, the settings file path, `chmod 600`, and the loopback LangGraph restart command; the copied JSON keeps `YOUR_API_KEY` as a literal placeholder.

Frontend tests assert model/key/CLI inputs are absent, `/agent-api/ok` readiness and `/api/agent/status` summary are both shown, template copy uses the shared clipboard helper, restart/path guidance is visible, and the independent `VR_API_KEY` access-key control remains.

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
cd backend && .venv/bin/pytest tests/agent/test_agent_status.py tests/test_api.py -q
cd frontend && npx vitest run src/pages/Settings.test.tsx
```

Expected: FAIL because the status endpoint and read-only page do not exist.

- [ ] **Step 3: Implement the secret-safe status endpoint**

Add `agent_status_summary()` beside the settings loader. It may load settings in backend memory but returns only the payload above; parse `base_url_host` with `urlsplit`, count repository built-in Skill directories, and count configured MCP server names. Catch `AgentSettingsError` and return the safe unconfigured shape.

Add `GET /api/agent/status` through the existing FastAPI auth middleware. It does not call LangGraph or a model. Add only `api.agentStatus()` to `frontend/src/lib/api.ts`; do not change objective-data methods.

- [ ] **Step 4: Replace settings UI**

Remove browser model/API-key and CLI-selection UI. Fetch status plus `/agent-api/ok`, display the safe fields, exact path, restart instruction, and copyable placeholder template. Retain the current FastAPI access-key field because it protects `/api/*`; explain that it does not configure or protect LangGraph.

- [ ] **Step 5: Run backend and frontend gates**

Run:

```bash
cd backend && .venv/bin/pytest tests/agent/test_agent_status.py tests/test_api.py -q && .venv/bin/pytest -m "not live"
cd frontend && npx vitest run src/pages/Settings.test.tsx && npm test && npm run test:unit && npm run build
```

Expected: PASS; serialized test responses contain none of the fixture secrets.

- [ ] **Step 6: Commit**

```bash
git add backend/agent/settings.py backend/tests/agent/test_agent_status.py backend/app.py backend/tests/test_api.py frontend/src/lib/api.ts frontend/src/pages/Settings.tsx frontend/src/pages/Settings.test.tsx
git commit -m "feat: expose read-only agent configuration status"
```

## Phase 4: Cutover, E2E, And Cleanup

### Task 13: Prove End-To-End Migration, Remove Legacy AI Exits, And Update Docs

**Files:**
- Modify: `backend/tests/agent_e2e/graph.py`
- Modify: `backend/tests/agent_e2e/langgraph.json`
- Modify: `backend/tests/agent_e2e/start_langgraph.py`
- Modify: `frontend/playwright.config.ts`
- Create: `frontend/e2e/unified-ai-workflows.spec.ts`
- Modify: `backend/app.py`
- Modify: `backend/mcp_server.py`
- Modify: `backend/tests/test_api.py`
- Modify: `backend/tests/test_fixes.py`
- Modify: `backend/tests/test_reports_and_security.py`
- Delete: `backend/tests/test_agents.py`
- Delete: `backend/tests/agent/test_ssrf.py`
- Delete: `backend/agent/ssrf.py`
- Delete: `backend/chat.py`
- Delete: `backend/debate.py`
- Delete: `backend/reflection.py`
- Delete: `backend/cli_runtime.py`
- Delete: `frontend/src/lib/llm.ts`
- Delete: `frontend/src/lib/agents.ts`
- Delete: `frontend/src/lib/ndjson.ts`
- Delete: `frontend/src/lib/ai-models.ts`
- Delete: `frontend/src/lib/agent/storage-keys.test.ts`
- Modify: `frontend/tests/agent-storage.test.mjs`
- Modify: `README.md`
- Modify: `README.zh-CN.md`
- Modify: `backend/.env.example`
- Modify: `AGENTS.md`

- [ ] **Step 1: Add a failing isolated browser acceptance suite before deletion**

The Playwright harness starts isolated FastAPI, deterministic six-Graph LangGraph, and Vite processes on free loopback ports. Tests cover:

1. workspace list hides embedded/workflow threads and retains missing-channel legacy threads;
2. Ask-AI persists by scope, updates page-context version, restores after refresh, and deletes explicitly;
3. standard and cross-exam debate display configured stages, history, stop/retry, and checkpoint recovery;
4. reflection history filters by source note and saves selected results to research records;
5. daily review/news digest use page snapshots and show their own histories;
6. duplicate sequence events are ignored and a sequence gap clears transient text until checkpoint replacement;
7. cancel followed by page close renders `interrupted`, not permanent `running` or guessed `cancelled`;
8. Server restart restores completed results and rejects an incompatible unfinished config version;
9. Settings shows only redacted config status and a placeholder template;
10. network capture contains no calls to `/api/chat`, `/api/debate`, or `/api/reflect`, and no model key in browser requests.

- [ ] **Step 2: Run E2E and verify it fails on remaining legacy calls**

Run: `cd frontend && npm run test:e2e -- e2e/unified-ai-workflows.spec.ts`

Expected: FAIL until the deterministic fixture and all new page paths are wired; after wiring, it must pass before any legacy file deletion.

- [ ] **Step 3: Complete the isolated fixture and make E2E pass**

Use only scripted model outputs and fake tool results. Bind all three services to `127.0.0.1`; assert the spawned commands contain no `0.0.0.0`. Store fixture settings/data under Playwright output directories and delete them on teardown. Never reuse port 2024/5899/8900 if already occupied.

Run: `cd frontend && npm run test:e2e -- e2e/unified-ai-workflows.spec.ts`

Expected: PASS with all ten scenarios and zero real network/model/data-source calls.

- [ ] **Step 4: Remove legacy FastAPI orchestration and direct consumers**

Remove `LLMConfig`, `ChatReq`, `DebateReq`, `ReflectReq`, `_check_llm`, `_ndjson`, and the three AI routes/imports from `app.py`. Change `mcp_server.py` to import `TOOLS` and `exec_tool` directly from `tools.py`. Move any still-needed pure prompt/data-shaping tests to the new Agent modules, then delete the four backend legacy modules.

Delete the four frontend legacy AI modules after:

```bash
rg -n '@/lib/(llm|agents|ndjson|ai-models)|/api/(chat|debate|reflect)|chat_layer|debate_layer|reflect_layer|cli_runtime|chat\.TOOLS|chat\._exec_tool' backend frontend/src frontend/tests
```

Expected before deletion: only files scheduled for deletion or test rewrites match. Expected after deletion: no matches.

Do not call `storageRemove("vr-llm")` and do not enumerate/delete `vr-askai-chat:*`.

- [ ] **Step 5: Rewrite removal tests and verify FastAPI data API stability**

Assert the removed paths return 404 and that representative unchanged objective endpoints keep their existing status/shape contracts. Move dossier/reflection tests to `tests/agent/test_workflow_runtime.py` and remove only tests whose subject no longer exists.

Run:

```bash
cd backend && .venv/bin/pytest -m "not live"
cd frontend && npm test && npm run test:unit && npm run build
```

Expected: PASS; FastAPI imports no model client or CLI runtime and frontend bundle imports no legacy AI module.

- [ ] **Step 6: Update operator and privacy documentation**

Document one model source (`~/.vibe-research/agent/settings.json`), six Graph IDs, two loopback services, business-page history, explicit deletion, local checkpoint/trace contents, removed CLI subscription, unchanged FastAPI data APIs, and the rule that public FastAPI deployments must not expose `/agent-api`. Update AGENTS.md's AI-layer section and commands to match the final tree. Keep `frontend/package.json` as the only version source; do not edit version numbers elsewhere.

- [ ] **Step 7: Run final static, full, and browser gates**

Run:

```bash
git diff --check
cd backend && .venv/bin/pip check
cd backend && .venv/bin/pytest -m "not live"
cd frontend && npm test && npm run test:unit && npm run build
cd frontend && npm run test:e2e -- e2e/unified-ai-workflows.spec.ts
rg -n '/api/(chat|debate|reflect)|vr-llm|cli-' backend frontend/src README.md README.zh-CN.md AGENTS.md
```

Expected: all commands PASS. The final `rg` may match only migration-history wording that explicitly says old browser values are not deleted; it must not match executable reads, routes, providers, or startup instructions.

- [ ] **Step 8: Perform visual and overlap verification**

Probe `127.0.0.1:16002/json/version`. If available, connect to the existing Chrome context and open a new tab; otherwise use the installed headless Playwright Chromium. Capture desktop 1440x900 and mobile 390x844 screenshots for Ask-AI, debate history/detail, reflection, daily review, news digest, and Settings in light/dark themes. Verify no text/button overlap, no stage/history layout shift, Markdown is readable, and loading/error/terminal states fit their containers.

- [ ] **Step 9: Commit the cutover**

```bash
git add backend/app.py backend/mcp_server.py backend/tests/test_api.py backend/tests/test_fixes.py backend/tests/test_reports_and_security.py backend/tests/agent_e2e/graph.py backend/tests/agent_e2e/langgraph.json backend/tests/agent_e2e/start_langgraph.py frontend/playwright.config.ts frontend/e2e/unified-ai-workflows.spec.ts frontend/tests/agent-storage.test.mjs README.md README.zh-CN.md backend/.env.example AGENTS.md
git add -u backend/chat.py backend/debate.py backend/reflection.py backend/cli_runtime.py backend/agent/ssrf.py backend/tests/test_agents.py backend/tests/agent/test_ssrf.py frontend/src/lib/llm.ts frontend/src/lib/agents.ts frontend/src/lib/ndjson.ts frontend/src/lib/ai-models.ts frontend/src/lib/agent/storage-keys.test.ts
git commit -m "feat: complete langgraph ai workflow migration"
```

## Final Acceptance Checklist

- [ ] All model calls originate from the LangGraph Server and Agent `settings.json`.
- [ ] FastAPI exposes objective data/business APIs plus read-only Agent status, but no model orchestration route.
- [ ] Six Graphs are discoverable and all supported startup commands bind LangGraph/Vite to loopback.
- [ ] Workspace, embedded, and workflow thread histories are isolated and recoverable.
- [ ] Workflow history uses one `threads.search(extract)` request and no per-row state fetch.
- [ ] Cancellation/crash orphan checkpoints render `interrupted`; explicit confirmed cancellation renders `cancelled`.
- [ ] Sequence gaps discard transient text and checkpoint state atomically restores complete content.
- [ ] Debate preserves the exact deterministic 13-item dossier split and neutral verification-list endpoint.
- [ ] Reflection only audits supplied text; review/digest consume supplied page snapshots.
- [ ] Eastmoney handlers are globally serial and parallel-safe handlers never exceed four in process.
- [ ] No API key, MCP secret, request header, or upstream response body appears in metadata/state/checkpoint/event/trace/error/frontend request.
- [ ] Existing行情、市场、持仓、研报、文件 API contracts remain unchanged.
- [ ] Old browser AI settings/chat values are neither read nor actively deleted.
- [ ] Backend offline tests, frontend Node/Vitest tests, TypeScript build, isolated Playwright E2E, and `git diff --check` pass.
