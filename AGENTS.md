# AGENTS.md

Workspace guide for ZCode agents working in `stockclaw` (project name: **Vibe-Research**).

## What this is

Open-source personal AI investment-research dashboard fork, now upgraded to a **personal local AI-native investment platform**: A-share (primary) plus **US / HK / Korea** stocks. This fork gives **concrete decision recommendations** (target price / stop-loss / take-profit / position cadence). Public `main` branch keeps the original "never recommends" stance; this fork is personal/local only.

## Layout

```
stockclaw/
├── backend/              FastAPI :8900 (Python 3.11+) — the real app
│   ├── app.py              FastAPI entry; all /api routes + per-code TTL caches
│   ├── astock.py           A-share data layer (ported from a-stock-data)
│   ├── gstock.py           US/HK stock data (East-money, in-domain)
│   ├── market.py           market sentiment + sector fund-flow + global indices
│   ├── newsradar.py        RSS news radar (12 tracks, 108 sources)
│   ├── portfolio.py        local holdings + closed positions (cached locally)
│   ├── myreports.py        user-uploaded research reports (local only)
│   ├── chat.py             AI function-calling loop (OpenAI-compatible) + TOOLS
│   ├── cli_runtime.py      subscription access via local CLIs (claude/codex/qwen/deepseek)
│   ├── mcp_server.py       MCP server over stdio (for Claude Code etc.)
│   └── tests/              pytest; conftest.py registers the `live` marker
├── frontend/            Vite + React 19 + TS + Tailwind :55890
│   └── src/
│       ├── lib/api.ts      API client (the `api` object, ApiError, authHeaders)
│       ├── lib/{llm,notes,watchlist,ai-models,utils}.ts
│       ├── pages/          route components (router.tsx)
│       ├── components/{ui,layout,common}/
│       └── data/sectors.json
├── a-stock-data/        VENDORED A-share data toolbox (v3.3) — see SKILL.md for 40 endpoints
├── global-stock-data/   VENDORED US/HK data toolbox (v1.0.1) — see SKILL.md
└── docs/                screenshots only
```

> Root `main.py`, `backend/main.py`, and root `pyproject.toml` are uv-workspace scaffolding stubs with empty deps. The **real backend entry is `backend/app.py`**; run the backend with its own `.venv`, not the root one.

## Commands

```bash
# Backend (FastAPI :8900)
cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8900

# Frontend (Vite :55890 — NOTE: README says :5899, that is stale; actual port is 55890)
cd frontend && npm install && npm run dev      # http://localhost:55890
npm run build                                   # tsc -b && vite build (typecheck + build)

# Tests
cd backend && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest -m "not live"    # offline unit + API contract tests (fast, no network)
.venv/bin/pytest -m live          # hits real data sources; run before releases/upgrades

# MCP server (mount into Claude Code)
claude mcp add vibe-research -- "$(pwd)/.venv/bin/python" "$(pwd)/mcp_server.py"
```

## Architecture boundaries & layer rules

- **Read-only, stateless backend.** Endpoints return objective data only. The only persisted state is local: portfolio, closed positions, and uploaded reports (all under local cache dirs, never uploaded, never in repo).
- **Tiered dependencies — must be preserved.** `astock.py` layers data sources by weight:
  - Quotes (Tencent) → stdlib `urllib` only → **always works**
  - Reports/announcements (East-money) → `requests` → lightweight, always installed
  - Consensus/news/disclosure → `akshare` → **lazy import**
  - K-line/financials/F10 → `mootdx` → **lazy import**
  - Missing lazy deps raise `DependencyMissing` → endpoint returns **501 + install hint**, never crashes the service. Do not make heavy deps hard requirements.
- **All routes under `/api`.** A-share codes are validated as 6 digits (`_validate()`, regex `^\d{6}$`). US/HK/Korea go through `/api/global/stock?symbol=` (e.g. `AAPL`, `00700`, `005930.KS`).
- **Caching is in `app.py`**, per-code with explicit TTLs (valuation 30min, announcements 15min, financials 30min, data-center 30min, fund-flow/hot-concepts/investor-qa 15min, industry 5min, market overview/emotion/turnover/global-indices 5min). Respect these — East-money has a ~1s rate limit.
- **Three AI channels** (configured by user, backend never persists keys): (1) API access with function-calling (`chat.py`), (2) subscription access via local CLI subprocess (`cli_runtime.py`), (3) MCP server (`mcp_server.py`, reuses `chat.TOOLS`). CLI channel is one-shot, no tool-calling — only suitable when data is already in the prompt context.

## Coding conventions

- **Python:** `from __future__ import annotations`, full type hints, Chinese docstrings/comments, `# noqa: BLE001` on broad `except Exception` boundary catches. Module-level constants in CAPS.
- **TypeScript:** strict mode, `noUnusedLocals`/`noUnusedParameters` on. Import via `@/*` alias (→ `src/*`). Use the `cn()` helper (`clsx` + `tailwind-merge`) for class merging.
- **Frontend styling:** Tailwind with HSL CSS-variable tokens (`hsl(var(--primary))` etc.), `darkMode: "class"`, glass/warm-orange theme, `GlassCard` shadow tokens. Fonts: Inter / JetBrains Mono. State management via **zustand**.
- **API client:** all backend calls go through `src/lib/api.ts` `api` object; it handles `ApiError`, `authHeaders()` (for `VR_API_KEY`), and graceful degradation when the backend is down. Add new endpoints here, not ad-hoc `fetch`.

## Gotchas

- **Use `127.0.0.1`, not `localhost`.** Some macOS/Node setups resolve `localhost` to IPv6 `::1` while the backend listens on IPv4 `127.0.0.1:8900` → `ECONNREFUSED` (issue #8). `vite.config.ts` and `.env.example` already default to `127.0.0.1`.
- **East-money data sources default to direct connect** and deliberately bypass science-VPN/Clash proxies (Chinese financial sites like `push2.eastmoney.com` fail through proxy). Only set `VR_DATA_PROXY=1` if the machine can *only* reach the net via proxy.
- **`push2his` fund-flow may return empty** on some mainland residential IPs due to intermittent risk-control — this is a data-source issue, not a code bug.
- **Korea stocks require the `.KS` suffix** (e.g. `005930.KS`); a bare 6-digit code is treated as A-share.
- **Frontend port is `55890`**, not the `5899` printed in the root README.
- **`.claude/`, `.venv/`, `node_modules/`, `.cache/`** are local-only / gitignored — don't commit them.

## 定位（个人本地部署）

本仓库（stockclaw 当前 fork）= **个人本地部署的 AI 原生投资分析平台**。是非投资建议风格——可给具体决策建议（目标价/止损/止盈/仓位节奏）。公开 main 仓库保持原「不荐股」红线，互不污染。

## 安全红线（不是合规红线）

- **本地部署，不分发、不开源此 fork**（公开 main 仓库是另一回事）
- **API 密钥本地化**：`VR_API_KEY` 等只存本地 `.env`，不入 git、不上传
- **不接真实券商 API**：仅做分析，不自动下单
- **决策卡内容含个人投资决策**：不入 git、不上传（`backend/.cache/` 已 gitignore）
- **客观数据层不污染**：`astock.py / gstock.py / market.py / newsradar.py` 永远只返回客观数据；决策建议只在 `agents/` 层产生。这条边界焊死——便于未来再分叉客观版本

## Privacy

Portfolio, watchlist, uploaded reports, and API keys are **local only** — never uploaded, never committed. `.gitignore` enforces this. The three private dev docs (`VibeResearch-开发日志.md`, `VibeResearch-方案定稿.md`, `VibeResearch-专业化建议.md`) must never enter the public repo — run `git status` before pushing to confirm they're absent.

## Read before changing sensitive areas

- `backend/README.md` — full endpoint table, MCP setup, dependency tiers.
- `backend/.env.example` — env vars: `VR_ALLOW_ORIGINS` (CORS), `VR_API_KEY` (auth), `VR_DATA_PROXY` (proxy override).
- `a-stock-data/SKILL.md` — copy-paste-ready code for all 40 A-share endpoints (full toolbox; backend `astock.py` is a ported subset).
- `global-stock-data/SKILL.md` — full US/HK endpoint toolbox.
