# AGENTS.md

Notes for future ZCode agents working in this repo. The product is **Vibe-Research**
(GitHub: `simonlin1212/Vibe-Research`; the local directory is named `stockclaw`) — a
self-hosted personal AI investment-research dashboard for A-share / US / HK stocks.
Read `VISION.md` before any product-facing change.

## Hard product boundary (read before editing)

Per `VISION.md`: this project **only provides objective data + analysis frameworks + tools**.
It must **never** recommend a buy/sell, predict price moves, give target prices / ratings /
rankings, or time trades. The multi-agent debate ends at "disagreement points + a
verification checklist", not a conclusion. `ROADMAP.md` lists explicit "明确不做"
(backtesting, quant scoring, auto-trading, stock picks) — do not add features that push
the product toward a "buy/sell signal" output.

Concretely: data endpoints and AI tools return only objective facts; `chat.py`'s
`SYSTEM_PROMPT` hard-codes a neutrality red-line; the UI has no buy/sell buttons.

## Layout

```
backend/            FastAPI :8900 (Python 3.11+) — data/business layer; AI runs on LangGraph :2024
  app.py              HTTP routes + CORS/auth middleware + startup scheduler (data/business APIs
                      + read-only /api/agent/status; NO AI orchestration routes anymore)
  astock.py           A-share data (ported from a-stock-data/) — eastmoney + tencent
  gstock.py           US/HK/KR data (ported from global-stock-data/) — eastmoney域内
  market.py           market sentiment / sector fund flow / global indices
  newsradar.py        RSS 资讯雷达 (+ news_sources.json, 12 tracks/108 feeds)
  tools.py            24 function-calling tools — the ONLY tool source, shared by all graphs + MCP
  mcp_server.py       MCP server over stdio (imports tools.py directly, zero third-party deps)
  portfolio.py        holdings + realized P&L (stored in user data dir)
  myreports.py        user-uploaded research reports (local only)
  version.py          single version source (reads frontend/package.json)
  langgraph.json      six graph registrations: agent, embedded_agent, debate, reflection,
                      daily_review, news_digest
  agent/              unified AI runtime: settings.py (static local config + status summary),
                      model_factory.py, policy.py (neutral red-line), skill_backends.py,
                      tool_executor.py (process-wide serial/parallel guards), tool_registry.py,
                      graph.py (workspace), embedded_graph.py (page ask-AI),
                      workflow_{state,loader,events,runtime,builder}.py + workflows/*.yaml
                      (strict-YAML workflows), workflows_graph.py (one-time exports)
  tests/              pytest; conftest.py isolates user data dir + agent settings;
                      agent_e2e/ = deterministic browser-fixture graphs
frontend/           Vite + React 19 + TS + Tailwind :5899 (glass warm-orange theme)
  src/lib/            api.ts (client), storage.ts, clipboard.ts,
                      agent/ (thread-adapter, workflow-{types,client,stream}, embedded-client)
  src/hooks/          useWorkflowRun (page workflow controller)
  src/components/     workflow/WorkflowHistory (per-page history), ui/AskAiButton (drawer)
  src/pages/          one page per top-level nav
a-stock-data/       vendored A-share data toolbox — see its SKILL.md (copy-paste code)
global-stock-data/  vendored US/HK data toolbox — see its SKILL.md
docs/, README*.md, VISION.md, ROADMAP.md, CHANGELOG.md
```

`a-stock-data/SKILL.md` and `global-stock-data/SKILL.md` are the authoritative
references when an agent needs full A-share / US-HK endpoints beyond what `backend/`
exposes. The backend data layer is a port of these toolboxes.

## Commands

```bash
# Backend (:8900)
cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8900

# Agent workspace backend (local LangGraph Server :2024, loopback only)
.venv/bin/langgraph dev --host 127.0.0.1 --port 2024 --no-browser

# Backend tests (run from backend/)
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest -m "not live"   # offline unit/API-contract tests (default, fast)
.venv/bin/pytest -m live          # network smoke tests against real data sources (pre-release)

# Frontend (:5899)
cd frontend && npm install && npm run dev    # dev server, proxies /api → 127.0.0.1:8900
npm run build      # tsc -b && vite build
npm test           # node --test tests/*.test.mjs
npm run test:unit  # vitest component/logic tests

# Deterministic six-graph browser acceptance (isolated FastAPI :8873 / LangGraph :2873 /
# Vite :5873, scripted models + fake tools; never touches 8900/2024 or ~/.vibe-research)
npm run test:e2e -- e2e/unified-ai-workflows.spec.ts
```

## Backend conventions

- **Sibling imports, no package.** Backend modules import each other as top-level siblings
  (`import astock`, `import tools`); the backend dir is put on `sys.path` (see
  `conftest.py`). Do not introduce package-relative imports.
- Start every module with `from __future__ import annotations`.
- Code, docstrings, and comments are **predominantly Chinese** — match this when editing.
  Error messages returned to the UI are Chinese.
- **Lazy optional deps.** `akshare` / `mootdx` are imported lazily inside functions; if
  missing, the endpoint raises `astock.DependencyMissing` → HTTP **501 + install hint**,
  and the rest of the service keeps working. Never make a heavy dep required.
- **No AI orchestration in FastAPI.** `/api/chat`, `/api/debate`, `/api/reflect` (and the
  transitional `/api/daily-review`, `/api/news-digest`) were removed with the legacy
  modules (`chat.py`, `debate.py`, `reflection.py`, `cli_runtime.py`). All model calls go
  through the local LangGraph Server; FastAPI keeps data/business APIs plus the read-only
  `/api/agent/status`.
- **Caching.** Rate-limited eastmoney (`em_*`) endpoints use module-level TTL dicts
  (`_cached(...)`; typically 30 min). Reuse the existing cache helper; don't refetch.

### ⚠️ Rate-limiting / concurrency rule (do not break)

Eastmoney's `em_get` throttle works by **timestamp spacing, not locks**. **Throttled
fetches MUST stay serial — never parallelize them** or you will trip upstream rate
limits on the *user's* IP. In `debate.py` the dossier fetch deliberately groups calls:
non-throttled items run concurrent, throttled (`em_get`) items run serial. Preserve
this split when touching dossier/底稿 logic.

### Versioning (do not regress)

`frontend/package.json` `version` is the **single source of truth**. `version.py::read_version()`
reads it; HTTP `/api/health`, the frontend, and MCP `serverInfo` all consume that. **Never
hardcode a version anywhere else** — issue #20 was caused by 3+ hardcoded copies falling
out of sync. On read failure, `read_version` returns `"unknown"` and warns to **stderr**
(never stdout — `mcp_server` imports it and stdout is JSON-RPC). `version.py` is
deliberately a standalone module: importing `app.py` triggers `pf.start_scheduler(1800)`
as a side effect, and MCP must not inherit that.

### Security guards

- **SSRF.** `chat.py::_check_base_url` validates the user-supplied `baseURL` before the
  backend calls it: blocks cloud-metadata / link-local always; in **public mode**
  (`VR_API_KEY` set) also blocks private nets and resolves the domain to catch DNS
  rebinding. Keep this guard in front of any outbound call driven by user input.
- **baseURL normalization.** `/v1` is appended unless the base already ends in
  `/v1`, `/v3`, `/api/v3`, or `/v4` (issue #22 — don't mangle 智谱 `/v4` into `/v4/v1`).
  Update both `_call_llm` and `_resolve_base` together if you touch this list.
- **MCP stdio is UTF-8 JSON-RPC.** `mcp_server.py::_force_utf8_stdio` reconfigures stdio
  to UTF-8 (Windows defaults to GBK, issue #27). Never `print` to stdout in MCP code.

## Environment variables (see `backend/.env.example`)

- `VR_API_KEY` — Bearer auth for all `/api/*` (except `/api/health`); also flips the
  service into **public mode** (stricter SSRF, no local-network targets). Empty = open
  local self-host. Set a strong random value for public deployments.
- `VR_ALLOW_ORIGINS` — CORS allowlist, comma-separated (default `*`).
- `VR_DATA_DIR` — user data root, default `~/.vibe-research/` (holdings, etc.).
- `VR_REPORTS_DIR` — uploaded-reports dir (default `<VR_DATA_DIR>/myreports`).
- `VR_DATA_PROXY=1` — force system proxy for eastmoney (only when the host can *only*
  egress via proxy; eastmoney is direct by default and proxying it from China fails).
- `IWENCAI_API_KEY` / `IWENCAI_BASE_URL` — optional, for iwencai semantic report search.

## Dev-host specifics (remote dev)

This is a **remote dev server (192.168.1.13)**; the ZCode desktop client runs on a
**separate machine**. Two things an agent must know (both cost real turns to discover):

### Browser: CDP Chrome on :16002 first, headless Playwright chromium fallback

`agent.browsers` / browser-use routes through the **desktop client's broker socket**
(`ZCODE_NODE_REPL_BROWSER_BROKER_SOCKET`), which lives on the other machine — so
`agent.browsers.list()` returns `[]` and every `get(...)` fails with "backend unavailable".
Don't burn turns on it.

**Preferred: the user's real Chrome over CDP at `127.0.0.1:16002`.** Its availability
has flipped before (gone 2026-08-22, **back and verified 2026-08-24**: Chrome 150,
`/json/version` + `chromium.connectOverCDP` both work, live tabs open on :5899) — so
always probe first, never assume either way:

```bash
curl -m 3 127.0.0.1:16002/json/version   # JSON Browser info = alive; empty/timeout = down
```

When alive, reuse the existing context (`browser.contexts()[0]` — holds the user's
tabs/login state) rather than a clean `newContext()`. Verified semantics:
`page.close()` closes only the tab you opened; `browser.close()` merely drops your CDP
session and leaves the user's Chrome and tabs untouched.

```js
// cdp.mjs — run with: node cdp.mjs (same createRequire caveat as below)
import { createRequire } from "node:module";
const { chromium } = createRequire("/vol2/1000/code/stockclaw/frontend/package.json")("@playwright/test");

const browser = await chromium.connectOverCDP("http://127.0.0.1:16002");
const ctx = browser.contexts()[0];            // the user's real profile/tabs
const page = await ctx.newPage();             // or pick from ctx.pages()
await page.goto("http://127.0.0.1:5899/agent", { waitUntil: "domcontentloaded" });
await page.screenshot({ path: "/tmp/shot.png", fullPage: true });
await page.close();                           // close only OUR tab
await browser.close();                        // disconnect only — user's Chrome survives
```

**Fallback (when :16002 is down): headless Playwright chromium from `frontend/node_modules`.**
`@playwright/test` (a frontend devDep) re-exports `chromium`, and the browsers are
already cached in `~/.cache/ms-playwright` — zero external dependencies, nothing to
install, works without the desktop client or any user-visible Chrome. Run a node
script via Bash:

```js
// shots.mjs — run with: node shots.mjs
// ⚠ module resolution follows the SCRIPT's own path, not cwd: a script living in
//   /tmp cannot `import "@playwright/test"` — it must createRequire() the frontend:
import { createRequire } from "node:module";
const { chromium } = createRequire("/vol2/1000/code/stockclaw/frontend/package.json")("@playwright/test");

const browser = await chromium.launch();                          // headless chromium
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
await ctx.addInitScript((t) => localStorage.setItem("vr-theme", t), "dark"); // seed theme
const page = await ctx.newPage();
await page.goto("http://127.0.0.1:5999/agent", { waitUntil: "domcontentloaded" });
await page.waitForLoadState("networkidle", { timeout: 15000 }).catch(() => {});
await page.screenshot({ path: "/tmp/shot.png", fullPage: true });
// DOM 检查同样可用:page.evaluate 里 document.elementFromPoint(x, y) 能把截图差异
// 像素簇反查到具体元素(视觉回归定位惯用)
await browser.close();
```

Pitfalls learned the hard way (each cost a turn):

- **`import "@playwright/test"` fails outside `frontend/`** (`ERR_MODULE_NOT_FOUND`)
  even when cwd is the frontend dir — always `createRequire` against
  `/vol2/1000/code/stockclaw/frontend/package.json` as above.
- **Ad-hoc vite servers need `--host 127.0.0.1`** — bound to `localhost` they listen
  on `::1` only (same root cause as issue #8) and `curl 127.0.0.1:<port>` gets
  nothing. Also: the user's own dev server on :5899 goes stale after dependency
  upgrades (holds unloaded modules in memory) — check
  `curl 127.0.0.1:5899/src/index.css` returns CSS, not an error page, before blaming
  your script; start your own instance on a spare port instead of killing theirs.
- **Background processes started with `nohup ... &` inside a Bash call are reaped
  when the call ends** — start long-lived servers (vite / uvicorn) with the Bash
  tool's background mode (`run_in_background`), not shell `&`.

### Futu OpenAPI: OpenD is remote; pypi needs the Tsinghua mirror

`futu-api` is **not** installed and **OpenD is not on this box** — OpenD runs at
**`192.168.1.30:11111`** (already logged into the Futu account). Also **`pypi.org` is
unreachable** from here, so `uv` / `pip` must use the Tsinghua mirror or
`uv run --with futu-api` times out. Skill scripts read `FUTU_OPEND_HOST` / `FUTU_OPEND_PORT`
from env; `import-sector-chain.py` takes `--opend-host`.

```bash
export FUTU_OPEND_HOST=192.168.1.30 FUTU_OPEND_PORT=11111
export UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple   # REQUIRED — pypi.org blocked

# 0) BEFORE importing: verify every plate_id exists in Futu's official chain. A wrong
#    plate_id still "succeeds" but fetches the WRONG board's stocks (e.g. pharmacy stocks
#    surfaced under 能源供给) — verify-sector-plates.py catches such copy-paste typos.
uv run --with futu-api==10.9.6908 python scripts/verify-sector-plates.py --opend-host 192.168.1.30:11111
# 1) diagnose connectivity (futu-api import + OpenD + backend)
uv run --with futu-api==10.9.6908 python scripts/import-sector-chain.py --diagnose --opend-host 192.168.1.30:11111
# 2) import a sector's constituents (all-or-nothing; original order + --limit, NOT market-cap rank)
uv run --with futu-api==10.9.6908 python scripts/import-sector-chain.py --key humanoid --opend-host 192.168.1.30:11111 --limit 8
```

## User data & privacy (do not leak)

Holdings, watchlist, uploaded reports, and all API keys are **local only** — never
persisted server-side, never committed. Model keys live in plaintext only in the local
static settings file (default `~/.vibe-research/agent/settings.json`, override with
`VR_AGENT_SETTINGS`, permission `0600`), read once when the LangGraph Server starts —
never let settings secrets enter thread metadata, graph state, checkpoints, logs, or error
messages. The only key still held in the browser is the FastAPI access key
(`localStorage`: `vr-access-key`, sent per-request to `/api/*` when `VR_API_KEY` is set).
Old `vr-llm` / `vr-askai-chat:*` values are pre-migration leftovers: never read, migrated
or deleted.
`.gitignore` also guards private internal docs — run `git status` before any
release to confirm none are staged.

**Tests must not mutate real user data.** `conftest.py` points `VR_DATA_DIR` /
`VR_REPORTS_DIR` at a temp dir **before any test imports `app`** (because `portfolio.py`
/ `myreports.py` fix their paths at import time), and writes an isolated
`VR_AGENT_SETTINGS` fixture the same way. Do not reorder this.

## Frontend conventions

- Path alias `@` → `src/`. `tsconfig` is `strict` with `noUnusedLocals` /
  `noUnusedParameters` — `npm run build` (which runs `tsc -b`) will fail on violations.
- All `localStorage` access goes through `lib/storage.ts` wrappers — privacy mode /
  WebView throws on raw access and will white-screen the app.
- API access goes through `lib/api.ts` `request<T>`, which returns `payload.data ?? payload`
  and throws `ApiError` on non-2xx (pages degrade gracefully). Add new endpoints there.
- `/api` is proxied to `http://127.0.0.1:8900` (not `localhost` — IPv6-first resolution
  causes ECONNREFUSED, issue #8); override with `VITE_API_URL`.
- State: **zustand**. Charts: **echarts**. Styling: **Tailwind v4** — the whole theme
  lives in `src/index.css` (`@theme inline` maps the runtime HSL vars, `@custom-variant
  dark`, `@plugin "@tailwindcss/typography"`, `tw-animate-css` imported); there is
  **no `tailwind.config.ts`** (removed in the v4 upgrade, commit `8834859`). Follow the
  existing glass-card / warm-orange design language.

## AI layer (Unified LangGraph AI Workflows)

`tools.py` defines the 24 function-calling tools and is the **only** place to add new
ones — shared across all LangGraph graphs, MCP, and legacy endpoints:
- `agent/tool_executor.py` classifies tools into `parallel_safe` (7 tools guarded by `BoundedSemaphore(4)`)
  and `eastmoney_serial` (guarded by process-wide `threading.Lock()`).
- `agent/policy.py` (`fixed_system_policy()`) defines the universal neutrality red-line
  plus tool grounding rules. Tool-bound graphs (`agent`, `embedded_agent`) use the full
  policy; workflow stages call the model **without tools bound** and must pass
  `tools=False` (a tool-advertising prompt makes strong agentic models narrate
  "I'll call query_market" instead of analyzing — glm-5.2 verified).
- `agent/skill_backends.py` enforces read-only virtual filesystem access to `/builtin/`
  and `/user/` Skills.

### Six registered LangGraph graphs (`langgraph.json` :2024):
1. **`agent`** (`agent/graph.py`): Full workspace interactive agent with HITL approval
   for MCP tool calls, Skills `/builtin/` + `/user/`, and SessionTrace middleware.
2. **`embedded_agent`** (`agent/embedded_graph.py`): Isolated page-level Ask-AI agent.
   Built-in tools and `/builtin/` Skills only; no MCP, no HITL, no `/user/` skills.
   Manages versioned `page_context` snapshots with non-empty overwrite semantics and scope guard.
3. **`debate`** (`agent/workflows_graph.py`): Multi-stage debate workflow (`debate.yaml`).
   Serializes objective dossier, executes `bull` -> `bear` -> `referee` (`standard`)
   or with cross-examination (`cross_exam`), emitting typed monotonic custom events.
4. **`reflection`** (`agent/workflows_graph.py`): Reasoning audit workflow (`reflection.yaml`).
5. **`daily_review`** (`agent/workflows_graph.py`): Market daily review workflow (`daily_review.yaml`).
6. **`news_digest`** (`agent/workflows_graph.py`): Multi-track news digest workflow (`news_digest.yaml`).

### Legacy FastAPI AI routes — removed
`/api/chat`, `/api/debate`, `/api/reflect`, `/api/daily-review`, `/api/news-digest` and
their modules (`chat.py`, `debate.py`, `reflection.py`, `cli_runtime.py`, `agent/ssrf.py`)
are deleted; the cutover happened only after the deterministic six-graph Playwright
acceptance (`frontend/e2e/unified-ai-workflows.spec.ts`, fixture graphs in
`backend/tests/agent_e2e/unified_graphs.py`) passed. `mcp_server.py` imports `tools.py`
directly. Old browser keys (`vr-llm`, `vr-askai-chat:*`) are never read — leave them.

`langgraph dev` is an in-memory runtime. Thread/run/assistant records live in
`.langgraph_api/.langgraph_ops.pckl`; checkpoints live in
`.langgraph_checkpoint.{1,2,3}.pckl`. Both flush on a ~10s timer and on SIGTERM.
A `PersistentDict.load()` failure (incompatible cache / `ModuleNotFoundError`) starts
an empty ops index, then the next flush atomically overwrites the pickle — thread
list gone even though checkpoints remain. `scripts/dev` copies `*.pckl` to
`~/.vibe-research/agent/server/pickle-backups/<timestamp>/` (keeps 5) before each
agent start, and warns if startup logs contain `Failed to load file`. True
restart-durability still needs Postgres (`langgraph up`); the E2E restart scenario
asserts the honest dev-runtime behaviour (histories listed, lost in-flight states
derive to `interrupted`, new runs work).

### Frontend workflow wiring
- Pages use `hooks/useWorkflowRun` (thread/run/state/transient controller) +
  `components/workflow/WorkflowHistory` (one `threads.search(extract)`; `getState` only on
  open/rerun — never per row). Embedded ask-AI uses `lib/agent/embedded-client.ts`.
- Retry refuses incompatible `config_version` checkpoints (`WORKFLOW_CONFIG_VERSIONS` in
  `lib/agent/workflow-types.ts` must mirror `config_version` in the YAML files).
- Client cancel/interrupt patches write JSON-dict stage values via `updateState`;
  `merge_stage_results` coerces them back to `StageResult` (backend contract — do not
  remove the coercion).
