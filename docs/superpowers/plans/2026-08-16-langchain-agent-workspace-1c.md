# LangChain Agent Workspace 1C Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add safely managed local Skills and stdio/Streamable HTTP MCP tools to the existing authoritative Agent workspace, with immutable capability snapshots, approval and thread-session allowances, recovery, and a focused management UI.

**Architecture:** Preserve the 1A/1B AG-UI, request-scoped Graph, server-authoritative history, duplicate, retry, and cancellation contracts. Add isolated Skill, MCP, and capability modules; `RunCoordinator` performs two-phase capability admission, `ActiveRunHandle` owns one secret-free lease, and request-scoped middleware is rebuilt from the current model key on start/resume. Deliver three independently green slices: Skills, MCP management/catalog only, then MCP runtime plus approval.

**Tech Stack:** Python 3.10+, FastAPI, Pydantic 2, LangChain 1.3.15, LangGraph 1.2.11, langchain-mcp-adapters 0.3.2, MCP SDK 1.26.0, httpx 0.28.1, React 19, TypeScript, assistant-ui 0.15.14, Vitest, React Testing Library, pytest

---

## Scope And Non-Negotiable Invariants

Implement only milestone 1C from `docs/superpowers/specs/2026-08-16-langchain-agent-workspace-1c-design.md`. Do not add Artifact storage, budgets, context pruning, a final Inspector, Skill script execution, OAuth, MCP Resources/Prompts, permanent trust, multi-worker support, or migrate legacy chat/debate/reflection entry points.

Keep these invariants green after every task:

- `chat.SYSTEM_PROMPT` remains the first and highest-priority instruction; Skill/MCP text is untrusted external content and never becomes a replacement system message.
- Model keys, resolved MCP env/header values, `ClientSession`, and connection configs never enter thread/run/MCP JSON, Graph state, MemorySaver, SSE, logs, `CapabilityLease`, or `ActiveRunHandle`.
- New product runs use a two-phase preview/lease/final-check sequence; duplicate detection remains before capability I/O and before revision conflicts.
- Pure resume reuses the same lease and MemorySaver, re-supplies `X-VR-Agent-Model-Key`, validates `ModelRef`, and rebuilds request-scoped middleware before any allowance write.
- Built-in Eastmoney-backed tools remain serial; do not parallelize throttled data calls while adding cross-server MCP concurrency.
- All tests and browser fixtures inject a temporary `AgentServices` root. They never read, scan, connect, or mutate real `~/.vibe-research/agent` data.
- Slice 2 exposes no MCP alias to any Graph. Task 11 may commit dormant bindings only; production exposure in Task 12 must land with `McpArgumentGuard` and exhaustive HITL policy in the same green completion task.
- The backend remains single-worker for 1C.

## File Map

### Backend production

- `backend/vendor/mootdx_compat/pyproject.toml`: local `mootdx==0.11.7+vr1` metadata with only the httpx constraint changed.
- `backend/vendor/mootdx_compat/LICENSE`, `SOURCE.md`, `upstream.sha256`: license, provenance, and byte-for-byte Python-source manifest.
- `backend/vendor/mootdx_compat/src/mootdx/`: unchanged upstream 0.11.7 wheel sources.
- `backend/requirements.txt`: install local mootdx plus locked MCP dependencies and explicit multipart/YAML dependencies.
- `backend/agent/ssrf.py`: side-effect-free shared URL/address policy used by model and MCP clients.
- `backend/agent/skills.py`: Skill models, scan/generation, safe resource resolution, importer/recovery, and two read-only runtime tools.
- `backend/agent/mcp.py`: MCP models/store, trust, transports, sessions/generations, redaction, catalog, aliases, and stable bindings.
- `backend/agent/capabilities.py`: immutable capability preview/lease, resolver, allowance registry, argument guard, and HITL policy factory.
- `backend/agent/models.py`: computed thread detail fields remain transport-only; persisted schema stays version 1.
- `backend/agent/tool_registry.py`: narrow composition of built-in, Skill, and later MCP bindings.
- `backend/agent/runtime.py`: capability catalog system context plus request-scoped middleware factory on start/resume.
- `backend/agent/runs.py`: two-phase admission, lease ownership/release, resume allowance ordering, steer-away, delete, and shutdown.
- `backend/agent/protocol.py`: MCP interrupt metadata and strict approval identity validation.
- `backend/agent/router.py`: Skill/MCP REST endpoints, multipart streaming, error mapping, computed `resume_available`, and `/run` wiring.
- `backend/app.py`: preserve the existing lifespan and call expanded Agent startup/shutdown hooks only.

### Backend tests

- `backend/tests/agent/test_dependency_compat.py`: vendored mootdx hashes/API surface and locked dependency imports.
- `backend/tests/test_live.py`: serial live release smokes for the real kline/finance routes and direct `Quotes.F10` contract.
- `backend/tests/agent/test_skills.py`: scan, manifest, resource, snapshot, progressive loading, and active-use rules.
- `backend/tests/agent/test_skill_import.py`: zip limits, atomic replace, crash recovery, and cleanup.
- `backend/tests/agent/test_skill_api.py`: Skill REST, asset headers, thread selection CAS, and preflight failures.
- `backend/tests/agent/test_ssrf.py`: unchanged model SSRF behavior plus MCP local/public rules.
- `backend/tests/agent/test_mcp_config.py`: schema, atomic store, revision, corruption, alias, trust, and secret references.
- `backend/tests/agent/fake_mcp_server.py`: deterministic stdio/Streamable HTTP MCP fixture.
- `backend/tests/agent/test_mcp_registry.py`: transports, discovery, generations, concurrency, cancellation, shutdown, timeout, and redaction.
- `backend/tests/agent/test_mcp_api.py`: MCP management REST and slice-2 Graph non-exposure.
- `backend/tests/agent/test_capabilities.py`: resolver, lease, fail-closed admission, object graph, and exactly-once release.
- `backend/tests/agent/test_mcp_approval.py`: guard, exhaustive HITL, metadata, approval, allowances, recovery, and steer-away.
- Existing `test_models.py`, `test_router.py`, `test_resume_contract.py`, `test_run_persistence.py`, `test_thread_api.py`, and `fakes.py`: migrate existing fixtures in the same task as each signature change.

### Frontend

- `frontend/src/lib/agent/types.ts`: Skill/MCP REST types, computed `resume_available`, and custom interrupt metadata.
- `frontend/src/lib/agent/api.ts`: JSON and multipart Skill/MCP management calls with structured errors.
- `frontend/src/lib/agent/history.ts`: restore actionable interrupts only when `resume_available` is true.
- `frontend/src/lib/agent/approval.ts`: the sole wrapper for version-sensitive approval/steer-away hooks and scoped decisions.
- `frontend/src/lib/agent/runtime.tsx`: surface pre-stream 503 separately from 409 and preserve request-scoped model key behavior.
- `frontend/src/components/agent/CapabilityBar.tsx`: compact capability summary and commands.
- `frontend/src/components/agent/CapabilityManagerDialog.tsx`: modal/sheet shell and one-submit draft lifecycle.
- `frontend/src/components/agent/SkillManager.tsx`: Skill list/detail/import/resource preview and selection.
- `frontend/src/components/agent/McpManager.tsx`: server/transport/trust/test/refresh/tool controls.
- `frontend/src/components/agent/ApprovalPanel.tsx`: all-interrupt decision form.
- `frontend/src/components/agent/SteerAwayComposer.tsx`: pending-only new-message path.
- `frontend/src/components/agent/AgentThread.tsx`, `frontend/src/pages/Agent.tsx`: assemble 1C without introducing the final 1D layout.
- Matching colocated `*.test.tsx` plus existing `history.test.tsx`, `runtime.test.tsx`, `approval.contract.test.ts`, and `Agent.test.tsx`.

## Commit Gates

Focused commands establish each red/green cycle. Before every backend task commit run:

```bash
cd backend && .venv/bin/pytest -m "not live"
```

Before every frontend task commit run:

```bash
cd frontend && npm test && npx vitest run && npm run build
```

At the end of each slice run both gates plus `git diff --check`. Stage only the files listed by that task; never use directory-wide `git add` for `backend/vendor`, `backend/agent`, or `frontend/src`.

## Slice 1: Skill Discovery, Import, Progressive Loading

### Task 1: Resolve The mootdx/MCP Dependency Conflict

**Files:**
- Create: `backend/vendor/mootdx_compat/pyproject.toml`
- Create: `backend/vendor/mootdx_compat/LICENSE`
- Create: `backend/vendor/mootdx_compat/SOURCE.md`
- Create: `backend/vendor/mootdx_compat/upstream.sha256`
- Create unchanged upstream files under `backend/vendor/mootdx_compat/src/mootdx/`: `__init__.py`, `__main__.py`, `affair.py`, `config.py`, `consts.py`, `exceptions.py`, `logger.py`, `parse.py`, `quotes.py`, `reader.py`, `server.py`, `version.py`, `cache/__init__.py`, `cache/compat.py`, `cache/file.py`, `cache/timed.py`, `cache/timer.py`, `contrib/__init__.py`, `contrib/adjust.py`, `contrib/compat.py`, `financial/__init__.py`, `financial/base.py`, `financial/columns.py`, `financial/financial.py`, `tools/__init__.py`, `tools/DownloadTDXCaiWu.py`, `tools/customize.py`, `tools/reversion.py`, `tools/tdx2csv.py`, `utils/__init__.py`, `utils/adjust.py`, `utils/demjson.py`, `utils/factor.py`, `utils/holiday.js`, `utils/holiday.py`, `utils/pandas_cache.py`, `utils/timer.py`
- Modify: `backend/requirements.txt`
- Create: `backend/tests/agent/test_dependency_compat.py`
- Modify: `backend/tests/test_live.py`

- [ ] **Step 1: Write failing dependency and upstream-integrity tests**

Create tests that require the local version, locked versions, byte hashes, and offline `Quotes` APIs:

```python
def test_locked_mcp_stack_and_local_mootdx_are_importable():
    assert version("mootdx") == "0.11.7+vr1"
    assert version("langchain-mcp-adapters") == "0.3.2"
    assert version("mcp") == "1.26.0"
    assert version("httpx") == "0.28.1"


def test_vendored_python_sources_match_upstream_manifest():
    root = Path(__file__).parents[2] / "vendor/mootdx_compat/src/mootdx"
    expected = parse_manifest(root.parents[1] / "upstream.sha256")
    actual = {p.relative_to(root).as_posix(): sha256(p.read_bytes()).hexdigest()
              for p in root.rglob("*") if p.is_file() and p.suffix in {".py", ".js"}}
    assert actual == expected


def test_mootdx_factory_bars_finance_and_f10_use_offline_tdx_fake(monkeypatch):
    monkeypatch.setattr("mootdx.quotes.TdxHq_API", FakeTdxHqApi)
    quotes = Quotes.factory(market="std", server=("127.0.0.1", 7709))
    assert not quotes.bars("600519", offset=1).empty
    assert not quotes.finance("600519").empty
    assert quotes.F10C("600519")[0]["name"] == "公司概况"
    assert "公司概况" in quotes.F10("600519")
```

`FakeTdxHqApi` must implement `connect`, `get_security_bars`, `get_finance_info`, `get_company_info_category`, and `get_company_info_content` with deterministic local data; it must not open a socket.

Also add three explicitly named `@pytest.mark.live` release smokes to `backend/tests/test_live.py`: `test_mootdx_kline_route_live`, `test_mootdx_finance_route_live`, and `test_mootdx_f10_live`. The first two call the real `/api/kline` and `/api/finance` routes through `TestClient`; the third calls `Quotes.factory(market="std").F10(CODE)` directly and asserts the returned mapping shape when data is available. Keep them serial and shape-oriented. Do not add an F10 HTTP route, and do not run these tests in the offline gate.

- [ ] **Step 2: Run the focused test and verify it fails**

Run: `cd backend && .venv/bin/pytest tests/agent/test_dependency_compat.py -q`

Expected: FAIL because the local distribution, manifest, and MCP packages do not exist.

- [ ] **Step 3: Vendor the exact official 0.11.7 wheel payload**

Use the configured Tsinghua mirror and an isolated temp directory:

```bash
VR_MOOTDX_TMP=$(mktemp -d)
python -m pip download mootdx==0.11.7 --no-deps -d "$VR_MOOTDX_TMP" -i https://pypi.tuna.tsinghua.edu.cn/simple
python -m zipfile -e "$VR_MOOTDX_TMP/mootdx-0.11.7-py3-none-any.whl" "$VR_MOOTDX_TMP/unpacked"
```

Copy only the enumerated `mootdx/` files, the wheel `LICENSE`, and generate `upstream.sha256` over relative `.py`/`.js` paths. `SOURCE.md` must record package name/version, wheel filename, SHA-256, PyPI project URL, retrieval date, and state that only packaging metadata changed.

- [ ] **Step 4: Add the local package metadata and lock requirements**

Use this package contract:

```toml
[build-system]
requires = ["setuptools>=69", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "mootdx"
version = "0.11.7+vr1"
requires-python = ">=3.8,<4"
license = { file = "LICENSE" }
dependencies = [
  "click>=8.1.3,<9",
  "httpx>=0.27.1,<1",
  "prettytable>=3.5,<4",
  "py-mini-racer>=0.6,<0.7",
  "tdxpy>=0.2.5,<0.3",
  "tenacity>=8.1,<9",
  "tqdm",
  "typing-extensions>=4.5,<5",
]

[project.scripts]
mootdx = "mootdx.__main__:entry"

[tool.setuptools.packages.find]
where = ["src"]

[tool.setuptools.package-data]
"mootdx.utils" = ["holiday.js"]
```

Replace `mootdx>=0.10` in `requirements.txt` with `./vendor/mootdx_compat` and add exact `langchain-mcp-adapters==0.3.2`, `mcp==1.26.0`, `httpx==0.28.1`, plus explicit `PyYAML>=6,<7` and `python-multipart>=0.0.18,<1`.

- [ ] **Step 5: Verify clean installation and all offline tests**

Run:

```bash
VR_1C_VENV=$(mktemp -d)/venv
python3 -m venv "$VR_1C_VENV"
cd backend && "$VR_1C_VENV/bin/pip" install -r requirements.txt
"$VR_1C_VENV/bin/pip" check
"$VR_1C_VENV/bin/python" -c 'import httpx, mcp, mootdx, langchain_mcp_adapters; print(mootdx.__version__, httpx.__version__)'
.venv/bin/pip install -r requirements.txt
.venv/bin/pip check
.venv/bin/pytest tests/agent/test_dependency_compat.py -q
.venv/bin/pytest -m "not live"
```

Expected: both `pip check` commands pass, the clean install contains one `mootdx` distribution, output contains `0.11.7 0.28.1`, and all tests PASS. The fresh venv is the dependency-resolution authority; an existing `.venv` may contain stale or out-of-band packages and must be recreated before continuing if its install, exact-version assertion, or `pip check` disagrees with the fresh environment.

- [ ] **Step 6: Commit**

```bash
sed 's/^[0-9a-f]\{64\}  /backend\/vendor\/mootdx_compat\/src\/mootdx\//' backend/vendor/mootdx_compat/upstream.sha256 | git add --pathspec-from-file=-
git add backend/requirements.txt backend/vendor/mootdx_compat/pyproject.toml backend/vendor/mootdx_compat/LICENSE backend/vendor/mootdx_compat/SOURCE.md backend/vendor/mootdx_compat/upstream.sha256 backend/tests/agent/test_dependency_compat.py backend/tests/test_live.py
git commit -m "build(agent): add compatible mootdx and MCP stack"
```

### Task 2: Build The Skill Registry And Safe Manifest

**Files:**
- Create: `backend/agent/skills.py`
- Create: `backend/tests/agent/test_skills.py`
- Modify: `backend/agent/stores.py`

- [ ] **Step 1: Write failing scan, collision, manifest, and resource tests**

Cover valid/invalid frontmatter, custom YAML tags, size limits, duplicate normalized names, symlink/special-file rejection, NFC/casefold path collision, MIME signatures, scripts/other denial, path traversal, error redaction, stable digest, and monotonic generation. Use only `tmp_path`:

```python
def test_duplicate_normalized_names_invalidate_both(tmp_path):
    write_skill(tmp_path / "one", name="cash-flow", description="A")
    write_skill(tmp_path / "two", name="CASH-FLOW", description="B")
    generation = SkillRegistry(tmp_path).refresh()
    assert [item.valid for item in generation.skills] == [False, False]
    assert {item.error_code for item in generation.skills} == {"SKILL_INVALID"}


def test_manifest_never_follows_symlink_or_exposes_scripts(tmp_path):
    root = write_skill(tmp_path / "safe", name="safe", description="safe")
    (root / "scripts").mkdir()
    (root / "scripts/run.py").write_text("print('no')", encoding="utf-8")
    (root / "references").mkdir()
    (root / "references/link").symlink_to("/etc/passwd")
    item = SkillRegistry(tmp_path).refresh().by_directory("safe")
    assert item.valid is False
    assert "/etc/passwd" not in (item.error_detail or "")
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run: `cd backend && .venv/bin/pytest tests/agent/test_skills.py -q`

Expected: FAIL because `agent.skills` does not exist.

- [ ] **Step 3: Add paths and immutable Skill types**

Extend `AgentPaths` with `skills` and define frozen public/internal models in `skills.py`:

```python
@dataclass(frozen=True)
class SkillFile:
    relative_path: str
    category: Literal["skill", "reference", "asset", "script", "other"]
    size: int
    mtime_ns: int
    sha256: str
    mime: str | None
    downloadable: bool


@dataclass(frozen=True)
class SkillRecord:
    directory: str
    name: str | None
    description: str | None
    digest: str | None
    valid: bool
    instructions: str | None
    files: tuple[SkillFile, ...]
    error_code: str | None = None
    error_detail: str | None = None


@dataclass(frozen=True)
class SkillGeneration:
    number: int
    skills: tuple[SkillRecord, ...]
```

Errors must expose stable codes `SKILL_INVALID`, `SKILL_CONFLICT`, `SKILL_IN_USE`, `SKILL_ARCHIVE_REJECTED`, `SKILL_RESOURCE_FORBIDDEN`, `SKILL_CHANGED`, and `SKILL_UNAVAILABLE` with no absolute paths.

- [ ] **Step 4: Implement deterministic non-following scan and manifest resolution**

`SkillRegistry.refresh()` must hold one registry lock, scan direct children with `os.scandir(root)`, reject `DirEntry.is_symlink()`, and use `is_dir/is_file(follow_symlinks=False)` for every type check. Validate normalized POSIX paths before opening, parse YAML with `yaml.safe_load`, validate signatures/content, compute a canonical digest without absolute paths, invalidate all duplicate names, then atomically replace the in-memory `SkillGeneration`. Expose narrow `refresh`, `current`, `list`, `require`, and `resolve_file` methods; the latter returns the current `SkillRecord`, exact manifest `SkillFile`, and resolved `Path` for a validated name/path pair.

`resolve_file` must resolve by current record and exact manifest key, never concatenate untrusted route text.

- [ ] **Step 5: Run focused and full backend tests**

Run:

```bash
cd backend && .venv/bin/pytest tests/agent/test_skills.py -q
.venv/bin/pytest -m "not live"
```

Expected: PASS; no file outside `tmp_path` is opened.

- [ ] **Step 6: Commit**

```bash
git add backend/agent/skills.py backend/agent/stores.py backend/tests/agent/test_skills.py
git commit -m "feat(agent): add safe skill registry"
```

### Task 3: Add Atomic Skill Import, Overwrite, Delete, And Recovery

**Files:**
- Modify: `backend/agent/skills.py`
- Create: `backend/tests/agent/test_skill_import.py`

- [ ] **Step 1: Write failing archive and crash-state tests**

Generate zips in memory and cover 20 MB upload, 500 entries, 50 MB declared/actual extraction, encrypted/symlink/special modes, absolute/backslash/NFC collisions, zip-slip, both accepted root layouts, conflict/digest CAS, and every startup recovery shape. Active-use protection is added in Task 5 after capability leases exist:

```python
def test_overwrite_requires_current_digest_and_recovers_backup(tmp_path):
    registry = SkillRegistry(tmp_path)
    importer = SkillImporter(tmp_path, registry)
    install_skill(tmp_path, "quality", body="old")
    digest = registry.refresh().require("quality").digest
    archive = write_skill_zip(tmp_path / "quality.zip", name="quality", body="new")
    with pytest.raises(SkillConflict):
        importer.install(archive, overwrite=True,
                         expected_digest="stale")
    importer.install(archive, overwrite=True,
                     expected_digest=digest)
    assert registry.current().require("quality").instructions.endswith("new")
    assert not list(tmp_path.glob(".skill-backup-*.tmp"))
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run: `cd backend && .venv/bin/pytest tests/agent/test_skill_import.py -q`

Expected: FAIL because `SkillImporter` and recovery do not exist.

- [ ] **Step 3: Implement bounded upload and entry-by-entry extraction**

Add constants `UPLOAD_LIMIT = 20 * 1024 * 1024`, `EXTRACT_LIMIT = 50 * 1024 * 1024`, and `ENTRY_LIMIT = 500`. Implement `SkillImporter.receive(upload)` for bounded staging, `install(upload_path, *, overwrite, expected_digest)` for installation, `delete(name, expected_digest)` for CAS deletion, and `recover()` for startup cleanup/recovery.

`receive` writes fixed chunks and deletes on overflow. `install` must inspect `ZipInfo`, copy each file through a remaining-byte counter, validate the staged payload with the same registry scanner, `fsync` files/directories, use `os.replace`, and never call `extractall`.

- [ ] **Step 4: Implement exact recovery ownership rules**

Only names matching `.skill-upload-<uuid>.tmp`, `.skill-import-<uuid>.tmp`, and `.skill-backup-<uuid>.tmp` are owned. Recover target/backup pairs exactly as the spec states; ambiguous shapes remain and emit relative-name warnings. Never recursively delete non-owned or non-empty MCP directories.

- [ ] **Step 5: Run focused and full backend tests**

Run: `cd backend && .venv/bin/pytest tests/agent/test_skill_import.py tests/agent/test_skills.py -q && .venv/bin/pytest -m "not live"`

Expected: PASS with no stage/backup residue after successful cases.

- [ ] **Step 6: Commit**

```bash
git add backend/agent/skills.py backend/tests/agent/test_skill_import.py
git commit -m "feat(agent): import and recover skills atomically"
```

### Task 4: Expose Skill REST And Revision-Safe Thread Selection

**Files:**
- Modify: `backend/agent/router.py`
- Modify: `backend/agent/runs.py`
- Modify: `backend/tests/agent/test_thread_api.py`
- Create: `backend/tests/agent/test_skill_api.py`

- [ ] **Step 1: Write failing REST and selection tests**

Cover all six Skill routes, multipart cleanup, safe asset headers, script/other fixed 403, invalid/missing records, digest conflict, and one CAS update for `selected_skills`. Active-lease conflicts and computed `resume_available` belong to Tasks 5 and 13 respectively.

```python
def test_patch_selected_skills_uses_one_revision_and_rejects_missing(api):
    thread = api.post("/api/agent/threads", json={"title": "研究"}).json()
    response = api.patch(f"/api/agent/threads/{thread['id']}", json={
        "revision": thread["revision"], "selected_skills": ["quality"],
    })
    assert response.status_code == 200
    assert response.json()["selected_skills"] == ["quality"]
    assert response.json()["revision"] == thread["revision"] + 1
```

- [ ] **Step 2: Run tests and verify they fail**

Run: `cd backend && .venv/bin/pytest tests/agent/test_skill_api.py tests/agent/test_thread_api.py -q`

Expected: FAIL because services/routes/patch shape do not include Skills.

- [ ] **Step 3: Inject Skill services without import-time real-data access**

Extend `AgentServices` and `build_services(root)` with `SkillRegistry` and `SkillImporter` rooted at `paths.skills`. Keep module-level `services` replaceable so every `TestClient` monkeypatches a temp-root instance before lifespan. Startup calls `importer.recover()` and `skills.refresh()` via `asyncio.to_thread`; it must not inspect the default data root in tests.

- [ ] **Step 4: Add strict patch and Skill endpoints**

Use one patch model and one coordinator CAS:

```python
class ThreadPatch(BaseModel):
    revision: int = Field(ge=0)
    title: str | None = Field(default=None, min_length=1)
    selected_skills: list[str] | None = None

    @model_validator(mode="after")
    def require_change(self):
        if self.title is None and self.selected_skills is None:
            raise ValueError("至少提交 title 或 selected_skills")
        return self
```

Validate and deduplicate selected names against one current generation before calling `RunCoordinator.patch_thread`; recheck the same generation under the thread lock before write. Route file responses from the validated manifest and set `nosniff`, restrictive sandbox CSP, and safe `Content-Disposition`. Run Registry scan/import/delete and file hashing through `asyncio.to_thread`; multipart chunk receipt remains async.

- [ ] **Step 5: Run focused and full tests**

Run: `cd backend && .venv/bin/pytest tests/agent/test_skill_api.py tests/agent/test_thread_api.py -q && .venv/bin/pytest -m "not live"`

Expected: PASS; existing title-only PATCH remains compatible.

- [ ] **Step 6: Commit**

```bash
git add backend/agent/router.py backend/agent/runs.py backend/tests/agent/test_thread_api.py backend/tests/agent/test_skill_api.py
git commit -m "feat(agent): expose skill management APIs"
```

### Task 5: Add Skill Snapshots, Progressive Tools, And Two-Phase Admission

**Files:**
- Create: `backend/agent/capabilities.py`
- Modify: `backend/agent/skills.py`
- Modify: `backend/agent/tool_registry.py`
- Modify: `backend/agent/runtime.py`
- Modify: `backend/agent/runs.py`
- Modify: `backend/agent/router.py`
- Modify: `backend/tests/agent/test_skills.py`
- Modify: `backend/tests/agent/test_skill_api.py`
- Create: `backend/tests/agent/test_capabilities.py`
- Modify: `backend/tests/agent/test_run_persistence.py`
- Modify: `backend/tests/agent/test_router.py`
- Modify: `backend/tests/agent/test_resume_contract.py`

- [ ] **Step 1: Write failing progressive-loading and admission-race tests**

Assert system context includes only selected name/description after the unchanged neutrality prompt, `load_skill` returns cached instructions, `read_skill_resource` checks the current file hash, scripts are absent, selected missing Skills fail before user/run writes, preview/final revision or selection races return 409, active selected Skills cannot be overwritten/deleted, and every failed acquisition releases its lease.

```python
async def test_selected_skill_disappears_before_final_admission_without_writes(tmp_services):
    previewed = asyncio.Event()
    resolver = PausingCapabilityResolver(tmp_services.skills, previewed)
    task = asyncio.create_task(start_run(tmp_services, resolver, selected=["quality"]))
    await previewed.wait()
    remove_skill(tmp_services.paths.skills / "quality")
    resolver.resume()
    with pytest.raises(SkillUnavailable):
        await task
    assert tmp_services.runs.list_documents() == []
    assert tmp_services.threads.get("th-1").messages == []
```

- [ ] **Step 2: Run focused tests and verify they fail**

Run: `cd backend && .venv/bin/pytest tests/agent/test_capabilities.py tests/agent/test_skills.py tests/agent/test_skill_api.py -q`

Expected: FAIL because snapshots/resolver/lease do not exist.

- [ ] **Step 3: Implement immutable Skill runtime and two tools**

Add secret-free snapshot types and tools whose closures capture only the snapshot and registry path resolver:

```python
@dataclass(frozen=True)
class SkillRuntimeItem:
    name: str
    description: str
    digest: str
    instructions: str
    files: tuple[SkillFile, ...]


@dataclass(frozen=True)
class SkillRuntimeSnapshot:
    generation: int
    items: tuple[SkillRuntimeItem, ...]
```

Give the snapshot a deterministic catalog renderer and a builder for exactly two `BaseTool` instances. `read_skill_resource` must use the snapshot manifest, reopen only the exact current reference, recompute SHA-256, and return `SKILL_CHANGED` instead of changed content.

- [ ] **Step 4: Implement the slice-1 capability lease and two-phase coordinator sequence**

Define the production interfaces now so slice 3 extends rather than replaces them:

```python
@dataclass(frozen=True)
class CapabilityPreview:
    thread_id: str
    thread_revision: int
    selected_skills: tuple[str, ...]


class CapabilityLease:
    tools: tuple[BaseTool, ...]
    system_context: str
    skill_digests: tuple[tuple[str, str], ...]
    def build_request_middleware(self, secrets: RunSecrets) -> tuple[AgentMiddleware, ...]:
        return ()
```

`CapabilityLease.aclose()` is idempotent and performs exactly one underlying release. `CapabilityResolver.acquire(preview)` returns that lease asynchronously. For start/retry/steer-away: acquire the thread lock for duplicate/busy/revision/head preview, release it, acquire the capability lease, reacquire the same thread lock, repeat the full checks and compare preview facts, then persist and build. Resume reuses the handle lease. Add `capability_lease` to `ActiveRunHandle`, release it on all terminal/build-failure/cancel/steer/shutdown paths, and keep old 1B fixtures green by migrating their helper to a deterministic resolver in this same task.

Track selected Skill identities/digests in active leases. Skill overwrite/delete endpoints must ask the coordinator for active use and return `SKILL_IN_USE` as a structured 409 before changing disk state; read-only refresh remains allowed.

- [ ] **Step 5: Rebuild the Graph from catalog context without storing secrets**

Change `AgentFactory.create` to accept `system_context` plus a secret-free `middleware_factory: Callable[[RunSecrets], tuple[AgentMiddleware, ...]]`; `AgentFactory.resume` reuses both values from `RuntimeHandle`. The coordinator passes `lease.system_context` and the bound method `lease.build_request_middleware`, and the factory constructs each request Graph as:

```python
system_prompt = chat.SYSTEM_PROMPT.format(context="Agent 工作台") + system_context
middleware = middleware_factory(secrets)
```

`RuntimeHandle` stores tools, checkpointer, system context, and the middleware factory, never the built request middleware or model key. Resume validates `ModelRef` before consuming the new key, then calls the stored factory with the new `RunSecrets`.

- [ ] **Step 6: Run focused and full backend tests**

Run: `cd backend && .venv/bin/pytest tests/agent/test_capabilities.py tests/agent/test_run_persistence.py tests/agent/test_resume_contract.py tests/agent/test_router.py -q && .venv/bin/pytest -m "not live"`

Expected: PASS; all 1A/1B duplicate/retry/cancel tests remain green.

- [ ] **Step 7: Commit**

```bash
git add backend/agent/capabilities.py backend/agent/skills.py backend/agent/tool_registry.py backend/agent/runtime.py backend/agent/runs.py backend/agent/router.py backend/tests/agent/test_capabilities.py backend/tests/agent/test_skills.py backend/tests/agent/test_skill_api.py backend/tests/agent/test_run_persistence.py backend/tests/agent/test_router.py backend/tests/agent/test_resume_contract.py
git commit -m "feat(agent): admit immutable skill capabilities"
```

### Task 6: Build The Skill Capability UI And Close Slice 1

**Files:**
- Modify: `frontend/src/lib/agent/types.ts`
- Modify: `frontend/src/lib/agent/api.ts`
- Modify: `frontend/src/lib/agent/history.ts`
- Modify: `frontend/src/lib/agent/history.test.tsx`
- Create: `frontend/src/components/agent/CapabilityBar.tsx`
- Create: `frontend/src/components/agent/CapabilityBar.test.tsx`
- Create: `frontend/src/components/agent/CapabilityManagerDialog.tsx`
- Create: `frontend/src/components/agent/CapabilityManagerDialog.test.tsx`
- Create: `frontend/src/components/agent/SkillManager.tsx`
- Create: `frontend/src/components/agent/SkillManager.test.tsx`
- Modify: `frontend/src/pages/Agent.tsx`
- Modify: `frontend/src/pages/Agent.test.tsx`

- [ ] **Step 1: Write failing types/API/component tests**

Test valid/invalid/missing states, details/manifests, import/refresh/overwrite digest confirmation, escaped text preview, authenticated asset/PDF blob fetch and object-URL cleanup, script denial, dialog draft cancellation, exactly one `patchThread` on Apply, 409 draft discard/reload, and disabled controls while running/converging.

```tsx
it("applies selected skills once with the current thread revision", async () => {
  render(<CapabilityManagerDialog open thread={thread} skills={skills} />);
  await user.click(screen.getByRole("checkbox", { name: /quality/ }));
  await user.click(screen.getByRole("button", { name: "应用到本会话" }));
  expect(api.patchThread).toHaveBeenCalledTimes(1);
  expect(api.patchThread).toHaveBeenCalledWith("th-1", 4, {
    selected_skills: ["quality"],
  });
});
```

- [ ] **Step 2: Run Vitest and verify it fails**

Run: `cd frontend && npx vitest run src/components/agent/CapabilityBar.test.tsx src/components/agent/CapabilityManagerDialog.test.tsx src/components/agent/SkillManager.test.tsx src/pages/Agent.test.tsx`

Expected: FAIL because the types, API calls, and components do not exist.

- [ ] **Step 3: Add exact REST types and multipart API calls**

Add `SkillSummary`, `SkillDetail`, `SkillFile`, `SkillImportResult`, and `SkillRecoveryWarning`. Change `patchThread` to accept `{title?: string; selected_skills?: string[]}` while always adding the revision. In the same step migrate `ThreadHistoryController.rename()` and its test from the positional title argument to `{title}`. Add list/detail/import/refresh/delete calls plus an authenticated file fetch returning a validated `Blob`; multipart upload must omit manual `Content-Type` so the browser supplies its boundary.

- [ ] **Step 4: Implement the compact capability UI**

Use Lucide `Settings`, `Upload`, `RefreshCw`, and `Trash2` icon buttons with tooltips. Keep the bar unframed inside the existing workspace section; use one modal on desktop and full-screen sheet on mobile, no nested cards. Text/JSON/Markdown previews render escaped `<pre>` content. Image/PDF previews use only object URLs created from the authenticated, backend-validated blob and revoke them on replacement, close, and unmount; never place the protected API URL directly in `src`.

- [ ] **Step 5: Integrate page state and conflicts**

`Agent.tsx` owns dialog open/draft/loading state. Applying calls one thread PATCH then reloads; 409 discards draft and reloads once. Capability mutation/import controls are disabled for `running`, `awaiting_approval`, and converging states without disabling read-only detail viewing.

- [ ] **Step 6: Run the slice-1 gate**

Run:

```bash
cd frontend && npm test && npx vitest run && npm run build
cd ../backend && .venv/bin/pytest -m "not live"
cd .. && git diff --check
```

Expected: all PASS; the Agent page still performs a built-in-tool run with no MCP package configured.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/lib/agent/types.ts frontend/src/lib/agent/api.ts frontend/src/lib/agent/history.ts frontend/src/lib/agent/history.test.tsx frontend/src/components/agent/CapabilityBar.tsx frontend/src/components/agent/CapabilityBar.test.tsx frontend/src/components/agent/CapabilityManagerDialog.tsx frontend/src/components/agent/CapabilityManagerDialog.test.tsx frontend/src/components/agent/SkillManager.tsx frontend/src/components/agent/SkillManager.test.tsx frontend/src/pages/Agent.tsx frontend/src/pages/Agent.test.tsx
git commit -m "feat(agent): manage session skills in workspace"
```

## Slice 2: MCP Configuration, Connection, Catalog (No Graph Exposure)

### Task 7: Add MCP Config Models, Atomic Store, Aliases, And Shared SSRF

**Files:**
- Create: `backend/agent/ssrf.py`
- Create: `backend/agent/mcp.py`
- Modify: `backend/chat.py`
- Modify: `backend/agent/stores.py`
- Create: `backend/tests/agent/test_ssrf.py`
- Create: `backend/tests/agent/test_mcp_config.py`
- Modify: `backend/tests/test_reports_and_security.py`

- [ ] **Step 1: Write failing schema/store/SSRF/alias tests**

Cover schema version, revision CAS, corruption preservation, stdio/HTTP discriminated configs, ID/name/env/header constraints, forbidden headers/query/userinfo/fragment, raw secrets, alias normalization/collision/64-char truncation, enabled inheritance, and unchanged model Base URL behavior.

```python
def test_alias_is_stable_and_bounded_for_unicode_tool_name():
    first = mcp_alias("finance", "查询 现金流/年度")
    second = mcp_alias("finance", "查询 现金流/年度")
    assert first == second
    assert first.startswith("mcp__finance__")
    assert len(first) <= 64
```

- [ ] **Step 2: Run focused tests and verify they fail**

Run: `cd backend && .venv/bin/pytest tests/agent/test_ssrf.py tests/agent/test_mcp_config.py -q`

Expected: FAIL because shared SSRF and MCP store do not exist.

- [ ] **Step 3: Extract side-effect-free SSRF policy**

Move address parsing/resolution policy from `chat.py` into `agent/ssrf.py` without importing `app.py` or starting schedulers. Expose `validate_outbound_url(url: str, *, public_mode: bool, require_public_https: bool, allow_query: bool, allow_userinfo: bool) -> ParseResult`.

`chat._check_base_url` remains as a compatibility wrapper using the shared helper and preserves current messages/tests. MCP calls it with query/userinfo/fragment forbidden and public HTTPS required.

- [ ] **Step 4: Implement strict MCP document models and store**

Define `McpDocument`, `McpServer`, `StdioTransport`, `StreamableHttpTransport`, `EnvReference`, `McpToolCatalogEntry`, and `McpHealth` with `extra="forbid"`. `McpConfigStore` uses the existing `atomic_write_json`, one `RLock`, whole-document revision CAS, corrupt-file preservation, and returns immutable copies. Add `AgentPaths.mcp_config` and `mcp_work`; async callers run JSON load/write methods through `asyncio.to_thread` while transport/session work remains native async.

- [ ] **Step 5: Implement deterministic alias generation**

Normalize the original tool name to lowercase ASCII `[a-z0-9_]`, collapse separators, prefix `mcp__<server_id>__`, and when empty/colliding/over 64 append `-<sha256[:10]>` after truncating. Refresh uses `(server_id, original_tool_name)` as identity and preserves enabled only for the exact pair.

- [ ] **Step 6: Run focused and full tests**

Run: `cd backend && .venv/bin/pytest tests/agent/test_ssrf.py tests/agent/test_mcp_config.py tests/test_reports_and_security.py -q && .venv/bin/pytest -m "not live"`

Expected: PASS; existing local/public model endpoint behavior is unchanged.

- [ ] **Step 7: Commit**

```bash
git add backend/agent/ssrf.py backend/agent/mcp.py backend/agent/stores.py backend/chat.py backend/tests/agent/test_ssrf.py backend/tests/agent/test_mcp_config.py backend/tests/test_reports_and_security.py
git commit -m "feat(agent): add durable MCP configuration"
```

### Task 8: Add Stdio Trust And Deterministic MCP Test Server

**Files:**
- Modify: `backend/agent/mcp.py`
- Create: `backend/tests/agent/fake_mcp_server.py`
- Create: `backend/tests/agent/test_mcp_registry.py`

- [ ] **Step 1: Write failing stdio trust/process tests**

Test that add does not spawn, test/refresh/enable require trust, fingerprint includes resolved executable and exact args, PATH/executable/args changes invalidate trust, env values resolve only at connection time, `shell=False`, fixed cwd, missing env prevents spawn, empty work directories are removed while non-empty ones are retained with a relative recovery warning, and stubborn child shutdown reaches terminate then kill with no orphan.

```python
async def test_stdio_add_never_spawns_before_matching_trust(tmp_path, monkeypatch):
    registry = registry_for(tmp_path)
    await registry.add(stdio_server(args=[fixture_script]))
    assert registry.process_count == 0
    with pytest.raises(StdioTrustRequired) as exc:
        await registry.test("fixture")
    assert exc.value.preview.args == [fixture_script]
    assert registry.process_count == 0
```

- [ ] **Step 2: Run focused tests and verify they fail**

Run: `cd backend && .venv/bin/pytest tests/agent/test_mcp_registry.py -q -k stdio`

Expected: FAIL because Registry/process/trust APIs do not exist.

- [ ] **Step 3: Add the deterministic official-SDK fake server**

Use `mcp.server.fastmcp.FastMCP` with tools `echo`, `echo_secret`, `sleep`, `fail`, `large`, and `unsupported`; accept CLI flags for stdio or streamable HTTP. It writes process lifecycle diagnostics only to stderr/a test-owned file, never stdout in stdio mode:

```python
mcp = FastMCP("vr-1c-fixture")

@mcp.tool()
async def echo(value: str) -> str:
    return value

@mcp.tool()
async def echo_secret(value: str) -> str:
    return f"secret={value}"
```

- [ ] **Step 4: Implement trust resolution and stdio session creation**

`McpRegistry` resolves `shutil.which`/absolute executable, computes canonical fingerprint, creates `mcp-work/<id>`, combines `get_default_environment()` with resolved refs, and constructs `StdioServerParameters` from the resolved command, exact args, resolved environment, and fixed work directory. Enter `stdio_client(parameters)` plus `ClientSession` through a Registry-owned `AsyncExitStack`. Never use a shell or user cwd.

- [ ] **Step 5: Preserve the SDK bounded close contract**

Do not replace MCP 1.26.0's context-manager shutdown. Add a test-owned stubborn subprocess fixture proving stdin close, two-second wait, process-tree terminate, second wait, then kill is bounded on the current platform. Registry cleanup runs in its own protected task, not the cancelled request scope.

- [ ] **Step 6: Run focused and full tests**

Run: `cd backend && .venv/bin/pytest tests/agent/test_mcp_registry.py -q -k stdio && .venv/bin/pytest -m "not live"`

Expected: PASS; no fixture process remains after pytest.

- [ ] **Step 7: Commit**

```bash
git add backend/agent/mcp.py backend/tests/agent/fake_mcp_server.py backend/tests/agent/test_mcp_registry.py
git commit -m "feat(agent): connect trusted stdio MCP servers"
```

### Task 9: Add Streamable HTTP, Catalog Refresh, Session Generations, And Redaction

**Files:**
- Modify: `backend/agent/mcp.py`
- Modify: `backend/tests/agent/test_mcp_registry.py`
- Modify: `backend/tests/agent/fake_mcp_server.py`

- [ ] **Step 1: Write failing HTTP/catalog/lifecycle tests**

Cover local/public SSRF, metadata/link-local, redirect refusal, forbidden headers, 15-second admission, 60-second read, official `load_mcp_tools` with the active session, catalog/schema/count limits, enabled inheritance, non-text rejection, `isError`, health redaction-before-truncation, same-server serial/different-server parallel, config/session generations, and shutdown.

- [ ] **Step 2: Run focused tests and verify they fail**

Run: `cd backend && .venv/bin/pytest tests/agent/test_mcp_registry.py -q -k 'http or catalog or generation or redact'`

Expected: FAIL because HTTP and catalog/session behavior is incomplete.

- [ ] **Step 3: Implement Streamable HTTP with a no-redirect client**

Create the SDK session with a custom factory that returns `httpx.AsyncClient` configured with `follow_redirects=False`, `trust_env=False`, resolved headers, a 15-second connect timeout, and a 60-second read timeout. Validate URL/address immediately before connection; missing secrets must result in zero HTTP requests.

- [ ] **Step 4: Discover, sanitize, bound, and persist the catalog**

Initialize `ClientSession` and call `load_mcp_tools(session=session, server_name=server_id)` to obtain the official adapter Tools. Treat `tool_interceptors`, if used, as request-side hooks only; they are not the response-redaction security boundary. Recursively redact descriptions/schema/health, reject unsupported content, enforce the design's count/schema/catalog limits, assign aliases, preserve enabled by original identity, and write one revision update. Wrap official Tool invocation at the Registry/binding boundary so every result and exception is recursively redacted before supported-content normalization, encoding, and truncation to 6,000 characters in that order. Health strings are redacted before 500-character truncation. Tests must make an official adapter Tool return a fixture secret and prove the Registry returns only `[redacted]` without relying on an interceptor to rewrite `CallToolResult`.

- [ ] **Step 5: Implement management-session generations**

Represent each cached session with monotonic `generation`, `state: Literal["accepting", "draining", "closed"]`, `in_flight`, `AsyncExitStack`, official tools by original name, and a protected close task. A Registry state lock serializes accepting-to-draining transitions, reference acquisition/release, and zero-reference close scheduling. Test/refresh/config changes mark the prior generation draining and close at zero references. Keep capability config/catalog generations distinct from the MCP document revision: a health-only write increments document revision but does not invalidate an otherwise compatible generation. Slice 2 may exercise management calls only; do not expose bindings through `CapabilityResolver` or `AgentFactory` yet.

- [ ] **Step 6: Run focused and full tests**

Run: `cd backend && .venv/bin/pytest tests/agent/test_mcp_registry.py -q && .venv/bin/pytest -m "not live"`

Expected: PASS; all serialized/logged fixture outputs contain `[redacted]`, never the fixture secret.

- [ ] **Step 7: Commit**

```bash
git add backend/agent/mcp.py backend/tests/agent/test_mcp_registry.py backend/tests/agent/fake_mcp_server.py
git commit -m "feat(agent): discover and manage MCP catalogs"
```

### Task 10: Expose MCP REST, Management UI, And Close Slice 2

**Files:**
- Modify: `backend/agent/router.py`
- Modify: `backend/agent/runs.py`
- Create: `backend/tests/agent/test_mcp_api.py`
- Modify: `backend/tests/agent/test_router.py`
- Modify: `frontend/src/lib/agent/types.ts`
- Modify: `frontend/src/lib/agent/api.ts`
- Create: `frontend/src/components/agent/McpManager.tsx`
- Create: `frontend/src/components/agent/McpManager.test.tsx`
- Modify: `frontend/src/components/agent/CapabilityManagerDialog.tsx`
- Modify: `frontend/src/components/agent/CapabilityManagerDialog.test.tsx`
- Modify: `frontend/src/components/agent/CapabilityBar.tsx`
- Modify: `frontend/src/pages/Agent.tsx`
- Modify: `frontend/src/pages/Agent.test.tsx`

- [ ] **Step 1: Write failing API and UI tests**

Cover the seven slice-2 MCP routes, document revision on every mutation/action, trust preview/confirmation, process-free add, test/refresh health, tool enable, corrupt document, recovery warning, full command display, env/header names without values, segmented transport controls, 409 reload, and no MCP alias in a slice-2 run. The eighth MCP-area route, allowance deletion, lands in Task 13.

```python
def test_slice_2_run_exposes_no_mcp_alias(api, fake_model):
    enable_catalog_tool(api, server="fixture", tool="echo")
    run_agent(api, fake_model)
    assert all(not name.startswith("mcp__") for name in fake_model.bound_tool_names)
```

- [ ] **Step 2: Run backend/frontend focused tests and verify they fail**

Run:

```bash
cd backend && .venv/bin/pytest tests/agent/test_mcp_api.py tests/agent/test_router.py -q
cd ../frontend && npx vitest run src/components/agent/McpManager.test.tsx src/components/agent/CapabilityManagerDialog.test.tsx src/pages/Agent.test.tsx
```

Expected: FAIL because endpoints/UI do not exist.

- [ ] **Step 3: Add MCP services and REST error mapping**

Inject `McpConfigStore` and `McpRegistry` into `AgentServices`; startup loads config without connecting, shutdown drains Registry after coordinator leases. Add exact routes from spec 15.2 except allowance deletion (slice 3). Map validation/secret/SSRF to 400, missing to 404, revision/trust/busy to structured 409, and admission unavailability only to 503.

- [ ] **Step 4: Implement the MCP manager**

Add/edit/delete/enable forms must use references only, never secret values. Use a segmented `stdio`/`Streamable HTTP` control, Lucide action icons with tooltips, complete trust command data, and tool checkboxes. Each mutation uses the displayed MCP document revision; a 409 discards the draft and reloads once.

- [ ] **Step 5: Prove slice-2 runtime isolation**

Keep production `CapabilityResolver` constructed without MCP runtime binding support. Add assertions at resolver, model binding, journal, and interrupt boundaries that only built-in and Skill tools exist even when `mcp.json` has enabled healthy tools.

- [ ] **Step 6: Run the slice-2 gate**

Run:

```bash
cd backend && .venv/bin/pytest -m "not live"
cd ../frontend && npm test && npx vitest run && npm run build
cd .. && git diff --check
```

Expected: all PASS; stdio/HTTP management works and MCP execution remains impossible.

- [ ] **Step 7: Commit**

```bash
git add backend/agent/router.py backend/agent/runs.py backend/tests/agent/test_mcp_api.py backend/tests/agent/test_router.py frontend/src/lib/agent/types.ts frontend/src/lib/agent/api.ts frontend/src/components/agent/McpManager.tsx frontend/src/components/agent/McpManager.test.tsx frontend/src/components/agent/CapabilityManagerDialog.tsx frontend/src/components/agent/CapabilityManagerDialog.test.tsx frontend/src/components/agent/CapabilityBar.tsx frontend/src/pages/Agent.tsx frontend/src/pages/Agent.test.tsx
git commit -m "feat(agent): manage MCP servers and catalogs"
```

## Slice 3: MCP Runtime, Approval, Allowances, Frontend Interaction

### Task 11: Add Dormant Stable MCP Bindings And Registry Invocation

**Files:**
- Modify: `backend/agent/mcp.py`
- Modify: `backend/tests/agent/test_mcp_registry.py`
- Modify: `backend/tests/agent/test_capabilities.py`

- [ ] **Step 1: Write failing binding/generation tests**

Test object graph secret/session exclusion, official-adapter metadata freezing, 60-second end-to-end queue+call budget, cross-thread cancellation, stale/reference atomicity, exactly-once release on every exception/cancellation path, successor compatibility, late-result discard, health-only revision updates without capability drift, redacted transport-error health, and different-server parallelism. Also retain an explicit assertion that the production `CapabilityResolver` still returns no MCP alias.

- [ ] **Step 2: Run focused tests and verify they fail**

Run: `cd backend && .venv/bin/pytest tests/agent/test_mcp_registry.py tests/agent/test_capabilities.py -q -k 'mcp or binding or generation'`

Expected: FAIL because immutable bindings and Registry invocation do not exist.

- [ ] **Step 3: Add immutable stable binding metadata**

Define a frozen, secret-free `McpToolBinding` with `server_id`, `original_name`, `alias`, `description`, copied `args_schema`, `config_generation`, and `catalog_generation`. Its `as_langchain_tool(registry)` method returns a `BaseTool` whose coroutine delegates to `McpRegistry.invoke(binding, arguments)`.

No adapter Tool or `ClientSession` may be a field or closure cell. Name, description, and schema are frozen from the official adapter Tool acquired from the accepting session generation, not reconstructed from persisted catalog JSON.

- [ ] **Step 4: Implement Registry invocation ordering and total timeout A**

The binding coroutine starts one 60-second end-to-end budget at binding entry, including queue wait and remote call. It waits for the per-server lock without a session reference, then under the Registry state lock atomically requires an accepting compatible generation and increments `in_flight`. After lock acquisition, it revalidates that generation before invoking the current official adapter Tool with the remaining budget and releases the reference exactly once in `finally`.

Transport failure or cancellation after entering the remote call marks the generation draining; queue timeout/cancellation acquires no reference and does not stale a session. Transport health is redacted before its health-only document update and does not change the pinned capability generation. During shutdown drain, new acquisitions return a bounded redacted `MCP_UNAVAILABLE` ToolMessage. Successor discovery must contain the pinned original tool with identical args schema or return the same bounded error and mark the successor unhealthy.

- [ ] **Step 5: Keep bindings unreachable from production Graphs**

Do not modify `CapabilityResolver`, `ToolRegistry`, `RunCoordinator`, router wiring, or `AgentFactory` in this task. Tests may construct bindings directly against the fake Registry, but an enabled healthy MCP catalog must still produce a production lease containing built-in/Skill tools only.

- [ ] **Step 6: Run focused and full tests**

Run: `cd backend && .venv/bin/pytest tests/agent/test_mcp_registry.py tests/agent/test_capabilities.py -q && .venv/bin/pytest -m "not live"`

Expected: PASS; binding lifecycle is tested directly and no MCP alias is reachable from a production Graph.

- [ ] **Step 7: Commit the dormant binding layer**

```bash
git add backend/agent/mcp.py backend/tests/agent/test_mcp_registry.py backend/tests/agent/test_capabilities.py
git commit -m "feat(agent): add dormant MCP bindings"
```

### Task 12: Add Argument Guard, Exhaustive HITL Policy, Protocol Metadata, And Allowances

**Files:**
- Modify: `backend/agent/capabilities.py`
- Modify: `backend/agent/tool_registry.py`
- Modify: `backend/agent/protocol.py`
- Modify: `backend/agent/runtime.py`
- Modify: `backend/agent/runs.py`
- Modify: `backend/agent/router.py`
- Modify: `backend/tests/agent/test_capabilities.py`
- Modify: `backend/tests/agent/test_mcp_api.py`
- Create: `backend/tests/agent/test_mcp_approval.py`
- Modify: `backend/tests/agent/test_protocol_bridge.py`
- Modify: `backend/tests/agent/test_resume_contract.py`
- Modify: `backend/tests/agent/test_router.py`
- Modify: `backend/tests/agent/test_run_persistence.py`

- [ ] **Step 1: Write failing guard/HITL/protocol tests**

Cover 65,536/65,537 UTF-8 bytes after recursive redaction, zero interrupt/server call on overflow, raw args unchanged on success, exact `interrupt_on` set equality, every enabled alias interrupting, no built-in/Skill interrupts, relevant-server fail-closed admission, all-tools-disabled non-blocking behavior, 503 before user/run writes, active MCP lease blocking config/test/refresh with `MCP_CONFIG_BUSY` while health-only tool updates remain allowed, camelCase metadata, full bridge/tool identity validation, three legal decisions, invalid/missing/duplicate IDs, multi-interrupt order, secret-free persisted/SSE metadata, missing resume key, `RUN_CONFIG_MISMATCH`, and a fresh guard/middleware object on every resume.

```python
async def test_argument_guard_boundary_and_zero_execution(fake_registry):
    accepted = call_with_encoded_args(65_536)
    rejected = call_with_encoded_args(65_537)
    await guard_response(accepted)
    with pytest.raises(McpArgumentsTooLarge):
        await guard_response(rejected)
    assert fake_registry.calls == []
    assert fake_registry.interrupts == []
```

- [ ] **Step 2: Run focused tests and verify they fail**

Run: `cd backend && .venv/bin/pytest tests/agent/test_capabilities.py tests/agent/test_mcp_api.py tests/agent/test_mcp_approval.py tests/agent/test_protocol_bridge.py tests/agent/test_resume_contract.py tests/agent/test_router.py tests/agent/test_run_persistence.py -q`

Expected: FAIL because guard/scoped approval/metadata do not exist.

- [ ] **Step 3: Implement request-scoped `McpArgumentGuard`**

Subclass `AgentMiddleware` and implement `awrap_model_call`: await the handler, normalize `AIMessage` instances from `ModelResponse`/`ExtendedModelResponse`, inspect every MCP alias call before state/HITL, and recursively redact an argument copy with the current model key plus Registry secret set. Encode that copy with `json.dumps(redacted_arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":"))`, and reject only values over 65,536 UTF-8 bytes. The guard is built per start/resume and never stored in the lease.

- [ ] **Step 4: Build an exhaustive HITL policy factory**

`AllowanceRegistry` stores only `(thread_id, server_id, original_tool_name)` in memory and provides synchronous lookup. The policy factory must assert:

```python
assert set(interrupt_on) == {binding.alias for binding in lease.mcp_bindings}
```

Each entry is an `InterruptOnConfig` with `allowed_decisions=["approve", "reject"]` and a synchronous allowance predicate keyed by `(thread_id, server_id, original_tool_name)`. Put `McpArgumentGuard` before `HumanInTheLoopMiddleware` in the middleware tuple. Built-in/Skill tools have no entries.

- [ ] **Step 5: Integrate fail-closed MCP admission only with guard and HITL available**

Extend production `CapabilityResolver` so a relevant server means a globally enabled server whose catalog has at least one enabled tool. After the duplicate/busy/revision preview and before any user/run write, connect/discover every relevant server and create bindings for its enabled subset. If any relevant server fails, release all partial references and raise a redacted `MCP_UNAVAILABLE` 503; a server with every tool disabled is not relevant.

Extend `CapabilityLease` with `mcp_bindings: tuple[McpToolBinding, ...]` and the exact config/catalog lease-release callback; it still contains no session, adapter Tool, connection config, or secret set. Only after every relevant server succeeds may the resolver return a lease containing MCP bindings. `ToolRegistry` adds their wrappers, and `AgentFactory` builds the Graph with the per-request guard followed by exhaustive HITL. Final thread checks and persistence still occur after lease acquisition. There must be no code path or test fixture that binds MCP tools without both middleware instances.

- [ ] **Step 6: Extend pending metadata and strict resume conversion**

Add secret-free `server_id`, `server_name`, `original_tool_name`, `tool_alias`, and redacted `arguments` to `PendingInterrupt`. `interrupt_payloads` emits project custom camelCase fields (`serverId`, `serverName`, `toolName`, `toolAlias`, `arguments`) beside standard IDs/schema. `AgentProtocolBridge.resume_value` validates bridge ID, tool-call ID, alias, original mapping, and only accepts approve+once, approve+thread_session, or reject+once in original order.

- [ ] **Step 7: Write allowances only after successful resume rebuild/persistence**

Return decisions plus proposed allowance identities from protocol validation without mutating the Registry. `acquire_resume` validates `ModelRef`, builds model/guard/HITL, persists the same product run back to running, then inserts thread-session allowances and finally clears pending. On any earlier failure, allowances and pending remain unchanged.

- [ ] **Step 8: Run all slice-3 focused tests and commit the production approval unit**

Run:

```bash
cd backend && .venv/bin/pytest tests/agent/test_capabilities.py tests/agent/test_mcp_api.py tests/agent/test_mcp_registry.py tests/agent/test_mcp_approval.py tests/agent/test_protocol_bridge.py tests/agent/test_resume_contract.py tests/agent/test_router.py tests/agent/test_run_persistence.py -q
.venv/bin/pytest -m "not live"
```

Expected: PASS; fail-closed admission occurs before authoritative writes and no production state exists where an MCP alias can execute without guard and HITL.

```bash
git add backend/agent/capabilities.py backend/agent/tool_registry.py backend/agent/protocol.py backend/agent/runtime.py backend/agent/runs.py backend/agent/router.py backend/tests/agent/test_capabilities.py backend/tests/agent/test_mcp_api.py backend/tests/agent/test_mcp_approval.py backend/tests/agent/test_protocol_bridge.py backend/tests/agent/test_resume_contract.py backend/tests/agent/test_router.py backend/tests/agent/test_run_persistence.py
git commit -m "feat(agent): gate MCP execution with approval"
```

### Task 13: Complete Allowance Cleanup, Recovery, Cancellation, And Steer-Away

**Files:**
- Modify: `backend/agent/capabilities.py`
- Modify: `backend/agent/runs.py`
- Modify: `backend/agent/router.py`
- Modify: `backend/agent/mcp.py`
- Modify: `backend/tests/agent/test_mcp_api.py`
- Modify: `backend/tests/agent/test_mcp_approval.py`
- Modify: `backend/tests/agent/test_run_persistence.py`
- Modify: `backend/tests/agent/test_thread_api.py`

- [ ] **Step 1: Write failing lifecycle/recovery tests**

Cover approve once, thread-session bypass, reject continuation, clear endpoint/count, thread delete, server/tool change, shutdown/restart, `resume_available`, reload with/without active handle, steer-away zero old tool execution and new lease admission, stop/disconnect stale session, and late result exclusion from Graph/SSE/journal.

- [ ] **Step 2: Run focused tests and verify they fail**

Run: `cd backend && .venv/bin/pytest tests/agent/test_mcp_api.py tests/agent/test_mcp_approval.py tests/agent/test_run_persistence.py tests/agent/test_thread_api.py -q -k 'allowance or resume_available or steer or cancel or restart'`

Expected: FAIL because cleanup/computed detail paths are incomplete.

- [ ] **Step 3: Centralize allowance cleanup ownership**

Add `clear_thread`, `clear_server`, `clear_tool`, and `clear_all` returning removed counts. Call them inside the same coordinator/Registry mutation path as thread delete, server/tool config changes, and shutdown. `DELETE /api/agent/threads/{thread_id}/allowances` returns `{"cleared": n}` without changing thread revision.

- [ ] **Step 4: Add computed recovery state without persistence**

Thread detail response becomes:

```python
payload = doc.model_dump(mode="json")
payload["resume_available"] = services.coordinator.resume_available(doc.id)
return payload
```

Do not add the field to `ThreadDocument`. Startup reconciliation still changes old awaiting runs to interrupted; it clears all allowances and never reconstructs a lease/checkpointer.

- [ ] **Step 5: Make lease/session cancellation exactly once**

On terminal/cancel/steer/delete/shutdown, detach the active handle under the thread lock, release Graph, and `await lease.aclose()` exactly once outside cancelled request scope via the existing protected persistence/cleanup pattern. An MCP call already inside the remote adapter marks its session generation draining; a queue wait cancellation never does. Closed journals discard late tool results.

- [ ] **Step 6: Run focused and full backend tests**

Run: `cd backend && .venv/bin/pytest tests/agent/test_mcp_api.py tests/agent/test_mcp_approval.py tests/agent/test_run_persistence.py tests/agent/test_thread_api.py -q && .venv/bin/pytest -m "not live"`

Expected: PASS; restart shows interrupted history with `resume_available=false` and no actionable interrupts.

- [ ] **Step 7: Commit**

```bash
git add backend/agent/capabilities.py backend/agent/runs.py backend/agent/router.py backend/agent/mcp.py backend/tests/agent/test_mcp_api.py backend/tests/agent/test_mcp_approval.py backend/tests/agent/test_run_persistence.py backend/tests/agent/test_thread_api.py
git commit -m "feat(agent): finalize MCP allowance lifecycle"
```

### Task 14: Build Approval, Recovery, Steer-Away, And 503 Frontend UX

**Files:**
- Modify: `frontend/src/lib/agent/types.ts`
- Modify: `frontend/src/lib/agent/api.ts`
- Modify: `frontend/src/lib/agent/history.ts`
- Modify: `frontend/src/lib/agent/history.test.tsx`
- Modify: `frontend/src/lib/agent/approval.ts`
- Modify: `frontend/src/lib/agent/approval.contract.test.ts`
- Modify: `frontend/src/lib/agent/runtime.tsx`
- Modify: `frontend/src/lib/agent/runtime.test.tsx`
- Create: `frontend/src/components/agent/ApprovalPanel.tsx`
- Create: `frontend/src/components/agent/ApprovalPanel.test.tsx`
- Create: `frontend/src/components/agent/SteerAwayComposer.tsx`
- Create: `frontend/src/components/agent/SteerAwayComposer.test.tsx`
- Modify: `frontend/src/components/agent/AgentThread.tsx`
- Modify: `frontend/src/components/agent/AgentThread.test.tsx`
- Modify: `frontend/src/pages/Agent.tsx`
- Modify: `frontend/src/pages/Agent.test.tsx`

- [ ] **Step 1: Write failing approval/history/transport tests**

Test camelCase custom metadata round-trip, actionable hydration only for awaiting+available, three exclusive decisions, all rows required, one submit, disabled concurrent actions, pending composer replacement, hook-generated cancelled entries, 503 message preservation/Manage MCP action/no retry/no 409 reload, and all converging controls disabled.

```tsx
it("submits all scoped decisions exactly once", async () => {
  render(<ApprovalPanel interrupts={twoInterrupts} />);
  await user.click(screen.getByLabelText("echo：本会话允许"));
  await user.click(screen.getByLabelText("lookup：拒绝"));
  await user.click(screen.getByRole("button", { name: "提交全部决定" }));
  expect(resolveAll).toHaveBeenCalledWith([
    { id: "i-1", decision: "approve", scope: "thread_session" },
    { id: "i-2", decision: "reject", scope: "once" },
  ]);
});
```

- [ ] **Step 2: Run Vitest and verify it fails**

Run: `cd frontend && npx vitest run src/lib/agent/history.test.tsx src/lib/agent/runtime.test.tsx src/lib/agent/approval.contract.test.ts src/components/agent/ApprovalPanel.test.tsx src/components/agent/SteerAwayComposer.test.tsx src/components/agent/AgentThread.test.tsx src/pages/Agent.test.tsx`

Expected: FAIL because 1C UI/contracts are absent.

- [ ] **Step 3: Extend types/history without changing custom casing**

Add `resume_available` to thread detail only and typed `McpInterruptMetadata` with camelCase properties. `toThreadMessageLike` injects `metadata.custom["ag-ui"].interrupts` only when the containing thread is awaiting approval and `resume_available===true`; otherwise pending content remains visible but non-actionable.

- [ ] **Step 4: Extend the single version-sensitive approval wrapper**

Keep all three assistant-ui hooks only in `approval.ts`. Return full metadata/response schema and accept:

```ts
type ApprovalDecision = {
  id: string;
  decision: "approve" | "reject";
  scope: "once" | "thread_session";
};
```

Map reject only to `scope:"once"`; `steerAway(message)` delegates one new user message so the locked hook supplies all cancelled entries.

- [ ] **Step 5: Implement stable approval and steer-away surfaces**

Render full redacted JSON arguments in bounded `<pre>` regions, three radio choices per row, and one submit button disabled until every row is selected. While submitting, disable decisions, thread switching, capabilities, and steer-away. Replace the normal Composer with `SteerAwayComposer` only while actionable interrupts exist; use familiar Send/Stop icons and tooltips.

- [ ] **Step 6: Handle pre-stream 503 separately from 409**

In the `transportFetch` closure passed to `AgentHttpAgent`, parse non-2xx JSON before stream handling. Keep 409 behavior unchanged. For `MCP_UNAVAILABLE`/503 call a dedicated callback carrying only backend detail; the page retains the composed message, shows a non-retrying error with a Manage MCP command, and does not reload or auto-resubmit.

- [ ] **Step 7: Run the slice-3 frontend and full gates**

Run:

```bash
cd frontend && npm test && npx vitest run && npm run build
cd ../backend && .venv/bin/pytest -m "not live"
cd .. && git diff --check
```

Expected: PASS; the production TypeScript build reports no unused symbols, and the contract test finds no approval hook imports outside `approval.ts`. Colocated test files are exercised by Vitest rather than the `tsc -b` unused-symbol gate.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/lib/agent/types.ts frontend/src/lib/agent/api.ts frontend/src/lib/agent/history.ts frontend/src/lib/agent/history.test.tsx frontend/src/lib/agent/approval.ts frontend/src/lib/agent/approval.contract.test.ts frontend/src/lib/agent/runtime.tsx frontend/src/lib/agent/runtime.test.tsx frontend/src/components/agent/ApprovalPanel.tsx frontend/src/components/agent/ApprovalPanel.test.tsx frontend/src/components/agent/SteerAwayComposer.tsx frontend/src/components/agent/SteerAwayComposer.test.tsx frontend/src/components/agent/AgentThread.tsx frontend/src/components/agent/AgentThread.test.tsx frontend/src/pages/Agent.tsx frontend/src/pages/Agent.test.tsx
git commit -m "feat(agent): add MCP approval experience"
```

## Final Verification

### Task 15: Verify 1C End To End And Record Evidence

**Files:**
- Create: `docs/superpowers/verification/2026-08-16-langchain-agent-workspace-1c.md`
- Modify only if verification finds a 1C defect: the exact production/test files from Tasks 1-14

- [ ] **Step 1: Run clean dependency installation and offline compatibility**

Run:

```bash
VR_1C_VERIFY=$(mktemp -d)
python3 -m venv "$VR_1C_VERIFY/venv"
cd backend
"$VR_1C_VERIFY/venv/bin/pip" install -r requirements.txt
"$VR_1C_VERIFY/venv/bin/pip" check
.venv/bin/pytest tests/agent/test_dependency_compat.py -q
```

Expected: install and `pip check` succeed; dependency tests PASS.

- [ ] **Step 2: Run every automated gate from a temp Agent root**

Run:

```bash
cd backend && .venv/bin/pytest -m "not live"
cd ../frontend && npm test && npx vitest run && npm run build
cd .. && git diff --check
```

Expected: all PASS. Confirm test logs and `git status --short` contain no file under a real user data directory and no secret fixture value.

- [ ] **Step 3: Run the fake MCP browser acceptance flow through local CDP**

Start backend/frontend with a new `VR_DATA_DIR` under `mktemp -d`, then use `puppeteer-core` against `127.0.0.1:16002`. Verify: Skill import/select/preview/tools; stdio add without spawn, trust, test, refresh; HTTP discovery; approve once/session, clear, reject, steer-away; refresh recovery; simulated backend restart to interrupted; desktop 1440x900 and mobile 390x844 screenshots with no overlap/overflow. Never point the services at the default Agent root.

- [ ] **Step 4: Run one real provider approval flow**

With a user-selected OpenAI-compatible function-calling provider, run one neutral research prompt that calls the deterministic MCP tool. Record provider/model, start/finish result, approval decision, and confirm no key appears in browser/backend output. Do not record the key itself.

- [ ] **Step 5: Record live mootdx status separately**

Run the three live smokes added in Task 1, serially and without xdist:

```bash
cd backend && .venv/bin/pytest tests/test_live.py -m live -q \
  -k 'mootdx_kline_route_live or mootdx_finance_route_live or mootdx_f10_live'
```

If upstream/network prevents completion, mark the verification document `PARTIAL`; do not claim complete 1C verification.

- [ ] **Step 6: Write the verification document**

Record exact commands, timestamps, PASS/PARTIAL/NOT RUN status, test counts, browser viewport evidence, real-provider evidence, secret scan, and any external failure. Include the slice-closing commit SHAs from Tasks 6, 10, and 14 plus the complete 1C commit range, and confirm 1A/1B duplicate/retry/cancel/history regressions stayed green.

- [ ] **Step 7: Commit verification only after all required non-live gates pass**

```bash
git add docs/superpowers/verification/2026-08-16-langchain-agent-workspace-1c.md
git commit -m "test(agent): verify workspace milestone 1c"
```

## 1C Exit Checklist

- [ ] Slice 1 independently passes: Skills scan/import/select/progressive loading, active-use protection, and frontend management.
- [ ] Slice 2 independently passes: stdio/HTTP config/trust/test/refresh/catalog/UI, with zero MCP aliases in Graph.
- [ ] Slice 3 independently passes: immutable bindings, fail-closed admission, argument guard, exhaustive approval, allowances, recovery, cancellation, and frontend interaction.
- [ ] Clean `pip install -r backend/requirements.txt` and `pip check` succeed with `mootdx==0.11.7+vr1`, MCP 1.26.0, adapter 0.3.2, and httpx 0.28.1.
- [ ] Fake stdio and Streamable HTTP tests leave no child process, socket, temp stage, or secret output.
- [ ] Every Skill/MCP traversal, script, archive, SSRF, trust, secret, content, size, revision, and identity guard fails closed.
- [ ] All capability and session references release exactly once; no cancelled/late result reaches Graph, SSE, or journal.
- [ ] Thread JSON remains authoritative; retry, duplicate, revision, partial history, and restart reconciliation match 1B.
- [ ] Frontend builds, desktop/mobile CDP acceptance passes, and at least one real provider completes the approval flow.
- [ ] Verification is marked `PARTIAL` rather than PASS if required mootdx live checks cannot run.
