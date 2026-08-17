# LangChain Agent Workspace 1D Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the first-phase Agent workspace with product-run governance that is enforced across resume, immutable typed Artifacts and provenance, and a responsive three-column workspace whose Inspector converges from authoritative REST state.

**Architecture:** Preserve `create_agent`, request-scoped Graph/model construction, the product-run `MemorySaver`, the immutable Capability lease, and all 1A-1C revision/approval/secret contracts. Add a secret-free `RunControl` to each `ActiveRunHandle`; focused Policy, governance, executor, provenance, and Artifact modules own the new behavior while `runs.py` and `router.py` retain lifecycle and transport assembly. Deliver three independently green slices: governance, Artifact/provenance, then the final workspace and isolated Playwright harness.

**Tech Stack:** Python 3.10+, FastAPI, Pydantic 2, LangChain 1.3.15, LangGraph 1.2.11, AG-UI, `commonmark==0.9.1`, React 19, TypeScript, Zustand 5, assistant-ui 0.15.14, Tailwind CSS, Vitest, React Testing Library, Playwright

---

## Scope And Non-Negotiable Invariants

Implement only the approved specification in `docs/superpowers/specs/2026-08-16-langchain-agent-workspace-1d-design.md`. Do not replace `create_agent` with a `StateGraph`, persist LangGraph checkpoints, add monetary cost estimates, fetch model-provided URLs, render executable Artifact content, add background MCP health monitoring, or migrate legacy `/api/chat`, debate, reflection, or CLI behavior.

Keep these invariants green after every task:

- The product boundary remains objective data, research frameworks, and verification tools. No Artifact, Source, prompt, UI label, fixture, or test introduces buy/sell advice, forecasts, target prices, ratings, rankings, or timing.
- `RunControl`, Policy, Artifact, Source, events, Graph state, checkpoints, JSON, and logs contain no model key, MCP secret, resolved environment value, session object, or private unbounded tool output.
- Duplicate/message/revision/busy checks retain their 1A-1C ordering. A corrupt Policy blocks only a genuinely new product run; it does not replace a duplicate 409 or block resume of a run that already owns a valid snapshot.
- Start, retry, and steer-away share one new-run governance commit helper. Resume reuses the original `PolicySnapshot`, `RunControl`, execution lock, Artifact mutation lock, Capability lease, and `MemorySaver`.
- Tool argument rejection, MCP guard rejection, pending approval, reject, and steer-away consume no tool reservation. Approved/allowed calls, `create_artifact`, Skill calls, and handlers returning structured business errors do consume one reservation.
- All legacy built-in dispatch is process-serial across runs, because several handlers can enter an Eastmoney `em_get` path only after fallback; this preserves the repository's IP rate-limit rule without a brittle name classification. The process-wide executor still has four admitted calls for independent work such as Artifact staging, with no unbounded queue; a timed-out running future retains capacity until it actually exits.
- Reservation persistence precedes every real Provider/handler invocation. `RunDocument.usage.model_calls/tool_calls` comes only from persisted reservations, never AG-UI event inference.
- Lock order is fixed: local execution lock -> process-wide legacy-built-in serial lock when applicable -> executor capacity -> reservation lock -> short coordinator thread-lock persistence; Artifact mutation lock -> short coordinator thread lock. Code holding the coordinator thread lock must never await the reservation lock, execution/serial lock, capacity, network, or staging work; the only permitted await in that short section is the existing `asyncio.to_thread` bridge for local atomic JSON write/fsync.
- Artifact staging never mutates run/thread state. Only the coordinator commit section may add Sources, publish the final Artifact file, and add the thread reference, in that order.
- URL provenance is normalization and recording only. It performs no DNS, HTTP, redirect, preview, health, scoring, ranking, or truth verification.
- Tests set a temporary `VR_DATA_DIR` before importing the production app/router. Playwright uses fake model/MCP/local-tool seams and never touches the user's data or external services.
- The backend remains a single FastAPI worker for 1D.

## File Map

### Backend Production

- `backend/requirements.txt`: add the exact CommonMark parser dependency.
- `backend/agent/models.py`: additive Policy snapshot, usage/token, context telemetry, Source, and lightweight Artifact wire models with schema-version-1 defaults.
- `backend/agent/stores.py`: add Policy/Artifact paths, expose the existing canonical atomic write/fsync primitives, paginate thread runs with warnings, and extend recovery warning types.
- `backend/agent/policy.py`: Policy defaults/ranges, non-destructive corrupt reads, CAS patch, normal reset, and explicit corrupt reset.
- `backend/agent/governance.py`: `RunControl`, active segments, reservation transactions, canonical context trimming, Provider usage aggregation, governance errors, and model/tool middleware.
- `backend/agent/tool_executor.py`: four-slot process executor, capacity leases, deadline/cancel behavior, late-result disposal, and bounded shutdown.
- `backend/agent/provenance.py`: recursive-redacted tool records, CommonMark URL extraction, URL normalization/deduplication, and 200-record admission.
- `backend/agent/artifacts.py`: typed Artifact validation, canonical size accounting, staging/publish, immutable chains, REST reads/downloads/deletes, and startup reconciliation.
- `backend/agent/tool_registry.py`: mark tool origin/admission requirements, route built-in sync work through the bounded executor, and register the generic `create_artifact` tool only in production service composition.
- `backend/agent/capabilities.py`: preserve MCP guard/HITL and compose them before the innermost tool governance wrapper.
- `backend/agent/runtime.py`: rebuild request middleware around the same `RunControl`, apply context governance at `ModelRequest`, and retain request-only secrets.
- `backend/agent/runs.py`: own `RunControl` and locks on `ActiveRunHandle`, share new-run admission, persist reservations/telemetry/Sources, enforce terminal ordering, and coordinate Artifact/thread deletion.
- `backend/agent/protocol.py`: validate and pass exactly `budget.updated`, `artifact.created`, and `sources.updated`; keep all other unknown Graph CustomEvents fail-closed.
- `backend/agent/router.py`: assemble services/lifespan, add Policy/run-list/Artifact REST, map governance and persistence errors, and emit committed state before terminal events.

### Backend Tests

- `backend/tests/agent/test_models.py`, `test_stores.py`, `test_run_persistence.py`, `test_resume_contract.py`, `test_router.py`, `test_thread_api.py`, `test_protocol_bridge.py`, `test_tool_registry.py`, `test_capabilities.py`, and `test_mcp_approval.py`: extend existing regression fixtures as signatures evolve.
- `backend/tests/agent/test_policy.py`: Policy defaults, ranges, CAS, corruption, and reset.
- `backend/tests/agent/test_governance.py`: `RunControl`, deadlines, reservations, usage, and middleware ordering.
- `backend/tests/agent/test_context_governance.py`: canonical rendering and deterministic complete-turn trimming.
- `backend/tests/agent/test_tool_executor.py`: bounded capacity, late futures, locks, and shutdown.
- `backend/tests/agent/test_provenance.py`: CommonMark extraction, URL golden corpus, redaction, deduplication, and capacity.
- `backend/tests/agent/test_artifacts.py`: all four schemas, immutable chains, staging, commit compensation, deletion, and recovery.
- `backend/tests/agent/test_artifact_api.py`: thread-scoped REST, headers, pagination-independent lookup, and structured errors.
- `backend/tests/agent/test_agent_1d_integration.py`: hard-limit, resume/snapshot, concurrent-tool, event-order, and secret-regression vertical tests.
- `backend/tests/agent_e2e_app.py`: isolated production-router app with fake model/MCP/tools and deterministic seed data for Playwright.

### Frontend Production

- `frontend/src/lib/agent/types.ts`: exact Policy, usage, context, run-list, Source, Artifact, event, and error wire types.
- `frontend/src/lib/agent/api.ts`: Policy, historical run, Artifact list/detail/download/delete calls through the existing authenticated request discipline.
- `frontend/src/lib/agent/runtime.tsx`: tee-scan all four project events and notify the workspace store while keeping the AG-UI stream intact.
- `frontend/src/lib/agent/workspace.ts`: non-persistent thread-scoped Inspector selection, event revision watermarks, invalidation, and drawer exclusivity.
- `frontend/src/components/agent/AgentWorkspace.tsx`: fixed-height desktop grid and mutually exclusive mobile drawers.
- `frontend/src/components/agent/AgentThreadList.tsx`: searchable thread column with status/warning rows and revision-safe actions.
- `frontend/src/components/agent/AgentThread.tsx`: compact chat header/composer states and Artifact result action.
- `frontend/src/components/agent/AgentInspector.tsx`: accessible Run/Approval/Artifact/Sources tabs and historical run selector.
- `frontend/src/components/agent/RunInspector.tsx`: budgets, timing, Provider tokens, truncation, and terminal error facts.
- `frontend/src/components/agent/ArtifactViewer.tsx`: non-executable Markdown/table/JSON/sources views, version navigation, download, and leaf delete.
- `frontend/src/components/agent/SourceInspector.tsx`: execution records and unverified model URLs without scoring.
- `frontend/src/components/agent/AgentSettingsDrawer.tsx`: Model/Skills/MCP/Policy tabs, busy gates, CAS conflicts, and corrupt reset.
- `frontend/src/components/agent/WorkspaceDrawer.tsx`: dialog semantics, focus trap/return, Escape/backdrop, and unsaved-change guard.
- `frontend/src/pages/Agent.tsx`: state/controller orchestration only; no large embedded forms or panel markup.
- `frontend/src/components/layout/Layout.tsx`: allow `/agent` to consume the available main viewport without the normal max-width/outer scroll container.
- `frontend/src/index.css`: only the Agent workspace height/overflow and safe Markdown viewer rules that cannot be expressed clearly with existing utilities.

### Frontend Tests And Documentation

- Matching colocated `*.test.tsx`/`*.test.ts` files for every new frontend module plus existing `Agent.test.tsx`, `runtime.test.tsx`, `history.test.tsx`, and approval contract tests.
- `frontend/playwright.config.ts`: isolated backend/frontend `webServer`, fixed ports/base URL, and deterministic teardown.
- `frontend/e2e/agent-workspace.spec.ts`: complete desktop/mobile/light/dark behavior and screenshot checks.
- `frontend/package.json`, `frontend/package-lock.json`: exact Playwright dev dependency and explicit `test:e2e` / `test:e2e:install` scripts.
- `README.md`, `README_en.md`: Agent Workspace usage, Policy/Artifact safety, and browser-test setup.
- `docs/superpowers/verification/2026-08-17-langchain-agent-workspace-1d.md`: final commands, counts, screenshots, secret scan, and commit evidence.

## Commit Gates

For every backend task, first run the focused red/green command in that task, then run:

```bash
cd backend && .venv/bin/pytest -m "not live"
```

For every frontend task, run:

```bash
cd frontend && npm test && npm run test:unit && npm run build
```

At each slice boundary run both gates plus `git diff --check`. Stage only the exact files named by that task; never use `git add .`, and never stage `.superpowers/`, `.vr-dev/`, `.zcode/`, `AGENTS.md`, `scripts/`, or the existing untracked 1C plan.

## Slice 1: Enforced Product-Run Governance

### Task 1: Add Additive 1D Wire Models And Backward-Compatible Defaults

**Files:**
- Modify: `backend/agent/models.py`
- Modify: `backend/tests/agent/test_models.py`
- Modify: `backend/tests/agent/test_run_persistence.py`

- [ ] **Step 1: Write failing model and old-document tests**

Add tests that validate exact defaults for a pre-1D run, strict governance inputs, token status derivation fields, and that `budget_snapshot={}` remains distinguishable from a current default Policy. Source models and Source defaults are introduced in Task 9 so they remain wholly inside slice 2.

```python
def test_pre_1d_run_loads_without_inventing_governance_data():
    run = RunDocument.model_validate(legacy_run_payload())
    assert run.budget_snapshot == {}
    assert run.control_revision == 0
    assert run.usage.token_status == "unavailable"
    assert run.context_truncation == ContextTruncation(occurred=False)
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run: `cd backend && .venv/bin/pytest tests/agent/test_models.py tests/agent/test_run_persistence.py -q -k 'pre_1d or policy_snapshot or token_status or context_truncation'`

Expected: FAIL because the typed fields and defaults do not exist.

- [ ] **Step 3: Add the exact additive models**

Define strict models in `models.py` and use them directly on `RunDocument`:

```python
TokenStatus = Literal["available", "partial", "unavailable"]


class PolicySnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    policy_revision: int = Field(ge=0)
    max_model_calls: int = Field(ge=1, le=32)
    max_tool_calls: int = Field(ge=1, le=64)
    tool_timeout_seconds: int = Field(ge=5, le=120)
    max_active_seconds: int = Field(ge=30, le=1800)
    max_context_chars: int = Field(ge=16000, le=500000)


class ContextTruncation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    occurred: bool = False
    original_chars: int | None = Field(default=None, ge=0)
    retained_chars: int | None = Field(default=None, ge=0)
    removed_turns: int | None = Field(default=None, ge=0)


class RunUsage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    model_calls: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    token_status: TokenStatus = "unavailable"
```

Keep `RunDocument.budget_snapshot` typed as `PolicySnapshot | dict[str, Any]` and add a field validator that accepts a dictionary only when it is exactly `{}`; add `control_revision` and `context_truncation`. Keep the existing `RunSummary` shape unchanged; historical pagination uses the separate `RunListItem` response model introduced in Task 8. Artifact wire models remain in slice 2.

- [ ] **Step 4: Run focused and full backend tests**

Run: `cd backend && .venv/bin/pytest tests/agent/test_models.py tests/agent/test_run_persistence.py -q && .venv/bin/pytest -m "not live"`

Expected: PASS; no existing thread/run fixture requires a migration write.

- [ ] **Step 5: Commit**

```bash
git add backend/agent/models.py backend/tests/agent/test_models.py backend/tests/agent/test_run_persistence.py
git commit -m "feat(agent): add 1d run wire models"
```

### Task 2: Implement The Versioned Policy Store And REST Contract

**Files:**
- Create: `backend/agent/policy.py`
- Modify: `backend/agent/stores.py`
- Modify: `backend/agent/router.py`
- Create: `backend/tests/agent/test_policy.py`
- Modify: `backend/tests/agent/test_router.py`

- [ ] **Step 1: Write failing Policy store and API tests**

Cover missing/default/non-persisted GET, every inclusive range edge, unknown fields, partial PATCH, CAS conflict/current revision, normal reset, malformed/schema/range corruption, two repeated non-destructive corrupt GETs, new-run fail-closed status, mutually exclusive reset bodies, retained `.corrupt-<timestamp>`, and explicit recovery to revision 1.

```python
def test_corrupt_get_is_non_destructive_until_confirmed_reset(tmp_path):
    path = tmp_path / "policy.json"
    path.write_text('{"schema_version":1,"max_model_calls":0}', encoding="utf-8")
    store = PolicyStore(path)
    for _ in range(2):
        with pytest.raises(PolicyCorrupt) as raised:
            store.get()
        assert raised.value.code == "POLICY_CORRUPT"
        assert path.exists()
    reset = store.reset_corrupt(confirm_corrupt=True)
    assert reset.revision == 1
    assert list(tmp_path.glob("policy.json.corrupt-*"))
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run: `cd backend && .venv/bin/pytest tests/agent/test_policy.py tests/agent/test_router.py -q -k policy`

Expected: FAIL because `PolicyStore` and Policy routes do not exist.

- [ ] **Step 3: Implement strict Policy models and store**

Use these constants and response separation:

```python
POLICY_DEFAULTS = {
    "max_model_calls": 8,
    "max_tool_calls": 16,
    "tool_timeout_seconds": 30,
    "max_active_seconds": 300,
    "max_context_chars": 120_000,
}


class PolicyDocument(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal[1] = 1
    revision: int = Field(ge=1)
    updated_at: str
    max_model_calls: int = Field(ge=1, le=32)
    max_tool_calls: int = Field(ge=1, le=64)
    tool_timeout_seconds: int = Field(ge=5, le=120)
    max_active_seconds: int = Field(ge=30, le=1800)
    max_context_chars: int = Field(ge=16000, le=500000)
```

`PolicyView` adds transport-only `persisted`; missing returns defaults with revision 0 and no write. `PolicyStore.get()` reads the current file directly every time and never calls `_JsonDocuments.get()` because that path quarantines on read. PATCH validates the exact payload before taking the store lock, re-reads under the lock, compares revision, merges normalized values, and calls `atomic_write_json`. Normal reset requires `revision`; corrupt reset requires only `confirm_corrupt=true`, renames the original, fsyncs the directory, then writes revision 1 defaults.

- [ ] **Step 4: Add Policy routes and service ownership**

Add `AgentPaths.policy`, `AgentServices.policy`, and these exact endpoints:

```python
@router.get("/policy")
async def get_policy():
    return await asyncio.to_thread(services.policy.get)

@router.patch("/policy")
async def patch_policy(payload: PolicyPatch):
    return await asyncio.to_thread(services.policy.patch, payload)

@router.post("/policy/reset")
async def reset_policy(payload: PolicyReset):
    return await asyncio.to_thread(services.policy.reset, payload)
```

Map validation to `400 POLICY_INVALID`, stale revision to `409 POLICY_REVISION_CONFLICT` including the current revision, and every corrupt read/PATCH to `503 POLICY_CORRUPT` with a filename/reason that contains no absolute path or file content. Keep all routes under existing `/api/*` auth.

- [ ] **Step 5: Run focused and full backend tests**

Run: `cd backend && .venv/bin/pytest tests/agent/test_policy.py tests/agent/test_router.py -q -k policy && .venv/bin/pytest -m "not live"`

Expected: PASS; repeated corrupt GET never falls back to defaults.

- [ ] **Step 6: Commit**

```bash
git add backend/agent/policy.py backend/agent/stores.py backend/agent/router.py backend/tests/agent/test_policy.py backend/tests/agent/test_router.py
git commit -m "feat(agent): add versioned run policy"
```

### Task 3: Build The Bounded Synchronous Tool Executor

**Files:**
- Create: `backend/agent/tool_executor.py`
- Modify: `backend/agent/tool_registry.py`
- Modify: `backend/agent/router.py`
- Create: `backend/tests/agent/test_tool_executor.py`
- Modify: `backend/tests/agent/test_tool_registry.py`

- [ ] **Step 1: Write failing capacity, timeout, late-result, and shutdown tests**

Use barriers/events, not wall-clock sleeps, to prove four running workers occupy all capacity, the fifth is rejected before submit, capacity remains held after waiter timeout/cancel, a late result is not returned, capacity recovers only after the worker exits, no new work is admitted after shutdown begins, and shutdown returns within a fixed test deadline before blocked workers are released. Start two built-in calls from different product runs and assert their legacy dispatch sections never overlap.

```python
async def test_timeout_does_not_release_running_future_capacity(executor):
    release = threading.Event()
    started = [threading.Event() for _ in range(4)]
    tasks = [asyncio.create_task(blocking_call(executor, started[i], release)) for i in range(4)]
    await wait_all(started)
    with pytest.raises(ToolCapacityExhausted):
        await executor.acquire(capacity_wait_seconds=0.01, deadline=far_deadline())
    tasks[0].cancel()
    with pytest.raises(ToolCapacityExhausted):
        await executor.acquire(capacity_wait_seconds=0.01, deadline=far_deadline())
    release.set()
    await asyncio.gather(*tasks, return_exceptions=True)
    lease = await executor.acquire(capacity_wait_seconds=0.1, deadline=far_deadline())
    lease.release_unsubmitted()
```

- [ ] **Step 2: Run focused tests and verify they fail**

Run: `cd backend && .venv/bin/pytest tests/agent/test_tool_executor.py tests/agent/test_tool_registry.py -q`

Expected: FAIL because built-ins still use `asyncio.to_thread` and the default executor.

- [ ] **Step 3: Implement explicit capacity leases and lease-aware submit**

Expose the minimum public contract: `CapacityLease.release_unsubmitted()`, `BoundedToolExecutor.acquire(capacity_wait_seconds, deadline)`, `run_with_lease(lease, fn, deadline)`, and `shutdown()`. The lease object contains only executor ownership and submit/release state:

```python
@dataclass
class CapacityLease:
    owner: "BoundedToolExecutor"
    submitted: bool
    released: bool = False
```

Back it with `ThreadPoolExecutor(max_workers=4)` plus `BoundedSemaphore(4)`. Acquire the semaphore through an interruptible async bridge before submit; never enqueue without a lease. Transfer ownership to the future when submitted and release in its done callback. On deadline, attempt `future.cancel()` but do not release a running future's token. `begin_shutdown()` atomically rejects new acquisitions; `shutdown()` then calls `shutdown(wait=False, cancel_futures=True)` and returns without waiting for running third-party code.

- [ ] **Step 4: Route built-in handlers through the explicit executor**

Replace the current `async with execution_lock: await asyncio.to_thread(legacy_tools.exec_tool, name, kwargs)` path in `_build_one` with a call that requires a governance-provided `ToolExecutionContext`/capacity lease. Move ownership of the execution lock that `build_builtin_tools()` currently creates per registry to the product-run `RunControl`. Attach immutable metadata to each tool:

```python
metadata={
    "vr_origin": "builtin",
    "vr_execution_lock": True,
    "vr_builtin_serial": True,
    "vr_capacity": True,
}
```

The handler still returns `_encode_result`, preserves existing result trimming, and never reacquires the run execution lock, process serial lock, or capacity token. Inject one executor and one process-wide `asyncio.Lock` for all legacy built-ins into `AgentServices`; do not attempt to classify only currently known Eastmoney names because fallback paths can change. At lifespan shutdown call `begin_shutdown()` first, then cancel/persist coordinator runs and close MCP, then call executor `shutdown()`; this prevents new submissions throughout shutdown while preserving bounded return.

- [ ] **Step 5: Run focused and full backend tests**

Run: `cd backend && .venv/bin/pytest tests/agent/test_tool_executor.py tests/agent/test_tool_registry.py -q && .venv/bin/pytest -m "not live"`

Expected: PASS; test teardown releases every blocking worker so Python does not wait on it at interpreter exit.

- [ ] **Step 6: Commit**

```bash
git add backend/agent/tool_executor.py backend/agent/tool_registry.py backend/agent/router.py backend/tests/agent/test_tool_executor.py backend/tests/agent/test_tool_registry.py
git commit -m "feat(agent): bound synchronous tool execution"
```

### Task 4: Implement RunControl Reservations, Active Segments, And Usage

**Files:**
- Create: `backend/agent/governance.py`
- Modify: `backend/agent/runs.py`
- Create: `backend/tests/agent/test_governance.py`
- Modify: `backend/tests/agent/test_run_persistence.py`

- [ ] **Step 1: Write failing `RunControl` concurrency and clock tests**

Cover model/tool reservation limits under concurrent tasks, persisted count before callback execution, rollback on persistence failure, terminal observation by queued waiters, monotonic `control_revision`, cancel blocking new reservations, active segment start/stop/idempotence, approval pause/resume, earlier tool-vs-active deadline selection, and Provider usage status aggregation.

```python
async def test_parallel_tool_reservations_never_overwrite_a_newer_count():
    persisted: list[tuple[int, int]] = []
    control = RunControl(snapshot(max_tool_calls=2), clock=FakeClock())

    async def persist(view):
        await asyncio.sleep(0)
        persisted.append((view.usage.tool_calls, view.control_revision))

    await asyncio.gather(control.reserve_tool(persist), control.reserve_tool(persist))
    assert [count for count, _ in persisted] == [1, 2]
    assert control.view().usage.tool_calls == 2


async def test_persistence_failure_rolls_back_and_stops_waiters():
    control = RunControl(snapshot(max_tool_calls=2), clock=FakeClock())
    with pytest.raises(GovernancePersistenceFailed):
        await control.reserve_tool(failing_persist)
    assert control.view().usage.tool_calls == 0
    assert control.terminal_error.code == "PERSISTENCE_FAILED"
```

- [ ] **Step 2: Run focused tests and verify they fail**

Run: `cd backend && .venv/bin/pytest tests/agent/test_governance.py tests/agent/test_run_persistence.py -q -k 'control or reservation or active_segment or token'`

Expected: FAIL because the product-run control does not exist.

- [ ] **Step 3: Implement the secret-free control and immutable snapshots**

`RunControl` owns one `threading.RLock` for synchronous counters/clock telemetry and one `asyncio.Lock` named `reservation_lock` around only check/increment -> full run persistence -> rollback. It also owns `execution_lock` and `artifact_mutation_lock` as product-run `asyncio.Lock` objects. Its constructor accepts only a `PolicySnapshot`, a monotonic clock, and no key/session/path/tool body.

Use immutable views when crossing locks:

```python
@dataclass(frozen=True)
class GovernanceView:
    control_revision: int
    usage: RunUsage
    active_elapsed_ms: int
    context_truncation: ContextTruncation


class RunControl:
    reservation_lock: asyncio.Lock
    execution_lock: asyncio.Lock
    artifact_mutation_lock: asyncio.Lock
```

Its public operations are `reserve_model(persist)`, `reserve_tool(persist)`, `begin_active_segment()`, `close_active_segment()`, `remaining_active_seconds()`, `record_model_usage(usage)`, and `mark_terminal(code)`. Each returns an immutable `GovernanceView`; reservation methods are async because the supplied persistence callback is async.

Count an attempted Provider call as missing usage if it was reserved and no complete usage arrived. Derive `available/partial/unavailable` from reserved/completed-with-usage/completed-without-usage counters; never tokenize locally. Allocate and publish `control_revision` only as part of a successful Inspector-visible persistence transaction; rollback restores the prior count/revision, while a persistence failure marks the control terminal so queued reservation waiters cannot proceed.

- [ ] **Step 4: Attach control ownership without changing lifecycle behavior yet**

Add non-optional `control` plus defaulted locks only to production `ActiveRunHandle` creation paths; migrate pure-memory test fixtures through a helper that supplies the default snapshot. Add a coordinator persistence callback that, under the existing thread lock, replaces the full current run with `usage`, `control_revision`, `active_elapsed_ms`, `context_truncation`, and `updated_at` from the view. It must reject a detached/terminal handle and must not persist a lower revision over a higher one.

- [ ] **Step 5: Run focused and full tests**

Run: `cd backend && .venv/bin/pytest tests/agent/test_governance.py tests/agent/test_run_persistence.py -q && .venv/bin/pytest -m "not live"`

Expected: PASS; the old journal still functions until Task 7 removes inferred counts.

- [ ] **Step 6: Commit**

```bash
git add backend/agent/governance.py backend/agent/runs.py backend/tests/agent/test_governance.py backend/tests/agent/test_run_persistence.py
git commit -m "feat(agent): track product run governance"
```

### Task 5: Add Deterministic Context Governance And Model Reservations

**Files:**
- Modify: `backend/agent/governance.py`
- Modify: `backend/agent/runtime.py`
- Modify: `backend/agent/protocol.py`
- Create: `backend/tests/agent/test_context_governance.py`
- Modify: `backend/tests/agent/test_model_factory.py`
- Modify: `backend/tests/agent/test_resume_contract.py`
- Modify: `backend/tests/agent/test_protocol_bridge.py`

- [ ] **Step 1: Write failing canonical-render and model-wrapper tests**

Cover strings, structured blocks, roles/names/call IDs/separators, stable sorted compact JSON, complete user-turn grouping, assistant/tool-call/result atomicity, newest-first optional retention restored to chronological order, current turn, the latest complete `load_skill` turn for every loaded Skill, forced-content overflow before reservation, no thread JSON mutation, resume rebuilding the same wrapper, Provider error/cancel still counted, available/partial/unavailable token usage, and strict `budget.updated` bridge acceptance before any governed stream emits that event.

```python
async def test_forced_context_overflow_prevents_provider_and_reservation():
    control = RunControl(snapshot(max_context_chars=16_000), clock=FakeClock())
    provider = AsyncMock()
    middleware = ContextAndModelGovernance(control, persist=record_view)
    request = model_request(system="x" * 15_000, current_user="y" * 2_000)
    with pytest.raises(ContextLimitExceeded):
        await middleware.awrap_model_call(request, provider)
    provider.assert_not_awaited()
    assert control.view().usage.model_calls == 0
```

- [ ] **Step 2: Run focused tests and verify they fail**

Run: `cd backend && .venv/bin/pytest tests/agent/test_context_governance.py tests/agent/test_model_factory.py tests/agent/test_resume_contract.py tests/agent/test_protocol_bridge.py -q`

Expected: FAIL because `ModelRequest` is not governed and `budget.updated` is not registered.

- [ ] **Step 3: Implement one canonical renderer and complete-turn trimmer**

Implement pure `render_model_context(system_message, messages)` and `trim_model_request(request, limit)` functions in `governance.py`. The latter returns the overridden `ModelRequest` and `ContextTruncation`.

The same rendered string length drives both admission and telemetry. Structured values use `json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)`. Build a deterministic, secret-free Policy explanation from the run snapshot and append it after the unchanged neutrality prompt and before Skill catalog context; system prompt, Policy explanation, and Skill metadata are one forced system unit. Group history from each user message until the next user message. Mark the current user turn and every latest turn containing a loaded Skill's complete `load_skill` result as forced. If forced units plus system content exceed the limit, raise `CONTEXT_LIMIT_EXCEEDED`; never slice a unit, tool args, tool result, system prompt, or Skill instruction.

- [ ] **Step 4: Implement `ContextAndModelGovernance.awrap_model_call`**

Before adding the emitter, register only `budget.updated` in `protocol.py` and test the bridge fail-closed contract. Its strict `extra="forbid"` payload model uses the exact seven camelCase fields from specification section 19.1: `threadId`, `runId`, `controlRevision`, `budgetSnapshot`, `usage`, `activeElapsedMs`, and `contextTruncation`. Parse string/dict values, re-encode canonical JSON, return the same `CustomEvent` type, map malformed known payloads to `INVALID_CUSTOM_EVENT`, and keep unknown names as `UNSUPPORTED_CUSTOM_EVENT`.

Then perform trim/telemetry persistence first. Check the active deadline, reserve/persist one model call, emit `budget.updated` only after successful persistence, then call `handler(request.override(messages=trimmed))` under the remaining active deadline. In `finally`, record either complete `usage_metadata.input_tokens/output_tokens` from the returned AI message or a missing-usage completion for Provider error/cancel. Persist and emit the updated token view without replacing the original exception. Segment open/close, context telemetry, every reservation, and every token-usage change each emits `budget.updated` only after the matching run replacement succeeds.

- [ ] **Step 5: Rebuild the wrapper on start and resume**

Change `AgentFactory.create/resume` to receive a request-middleware factory that closes over the same secret-free control and persistence/event callbacks. Each request still creates a fresh middleware object and model from the current request key. The middleware tuple begins with `ContextAndModelGovernance`; `chat.SYSTEM_PROMPT` and Skill system context remain unchanged and highest priority.

- [ ] **Step 6: Run focused and full backend tests**

Run: `cd backend && .venv/bin/pytest tests/agent/test_context_governance.py tests/agent/test_model_factory.py tests/agent/test_resume_contract.py tests/agent/test_protocol_bridge.py -q && .venv/bin/pytest -m "not live"`

Expected: PASS; resume uses the same counters/snapshot and a newly constructed wrapper.

- [ ] **Step 7: Commit**

```bash
git add backend/agent/governance.py backend/agent/runtime.py backend/agent/protocol.py backend/tests/agent/test_context_governance.py backend/tests/agent/test_model_factory.py backend/tests/agent/test_resume_contract.py backend/tests/agent/test_protocol_bridge.py
git commit -m "feat(agent): govern model context and usage"
```

### Task 6: Enforce Tool Admission After Guards And Approval

**Files:**
- Modify: `backend/agent/governance.py`
- Modify: `backend/agent/tool_registry.py`
- Modify: `backend/agent/capabilities.py`
- Modify: `backend/agent/runtime.py`
- Modify: `backend/tests/agent/test_governance.py`
- Modify: `backend/tests/agent/test_tool_executor.py`
- Modify: `backend/tests/agent/test_tool_registry.py`
- Modify: `backend/tests/agent/test_capabilities.py`
- Modify: `backend/tests/agent/test_mcp_approval.py`

- [ ] **Step 1: Write failing middleware-order and exact-count tests**

Test schema rejection, MCP argument guard rejection, HITL pending, once reject, and steer-away as zero reservations/zero handler calls; approve once, thread-session allowance, Skill, built-in, `create_artifact`, and structured business error as one reservation. Test execution-lock, process-wide built-in serial-lock, and capacity timeout before reservation, persistence failure releasing all prerequisites with zero submit, parallel calls hitting the exact limit, MCP's existing 60-second lifecycle under a shorter Policy deadline, and one run's built-in/Skill/Artifact handlers never overlapping. In the cross-run serial-lock case, hold the first run in legacy dispatch until the second run's earlier tool/active deadline expires, then assert the second handler was never called and received the correct timeout code.

```python
@pytest.mark.parametrize("path", ["schema", "guard", "pending", "reject", "steer"])
async def test_pre_execution_paths_do_not_consume_tool_budget(path, governed_graph):
    result = await governed_graph.exercise(path)
    assert result.handler_calls == 0
    assert result.run.usage.tool_calls == 0


@pytest.mark.parametrize("path", ["approve", "allowance", "business_error"])
async def test_real_execution_paths_reserve_once(path, governed_graph):
    result = await governed_graph.exercise(path)
    assert result.handler_calls == 1
    assert result.run.usage.tool_calls == 1
```

- [ ] **Step 2: Run focused tests and verify they fail**

Run: `cd backend && .venv/bin/pytest tests/agent/test_governance.py tests/agent/test_tool_executor.py tests/agent/test_tool_registry.py tests/agent/test_capabilities.py tests/agent/test_mcp_approval.py -q -k 'govern or budget or reservation or capacity or approval'`

Expected: FAIL because execution is not yet wrapped at the real handler boundary.

- [ ] **Step 3: Implement tool classification and schema prevalidation**

Use tool metadata (`vr_origin`, `vr_execution_lock`, `vr_capacity`) rather than name prefixes except for already-bound MCP identity metadata. Before any lock/reservation, validate `request.tool_call["args"]` with the bound tool's `args_schema` (`model_validate` for Pydantic 2, `parse_obj` fallback for locked compatibility). On failure call the inner handler without reservation so LangGraph creates its native validation `ToolMessage`; the real handler must remain uncalled.

- [ ] **Step 4: Implement `ToolExecutionGovernance.awrap_tool_call`**

The wrapper starts the tool deadline immediately after successful schema prevalidation. For local/Skill/Artifact tools acquire the product-run execution lock within `min(tool, active)`; a legacy built-in then acquires the shared process serial lock under that same earlier deadline; capacity tools next acquire a `CapacityLease`. Every lock/capacity wait, handler execution, and result wait is bounded by `min(tool, active)`. Only after all applicable prerequisites succeed call `RunControl.reserve_tool(persist)`. Install a request-only `ContextVar[ToolExecutionContext]` containing validated thread/product-run identity, the `RunControl`, Artifact service reference, optional capacity lease, and absolute deadlines; invoke the inner handler, then reset the context and release locks in reverse order. The context contains no secret. MCP skips local/serial locks and capacity but uses the same reservation immediately before its real handler.

Map the earlier exhausted deadline to `RUN_ACTIVE_TIMEOUT`; otherwise map it to `TOOL_TIMEOUT`. Capacity's one-second admission failure is `TOOL_CAPACITY_EXHAUSTED`. A running sync future retains its lease through the future callback, not wrapper cleanup.

- [ ] **Step 5: Compose middleware in the locked-version order**

Have the runtime produce this logical tuple for each request:

```python
(
    ContextAndModelGovernance(control=control, persist=persist, emit=emit),
    *lease.execution_guards(secrets),  # McpArgumentGuard, then HITL when present
    ToolExecutionGovernance(
        control=control,
        persist=persist,
        executor=executor,
        builtin_serial_lock=builtin_serial_lock,
    ),
)
```

Do not assert correctness from tuple order alone. Preserve `capabilities.py`'s `McpArgumentGuard` model hook and HITL construction while passing their lease-derived bindings/secrets through this composition point; do not turn either into a tool wrapper. The integration tests must execute guard/pending/reject/approve/allowance paths because guard is an `awrap_model_call` hook and HITL is `after_model`, not a tool wrapper. Audit any future/custom middleware with `awrap_tool_call` before placing it after governance.

- [ ] **Step 6: Run focused and full backend tests**

Run: `cd backend && .venv/bin/pytest tests/agent/test_governance.py tests/agent/test_tool_executor.py tests/agent/test_tool_registry.py tests/agent/test_capabilities.py tests/agent/test_mcp_approval.py -q && .venv/bin/pytest -m "not live"`

Expected: PASS; capacity/lock rejection never increments `tool_calls`, while a timed-out executing handler remains counted.

- [ ] **Step 7: Commit**

```bash
git add backend/agent/governance.py backend/agent/tool_registry.py backend/agent/capabilities.py backend/agent/runtime.py backend/tests/agent/test_governance.py backend/tests/agent/test_tool_executor.py backend/tests/agent/test_tool_registry.py backend/tests/agent/test_capabilities.py backend/tests/agent/test_mcp_approval.py
git commit -m "feat(agent): enforce tool run policy"
```

### Task 7: Integrate Governance With Every Product-Run Lifecycle

**Files:**
- Modify: `backend/agent/runs.py`
- Modify: `backend/agent/router.py`
- Modify: `backend/tests/agent/test_run_persistence.py`
- Modify: `backend/tests/agent/test_resume_contract.py`
- Modify: `backend/tests/agent/test_router.py`
- Create: `backend/tests/agent/test_agent_1d_integration.py`

- [ ] **Step 1: Write failing lifecycle and hard-limit vertical tests**

Cover the ninth Provider request and seventeenth actual handler blocked at defaults; parallel tool calls at the boundary; model reservation JSON failure causing zero Provider calls and no false `budget.updated`; tool reservation JSON failure causing zero handler/provenance; four product runs occupying all synchronous workers, a fifth product run failing with `TOOL_CAPACITY_EXHAUSTED`, and a new run succeeding after release; capacity wait advancing active elapsed; start/resume count reuse; retry/steer new snapshots; Policy mutation during a run; corrupt Policy during resume; duplicate precedence during corruption; active clock across approval; cancel blocking new reservations; Graph-build compensation; and steer-away Policy/Capability failure leaving the old pending run and submitted entries untouched.

```python
async def test_steer_admission_failure_preserves_old_pending_run(services):
    awaiting = await create_awaiting_run(services)
    corrupt_policy(services.paths.policy)
    with pytest.raises(PolicyCorrupt):
        await services.coordinator.acquire_steer_away(**steer_args(awaiting))
    assert services.runs.get(awaiting.product_run_id).status == "awaiting_approval"
    assert services.coordinator.active(awaiting.thread_id).pending_interrupts
    assert services.threads.get(awaiting.thread_id).messages == awaiting.messages_before
```

- [ ] **Step 2: Run focused tests and verify they fail**

Run: `cd backend && .venv/bin/pytest tests/agent/test_agent_1d_integration.py tests/agent/test_run_persistence.py tests/agent/test_resume_contract.py tests/agent/test_router.py -q`

Expected: FAIL because lifecycle admission does not yet load/reuse snapshots consistently.

- [ ] **Step 3: Create one shared new-product-run commit helper**

After existing duplicate/head/revision/busy preview and before any user/run write, load a Policy snapshot. Acquire the Capability lease outside the thread lock, then re-enter the lock and revalidate both preview facts and Policy-independent authoritative facts. Use one `_commit_new_run_locked` helper for start, retry, and steer-away to create `RunControl`, begin the first active segment, write the complete immutable `budget_snapshot`, persist run/user/summary, build the Graph, attach journal/control/locks, and install the handle.

Retry keeps its trigger-history rules but calls the helper. Steer-away must acquire a valid Policy snapshot and new Capability lease before the atomic transition that cancels the old run; any pre-transition failure preserves the old handle, interrupts, allowances, messages, and submitted cancelled entries.

- [ ] **Step 4: Reuse the same control through resume and all terminals**

Resume performs only its existing duplicate/revision/busy/shape/config checks, reopens the same control's active segment after successful rebuild/persistence, and never reads Policy. On approval interrupt, persist pending/thread state, close and persist the active segment, emit thread revision then budget, and only then send the standard interrupt `RUN_FINISHED` outcome. Persist terminal/cancel/context/usage before emitting terminal events. Cancel first marks control terminal, then stops waits and follows the existing shielded persistence path. Detach/release lease only after final control state is persisted.

- [ ] **Step 5: Remove event-inferred authoritative call counts**

Delete `RunJournal.model_calls/tool_calls` inference and change `_usage_update` to merge only the latest `RunControl` view. Keep event observation solely for message boundaries, tool summaries, Provider usage handoff, and provenance hooks. Provider error and tool timeout/cancel must therefore display the already persisted reservation.

- [ ] **Step 6: Run focused and full backend tests**

Run: `cd backend && .venv/bin/pytest tests/agent/test_agent_1d_integration.py tests/agent/test_run_persistence.py tests/agent/test_resume_contract.py tests/agent/test_router.py -q && .venv/bin/pytest -m "not live"`

Expected: PASS; all start/resume/retry/steer/cancel/approval regressions remain green.

- [ ] **Step 7: Commit**

```bash
git add backend/agent/runs.py backend/agent/router.py backend/tests/agent/test_run_persistence.py backend/tests/agent/test_resume_contract.py backend/tests/agent/test_router.py backend/tests/agent/test_agent_1d_integration.py
git commit -m "feat(agent): govern every run lifecycle"
```

### Task 8: Expose Governance State, Run Pagination, And Strict Events

**Files:**
- Modify: `backend/agent/stores.py`
- Modify: `backend/agent/router.py`
- Modify: `backend/tests/agent/test_stores.py`
- Modify: `backend/tests/agent/test_router.py`
- Modify: `backend/tests/agent/test_agent_1d_integration.py`

- [ ] **Step 1: Write failing run-list, error-code, payload, and order tests**

Cover `(started_at,id)` descending order, 1/100 range and default 50, same-thread `before`, cross-thread rejection, next cursor, corrupt filename-only warnings, light summaries with no messages/Sources/secrets, all governance error codes preserved instead of `AGENT_RUN_FAILED`, regression of the Task 5 `budget.updated` schema/unknown-event contract, and final thread -> budget -> optional sources -> terminal order.

- [ ] **Step 2: Run focused tests and verify they fail**

Run: `cd backend && .venv/bin/pytest tests/agent/test_stores.py tests/agent/test_protocol_bridge.py tests/agent/test_router.py tests/agent/test_agent_1d_integration.py -q -k 'run_list or pagination or budget or custom_event or terminal_order or governance_error'`

Expected: FAIL because pagination and lifecycle-level governance error/order mapping do not exist; the focused Task 5 bridge cases remain green.

- [ ] **Step 3: Add paginated historical run reads**

Implement `RunStore.page_for_thread(thread_id, limit, before)` from one scan result so warnings and documents describe the same filesystem observation. Validate `before` belongs to the requested thread, sort by `(started_at,id)` descending, return only exact lightweight `RunListItem` fields, and emit `{runs,next_before,warnings}` from `GET /api/agent/threads/{thread_id}/runs`.

- [ ] **Step 4: Preserve the strict governance-event validator**

Keep the Task 5 bridge contract unchanged while wiring lifecycle streams:

```python
CUSTOM_EVENT_MODELS = {
    "budget.updated": BudgetUpdatedEventValue,
    # Artifact events are registered in Task 13 when their models exist.
}
```

Regression-test string/dict parsing, canonical re-encoding, `on_interrupt` suppression, and router-owned `thread.revision.updated`. Do not emit `artifact.created` or `sources.updated` in slice 1; Task 13 adds those two models and producers together.

- [ ] **Step 5: Preserve governance error identity and terminal order**

Map `GovernanceError.code` to `RunDocument.error_code` and the terminal `RUN_ERROR.code`; do not route it through `_error_event()`'s hard-coded code. Every terminal branch persists final run/thread state, emits final thread revision, then final budget event, then any committed source event when that producer exists, then `RUN_FINISHED`/`RUN_ERROR`. Stream encoding/disconnect failure never rolls back committed JSON.

- [ ] **Step 6: Run the slice-1 gate**

Run:

```bash
cd backend && .venv/bin/pytest -m "not live"
cd ../frontend && npm test && npm run test:unit && npm run build
cd .. && git diff --check
```

Expected: all PASS. A run's hard limits, usage, context telemetry, and history are independently inspectable through REST before any final Inspector UI exists.

- [ ] **Step 7: Commit**

```bash
git add backend/agent/stores.py backend/agent/router.py backend/tests/agent/test_stores.py backend/tests/agent/test_router.py backend/tests/agent/test_agent_1d_integration.py
git commit -m "feat(agent): expose governed run state"
```

## Slice 2: Typed Artifacts And Provenance

### Task 9: Implement URL Provenance And Source Admission

**Files:**
- Modify: `backend/requirements.txt`
- Modify: `backend/agent/models.py`
- Create: `backend/agent/provenance.py`
- Modify: `backend/agent/runs.py`
- Modify: `backend/tests/agent/test_models.py`
- Create: `backend/tests/agent/test_provenance.py`
- Modify: `backend/tests/agent/test_run_persistence.py`

- [ ] **Step 1: Write failing CommonMark, normalization, redaction, and capacity tests**

Create a table-driven golden corpus covering Markdown link/autolink destinations, bare URLs in text nodes, link text, inline/fenced/indented code exclusions, iterative sentence punctuation, balanced and unmatched ASCII/CJK closers, 2,048-character boundary, absolute HTTP/HTTPS only, hostname required, userinfo rejection, lowercased scheme/host, default-port/fragment removal, empty path `/`, unchanged query order/percent encoding, and no network calls. Add the discriminated Source model tests deferred from Task 1: pre-1D defaults are `sources=[]`/`sources_truncated=false`, and fixed verification literals distinguish executed tool records from model-provided unverified URLs. Also cover tool-call ID dedupe, URL-key dedupe, redact-before-1,000-character truncation with both model and MCP secrets, automatic fill-to-200 with `sources_truncated=true`, and explicit capacity simulation with zero partial writes.

```python
@pytest.mark.parametrize(("markdown", "expected"), URL_GOLDEN_CORPUS)
def test_commonmark_url_golden_corpus(markdown, expected):
    assert [item.url for item in extract_model_urls(markdown)] == expected


def test_tool_summaries_are_redacted_before_truncation():
    source = tool_execution_source(args={"token": SECRET + "x" * 2000}, result={})
    assert SECRET not in source.arguments_summary
    assert len(source.arguments_summary) <= 1000


def test_source_record_verification_is_fixed_by_kind():
    tool = ToolExecutionSource.model_validate(tool_source_payload())
    url = ModelUrlSource.model_validate(url_source_payload())
    assert tool.verification == "executed_record"
    assert url.verification == "model_provided_unverified"
```

- [ ] **Step 2: Add the direct parser dependency and verify the red test**

Add `commonmark==0.9.1` to `backend/requirements.txt`, install it in the existing venv, then run:

```bash
cd backend
PIP_INDEX_URL="${PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}" \
  .venv/bin/pip install -r requirements.txt
.venv/bin/pytest tests/agent/test_models.py tests/agent/test_provenance.py tests/agent/test_run_persistence.py -q -k 'source or provenance or commonmark'
```

On this development host, set `PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple` for the install because `pypi.org` is unreachable. Expected: dependency installation succeeds; tests FAIL because the Source fields/models and provenance module do not exist.

- [ ] **Step 3: Implement structured URL parsing and the CommonMark walker**

Add discriminated `ToolExecutionSource` and `ModelUrlSource` models with the exact fields, limits, and fixed verification literals from the specification, then add backward-compatible `RunDocument.sources` and `sources_truncated` defaults. Use `commonmark.Parser().parse(text).walker()` to collect link destinations and text nodes. Never scan code nodes or link labels for bare URLs. A small URL-token recognizer may locate candidates only inside eligible text nodes; all acceptance/normalization uses `urllib.parse.urlsplit/urlunsplit`, rejects userinfo, and applies the exact punctuation/paired-closer algorithm before structured validation.

Expose four focused functions: `normalize_source_url(candidate) -> NormalizedUrl`, `extract_model_urls(markdown) -> list[NormalizedUrl]`, `summarize_tool_source(call, secrets) -> ToolExecutionSource`, and `plan_source_admission(existing, descriptors) -> SourcePlan`. The request-only `secrets` set must be the union of the current model API key and every MCP secret in the current Capability lease/registry secret sets; redact recursively before truncation, and never persist the set itself.

- [ ] **Step 4: Integrate automatic Sources behind `artifact_mutation_lock`**

After a completed tool result and tool summary are durably written, append one `tool_execution` Source under the product run's `artifact_mutation_lock`, keyed by `tool_call_id`; structured business errors count. After a complete non-pending assistant message is durable, extract URLs in text order and append only missing normalized keys until capacity. Partial/pending messages add none. Each successful Source persistence increments `control_revision`; automatic overflow sets `sources_truncated=true` without reordering existing records. Source commits use the synchronous RunControl/thread-lock revision path inside the short coordinator section and must never acquire `reservation_lock` while holding the coordinator thread lock.

- [ ] **Step 5: Add the accepted write-amplification benchmark**

Build a maximum legal run fixture with 20 x 6,000-character tool summaries and 200 Sources whose two summaries are 1,000 characters. Assert canonical full-document replacement stays within the specification's conservative approximately 3 MB bound and completes through `RunStore.replace`; do not lower the limit by silently trimming valid data.

- [ ] **Step 6: Run focused and full backend tests**

Run: `cd backend && .venv/bin/pytest tests/agent/test_models.py tests/agent/test_provenance.py tests/agent/test_run_persistence.py -q && .venv/bin/pytest -m "not live"`

Expected: PASS; no test opens a socket or records a secret.

- [ ] **Step 7: Commit**

```bash
git add backend/requirements.txt backend/agent/models.py backend/agent/provenance.py backend/agent/runs.py backend/tests/agent/test_models.py backend/tests/agent/test_provenance.py backend/tests/agent/test_run_persistence.py
git commit -m "feat(agent): record run provenance"
```

### Task 10: Add Typed Immutable Artifact Validation And Storage

**Files:**
- Modify: `backend/agent/models.py`
- Create: `backend/agent/artifacts.py`
- Modify: `backend/agent/stores.py`
- Modify: `backend/tests/agent/test_models.py`
- Create: `backend/tests/agent/test_artifacts.py`

- [ ] **Step 1: Write failing schema, canonical-size, path, and chain tests**

Cover the lightweight `ArtifactMetadata` wire model deferred from Task 1; title trim/1/200; four legal content types; table 1/50 columns, key regex/uniqueness, label 1/100, 5,000 rows, exact keys, explicit null, scalar-only and finite numbers; JSON root depth 1/max 32, 50,000-node accounting, finite values; sources 200, unique IDs, subset of public `source_ids`, note 2,000; canonical UTF-8 bytes including newline at 1,048,576/1,048,577; service UUID/path traversal/symlink rejection; parent same thread/type, leaf-only extension, cycle/fork detection; and immutable no-update behavior.

```python
def test_canonical_size_counts_exact_staged_bytes(tmp_path):
    artifact = artifact_with_exact_serialized_size(1_048_576)
    encoded = encode_artifact(artifact)
    assert len(encoded) == 1_048_576
    stage = ArtifactStore(paths(tmp_path)).stage(artifact)
    assert stage.path.read_bytes() == encoded
    with pytest.raises(ArtifactInvalid):
        encode_artifact(artifact_with_exact_serialized_size(1_048_577))
```

- [ ] **Step 2: Run focused tests and verify they fail**

Run: `cd backend && .venv/bin/pytest tests/agent/test_models.py tests/agent/test_artifacts.py -q -k 'artifact or schema or size or path or parent or chain'`

Expected: FAIL because the Artifact wire model and validation/storage do not exist.

- [ ] **Step 3: Implement strict typed content and canonical encoding**

Add the lightweight `ArtifactMetadata` model for events/list responses. Use discriminated Pydantic models for `markdown`, `table`, `json`, and `sources`, with explicit validators for finite numbers, row shape, node/depth counts, and source relations. `encode_artifact()` must be exactly:

```python
payload = artifact.model_dump(mode="json")
encoded = (
    json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    + "\n"
).encode("utf-8")
if len(encoded) > ARTIFACT_MAX_BYTES:
    raise ArtifactInvalid("ARTIFACT_INVALID", "Artifact 超过 1 MB")
```

- [ ] **Step 4: Implement controlled staging, publish, reads, and chain inspection**

Add `AgentPaths.artifacts`. Validate IDs before constructing `<root>/artifacts/<thread-id>/<artifact-id>.json`. `stage()` writes only `<artifact-id>.<nonce>.artifact.tmp`, flushes and fsyncs the file, and returns an immutable `StagedArtifact`; `publish()` uses `os.replace` and fsyncs the final directory. Never follow symlinks. List/detail reads validate the file's thread/run identity and calculate parent/child state without accepting a second global index.

- [ ] **Step 5: Run focused and full backend tests**

Run: `cd backend && .venv/bin/pytest tests/agent/test_models.py tests/agent/test_artifacts.py -q && .venv/bin/pytest -m "not live"`

Expected: PASS; no Artifact is yet exposed to a Graph or REST create endpoint.

- [ ] **Step 6: Commit**

```bash
git add backend/agent/models.py backend/agent/artifacts.py backend/agent/stores.py backend/tests/agent/test_models.py backend/tests/agent/test_artifacts.py
git commit -m "feat(agent): store typed immutable artifacts"
```

### Task 11: Implement The Transactional `create_artifact` Tool

**Files:**
- Modify: `backend/agent/artifacts.py`
- Modify: `backend/agent/tool_registry.py`
- Modify: `backend/agent/runs.py`
- Modify: `backend/agent/router.py`
- Modify: `backend/tests/agent/test_artifacts.py`
- Modify: `backend/tests/agent/test_tool_registry.py`
- Modify: `backend/tests/agent/test_agent_1d_integration.py`

- [ ] **Step 1: Write failing descriptor, lock-order, commit, and compensation tests**

Cover current-run completed tool references only; URL reuse; duplicate descriptor key/final source ID; `source_index` range/uniqueness/rewrite; 197+4 atomic capacity failure with descriptor index/reason/remaining capacity; full-200 reuse-only success; parent conflict; simultaneous same-parent creation with one winner; cancel/fork/revision recheck after staging; unique cross-thread temp names; staging capacity exhaustion; late future cleanup only; file/source/thread failure at every commit boundary; Source retained and event emitted when later Artifact publication fails; no false `artifact.created`; and Artifact survival after later run failure/cancel.

```python
async def test_source_capacity_failure_is_atomic(active_run):
    seed_sources(active_run, 197)
    result = await call_create_artifact(active_run, sources=four_new_urls())
    assert result == {
        "ok": False,
        "code": "ARTIFACT_SOURCE_INVALID",
        "descriptor_index": 3,
        "reason": "source_capacity_exceeded",
        "remaining_capacity": 3,
    }
    assert len(reload_run(active_run).sources) == 197
    assert reload_thread(active_run).artifact_ids == []
```

- [ ] **Step 2: Run focused tests and verify they fail**

Run: `cd backend && .venv/bin/pytest tests/agent/test_artifacts.py tests/agent/test_tool_registry.py tests/agent/test_agent_1d_integration.py -q -k 'create_artifact or descriptor or artifact_commit or source_capacity'`

Expected: FAIL because the tool/service transaction does not exist.

- [ ] **Step 3: Implement request planning and correctable results**

Define the exact tool args schema from the specification. Under `artifact_mutation_lock`, briefly acquire the coordinator thread lock and build an immutable `ArtifactCommitPlan` from current run/thread/parent/tool summaries/Sources. Resolve all descriptors in input order, reject duplicate normalized identities, simulate total capacity, rewrite sources-content `source_index` to final immutable `source_id`, validate final canonical bytes, then release the coordinator lock.

Schema/type/size/parent/source errors return a structured tool result with the original error code and no writes. Source errors include the first failing `descriptor_index`, normalized reason, and `remaining_capacity`, but never echo an unredacted full URL. `ARTIFACT_PERSISTENCE_FAILED` is raised as a terminal exception and never returned as a correctable result.

- [ ] **Step 4: Stage with the already-held capacity lease**

Mark `create_artifact` metadata as `origin=artifact`, `execution_lock=true`, and `capacity=true`. Its handler obtains the current `ToolExecutionContext`, then calls `BoundedToolExecutor.run_with_lease` to write/fsync staging. It must never reacquire capacity. On timeout/cancel, the future's done callback deletes only its own matching temp file and cannot publish or mutate authoritative state.

- [ ] **Step 5: Commit in the fixed coordinator-lock sequence**

Before any write, reacquire the coordinator thread lock and revalidate active control, thread revision, parent leaf, descriptor facts, and plan identity. While holding that short lock: persist new Source records to the run; publish/fsync Artifact; update thread `artifact_ids` with one revision. On run-source failure delete staging. On publish/thread-reference failure delete an unreferenced final file; if cleanup fails, record an orphan warning. Never roll back already committed independent Sources. Return only:

```python
{
    "ok": True,
    "artifact": {
        "id": artifact.id,
        "title": artifact.title,
        "type": artifact.type,
        "run_id": artifact.run_id,
        "parent_artifact_id": artifact.parent_artifact_id,
    },
    "thread_revision": updated_thread.revision,
}
```

- [ ] **Step 6: Register the tool for every run without weakening leases**

Register one generic `create_artifact` StructuredTool in the production tool provider before Graph creation. Wire `ArtifactService` through `router.py`'s `AgentServices` production composition so the registry receives the same service used by REST/recovery. The tool schema contains no thread/run IDs; at execution it obtains the already-validated thread ID, product run ID, `RunControl`, `ArtifactService`, capacity lease, and deadlines from the `ToolExecutionContext` installed by governance. Omit this tool when low-level tests construct a registry without an Artifact service. Count it through governance and keep MCP guard/HITL scope unchanged. No REST POST route is added.

- [ ] **Step 7: Run focused and full backend tests**

Run: `cd backend && .venv/bin/pytest tests/agent/test_artifacts.py tests/agent/test_tool_registry.py tests/agent/test_agent_1d_integration.py -q && .venv/bin/pytest -m "not live"`

Expected: PASS; every declared consistency failure has zero false Artifact event.

- [ ] **Step 8: Commit**

```bash
git add backend/agent/artifacts.py backend/agent/tool_registry.py backend/agent/runs.py backend/agent/router.py backend/tests/agent/test_artifacts.py backend/tests/agent/test_tool_registry.py backend/tests/agent/test_agent_1d_integration.py
git commit -m "feat(agent): create artifacts transactionally"
```

### Task 12: Add Thread-Scoped Artifact REST, Delete Tombstones, And Recovery

**Files:**
- Modify: `backend/agent/artifacts.py`
- Modify: `backend/agent/stores.py`
- Modify: `backend/agent/runs.py`
- Modify: `backend/agent/router.py`
- Modify: `backend/tests/agent/test_artifacts.py`
- Create: `backend/tests/agent/test_artifact_api.py`
- Modify: `backend/tests/agent/test_thread_api.py`
- Modify: `backend/tests/agent/test_stores.py`

- [ ] **Step 1: Write failing REST, delete, cascade, and startup-reconciliation tests**

Cover thread first/not-found; membership before file read; orphan and thread mismatch hidden; list metadata/parent-child/missing-reference warning; no scan of unrelated thread documents for detail/download/delete; fixed extension/MIME/attachment ID/CSP/nosniff; revision/busy/leaf delete; file-delete failure after reference commit; thread cascade tombstone rename rollback before commit; cleanup failure after commit; nested temp recursion; missing/corrupt/orphan/cross-thread/run/type/fork/cycle warnings; symlink skip; and interrupted-run recovery before Artifact reconciliation.

```python
def test_download_is_thread_scoped_and_non_executable(client, seeded_artifact, monkeypatch):
    monkeypatch.setattr(services.threads, "list_documents", forbidden_scan)
    response = client.get(
        f"/api/agent/threads/{seeded_artifact.thread_id}/artifacts/{seeded_artifact.id}/download"
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert seeded_artifact.title not in response.headers["content-disposition"]
```

- [ ] **Step 2: Run focused tests and verify they fail**

Run: `cd backend && .venv/bin/pytest tests/agent/test_artifacts.py tests/agent/test_artifact_api.py tests/agent/test_thread_api.py tests/agent/test_stores.py -q -k 'artifact or tombstone or cascade'`

Expected: FAIL because REST/delete/reconciliation are absent.

- [ ] **Step 3: Add exact thread-scoped Artifact routes**

Implement:

```text
GET    /api/agent/threads/{thread_id}/artifacts
GET    /api/agent/threads/{thread_id}/artifacts/{artifact_id}
GET    /api/agent/threads/{thread_id}/artifacts/{artifact_id}/download
DELETE /api/agent/threads/{thread_id}/artifacts/{artifact_id}
```

Read the path thread and verify membership before constructing the Artifact path. List returns lightweight metadata plus warnings; detail returns typed content. Download uses only `<artifact-id>.md|.json`, fixed MIME, attachment, `nosniff`, and restrictive CSP. DELETE accepts `{"thread_revision": n}`, rejects active/awaiting threads and non-leaves, removes the reference first, then deletes/fsyncs; cleanup failure returns `500 ARTIFACT_DELETE_FAILED` with a non-secret recovery warning and never restores a stale revision.

- [ ] **Step 4: Make thread deletion a tombstone transaction**

Under the coordinator thread lock, verify revision/not busy, then rename the thread's Artifact directory and run files to operation-specific controlled tombstones. Only after all renames succeed delete the thread file as the commit point. Roll back completed renames on pre-commit failure; after commit, cleanup failures become persistence/recovery warnings and cannot re-expose the deleted thread.

- [ ] **Step 5: Extend startup reconciliation after interrupted-run recovery**

Recursively scan only controlled Artifact roots without following symlinks. Delete only strict `<artifact-id>.<nonce>.artifact.tmp` staging files whose incomplete provenance is provable. Keep ordinary orphan JSON for diagnosis, quarantine identity/chain corruption, retain missing references with warnings, and recover/clean delete tombstones based on whether the thread commit point exists. Explicitly restart the service around a healthy historical Artifact fixture and assert the Artifact bytes, thread reference, and accessibility remain unchanged. Extend `RecoveryWarning.document_type` with `artifact` and do not rewrite healthy legacy JSON.

- [ ] **Step 6: Run focused and full backend tests**

Run: `cd backend && .venv/bin/pytest tests/agent/test_artifacts.py tests/agent/test_artifact_api.py tests/agent/test_thread_api.py tests/agent/test_stores.py -q && .venv/bin/pytest -m "not live"`

Expected: PASS; startup order is interrupted-run reconciliation, then Artifact/tombstone reconciliation.

- [ ] **Step 7: Commit**

```bash
git add backend/agent/artifacts.py backend/agent/stores.py backend/agent/runs.py backend/agent/router.py backend/tests/agent/test_artifacts.py backend/tests/agent/test_artifact_api.py backend/tests/agent/test_thread_api.py backend/tests/agent/test_stores.py
git commit -m "feat(agent): expose and recover artifacts"
```

### Task 13: Complete Artifact And Source Event Consistency

**Files:**
- Modify: `backend/agent/protocol.py`
- Modify: `backend/agent/runs.py`
- Modify: `backend/agent/router.py`
- Modify: `backend/tests/agent/test_protocol_bridge.py`
- Modify: `backend/tests/agent/test_run_persistence.py`
- Modify: `backend/tests/agent/test_agent_1d_integration.py`

- [ ] **Step 1: Write failing custom-event and commit-order tests**

Test valid/invalid `artifact.created` and `sources.updated`, unknown fail-closed behavior, source persistence before event, Artifact JSON/run Sources/thread reference before event, Source event without Artifact event after later Artifact failure, no event on precommit failure, stale revision behavior, and final thread -> budget -> sources -> terminal ordering.

```python
def test_artifact_failure_after_source_commit_emits_only_sources_update(stream):
    events = collect(stream.create_artifact(fail_at="thread_reference"))
    assert persisted_run().sources
    assert names(events).count("sources.updated") == 1
    assert "artifact.created" not in names(events)
```

- [ ] **Step 2: Run focused tests and verify they fail**

Run: `cd backend && .venv/bin/pytest tests/agent/test_protocol_bridge.py tests/agent/test_run_persistence.py tests/agent/test_agent_1d_integration.py -q -k 'artifact_created or sources_updated or source_event or terminal_order'`

Expected: FAIL because only `budget.updated` is currently allowlisted.

- [ ] **Step 3: Register the remaining strict event models**

Extend `CUSTOM_EVENT_MODELS` with exact camelCase aliases and `extra="forbid"` models for `artifact.created` and `sources.updated`. Event payloads contain metadata/counts only, never Source summaries or Artifact content. Emit them via the same LangGraph custom-event mechanism used by governance so `AgentProtocolBridge.convert` validates every Graph-originated event.

- [ ] **Step 4: Tie event emission to durable commit facts**

Return committed event facts from coordinator/journal operations instead of emitting inside stores. Router encodes `sources.updated` only after the run replacement succeeded and `artifact.created` only after Source/run, final Artifact, and thread reference all succeeded. Terminal paths emit latest revisions in the approved sequence; encoding failure does not change stores.

- [ ] **Step 5: Run the slice-2 gate**

Run:

```bash
cd backend && .venv/bin/pytest -m "not live"
cd ../frontend && npm test && npm run test:unit && npm run build
cd .. && git diff --check
```

Expected: all PASS; four Artifact types can be created only by the governed tool, safely downloaded, and recovered after restart.

- [ ] **Step 6: Commit**

```bash
git add backend/agent/protocol.py backend/agent/runs.py backend/agent/router.py backend/tests/agent/test_protocol_bridge.py backend/tests/agent/test_run_persistence.py backend/tests/agent/test_agent_1d_integration.py
git commit -m "feat(agent): publish artifact source events"
```

## Slice 3: Final Workspace, Inspector, And Browser Verification

### Task 14: Add Frontend Contracts, REST Clients, Store, And Event Convergence

**Files:**
- Modify: `frontend/src/lib/agent/types.ts`
- Modify: `frontend/src/lib/agent/api.ts`
- Modify: `frontend/src/lib/agent/runtime.tsx`
- Modify: `frontend/src/lib/agent/runtime.test.tsx`
- Create: `frontend/src/lib/agent/workspace.ts`
- Create: `frontend/src/lib/agent/workspace.test.ts`

- [ ] **Step 1: Write failing type/API/store/stream tests**

Cover exact snake_case REST and camelCase event shapes, run pagination cursors, Policy CAS/corrupt errors, Artifact blob handling, selected run per thread with no localStorage writes, fallback to new `last_run`, per-kind/per-run highest revisions, stale event rejection, REST overwrite advancing watermarks, revision-gap invalidation, and tee scanner preserving all bytes while observing thread/budget/artifact/source events.

```ts
it("drops stale events independently per run and event kind", () => {
  const store = createAgentWorkspaceStore();
  expect(store.getState().acceptBudget("th-1", "run-1", 4)).toBe(true);
  expect(store.getState().acceptBudget("th-1", "run-1", 3)).toBe(false);
  expect(store.getState().acceptSources("th-1", "run-1", 3)).toBe(true);
  expect(store.getState().selectedRuns).toEqual({});
  expect(localStorage.length).toBe(0);
});
```

- [ ] **Step 2: Run Vitest and verify it fails**

Run: `cd frontend && npx vitest run src/lib/agent/workspace.test.ts src/lib/agent/runtime.test.tsx`

Expected: FAIL because 1D contracts/store/event scanning do not exist.

- [ ] **Step 3: Add exact types and API methods**

Define `AgentPolicy`, `AgentRunDetail`, `AgentRunListItem/Response`, `ContextTruncation`, Source unions, Artifact metadata/detail/content unions, and structured management errors. Add `getPolicy/patchPolicy/resetPolicy`, `listRuns(threadId,limit,before)`, `getRun`, `listArtifacts/getArtifact/downloadArtifact/deleteArtifact`. Reuse `agentRequest`/`authHeaders`; validate blob responses and derive download filename from `Content-Disposition`, never Artifact title.

- [ ] **Step 4: Implement a non-persistent vanilla Zustand store**

Use `createStore`, not persistence middleware. Key selected historical runs and watermarks by thread/run; store only UI selection, invalidation flags, and drawer/tab state. REST detail replaces cached view and raises matching watermarks. Missing selected IDs fall back to `thread.last_run.id`. `openDrawer("threads"|"inspector"|"settings")` closes the other two.

- [ ] **Step 5: Extend `scanStream` without consuming the runtime stream**

Parse all four project events from the tee. Validate primitive identity/revision fields before notifying callbacks; malformed scan data only triggers REST invalidation and never throws into assistant-ui. A control-revision gap, stream end, or reconnect marks the relevant run stale so the page reloads authoritative REST. Keep 409 and pre-stream 503 behavior unchanged.

- [ ] **Step 6: Run focused and full frontend tests**

Run: `cd frontend && npx vitest run src/lib/agent/workspace.test.ts src/lib/agent/runtime.test.tsx && npm test && npm run test:unit && npm run build`

Expected: PASS; TypeScript strict/noUnused gates remain clean and no new localStorage key exists.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/lib/agent/types.ts frontend/src/lib/agent/api.ts frontend/src/lib/agent/runtime.tsx frontend/src/lib/agent/runtime.test.tsx frontend/src/lib/agent/workspace.ts frontend/src/lib/agent/workspace.test.ts
git commit -m "feat(agent): converge workspace event state"
```

### Task 15: Build The Three-Column Workspace And Compact Chat

**Files:**
- Create: `frontend/src/components/agent/AgentWorkspace.tsx`
- Create: `frontend/src/components/agent/AgentWorkspace.test.tsx`
- Modify: `frontend/src/components/agent/AgentThreadList.tsx`
- Create: `frontend/src/components/agent/AgentThreadList.test.tsx`
- Modify: `frontend/src/components/agent/AgentThread.tsx`
- Modify: `frontend/src/components/agent/AgentThread.test.tsx`
- Modify: `frontend/src/components/layout/Layout.tsx`
- Create: `frontend/src/components/layout/Layout.test.tsx`
- Modify: `frontend/src/pages/Agent.tsx`
- Modify: `frontend/src/pages/Agent.test.tsx`
- Modify: `frontend/src/index.css`

- [ ] **Step 1: Write failing workspace/thread/chat interaction tests**

Cover fixed desktop columns, independent scroll regions, no outer body/main scroll chain, local title search, status/warning rows, stable selected highlight, title tooltip, icon create/rename/delete with accessible names, revision conflict reload, compact header labels, settings/inspector commands, model gating without runtime mount, running Stop-only Composer, awaiting steer Composer, stable error area, and successful `create_artifact` result opening Inspector. At 1280px, assert the forced navigation rail does not write a new value to the persisted `vr-sidebar` preference and that the saved preference is restored after leaving `/agent` or widening the viewport.

```tsx
it("opens the artifact tab from a successful tool result", async () => {
  renderWorkspace({ toolResult: successfulArtifactResult });
  await user.click(screen.getByRole("button", { name: "在 Inspector 打开" }));
  expect(workspaceStore.getState().inspectorTab).toBe("artifact");
  expect(workspaceStore.getState().selectedArtifactId).toBe("artifact-1");
});
```

- [ ] **Step 2: Run focused tests and verify they fail**

Run: `cd frontend && npx vitest run src/components/agent/AgentWorkspace.test.tsx src/components/agent/AgentThreadList.test.tsx src/components/agent/AgentThread.test.tsx src/components/layout/Layout.test.tsx src/pages/Agent.test.tsx`

Expected: FAIL because the current page is one max-width card with an inline model form.

- [ ] **Step 3: Add the full-height workspace shell**

On `/agent`, make `Layout` render the outlet in a `min-w-0 flex-1 overflow-hidden` main area without the normal `max-w-6xl px-6 py-6` wrapper, and remove `Agent.tsx`'s own `mx-auto max-w-6xl space-y-4 p-4` wrapper so there is no second width/padding constraint. `AgentWorkspace` uses `h-full min-h-0` and desktop grid tracks `240px minmax(480px,1fr) 320px`; columns are unframed full-height regions separated by borders and each owns its scroll. At the exact 1280px desktop threshold, derive an effective forced-collapsed state for the existing global navigation's 56px rail so the 1040px minimum workspace plus margins cannot overflow; do not feed that effective state into the `vr-sidebar` persistence effect. Preserve the user's saved expanded/collapsed preference and restore it outside that constrained Agent viewport. At 1440px, allow the saved navigation state when it fits. Keep the existing global navigation and color tokens; do not nest glass cards, use viewport-scaled font sizes, or introduce non-zero letter spacing in the new workspace.

- [ ] **Step 4: Convert `AgentThreadList` into the left column**

Replace the `<select>` with a filtered row list. Create uses a `MessageSquarePlus` icon button with title/ARIA; each row shows title, update time, last-run status, and warning marker. Keep revision-safe rename/delete and confirmation; on 409 discard local action state, reload the authoritative list/thread, and preserve selection if still present.

- [ ] **Step 5: Compact the chat surface and model gate**

Move the model form out of `Agent.tsx`. The header shows thread title, model label, capability summary, and icon commands for threads/inspector/settings at responsive widths. Preserve the existing assistant-ui runtime and approval bridge. Running displays only the stable Stop command; awaiting approval uses `SteerAwayComposer`; terminal status/error occupies a fixed minimum control-row height. Parse only the fixed successful Artifact result shape for an Inspector action; other tools retain the safe fallback.

- [ ] **Step 6: Run focused and full frontend tests**

Run: `cd frontend && npx vitest run src/components/agent/AgentWorkspace.test.tsx src/components/agent/AgentThreadList.test.tsx src/components/agent/AgentThread.test.tsx src/components/layout/Layout.test.tsx src/pages/Agent.test.tsx && npm test && npm run test:unit && npm run build`

Expected: PASS; no non-Agent page layout changes visually or behaviorally.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/agent/AgentWorkspace.tsx frontend/src/components/agent/AgentWorkspace.test.tsx frontend/src/components/agent/AgentThreadList.tsx frontend/src/components/agent/AgentThreadList.test.tsx frontend/src/components/agent/AgentThread.tsx frontend/src/components/agent/AgentThread.test.tsx frontend/src/components/layout/Layout.tsx frontend/src/components/layout/Layout.test.tsx frontend/src/pages/Agent.tsx frontend/src/pages/Agent.test.tsx frontend/src/index.css
git commit -m "feat(agent): build final workspace shell"
```

### Task 16: Build Historical Run And Approval Inspector Tabs

**Files:**
- Create: `frontend/src/components/agent/AgentInspector.tsx`
- Create: `frontend/src/components/agent/AgentInspector.test.tsx`
- Create: `frontend/src/components/agent/RunInspector.tsx`
- Create: `frontend/src/components/agent/RunInspector.test.tsx`
- Modify: `frontend/src/components/agent/ApprovalPanel.tsx`
- Modify: `frontend/src/components/agent/ApprovalPanel.test.tsx`
- Modify: `frontend/src/components/agent/AgentWorkspace.tsx`
- Modify: `frontend/src/pages/Agent.tsx`

- [ ] **Step 1: Write failing Inspector selection/tab/content tests**

Cover four tabs and badges, default `last_run`, thread-scoped historical selection, pagination append, missing/isolated selected run fallback, REST refresh after invalidation, stable empty Approval panel, pending-tab highlight, all-interrupt decision behavior, Run status/timing/model/tool limit/token status/context truncation/error, no costs, and no private omitted text.

```tsx
it("falls back to the new last run when a historical selection disappears", async () => {
  const store = seededWorkspaceStore({ selectedRuns: { "th-1": "run-old" } });
  api.listRuns.mockResolvedValue({ runs: [runSummary("run-new")], next_before: null, warnings: [] });
  renderInspector({ thread: threadWithLastRun("run-new"), store });
  await waitFor(() => expect(store.getState().selectedRuns["th-1"]).toBe("run-new"));
});
```

- [ ] **Step 2: Run focused tests and verify they fail**

Run: `cd frontend && npx vitest run src/components/agent/AgentInspector.test.tsx src/components/agent/RunInspector.test.tsx src/components/agent/ApprovalPanel.test.tsx`

Expected: FAIL because Inspector components do not exist and ApprovalPanel returns no stable empty surface.

- [ ] **Step 3: Implement accessible tab and historical-run selection**

Use `role=tablist/tab/tabpanel`, `aria-selected`, roving tab index, and Left/Right arrow navigation. Load the first 50 run summaries on thread selection and next pages on command; do not fetch infinite history. Selection stays in the workspace store only. Fetch selected run detail independently from live runtime state and converge on event invalidation/stream end.

- [ ] **Step 4: Implement factual Run telemetry**

Show active, approval, and wall time separately; model/tool reservations against snapshot limits; Provider-reported input/output/total tokens with `available/partial/unavailable`; latest context characters and removed turns; status and terminal code/message. Render a clear legacy state when `budget_snapshot={}`. Never estimate money or imply performance/quality.

- [ ] **Step 5: Stabilize Approval tab**

Reuse `ApprovalPanel` and its existing once/thread-session/reject contract. Add an explicit bounded empty state instead of returning `null`; current actionable interrupts only belong to the active awaiting run, so a historical run shows a non-actionable state. Preserve the approval hook import boundary in `approval.contract.test.ts`.

- [ ] **Step 6: Run focused and full frontend tests**

Run: `cd frontend && npx vitest run src/components/agent/AgentInspector.test.tsx src/components/agent/RunInspector.test.tsx src/components/agent/ApprovalPanel.test.tsx src/lib/agent/approval.contract.test.ts && npm test && npm run test:unit && npm run build`

Expected: PASS; event revisions cannot overwrite newer REST detail.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/agent/AgentInspector.tsx frontend/src/components/agent/AgentInspector.test.tsx frontend/src/components/agent/RunInspector.tsx frontend/src/components/agent/RunInspector.test.tsx frontend/src/components/agent/ApprovalPanel.tsx frontend/src/components/agent/ApprovalPanel.test.tsx frontend/src/components/agent/AgentWorkspace.tsx frontend/src/pages/Agent.tsx
git commit -m "feat(agent): inspect run and approval state"
```

### Task 17: Build Safe Artifact And Source Inspector Tabs

**Files:**
- Create: `frontend/src/components/agent/ArtifactViewer.tsx`
- Create: `frontend/src/components/agent/ArtifactViewer.test.tsx`
- Create: `frontend/src/components/agent/SourceInspector.tsx`
- Create: `frontend/src/components/agent/SourceInspector.test.tsx`
- Modify: `frontend/src/components/agent/AgentInspector.tsx`
- Modify: `frontend/src/components/agent/AgentInspector.test.tsx`
- Modify: `frontend/src/pages/Agent.tsx`
- Modify: `frontend/src/index.css`

- [ ] **Step 1: Write failing viewer, network-safety, chain, and action tests**

Cover run-primary Artifact filtering plus thread chain context; four content types; Markdown raw HTML escaped/omitted; remote images/embed/iframe/script causing zero resource requests; HTTP/HTTPS links only with `_blank`/noopener/noreferrer; table first 200 rows and total count; JSON pure-data tree; source notes as text; fixed backend download; leaf-only delete/revision reload; version navigation; missing reference warnings; execution/unverified labels; no source score/rank/recommendation wording.

```tsx
it("never mounts remote markdown images or embeds", () => {
  const request = vi.spyOn(globalThis, "fetch");
  render(<ArtifactViewer artifact={markdownArtifact(
    '<script>alert(1)</script>\n![](https://tracker.invalid/pixel)\n<iframe src="https://tracker.invalid">'
  )} />);
  expect(document.querySelector("script,iframe,img")).toBeNull();
  expect(request).not.toHaveBeenCalledWith(expect.stringContaining("tracker.invalid"), expect.anything());
});
```

- [ ] **Step 2: Run focused tests and verify they fail**

Run: `cd frontend && npx vitest run src/components/agent/ArtifactViewer.test.tsx src/components/agent/SourceInspector.test.tsx src/components/agent/AgentInspector.test.tsx`

Expected: FAIL because Artifact/Source tabs do not exist.

- [ ] **Step 3: Implement non-executable Artifact viewers**

Use `react-markdown` without `rehype-raw`; override `img` to render a text link/placeholder and reject non-HTTP(S) link schemes. Do not render HTML nodes, iframe, embed, object, SVG, CSS, formulas, or components from content. Table cells render only scalar/null; cap preview at 200. JSON recursively renders escaped keys/scalars as data with bounded depth already guaranteed by backend. Sources Artifact notes are text nodes.

- [ ] **Step 4: Implement Artifact chains and mutations**

Fetch the thread list once, group by parent, and show the selected run's items first without hiding ancestors/children. Download uses the backend blob and response filename. Delete appears only for leaves, sends current thread revision, then reloads thread/list/detail; 409 discards stale state and reloads, 500 displays the recovery warning without pretending the file remains referenced.

- [ ] **Step 5: Implement factual Source groups**

Group `tool_execution` and `model_url` in stored order. Label them exactly as execution record and model-provided/unverified; show bounded summaries/URL/optional label with external-link safety. Do not add stars, ranks, scores, source quality colors, recommendation language, URL preview, or automatic fetch.

- [ ] **Step 6: Run focused and full frontend tests**

Run: `cd frontend && npx vitest run src/components/agent/ArtifactViewer.test.tsx src/components/agent/SourceInspector.test.tsx src/components/agent/AgentInspector.test.tsx && npm test && npm run test:unit && npm run build`

Expected: PASS; rendering model-controlled Markdown performs no remote request.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/agent/ArtifactViewer.tsx frontend/src/components/agent/ArtifactViewer.test.tsx frontend/src/components/agent/SourceInspector.tsx frontend/src/components/agent/SourceInspector.test.tsx frontend/src/components/agent/AgentInspector.tsx frontend/src/components/agent/AgentInspector.test.tsx frontend/src/pages/Agent.tsx frontend/src/index.css
git commit -m "feat(agent): inspect artifacts and sources"
```

### Task 18: Build Settings And Responsive Accessible Drawers

**Files:**
- Create: `frontend/src/components/agent/WorkspaceDrawer.tsx`
- Create: `frontend/src/components/agent/WorkspaceDrawer.test.tsx`
- Create: `frontend/src/components/agent/AgentSettingsDrawer.tsx`
- Create: `frontend/src/components/agent/AgentSettingsDrawer.test.tsx`
- Modify: `frontend/src/components/agent/CapabilityBar.tsx`
- Modify: `frontend/src/components/agent/CapabilityManagerDialog.tsx`
- Modify: `frontend/src/components/agent/CapabilityManagerDialog.test.tsx`
- Modify: `frontend/src/components/agent/SkillManager.tsx`
- Modify: `frontend/src/components/agent/McpManager.tsx`
- Modify: `frontend/src/components/agent/AgentWorkspace.tsx`
- Modify: `frontend/src/components/agent/AgentWorkspace.test.tsx`
- Modify: `frontend/src/pages/Agent.tsx`
- Modify: `frontend/src/pages/Agent.test.tsx`

- [ ] **Step 1: Write failing Settings, busy-gate, CAS, and drawer tests**

Cover four accessible tabs; Model local-only save; Model identity disabled when any known thread is running/awaiting; Policy editable while runs are active; Policy defaults/ranges/revision, 409 reload, corrupt reason and separately confirmed reset; non-blocking `max_context_chars <= 60000` warning without value mutation; Skill draft one PATCH/current revision and conflict discard/reload; MCP REST reload after test/refresh/mutation; thread/inspector/settings mobile drawer exclusivity; desktop persistent columns; dialog name/close/ARIA; focus enter/trap/return; Escape/backdrop; unsaved Policy close confirmation; and no raw localStorage access outside wrappers.

```tsx
it("warns about a small context budget without changing the saved value", async () => {
  renderSettings({ policy: policy({ max_context_chars: 60_000 }) });
  expect(screen.getByText(/Skill 指令可能占满上下文/)).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "保存 Policy" }));
  expect(api.patchPolicy).toHaveBeenCalledWith(expect.objectContaining({ max_context_chars: 60_000 }));
});
```

- [ ] **Step 2: Run focused tests and verify they fail**

Run: `cd frontend && npx vitest run src/components/agent/WorkspaceDrawer.test.tsx src/components/agent/AgentSettingsDrawer.test.tsx src/components/agent/AgentWorkspace.test.tsx src/components/agent/CapabilityManagerDialog.test.tsx src/pages/Agent.test.tsx`

Expected: FAIL because settings are embedded in the page/old capability dialog and mobile drawer behavior is absent.

- [ ] **Step 3: Implement the shared accessible drawer**

Use a portal with `role="dialog"`, `aria-modal="true"`, explicit `aria-labelledby`, a Lucide `X` close button, backdrop, Escape, first-focus placement, Tab/Shift+Tab wrap, and trigger focus return. Settings close asks for confirmation only when its Policy draft differs from the loaded revision. At widths below Tailwind `xl`, ordinary drawers use `min(88vw,360px)` and Settings uses full phone width; opening one closes the others.

- [ ] **Step 4: Migrate Model/Skills/MCP into Settings tabs**

Move the existing model form unchanged in storage/security semantics; all access remains through `model-config.ts`/`storage.ts`. Reuse `SkillManager` and `McpManager`. Move thread capability selection from `CapabilityBar`/`CapabilityManagerDialog` into the Skills tab with a local set draft and one PATCH. On 409 discard the draft and reload the authoritative thread. Keep read-only manager detail available when appropriate; apply existing busy rules to thread selection and Model identity.

- [ ] **Step 5: Implement Policy editing and corrupt reset**

Render numeric controls with server ranges/defaults and current revision. PATCH only changed fields plus revision. Conflict replaces the draft from GET and reports the conflict. `POLICY_CORRUPT` disables ordinary save, shows the non-secret reason, and requires a separate confirmation before `resetPolicy({confirm_corrupt:true})`. Policy editing remains enabled during active runs because snapshots are immutable.

- [ ] **Step 6: Add mobile workspace behavior and tab keyboard support**

Below `xl`, render only Chat in the workspace track; thread and Inspector mount in left/right drawers, Settings in its own full-height drawer. Preserve each panel's scroll container while toggling. All Inspector/Settings tabs use tablist semantics and arrow navigation; focus/selection/pending badges have non-color indicators. At desktop keep all three workspace columns and use Settings as a right overlay drawer.

- [ ] **Step 7: Run focused and full frontend tests**

Run: `cd frontend && npx vitest run src/components/agent/WorkspaceDrawer.test.tsx src/components/agent/AgentSettingsDrawer.test.tsx src/components/agent/AgentWorkspace.test.tsx src/components/agent/CapabilityManagerDialog.test.tsx src/pages/Agent.test.tsx && npm test && npm run test:unit && npm run build`

Expected: PASS; the model key remains only in `vr-agent-model` and request headers.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/agent/WorkspaceDrawer.tsx frontend/src/components/agent/WorkspaceDrawer.test.tsx frontend/src/components/agent/AgentSettingsDrawer.tsx frontend/src/components/agent/AgentSettingsDrawer.test.tsx frontend/src/components/agent/CapabilityBar.tsx frontend/src/components/agent/CapabilityManagerDialog.tsx frontend/src/components/agent/CapabilityManagerDialog.test.tsx frontend/src/components/agent/SkillManager.tsx frontend/src/components/agent/McpManager.tsx frontend/src/components/agent/AgentWorkspace.tsx frontend/src/components/agent/AgentWorkspace.test.tsx frontend/src/pages/Agent.tsx frontend/src/pages/Agent.test.tsx
git commit -m "feat(agent): add responsive workspace settings"
```

### Task 19: Add The Isolated Playwright Harness And Full Interaction Suite

**Files:**
- Create: `backend/tests/agent_e2e_app.py`
- Create: `frontend/playwright.config.ts`
- Create: `frontend/e2e/agent-workspace.spec.ts`
- Modify: `frontend/package.json`
- Modify: `frontend/package-lock.json`
- Modify: `README.md`
- Modify: `README_en.md`

- [ ] **Step 1: Write the E2E app contract and first failing browser test**

The test app imports and mounts the production Agent router/lifespan, then overrides only model builder, MCP adapter, and local tools with deterministic fixtures. It requires a non-default temporary `VR_DATA_DIR` and refuses startup if the path resolves to the normal user root. Seed threads/runs/Artifacts through production stores, never direct response mocks.

Start with a Playwright test that loads `/agent`, asserts the three desktop columns, sends a fake streaming prompt, observes a tool call and text, and opens the resulting Artifact from the tool result. Run it before implementation wiring and confirm failure.

- [ ] **Step 2: Add exact Playwright dependency and scripts**

Install a fixed `@playwright/test` version as a dev dependency and commit the lockfile. Add:

```json
{
  "scripts": {
    "test:e2e": "playwright test",
    "test:e2e:install": "playwright install chromium"
  }
}
```

`playwright.config.ts` owns fixed isolated backend/frontend ports, `baseURL`, both `webServer` commands, `reuseExistingServer:false`, trace/screenshot-on-failure, and teardown. The backend command creates/uses a temporary Agent root and local fake services; it never points at port 8900's normal app. Reuse the existing `router_module.services` replacement seam demonstrated by backend router tests, so production `router.py` does not need a test-only branch. Document `PLAYWRIGHT_DOWNLOAD_HOST` without embedding a mirror URL that may become stale; on this development host a reachable mirror is required for the first Chromium install.

- [ ] **Step 3: Implement the complete interaction matrix**

Test streaming text/tool calls, approve once, approve thread-session allowance, reject, steer-away, Stop, retry, structured 409 reload, Policy CAS/corrupt reset, Artifact create/open/version/download/delete, run pagination, and REST convergence after reload. Assert `mcp.health_changed` is never required: MCP settings reload after explicit test/refresh/mutation.

- [ ] **Step 4: Add responsive/light/dark and safety assertions**

Capture deterministic screenshots at 1440x900, 1280x800, and 390x844 in light and dark modes. At every viewport assert no document horizontal overflow, no overlapping toolbar/composer/columns, no clipped longest label, stable panel scroll, mobile drawer exclusivity/focus/Escape, and Composer visibility. Intercept all network requests and fail the test if Markdown images/embeds, real model hosts, Eastmoney, or non-fixture MCP endpoints are requested.

- [ ] **Step 5: Run the browser gate**

First-time setup:

```bash
cd frontend && npm run test:e2e:install
```

Gate:

```bash
cd frontend && npm run test:e2e
```

Expected: PASS on all three viewports and both themes; no console error, overflow, overlap, text clipping, external request, or residual server process.

- [ ] **Step 6: Document the user and test workflows**

Update both READMEs with concise Agent Workspace setup/use, local model-key behavior, enforced Policy snapshots, Artifact/Source safety labels, explicit Chromium setup, and E2E commands. Do not market the workspace as giving investment conclusions. Confirm the parent design already records the explicit `mcp.health_changed` deferral; change it only if that sentence is absent or stale.

- [ ] **Step 7: Run all frontend and backend gates**

Run:

```bash
cd backend && .venv/bin/pytest -m "not live"
cd ../frontend && npm test && npm run test:unit && npm run build && npm run test:e2e
cd .. && git diff --check
```

Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add backend/tests/agent_e2e_app.py frontend/playwright.config.ts frontend/e2e/agent-workspace.spec.ts frontend/package.json frontend/package-lock.json README.md README_en.md
git commit -m "test(agent): cover workspace in playwright"
```

## Final Verification

### Task 20: Verify 1D End To End And Record Evidence

**Files:**
- Create: `docs/superpowers/verification/2026-08-17-langchain-agent-workspace-1d.md`
- Modify only if verification finds a 1D defect: the exact production/test files from Tasks 1-19

- [ ] **Step 1: Verify dependencies from a clean temporary environment**

Run:

```bash
VR_1D_VERIFY=$(mktemp -d)
python3 -m venv "$VR_1D_VERIFY/venv"
cd backend
PIP_INDEX_URL="${PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}" \
  "$VR_1D_VERIFY/venv/bin/pip" install -r requirements.txt
"$VR_1D_VERIFY/venv/bin/pip" check
"$VR_1D_VERIFY/venv/bin/python" -c 'from importlib.metadata import version; print(version("commonmark"))'
```

Expected: clean install and `pip check` succeed; CommonMark reports the locked version.

- [ ] **Step 2: Run the complete required gate**

Run:

```bash
cd backend && .venv/bin/pytest -m "not live"
cd ../frontend && npm test
npm run test:unit
npm run build
npm run test:e2e
cd .. && git diff --check
```

Expected: all PASS. Record exact test counts and the existing build warning, if any, without converting warnings into success claims.

- [ ] **Step 3: Inspect generated browser evidence**

Review the 1440x900, 1280x800, and 390x844 light/dark screenshots. Record that the global sidebar plus 240/minmax(480)/320 tracks fit at the critical 1280 viewport; at 390 record no horizontal overflow, hidden Composer, overlap, or clipped control. Confirm Artifact Markdown created zero remote request and every drawer focus test passed.

- [ ] **Step 4: Run focused security and invariant scans**

Run searches/tests that prove no model key fixture, MCP secret, full environment, executable Artifact MIME, source score/rank/recommendation phrase, raw localStorage access, or non-whitelisted CustomEvent entered production output. Confirm test logs and `git status --short` contain no real user-data path and no modified file under the untouched untracked directories.

- [ ] **Step 5: Write the verification document**

Record commands, timestamps, PASS/PARTIAL/NOT RUN state, test counts, all slice-closing commit SHAs, Playwright screenshot paths, secret/network scan results, executor shutdown result, maximum RunDocument/Artifact byte evidence, and 1A-1C regression status. If Chromium installation or another required external prerequisite is unavailable, mark verification `PARTIAL`; do not claim 1D complete.

- [ ] **Step 6: Commit verification only after every non-external required gate passes**

```bash
git add docs/superpowers/verification/2026-08-17-langchain-agent-workspace-1d.md
git commit -m "test(agent): verify workspace milestone 1d"
```

## 1D Exit Checklist

- [ ] Slice 1 independently passes: Policy, cross-resume hard budgets, active deadline, bounded sync execution, deterministic context, Provider usage, run pagination, and strict `budget.updated`.
- [ ] Slice 2 independently passes: four immutable Artifact types, governed `create_artifact`, Source provenance, thread-scoped REST, deletion/recovery, and commit-ordered Artifact/Source events.
- [ ] Slice 3 independently passes: three-column desktop, mobile drawers, historical Inspector, Settings, safe viewers, event convergence, and complete isolated Playwright interactions.
- [ ] The ninth default model call and seventeenth default actual tool call are blocked before Provider/handler invocation; persisted reservation counts remain authoritative after errors/timeouts/cancel.
- [ ] Executor queue length never exceeds admitted capacity; timeout/cancelled running futures retain a slot until exit, and lifespan shutdown returns within its documented bound.
- [ ] Resume retains the original Policy and counters; retry/steer obtain a new snapshot; corrupt current Policy cannot block a valid resume or replace duplicate semantics.
- [ ] Context trimming preserves complete turns, tool pairs, current user input, neutrality prompt, and latest loaded Skill instructions; forced overflow fails before reservation.
- [ ] Artifact canonical bytes, paths, parent chains, source descriptors, commit compensation, tombstones, and startup recovery all fail closed without false success events.
- [ ] Sources distinguish executed records from model-provided unverified URLs, perform no network access, and contain no score/rank/recommendation.
- [ ] Protocol bridge permits and validates only the three 1D Graph CustomEvents; terminal ordering is committed revision, budget, sources, then standard terminal event.
- [ ] Desktop 1440/1280 and mobile 390 light/dark screenshots have no overlap, horizontal overflow, clipped text, hidden Composer, or broken focus behavior.
- [ ] Legacy `/api/chat`, debate, reflection, CLI, Eastmoney serial throttling, revision, duplicate, resume, retry, cancel, approval, Skill, MCP, and secret-isolation contracts remain green.
