# LangChain Agent Workspace 1B Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add authoritative server-side thread and run history to the 1A Agent loop, including revision-safe CRUD, duplicate protection, partial-message recovery, cancellation, retry, corruption quarantine, and startup reconciliation.

**Architecture:** Keep the locked AG-UI/LangChain bridge and request-scoped Graph boundary from 1A. Add Pydantic persistence documents plus one atomic JSON store, then make `RunCoordinator` the only writer for `/run` lifecycle transitions; the frontend hydrates and switches threads through REST-backed `useAgUiRuntime` history/thread-list adapters and never keeps a second durable browser history.

**Tech Stack:** Python 3, FastAPI, Pydantic 2, LangChain 1.3.15, LangGraph 1.2.11, AG-UI, React 19, TypeScript, assistant-ui 0.15.14, Vitest, React Testing Library, pytest

---

## Scope And Invariants

This plan implements milestone 1B only. It does not add Skills, MCP clients or approvals, Artifacts, policy/budget settings, the final Inspector, or the final responsive three-column workspace. The small thread selector added here is only the control surface required to verify create/switch/rename/delete and refresh recovery.

Preserve these 1A contracts:

- every protocol request creates a fresh `LangGraphAgent`;
- an approval resume uses `messages=[]`, the same `MemorySaver`, and a rebuilt equivalent Graph;
- model secrets exist only in the request and request-scoped Graph;
- built-in tools remain serial within a run;
- existing `/api/chat`, debate, reflection, and page-level AI entry points are unchanged.

Add these 1B invariants:

- `ThreadDocument` is the only authoritative message history;
- every mutating thread operation compares an expected revision under the same per-thread lock as its write;
- duplicate protocol-run/message detection runs before revision checking;
- accepted user messages, completed tool state, assistant terminal/partial state, and run terminal state are persisted at semantic boundaries, never per token;
- revision events are emitted only after the corresponding thread file is atomically replaced, and the terminal revision event precedes `RUN_FINISHED`;
- partial or pending-interrupt assistant turns are visible after reload but excluded from future model input;
- retry creates a new product run and protocol run, does not append the triggering user message again, and never restores the old `MemorySaver`;
- only one FastAPI process/worker may point at an Agent data directory during milestone 1B.

## File Map

- `backend/agent/models.py`: persisted thread/run documents, summaries, message completeness, and 1B runtime properties.
- `backend/agent/stores.py`: Agent data paths, durable atomic JSON writes, corruption quarantine, thread/run CRUD, revision compare-and-swap, ID lookup, and startup reconciliation.
- `backend/agent/runs.py`: product-run handles, per-thread locking, lifecycle persistence, message/event accumulation, cancellation, and retry preparation.
- `backend/agent/runtime.py`: build Graph inputs only from sanitized server history and reset the checkpointer for retry/new turns.
- `backend/agent/protocol.py`: expose converted standard events to the run journal and create persisted revision events without absorbing storage rules.
- `backend/agent/router.py`: thread/run REST endpoints plus strict start/resume/steer-away/retry admission and stream ordering.
- `backend/app.py`: invoke Agent startup reconciliation through FastAPI lifespan/startup wiring; no other legacy behavior changes.
- `backend/tests/agent/test_models.py`: persisted schema and secret-exclusion contracts.
- `backend/tests/agent/test_stores.py`: atomicity, corruption, revision, duplicate, and reconciliation tests.
- `backend/tests/agent/test_thread_api.py`: CRUD, busy delete, revision conflict, and run lookup API tests.
- `backend/tests/agent/test_run_persistence.py`: event-boundary persistence, partial history, disconnect/cancel, duplicate, and retry contracts.
- `frontend/src/lib/agent/types.ts`: REST documents and conflict codes.
- `frontend/src/lib/agent/api.ts`: Agent-only REST client, including `PATCH` and structured conflicts.
- `frontend/src/lib/agent/history.ts`: server-backed history/thread-list adapters and monotonic revision state.
- `frontend/src/lib/agent/runtime.tsx`: inject revision/retry metadata, observe revision events, and trigger authoritative reloads on 409.
- `frontend/src/components/agent/AgentThreadList.tsx`: minimal create/switch/rename/delete controls.
- `frontend/src/components/agent/AgentThread.tsx`: retry control and restored partial/interrupted status.
- `frontend/src/pages/Agent.tsx`: assemble the 1B thread session around the existing model form.
- `frontend/src/lib/agent/history.test.tsx`: adapter, revision monotonicity, and conflict reload tests.
- `frontend/src/pages/Agent.test.tsx`: refresh/switch/CRUD/retry interaction tests.

Every backend task commit must run `cd backend && .venv/bin/pytest -m "not live"`; focused commands establish the red/green cycle but do not replace that commit gate. Every frontend task commit must run `npm test`, `npx vitest run`, and `npm run build`. This keeps the 1A vertical slice usable at every intermediate commit instead of deferring fixture migrations to a later task.

### Task 1: Define Durable Thread And Run Documents

**Files:**
- Modify: `backend/agent/models.py`
- Modify: `backend/tests/agent/test_models.py`
- Modify: `backend/tests/agent/test_router.py`
- Modify: `backend/tests/agent/test_agent_vertical_slice.py`

- [ ] **Step 1: Write failing persistence-model tests**

Append tests that construct a thread with one complete user message, one partial assistant message, and a terminal run. Assert:

```python
def test_thread_document_tracks_revision_and_message_completeness():
    thread = ThreadDocument.new("thread-1", "新会话", now="2026-08-15T12:00:00Z")
    thread = thread.model_copy(update={
        "revision": 2,
        "messages": [
            AgentMessage(id="user-1", role="user", content="分析现金流", partial=False),
            AgentMessage(id="assistant-1", role="assistant", content="尚未完成", partial=True),
        ],
    })
    assert thread.schema_version == 1
    assert thread.revision == 2
    assert [item.id for item in thread.model_history()] == ["user-1"]


def test_run_document_never_serializes_model_key():
    run = RunDocument.start(
        run_id="run-1",
        thread_id="thread-1",
        protocol_run_id="protocol-1",
        model_ref=ModelRef(provider="openai", baseURL="https://api.openai.com/v1", model="gpt-5-mini"),
        trigger_message_id="user-1",
        history_head_id="user-1",
        now="2026-08-15T12:00:00Z",
    )
    encoded = run.model_dump_json(by_alias=True)
    assert run.status == "running"
    assert run.protocol_run_ids == ["protocol-1"]
    assert "api_key" not in encoded.lower()
    assert "secret" not in encoded.lower()
```

Replace the existing `test_runtime_props_require_model_and_reject_retry_in_1a` test rather than appending a contradictory assertion. Test that `RuntimeForwardedProps` accepts an omitted `threadRevision` only for the temporary 1A frontend compatibility path, rejects a supplied revision below zero, accepts `retryOf`, rejects an unknown runtime key, and rejects a request containing a model key anywhere below `runtime`. Add one fixture matching the actual 1A `AgentHttpAgent.requestInit` runtime payload exactly (`runtime.model`, with `command` remaining top-level) so the locked client shape passes with `extra="forbid"`. In the same task, add `threadRevision: 0` to every existing router and vertical-slice runtime fixture; no 1A request fixture may be left invalid between commits. Task 7 removes the compatibility default and makes the field required in the same commit that the real frontend starts sending the authoritative revision.

- [ ] **Step 2: Run the focused test and verify it fails**

Run: `cd backend && .venv/bin/pytest tests/agent/test_models.py -q`

Expected: FAIL because `ThreadDocument`, `AgentMessage`, and `RunDocument` do not exist and the temporary revision-compatibility contract is not implemented.

- [ ] **Step 3: Add the exact persisted schemas**

Extend `backend/agent/models.py` with frozen models whose public JSON uses camel-case only where AG-UI already does:

```python
SCHEMA_VERSION = 1
RunStatus = Literal[
    "running", "awaiting_approval", "completed", "failed", "cancelled", "interrupted"
]


class AgentMessage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    id: str = Field(min_length=1)
    role: Literal["user", "assistant", "tool"]
    content: Any
    partial: bool = False
    pending_interrupt: bool = False
    interrupts: list[dict[str, Any]] = Field(default_factory=list)
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    tool_call_id: str | None = None
    created_at: str | None = None


class RunSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    id: str
    status: RunStatus
    updated_at: str
    retry_of: str | None = None


class ThreadDocument(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal[1] = SCHEMA_VERSION
    id: str
    title: str
    created_at: str
    updated_at: str
    revision: int = Field(ge=0)
    selected_skills: list[str] = Field(default_factory=list)
    messages: list[AgentMessage] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    last_run: RunSummary | None = None

    @classmethod
    def new(cls, thread_id: str, title: str, *, now: str) -> "ThreadDocument":
        return cls(
            id=thread_id,
            title=title,
            created_at=now,
            updated_at=now,
            revision=0,
        )

    def model_history(self) -> list[AgentMessage]:
        return [m for m in self.messages if not m.partial and not m.pending_interrupt]


class RunUsage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    model_calls: int = 0
    tool_calls: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None


class RunDocument(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal[1] = SCHEMA_VERSION
    id: str
    thread_id: str
    protocol_run_ids: list[str]
    trigger_message_id: str
    retry_of: str | None = None
    status: RunStatus
    started_at: str
    updated_at: str
    ended_at: str | None = None
    elapsed_ms: int = 0
    active_elapsed_ms: int = 0
    approval_wait_ms: int = 0
    budget_snapshot: dict[str, Any] = Field(default_factory=dict)
    model_ref: ModelRef
    history_head_id: str | None = None
    usage: RunUsage = Field(default_factory=RunUsage)
    tool_summaries: list[dict[str, Any]] = Field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None
```

Replace the old `RunPhase.status` annotation with `RunStatus` so reconciled `interrupted` values validate everywhere. Implement `RunDocument.start` by assigning the supplied IDs/model/timestamp, setting `status="running"`, setting `trigger_message_id` and `history_head_id`, and relying on Pydantic factories for fresh list/dict fields. Change `RuntimeForwardedProps` to:

```python
class RuntimeForwardedProps(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    model: ModelRef
    thread_revision: int | None = Field(default=None, ge=0, validation_alias="threadRevision")
    retry_of: str | None = Field(default=None, validation_alias="retryOf")
```

`command` remains a top-level AG-UI forwarded prop and must not be duplicated inside `runtime`. The optional revision is an explicitly temporary wire-compatibility measure for the already-shipped 1A frontend, not the final 1B contract; only requests without the field use it, and Task 7 removes it.

- [ ] **Step 4: Run tests and commit**

```bash
cd backend
.venv/bin/pytest tests/agent/test_models.py -q
.venv/bin/pytest -m "not live"
git add backend/agent/models.py backend/tests/agent/test_models.py backend/tests/agent/test_router.py backend/tests/agent/test_agent_vertical_slice.py
git commit -m "feat(agent): define durable thread and run documents"
```

Expected: all model tests pass.

### Task 2: Build The Atomic JSON Stores

**Files:**
- Create: `backend/agent/stores.py`
- Create: `backend/tests/agent/test_stores.py`

- [ ] **Step 1: Write failing tests for durability and corruption**

Cover these named tests with `tmp_path`; each test must perform the stated concrete assertion:

- `test_atomic_write_flushes_file_replaces_and_fsyncs_parent`: spy on `os.fsync`/`os.replace`, assert the target parses, and assert no owned temp remains.
- `test_thread_compare_and_swap_rejects_stale_revision_without_writing`: save bytes, call `update` with the old expected revision, assert `RevisionConflict` and identical bytes.
- `test_list_threads_sorts_updated_at_desc_without_index_file`: write two documents with distinct timestamps, assert their IDs appear in descending `updated_at` order and assert no index file exists.
- `test_protocol_and_trigger_message_lookup`: seed one run and assert lookups find its protocol ID and trigger message ID while an unknown ID returns no match. Content conflicts are tested at coordinator level in Task 4 because authoritative content lives in the thread document.
- `test_corrupt_json_is_quarantined_and_reported`: write `{broken`, assert `DocumentCorrupt`, original absence, and exactly one `.corrupt-<timestamp>` file.
- `test_rejects_invalid_document_ids_before_path_construction`: try `""`, `"../escape"`, `"a/b"`, and a 129-character ID; assert `InvalidDocumentId`, no path outside the store root, and no file creation.
- `test_thread_scan_keeps_healthy_documents_and_reports_quarantined_files`: seed one valid thread plus one malformed thread and one already-quarantined filename; assert the scan returns only the valid document and a stable recovery warning for both corrupt files rather than treating either as an empty thread.
- `test_reconcile_repairs_run_status_and_thread_summary_then_removes_only_owned_tmp_files`: seed active and terminal runs, deliberately leave each thread summary stale, add owned/unowned temp files, reconcile, and assert active runs become interrupted, every latest run summary is repaired from its run file, and only owned temp files are removed.

For the temp-file test, create both `.vr-agent-<uuid>.tmp` and `notes.tmp`; assert reconciliation deletes only the first. Monkeypatch `os.fsync` and `os.replace` to record call order `file fsync -> replace -> directory fsync`. On platforms where opening/fsyncing a directory raises `OSError`, assert the final directory fsync is best-effort and the replace still succeeds.

- [ ] **Step 2: Run the test and verify the module is missing**

Run: `cd backend && .venv/bin/pytest tests/agent/test_stores.py -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'agent.stores'`.

- [ ] **Step 3: Implement one durable write pipeline**

Create `backend/agent/stores.py` with:

```python
OWNED_TMP_RE = re.compile(r"^\.vr-agent-[0-9a-f]{32}\.tmp$")


def default_agent_root() -> Path:
    return Path(os.environ.get("VR_DATA_DIR") or Path.home() / ".vibe-research") / "agent"


@dataclass(frozen=True)
class AgentPaths:
    root: Path

    @property
    def threads(self) -> Path: return self.root / "threads"
    @property
    def runs(self) -> Path: return self.root / "runs"


class StoreError(RuntimeError):
    code = "AGENT_STORE_ERROR"


class DocumentNotFound(StoreError):
    code = "DOCUMENT_NOT_FOUND"


class DocumentCorrupt(StoreError):
    code = "DOCUMENT_CORRUPT"


class RevisionConflict(StoreError):
    code = "THREAD_REVISION_CONFLICT"


class InvalidDocumentId(StoreError):
    code = "INVALID_DOCUMENT_ID"


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".vr-agent-{uuid4().hex}.tmp"
    try:
        with tmp.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass
    finally:
        if tmp.exists():
            tmp.unlink()
```

Add this frozen warning value and a private generic `_JsonDocuments[T]` responsible only for path validation, Pydantic decode, quarantine, list/read/write/delete, and a per-document `threading.RLock`:

```python
@dataclass(frozen=True)
class RecoveryWarning:
    code: Literal["DOCUMENT_CORRUPT"]
    document_type: Literal["thread", "run"]
    filename: str
```

The scan result contains valid documents plus stable recovery warnings for malformed files it just quarantined and matching `.corrupt-<timestamp>` siblings already present. `ThreadStore` adds create/list and `update(thread_id, expected_revision, mutate)`, where the lock covers read, compare, revision increment, timestamp update, and atomic write. `RunStore` adds run lookup by product ID plus scans for protocol run IDs and triggering user-message IDs; the coordinator compares message content against the authoritative thread document. Do not create a global index file.

Quarantine malformed or schema-invalid JSON by `os.replace(path, path.with_name(f"{path.name}.corrupt-{utc_stamp}"))`. A direct read raises `DocumentCorrupt` containing the quarantined filename; a collection scan isolates that file, continues returning healthy documents, and includes `{code: "DOCUMENT_CORRUPT", document_type, filename}` in its recovery warnings. Validate all IDs against `^[A-Za-z0-9_-]{1,128}$` before constructing a path and raise `InvalidDocumentId` before joining it to a directory.

- [ ] **Step 4: Implement startup reconciliation**

Add:

```python
def reconcile_agent_data(paths: AgentPaths, threads: ThreadStore, runs: RunStore) -> None:
    all_runs = runs.list_documents(include_corrupt=False)
    reconciled_runs = []
    for run in all_runs:
        if run.status in ("running", "awaiting_approval"):
            run = run.model_copy(update={
                "status": "interrupted",
                "updated_at": utc_now(),
                "ended_at": utc_now(),
                "error_code": "BACKEND_RESTARTED",
                "error_message": "后端重启，原运行无法原位恢复",
            })
            runs.replace(run)
        reconciled_runs.append(run)
    for thread_id, latest in latest_runs_by_thread(reconciled_runs).items():
        threads.repair_last_run(thread_id, latest)
    for directory in (paths.root, paths.threads, paths.runs, paths.root / "artifacts"):
        if not directory.exists():
            continue
        for child in directory.glob("*.tmp"):
            if OWNED_TMP_RE.fullmatch(child.name):
                child.unlink()
```

Define `latest_runs_by_thread` to choose each thread's maximum `(updated_at, id)` deterministically. Build that map from `reconciled_runs` after replacing active runs so it contains their updated values. `repair_last_run` must preserve messages and increment the thread revision only when the complete `RunSummary` actually differs. This reverse reconciliation closes the crash window where the run file reached a terminal state but the thread's `last_run` update did not commit. Root-level cleanup covers future `mcp.json`/`policy.json` temporary siblings without recursively entering user Skill directories.

- [ ] **Step 5: Run tests and commit**

```bash
cd backend
.venv/bin/pytest tests/agent/test_stores.py -q
.venv/bin/pytest -m "not live"
git add backend/agent/stores.py backend/tests/agent/test_stores.py
git commit -m "feat(agent): add durable atomic json stores"
```

Expected: store tests pass and no test writes outside `tmp_path`.

### Task 3: Add Revision-Safe Thread And Run REST APIs

**Files:**
- Modify: `backend/agent/router.py`
- Modify: `backend/agent/runs.py`
- Modify: `backend/app.py`
- Create: `backend/tests/agent/test_thread_api.py`
- Modify: `backend/tests/agent/test_router.py`
- Modify: `backend/tests/agent/test_agent_vertical_slice.py`
- Modify: `backend/tests/test_api.py`

- [ ] **Step 1: Write failing CRUD and startup tests**

Create an isolated router fixture that injects `AgentPaths(tmp_path / "agent")`, `ThreadStore`, `RunStore`, and `RunCoordinator`. Cover:

```text
POST /threads -> 201 document with revision 0
GET /threads -> updated_at descending summaries
GET /threads with corrupt files -> 200 healthy summaries plus DOCUMENT_CORRUPT recovery warnings
GET /threads/{id} -> full authoritative messages
PATCH title with current revision -> revision + 1
PATCH title during an active run -> succeeds and advances that handle's revision
PATCH with stale revision -> 409 THREAD_REVISION_CONFLICT
DELETE idle thread -> 204 and its run files removed
DELETE active thread -> 409 THREAD_BUSY
GET /runs/{id} -> persisted run
missing/directly-read corrupt document -> structured 404/409 or 500 without pretending it is empty
invalid path ID -> 400 INVALID_DOCUMENT_ID before store access
```

Use request bodies:

```python
{"title": "现金流研究"}
{"revision": 0, "title": "现金流与资本开支"}
```

Add an app lifespan test that builds services from `tmp_path`, monkeypatches `agent.router.services` before entering `TestClient(app)` as a context manager, seeds a `running` run in those injected stores, and asserts that exact run becomes `interrupted`. The global pytest `VR_DATA_DIR` is already isolated by `backend/conftest.py`, but explicit service replacement is still required because the test's `tmp_path` differs from that session-level directory. Preserve the existing CORS assertion that `PATCH` is allowed; do not duplicate it.

- [ ] **Step 2: Run focused tests and verify 404/failures**

Run: `cd backend && .venv/bin/pytest tests/agent/test_thread_api.py tests/test_api.py -q`

Expected: new endpoint assertions fail before implementation; legacy API assertions remain green.

- [ ] **Step 3: Add dependency-injectable Agent services and REST routes**

In `router.py`, replace standalone mutable globals with a small container:

```python
@dataclass
class AgentServices:
    paths: AgentPaths
    threads: ThreadStore
    runs: RunStore
    coordinator: RunCoordinator


def build_services(root: Path | None = None) -> AgentServices:
    paths = AgentPaths(root or default_agent_root())
    threads = ThreadStore(paths)
    runs = RunStore(paths)
    coordinator = RunCoordinator(factory=AgentFactory(), threads=threads, runs=runs)
    return AgentServices(paths, threads, runs, coordinator)


services = build_services()
```

In this task, extend `RunCoordinator.__init__` to accept optional `ThreadStore` and `RunStore` references and expose the per-thread lock to its PATCH/DELETE methods; preserve `RunCoordinator()` as a valid 1A test construction until fixtures are migrated. Add an optional `thread_revision` to the 1A handle. After a successful active-thread PATCH, update the active handle to the committed revision under the same lock so the next run-owned write does not fail against a revision advanced by rename. Task 4 will add durable run admission behavior, but it must not be the first task that introduces this constructor signature.

Add Pydantic request models `ThreadCreate(title="新会话")` and `ThreadPatch(revision, title)` plus these response models in `router.py`:

```python
class ThreadSummary(BaseModel):
    id: str
    title: str
    updated_at: str
    revision: int
    last_run: RunSummary | None = None


class RecoveryWarningResponse(BaseModel):
    code: Literal["DOCUMENT_CORRUPT"]
    document_type: Literal["thread", "run"]
    filename: str


class ThreadListResponse(BaseModel):
    threads: list[ThreadSummary]
    warnings: list[RecoveryWarningResponse]
```

`GET /threads` scans both thread and run documents and returns `ThreadListResponse` so healthy threads remain usable while quarantined JSON is visible to the UI; warnings expose only document kind and quarantined basename, never an absolute local path. Register the six endpoints that are fully implementable at this stage: `GET/POST /threads`, `GET/PATCH/DELETE /threads/{id}`, and `GET /runs/{id}`. Register the seventh 1B endpoint, `POST /runs/{id}/cancel`, in Task 5 together with its idempotent persisted state transition. Keep Skill selection and the later `/allowances` endpoint out of 1B.

Map `InvalidDocumentId` to HTTP 400 and map `RevisionConflict`, `ThreadBusy`, `DocumentNotFound`, and `DocumentCorrupt` to their specified structured error statuses, always using stable `{code, detail}` payloads. Route PATCH and DELETE through coordinator methods so they use the same per-thread `asyncio.Lock` as run admission; the store's compare-and-swap remains the file-level guard. Deleting a thread rechecks active state, removes the thread and all runs belonging to it, and then returns HTTP 204.

- [ ] **Step 4: Wire reconciliation into application startup**

Expose `async def startup_agent_services()` and `async def shutdown_agent_services()` from `router.py`. Both functions must dereference the module-level `services` at call time so tests can safely monkeypatch it before lifespan entry. Startup calls `await asyncio.to_thread(reconcile_agent_data, services.paths, services.threads, services.runs)`. Shutdown asks the coordinator to cancel/persist/close every active handle. Add a FastAPI lifespan hook in `backend/app.py` and register these functions there; `app.py` has no existing lifespan hook to reuse. Preserve the import-time portfolio scheduler behavior and do not import `app.py` from Agent modules.

Update `test_router.py` and `test_agent_vertical_slice.py` in this task: replace monkeypatches and assertions against `router_module.coordinator` with an injected `router_module.services`, and keep their existing in-memory coordinator semantics where persistence is irrelevant. All async REST store calls must use `asyncio.to_thread` so file flush/fsync never blocks the single FastAPI event loop.

- [ ] **Step 5: Run tests and commit**

```bash
cd backend
.venv/bin/pytest tests/agent/test_thread_api.py tests/test_api.py -q
.venv/bin/pytest -m "not live"
git add backend/agent/router.py backend/agent/runs.py backend/app.py backend/tests/agent/test_thread_api.py backend/tests/agent/test_router.py backend/tests/agent/test_agent_vertical_slice.py backend/tests/test_api.py
git commit -m "feat(agent): add revision safe thread api"
```

Expected: CRUD, startup reconciliation, auth, and CORS tests pass.

### Task 4: Make Run Admission Durable And Server-Authoritative

**Files:**
- Modify: `backend/agent/runs.py`
- Modify: `backend/agent/runtime.py`
- Create: `backend/tests/agent/test_run_persistence.py`
- Modify: `backend/tests/agent/test_runtime_stream.py`
- Modify: `backend/tests/agent/test_resume_contract.py`

- [ ] **Step 1: Write failing admission and history tests**

Test coordinator methods directly with temp stores. Use complete fixture setup rather than parametrized placeholders; each named test must assert the following:

- `test_start_persists_user_and_running_run_before_model_execution`: the model builder observes both committed files.
- `test_start_rebuilds_graph_input_from_complete_server_history`: captured input equals complete persisted messages plus the accepted user message and excludes a seeded partial assistant.
- `test_start_rejects_stale_revision_and_forked_head_before_model_builder`: both variants leave builder count zero.
- `test_active_and_terminal_duplicates_do_not_write_or_build_a_graph`: both variants preserve bytes/revision and builder count zero.
- `test_existing_message_id_with_different_content_is_message_conflict`: returns `MESSAGE_CONFLICT` before any write.
- `test_resume_appends_protocol_id_to_same_product_run`: run ID is unchanged and protocol ID list gains exactly one entry.
- `test_steer_away_cancels_old_run_before_persisting_new_user_and_run`: old persisted status is already cancelled when the new builder runs.

The fake model builder must be a counter and remain zero for revision, head, duplicate, and message-conflict rejection. Compare file bytes and revisions before/after rejected calls to prove no write occurred.

- [ ] **Step 2: Run the focused tests and verify failures**

Run: `cd backend && .venv/bin/pytest tests/agent/test_run_persistence.py tests/agent/test_runtime_stream.py -q`

Expected: admission tests fail because 1A coordinator has no stores, product run ID, revision, or server-history rebuild.

- [ ] **Step 3: Extend coordinator state and errors**

Use these stable exception codes:

```python
class DuplicateRunActive(RuntimeError): code = "DUPLICATE_RUN_ACTIVE"
class DuplicateRunTerminal(RuntimeError): code = "DUPLICATE_RUN_TERMINAL"
class MessageConflict(RuntimeError): code = "MESSAGE_CONFLICT"
class RetryNotAllowed(RuntimeError): code = "RETRY_NOT_ALLOWED"
```

Extend `ActiveRunHandle` with `product_run_id`, `protocol_run_ids`, `trigger_message_id`, `started_monotonic`, a required current `thread_revision`, and a sanitized immutable history/snapshot reference. Keep secrets out. Every internal run write uses that current revision under the per-thread lock and replaces it with the returned committed revision. Use the `ThreadStore` and `RunStore` constructor references introduced in Task 3 rather than changing the coordinator signature again.

Replace `acquire_start` with an operation that, under one thread lock:

1. checks protocol/message duplicates before revision;
2. reads the authoritative thread and compares `threadRevision`; while the temporary Task 1 compatibility value is `None`, use the revision read under this same lock as the expected revision, but still perform every head/content/duplicate check below;
3. verifies the client prefix IDs/content exactly match the server message head and that there is exactly one new user message;
4. creates a UUID product run and writes its `running` `RunDocument`;
5. appends the accepted user message to the thread and increments revision;
6. builds the Graph from `thread.model_history()` plus that accepted user message;
7. installs the handle only after persistence succeeds.

If Graph construction fails after persistence, finalize the product run as `failed` and update `last_run`; do not remove the accepted user message. Return an admission result containing the handle, product run, authoritative adapter input, and every committed revision so the router can emit them in order.

- [ ] **Step 4: Add explicit server-history input builders**

In `runtime.py`, remove `start_input(content)` and add:

```python
def run_input(
    self,
    *,
    protocol_run_id: str,
    messages: Sequence[AgentMessage],
) -> RunAgentInput:
    return RunAgentInput(
        thread_id=self.thread_id,
        run_id=protocol_run_id,
        state={},
        messages=[to_ag_ui_message(message) for message in messages],
        tools=[],
        context=[],
        forwarded_props={},
    )
```

`to_ag_ui_message` converts only validated user/assistant/tool records and never includes `partial`, persistence timestamps, run IDs, or revision metadata. `resume_input` remains `messages=[]`. A normal next turn and retry both create a fresh `MemorySaver`; only approval resume reuses the active handle's saver.

In the same step, migrate `test_resume_contract.py` from `start_input(content)` to `run_input(protocol_run_id="protocol-start", messages=[AgentMessage(id="user-start", role="user", content="run protected")])`. Preserve every existing assertion about `messages=[]` resume, equivalent-Graph reconstruction, and secret isolation; deleting the helper must not leave an intermediate `AttributeError` in the 1A suite.

- [ ] **Step 5: Preserve 1A resume and steer-away behavior while persisting transitions**

`acquire_resume` appends the new protocol run ID to the same `RunDocument`, validates revision and all bridge IDs under the thread lock, and rebuilds the request-scoped Graph. `acquire_steer_away` first persists the old product run as `cancelled`, preserves its pending assistant/tool turn as non-model history, releases the old handle, then performs a new start with the one new user message. If the new run write fails, the old run remains cancelled exactly as the design specifies.

- [ ] **Step 6: Run tests and commit**

```bash
cd backend
.venv/bin/pytest tests/agent/test_run_persistence.py tests/agent/test_runtime_stream.py tests/agent/test_resume_contract.py -q
.venv/bin/pytest -m "not live"
git add backend/agent/runs.py backend/agent/runtime.py backend/tests/agent/test_run_persistence.py backend/tests/agent/test_runtime_stream.py backend/tests/agent/test_resume_contract.py
git commit -m "feat(agent): persist authoritative run admission"
```

Expected: new persistence tests and all 1A resume contracts pass.

### Task 5: Persist Stream Boundaries, Partial Messages, And Revisions

**Files:**
- Modify: `backend/agent/runs.py`
- Modify: `backend/agent/protocol.py`
- Modify: `backend/agent/router.py`
- Modify: `backend/tests/agent/test_run_persistence.py`
- Modify: `backend/tests/agent/test_agent_vertical_slice.py`
- Modify: `backend/tests/agent/test_router.py`

- [ ] **Step 1: Write failing event-journal tests**

Drive the offline scripted model through the HTTP endpoint and assert:

```text
accepted user write -> revision event
tool completion write -> revision event
assistant completion write -> revision event
run terminal/last_run write -> final revision event -> RUN_FINISHED
```

Also cover disconnect after at least one text delta: the run becomes `cancelled`; the accumulated assistant message is saved with `partial=true`; the same partial text appears after `GET /threads/{id}`; the next start's captured model input excludes it. Assert the API key is absent from every thread/run JSON byte and every SSE frame. In `test_router.py`, replace the 1A `cancel_sync` spy with assertions against the single persisted coordinator transition. Add `test_cancelled_stream_shields_partial_persistence_before_reraising`: start the ASGI streaming task, wait until one text delta is accumulated, call `task.cancel()` rather than sending `http.disconnect`, assert the caller receives `CancelledError`, then assert the run is `cancelled`, the partial assistant is durable, and the Graph/model references are released.

Add a fixture whose model text and completed tool result deliberately contain the current request's model key. Assert the streamed error/result, journal accumulator, thread JSON, and run JSON contain `[redacted]` rather than the key. This verifies redaction at the persistence boundary instead of assuming built-in tools can never echo sensitive text.

Add a concurrency fixture that pauses between tool and assistant commits, PATCHes the active thread title with the current revision, then lets the run finish. Assert PATCH succeeds, the handle adopts the PATCH revision, later journal writes continue from it, and both REST responses and streamed revision events form a monotonic sequence even when the frontend receives them out of order.

- [ ] **Step 2: Run tests and verify event-order failures**

Run: `cd backend && .venv/bin/pytest tests/agent/test_run_persistence.py tests/agent/test_agent_vertical_slice.py -q`

Expected: FAIL because 1A only streams converted events and discards all content at terminal/disconnect.

- [ ] **Step 3: Add a semantic-boundary run journal**

In `runs.py`, add `RunJournal` owned by `ActiveRunHandle`. It consumes already-redacted standard AG-UI events and maintains one in-memory assistant message plus tool calls keyed by call ID. Its public surface is `observe(event) -> list[CommittedRevision]`, `persist_interrupt(pending) -> CommittedRevision`, `persist_terminal(status, error_code=None, error_message=None) -> CommittedRevision`, and `persist_partial_cancel() -> CommittedRevision`. Implement each body in this task using the exact persistence boundaries in the preceding paragraph; do not leave `pass`, `NotImplementedError`, or ellipsis in production code.

In `router.py`, recursively redact strings in every converted event's content/args/results against the current request-local `(model_key,)`, producing `safe_event` before either yielding or journaling it. Call `journal.observe(safe_event)` and encode the same `safe_event`; do not store the sensitive tuple, a closure over it, or `RunSecrets` on the journal or active handle. Text deltas only mutate journal memory. A completed tool result, `TEXT_MESSAGE_END`, captured interrupt, and terminal state call `ThreadStore.update` and/or `RunStore.replace`; `TOOL_CALL_END` alone does not claim that execution completed. Upsert by stable message/tool-call ID so resume finalizes the pending assistant message rather than appending a duplicate. Truncate persisted tool summaries with the existing 6,000-character boundary after redaction.

At each run transition, update `updated_at`, wall-clock/active/approval durations, model/tool call counts, and provider token usage when present. Keep the 1B `budget_snapshot` empty and do not estimate money or enforce budgets before 1D.

- [ ] **Step 4: Emit revision events only after commits**

Add a `thread_revision_updated(thread_id, revision, persisted_at)` constructor to `protocol.py` returning a `CustomEvent` named `thread.revision.updated`. In `router.py`, for each `CommittedRevision`, yield that custom event through `EventEncoder` directly after the store method returns. Never pass this project event into `AgentProtocolBridge.convert`, because the 1A bridge intentionally converts unknown CUSTOM events into `RUN_ERROR`. When handling `RunFinishedEvent`, defer the adapter's terminal event until journal persistence succeeds; yield the final revision event first and the converted `RUN_FINISHED` second.

For an interrupt, persist pending metadata and the incomplete assistant/tool turn, emit its revision event, call `mark_awaiting_approval` (which releases Graph/model), and only then emit the standard interrupt outcome. Do not set `expiresAt`.

If any required JSON commit fails, emit a redacted `RUN_ERROR` that explicitly says the state was not persisted, close the active handle, and do not emit a success `RUN_FINISHED` or advance the client revision optimistically.

Only journal operations that can write JSON run through `asyncio.to_thread`, for example a completed tool/result event uses `await asyncio.to_thread(journal.observe, safe_event)`; delta-only in-memory accumulation remains inline to avoid one worker hop per token. Keep the per-thread `asyncio.Lock` held across each awaited storage transition so another request cannot observe or mutate an intermediate state, while the blocking file flush and directory fsync run outside the event-loop thread.

- [ ] **Step 5: Make cancellation one idempotent persisted transition**

Register `POST /runs/{id}/cancel` in this task. That endpoint, request disconnect, and `asyncio.CancelledError` all call one coordinator transition. It marks the captured handle cancelled before removing it, drops late journal events, persists any accumulated assistant content as partial, finalizes the run and thread summary, and releases the Graph. A second cancellation returns the existing terminal run without incrementing revision again.

The endpoint and polled `http.disconnect` path await that transition normally. The `asyncio.CancelledError` handler cannot perform ordinary awaits inside Starlette/anyio's cancelled scope, so it must create one independent persistence task, then await `asyncio.shield(task)` inside `anyio.move_on_after(2, shield=True)` before re-raising the original cancellation. Do not cancel the persistence task when the two-second join expires: it retains the per-thread lock and finishes in the background, while the already-marked handle rejects late journal events. If the process exits before that task commits, startup reconciliation converts the still-active run to `interrupted` and repairs `last_run`; partial text in that crash window is explicitly best-effort. Keep a strong task reference until its done callback observes the result so exceptions are not lost.

- [ ] **Step 6: Run tests and commit**

```bash
cd backend
.venv/bin/pytest tests/agent/test_run_persistence.py tests/agent/test_agent_vertical_slice.py tests/agent/test_router.py -q
.venv/bin/pytest -m "not live"
git add backend/agent/runs.py backend/agent/protocol.py backend/agent/router.py backend/tests/agent/test_run_persistence.py backend/tests/agent/test_agent_vertical_slice.py backend/tests/agent/test_router.py
git commit -m "feat(agent): journal run history and partial output"
```

Expected: persistence boundaries, event order, cancellation, and 1A protocol behavior pass.

### Task 6: Implement Strict Retry And Complete Request Admission

**Files:**
- Modify: `backend/agent/models.py`
- Modify: `backend/agent/runs.py`
- Modify: `backend/agent/router.py`
- Modify: `backend/tests/agent/test_run_persistence.py`
- Modify: `backend/tests/agent/test_router.py`

- [ ] **Step 1: Write failing retry and conflict tests**

Cover all four valid shapes and reject every mixture. Retry tests must assert:

```python
assert retry_response.status_code == 200
assert retry_run.retry_of == failed_run.id
assert retry_run.id != failed_run.id
assert retry_run.protocol_run_ids == ["protocol-retry"]
assert [m.id for m in thread.messages].count("user-original") == 1
assert captured_model_history == history_before_failed_assistant
```

Reject with `409 RETRY_NOT_ALLOWED` when the target is not the latest product run, is not `failed/cancelled/interrupted`, belongs to another thread, or complete history advanced after it. Reject ordinary start/resume/steer-away/retry with stale revision as `THREAD_REVISION_CONFLICT`. Verify a lost revision event followed by the old revision converges through that 409 without a duplicate write.

The temporary missing-revision compatibility path is only for the three shapes already emitted by the 1A frontend: start, resume, and steer-away. A retry request with `threadRevision=None` must fail as `INVALID_RUNTIME_PROPS`; Task 7 removes the compatibility path for every shape.

- [ ] **Step 2: Run tests and verify retry is still rejected**

Run: `cd backend && .venv/bin/pytest tests/agent/test_run_persistence.py tests/agent/test_router.py -q`

Expected: retry tests receive the 1A `RETRY_REQUIRES_DURABLE_HISTORY` response.

- [ ] **Step 3: Classify and admit retry**

Remove the early 1A retry rejection. `_classify` must return exactly one of `start`, `resume`, `steer_away`, or `retry`:

```python
if retry_of is not None:
    if resume_entries or messages:
        raise ValueError("retry 不得携带 resume 或新消息")
    return "retry"
```

`acquire_retry` validates the target and current thread revision/head under the thread lock, creates a new product run with `retry_of`, creates a new `MemorySaver`, rebuilds input from the target's last complete/non-pending history, and streams through the same response. It must not mutate or reopen the target run.

Return structured conflict bodies using one exact snake_case wire schema: `code`, `detail`, `thread_id`, `product_run_id` where known, and `status` for duplicate active/terminal responses. Add an endpoint assertion for the complete JSON keys so the frontend cannot silently drift to camelCase. Keep malformed shapes as HTTP 400; semantic conflicts are HTTP 409.

- [ ] **Step 4: Run tests and commit**

```bash
cd backend
.venv/bin/pytest tests/agent/test_run_persistence.py tests/agent/test_router.py -q
.venv/bin/pytest -m "not live"
git add backend/agent/models.py backend/agent/runs.py backend/agent/router.py backend/tests/agent/test_run_persistence.py backend/tests/agent/test_router.py
git commit -m "feat(agent): add durable retry and duplicate guards"
```

Expected: all four shapes pass their contract tests; invalid mixtures and retry targets fail before model construction.

### Task 7: Connect Server History To assistant-ui

**Files:**
- Modify: `backend/agent/models.py`
- Modify: `backend/tests/agent/test_models.py`
- Modify: `backend/tests/agent/test_router.py`
- Create: `frontend/src/lib/agent/types.ts`
- Create: `frontend/src/lib/agent/api.ts`
- Create: `frontend/src/lib/agent/history.ts`
- Create: `frontend/src/lib/agent/history.test.tsx`
- Modify: `frontend/src/lib/agent/runtime.tsx`
- Modify: `frontend/src/lib/agent/runtime.test.tsx`
- Create: `frontend/src/components/agent/AgentThreadList.tsx`
- Modify: `frontend/src/components/agent/AgentThread.tsx`
- Modify: `frontend/src/pages/Agent.tsx`
- Modify: `frontend/src/pages/Agent.test.tsx`

- [ ] **Step 1: Write failing REST and revision-state tests**

Define fixtures for two threads and assert:

```text
list response is ordered by updated_at and preserves recovery warnings
switch hydrates stable message IDs, tool parts, partial status, and pending interrupt metadata
new creates a server thread before first send
rename/delete include the current revision
revision 4 then delayed revision 3 leaves local revision at 4
any structured 409 reloads the active thread and latest revision exactly once
history append/update do not issue a second write request
retry sends retryOf, current revision, no messages, and a fresh protocol run ID
the backend rejects a run payload that omits threadRevision after the frontend migration
```

- [ ] **Step 2: Run tests and verify missing modules**

Run:

```bash
cd frontend
npx vitest run src/lib/agent/history.test.tsx src/lib/agent/runtime.test.tsx src/pages/Agent.test.tsx
cd ../backend
.venv/bin/pytest tests/agent/test_models.py tests/agent/test_router.py -q
```

Expected: frontend tests fail because Agent REST/history modules and the thread controls do not exist; the new backend assertion fails because omitted `threadRevision` is still temporarily accepted.

- [ ] **Step 3: Add Agent REST types and client**

Define `AgentThread`, `AgentThreadSummary`, `AgentRun`, `AgentRunSummary`, `AgentMessage`, `AgentRecoveryWarning`, `AgentThreadListResponse`, and `AgentConflict` in `types.ts` matching the backend field names exactly. `AgentThreadListResponse` contains `threads` and `warnings`; a warning contains only `code`, `document_type`, and quarantined `filename`. `AgentConflict` uses the backend wire names `thread_id` and `product_run_id`; map them to presentation names only at component boundaries. In `api.ts`, use `authHeaders()` and a local `agentRequest<T>` supporting `GET | POST | PATCH | DELETE`; do not change the shared `lib/api.ts` request signature for this isolated subsystem.

Export:

```typescript
export const agentApi = {
  listThreads: () => agentRequest<AgentThreadListResponse>("/api/agent/threads"),
  createThread: (title = "新会话") => agentRequest<AgentThread>("/api/agent/threads", "POST", { title }),
  getThread: (id: string) => agentRequest<AgentThread>(`/api/agent/threads/${encodeURIComponent(id)}`),
  patchThread: (id: string, revision: number, title: string) =>
    agentRequest<AgentThread>(`/api/agent/threads/${encodeURIComponent(id)}`, "PATCH", { revision, title }),
  deleteThread: (id: string) => agentRequest<void>(`/api/agent/threads/${encodeURIComponent(id)}`, "DELETE"),
  cancelRun: (id: string) => agentRequest<AgentRun>(`/api/agent/runs/${encodeURIComponent(id)}/cancel`, "POST"),
};
```

`AgentApiError` retains HTTP status plus parsed `code`, `thread_id`, `product_run_id`, and run status so 409 handling never scrapes human text.

- [ ] **Step 4: Implement one in-memory view of authoritative server state**

Create `AgentHistoryController` in `history.ts`. It holds only the active fetched document, list cache, and recovery warnings for the current page lifetime; it does not use `localStorage`. Its `applyRevision(threadId, revision)` only applies values greater than the cached revision. Its `reload(threadId)` replaces messages and revision from REST. `AgentThreadList.tsx` renders a compact Chinese recovery notice when warnings are present, naming only the quarantined basename and never presenting a corrupt document as an empty conversation.

Convert persisted messages into assistant-ui's `ExportedMessageRepository`/`ThreadMessage` shape without changing IDs. Map `partial=true` to an incomplete assistant status and pending interrupts to `requires-action`. The `ThreadHistoryAdapter` is:

```typescript
{
  load: async () => controller.exportRepository(activeThreadId),
  append: async () => {},
  update: async () => {},
}
```

The no-op writes are intentional: `/api/agent/run` is the sole message writer, and the runtime reloads the committed document on revision/conflict/terminal boundaries. The thread-list adapter delegates create/switch/rename/delete to `agentApi`, returns hydrated messages from `onSwitchToThread`, and updates controller state only from successful REST responses.

- [ ] **Step 5: Extend runtime transport metadata and event observation**

Change `AgentHttpAgent` to receive getters for current revision and one-shot `retryOf`. `requestInit` always adds `runtime.threadRevision`; when retry is armed, it sends `messages: []`, adds `runtime.retryOf` only to that request, and consumes it after constructing the request with the runtime's fresh protocol run ID. In the same task and commit, change `RuntimeForwardedProps.thread_revision` in `backend/agent/models.py` from `int | None` to required `int`, replace the temporary compatibility test with a missing-field rejection test, and run the backend regression gate. Extend the SSE tee scanner to intercept `CUSTOM` events named `thread.revision.updated`, apply them monotonically, and never expose them as chat text.

For every structured 409 (`THREAD_REVISION_CONFLICT`, duplicates, message conflict, busy, retry-not-allowed, or config mismatch), call `controller.reload(threadId)`, replace runtime messages through the adapter, and show the returned Chinese detail. Reloading on `RUN_CONFIG_MISMATCH` is intentional: the approved design requires every structured `/run` 409 to converge through the same authoritative REST reload, even when the immediate conflict is model configuration rather than history. Do not automatically replay the rejected mutation or reconnect an old stream.

- [ ] **Step 6: Add the minimal 1B thread controls and retry action**

`AgentThreadList.tsx` renders a compact select/list plus New, Rename, and Delete commands using Lucide icons and tooltips. It must not introduce the final three-column layout. Disable delete while the active thread's last run is `running` or `awaiting_approval`.

In `AgentThread.tsx`, show a Retry command only when `activeThread.last_run.status` is `failed`, `cancelled`, or `interrupted`. The handler arms `retryOf=activeThread.last_run.id` and invokes assistant-ui reload for the latest eligible turn; `requestInit` strips the history payload so the resulting AG-UI request contains no new message. Running keeps the 1A input-disabled/Stop behavior. After Stop, wait for cancellation persistence and authoritative reload before re-enabling send.

- [ ] **Step 7: Assemble the page and test refresh recovery**

On first load, fetch the thread list. Select the most recently updated thread, or create `新会话` when none exists. Key only the history/runtime instance that must reset when the active server thread changes; keep model config state independent. Refresh the component with the same mocked server data and assert the exact message IDs/content return without reading `localStorage` keys other than `vr-agent-model` and `vr-access-key`.

- [ ] **Step 8: Run tests and commit**

```bash
cd frontend
npx vitest run src/lib/agent/history.test.tsx src/lib/agent/runtime.test.tsx src/pages/Agent.test.tsx src/components/agent/AgentThread.test.tsx
npm test
npx vitest run
npm run build
cd ../backend
.venv/bin/pytest tests/agent/test_models.py tests/agent/test_router.py tests/agent/test_agent_vertical_slice.py -q
.venv/bin/pytest -m "not live"
cd ..
git add backend/agent/models.py backend/tests/agent/test_models.py backend/tests/agent/test_router.py frontend/src/lib/agent/types.ts frontend/src/lib/agent/api.ts frontend/src/lib/agent/history.ts frontend/src/lib/agent/history.test.tsx frontend/src/lib/agent/runtime.tsx frontend/src/lib/agent/runtime.test.tsx frontend/src/components/agent/AgentThreadList.tsx frontend/src/components/agent/AgentThread.tsx frontend/src/pages/Agent.tsx frontend/src/pages/Agent.test.tsx
git commit -m "feat(agent): restore server backed thread history"
```

Expected: focused tests pass and strict TypeScript production build completes.

### Task 8: Verify The 1B Vertical Slice And Regressions

**Files:**
- Create: `docs/superpowers/verification/2026-08-15-langchain-agent-workspace-1b.md`
- Test: `backend/tests/agent/test_run_persistence.py`

- [ ] **Step 1: Add one offline end-to-end lifecycle test**

Extend `backend/tests/agent/test_run_persistence.py` with a deterministic sequence:

```text
create thread
start -> tool -> partial text -> disconnect
reload and observe partial + cancelled run
retry with fresh protocol/product IDs -> successful terminal
reload and observe one original user message + complete retry answer
simulate process restart -> no active handle and history unchanged
send next normal turn -> captured model history excludes old partial content
```

Assert revision strictly increases at each committed semantic boundary and every SSE revision matches the final REST document sequence.

- [ ] **Step 2: Run all automated checks**

```bash
cd backend
.venv/bin/pytest -m "not live"

cd ../frontend
npm test
npx vitest run
npm run build
```

Expected: all offline backend tests, legacy Node tests, Vitest tests, and production build pass. Record exact counts in the verification document.

- [ ] **Step 3: Perform the local browser acceptance pass**

Start the existing backend and frontend dev servers, then use the documented CDP endpoint at `127.0.0.1:16002`. Verify at desktop and mobile widths:

```text
new/rename/switch/delete work without overlap
refresh restores messages and selected thread
Stop persists a visible partial response
Retry creates a new run without duplicating the question
backend restart marks an active run interrupted and preserves history
stale revision produces a visible conflict then reloads authoritative state
quarantined JSON leaves healthy threads usable and shows a basename-only recovery notice
browser localStorage contains no thread/message history or server/model secrets
```

Capture screenshots and inspect console/network failures. This milestone does not require the final 1D three-column visual acceptance.

- [ ] **Step 4: Write the verification record**

Create `docs/superpowers/verification/2026-08-15-langchain-agent-workspace-1b.md` with:

```markdown
# LangChain Agent Workspace 1B Verification

- Atomic store/corruption/reconciliation suite: PASS|FAIL
- Revision/duplicate/message-conflict suite: PASS|FAIL
- Partial/cancel/retry lifecycle: PASS|FAIL
- Refresh and backend-restart recovery: PASS|FAIL
- Secret persistence scan: PASS|FAIL
- Legacy backend/frontend regression: PASS|FAIL
- Browser desktop/mobile acceptance: PASS|FAIL

Decision: Proceed to 1C only when every automated item passes and no secret or user data appears outside the isolated Agent data directory.
```

Record actual counts and any environment limitation truthfully; do not mark an unrun browser or provider check as PASS.

- [ ] **Step 5: Commit the verified milestone**

```bash
git status --short
git add backend/tests/agent/test_run_persistence.py docs/superpowers/verification/2026-08-15-langchain-agent-workspace-1b.md
git commit -m "test(agent): verify milestone 1b persistence"
```

Before committing, inspect the staged file list and remove any user data, `.vr-dev/`, `.superpowers/`, API keys, screenshots containing secrets, or unrelated work. Do not use broad staging if unrelated tracked edits are present.

## 1B Exit Checklist

- [ ] Refresh and backend restart preserve complete authoritative history.
- [ ] Partial assistant output remains visible but never enters later model input.
- [ ] Atomic writes cannot expose truncated JSON; corrupt documents are quarantined and reported.
- [ ] Thread listing keeps healthy documents visible and reports quarantined thread/run basenames without leaking absolute paths.
- [ ] Startup marks inherited active runs interrupted and removes only owned temporary files.
- [ ] Revision/head conflicts stop before model construction.
- [ ] After the Task 7 migration, every run shape requires `threadRevision`; the temporary 1A compatibility path is gone.
- [ ] Active/terminal duplicates and message conflicts return the specified 409 codes without another handle or write.
- [ ] Retry streams through the same `/api/agent/run` response with a new product run and no duplicated user message.
- [ ] `thread.revision.updated` is post-commit, monotonic on the client, and precedes terminal `RUN_FINISHED`.
- [ ] Stop/disconnect persist cancellation and partial state idempotently; late tool output is ignored.
- [ ] Thread/run files, SSE, checkpoints, logs, and browser history contain no model API key.
- [ ] Existing AI routes and all offline frontend/backend tests still pass.
