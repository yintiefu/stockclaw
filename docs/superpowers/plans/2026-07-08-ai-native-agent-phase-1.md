# AI-Native Agent Module · Phase 1 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 stockclaw 当前 fork 改造为个人本地部署的 AI 原生投资分析平台——Phase 1 完成「决策卡闭环」：用户在 `/agent` 输入"分析茅台 给目标价止损止盈仓位节奏"，拿到带 `basis_type` 色标的结构化决策卡，能收藏、能复制；美港股请求自动走 `model_fallback`；5 个并发工具请求不触发东财 403。

**Architecture:** 三层——①客观数据层（`astock.py / gstock.py / market.py / newsradar.py` 不动）→ ②quant 工具层（纯 Python 函数，无 LLM）→ ③Agent 层（LangGraph 编排：orchestrator → decision node → tools，输出 NDJSON SSE 事件流）。前端自定义轻量 React Chat + 原生 NDJSON SSE 解析（不用 CopilotKit）。持久化本地 SQLite（aiosqlite + WAL + `PRAGMA user_version` migration）。

**Tech Stack:** Python 3.11+ / FastAPI / LangGraph + langchain-core / aiosqlite / pytest · React 19 / Vite / TS strict / Tailwind / zustand / react-markdown。

---

## 前置准备（开工前一次性）

```bash
# 当前在 0.2.1 分支，从此分支切出 Phase 1 工作分支
cd /vol2/1000/code/stockclaw
git checkout -b phase-1/ai-native-agent

# 后端新增依赖（在 backend/pyproject.toml 的 [project] dependencies 加完后执行）
cd backend && uv pip install langgraph langchain-core aiosqlite
# 同步 dev 依赖
uv pip install pytest-asyncio  # 测试 async 用
```

**全局硬约束（每个 task 完成后自查）**：
1. **数据层不污染**：`astock.py / gstock.py / market.py / newsradar.py` 永远不改、永远只返回客观数据
2. **同步数据层卸载**：`agents/tools.py` 所有调 `astock.*` / `gstock.*` / `market.*` / `newsradar.*` 的 `@tool` 必须用 `await asyncio.to_thread(fn, ...)` 包装；`grep -E "await (astock|gstock|market|newsradable)" backend/agents/tools.py` 必须无命中
3. **结构化事件优先于 Markdown**：决策卡走独立 `decision_artifact` NDJSON 事件，**禁止在 LLM 输出的 Markdown 内夹 JSON 块**
4. **Rate Limiter 横跨业务**：`EastmoneyRateLimiter.__aenter__` acquire 锁，`__aexit__` 才 release——禁止用 `async with self._lock`（return 时立即释放，限流失效）
5. **不引入 CopilotKit**：前端 chat 用自定义 React 组件 + 原生 `fetch + ReadableStream + TextDecoder`

---

## 文件结构总览（Phase 1 完成后的最终形态）

```
backend/
├── chat_legacy.py            # Task 1：原 chat.py 改名；老 /api/chat 与 MCP server 仍用
├── app.py                    # Task 1+8：导入改 chat_legacy；新增 /api/agent/chat
├── mcp_server.py             # Task 1：导入改 chat_legacy
├── portfolio.py              # Task 6：totals 扩字段，向后兼容
├── pyproject.toml            # 加 langgraph/langchain-core/aiosqlite/pytest-asyncio
├── .env.example              # Task 8：加 VR_AGENT_* env
│
├── quant/                    # 纯 Python 工具层（无 LLM）
│   ├── __init__.py
│   ├── valuation.py          # Task 3：forward_pe_target / pe_percentile_revert
│   ├── stops.py              # Task 4：atr_stop / structure_stop / risk_based_position
│   └── cadence.py            # Task 5：pyramid_buy / batch_build / dca_plan
│
├── agents/                   # Agent 核心层
│   ├── __init__.py
│   ├── prompts.py            # Task 1：解禁版 SYSTEM_PROMPT
│   ├── rate_limiter.py       # Task 2：EastmoneyRateLimiter cool-down 1.0s
│   ├── state.py              # Task 7：AgentState TypedDict
│   ├── graph.py              # Task 7：build_agent_graph()
│   ├── tools.py              # Task 7：@tool 包装，asyncio.to_thread 卸载
│   └── nodes/
│       ├── __init__.py
│       ├── orchestrator.py   # Task 7：分类意图 + 路由
│       └── decision.py       # Task 7：合并工具结果为决策卡 + basis_type 归并
│
├── persistence/              # 本地 SQLite（aiosqlite）
│   ├── __init__.py
│   ├── db.py                 # Task 9：连接 + WAL + PRAGMA user_version migration
│   ├── threads.py            # Task 9：会话 CRUD
│   ├── conversations.py      # Task 9：消息 CRUD（含 tool_calls_json / artifacts_json）
│   └── decisions.py          # Task 9：决策卡归档 + 收益追踪字段
│
├── runner.py                 # Task 8：run_agent(req) -> AsyncGenerator[NDJSON event]
└── tests/
    ├── test_rate_limiter.py        # Task 2
    ├── test_quant_valuation.py     # Task 3
    ├── test_quant_stops.py         # Task 4
    ├── test_quant_cadence.py       # Task 5
    ├── test_portfolio_totals.py    # Task 6
    ├── test_agents_decision.py     # Task 7
    ├── test_agent_endpoint.py      # Task 8
    ├── test_runner.py              # Task 8
    └── test_persistence.py         # Task 9

frontend/src/
├── router.tsx                          # Task 10：加 /agent 路由
├── components/layout/Layout.tsx        # Task 10：侧栏加 "股神" 入口
├── lib/api.ts                          # Task 10：加 agent.threads.* / agent.decisions.* 辅助
├── lib/types/agent.ts                  # Task 10：NDJSON 事件 + DecisionCardData TS 类型
├── lib/stores/agent.ts                 # Task 11：zustand store
├── hooks/useAgentStream.ts             # Task 11：line buffer + TextDecoder{stream:true}
├── pages/Agent.tsx                     # Task 10：路由组件 + CLI 模型拦截覆盖层
└── components/agent/
    ├── AgentWorkspace.tsx              # Task 10：左侧栏 + Main + Drawer 三栏布局
    ├── AgentSidebar.tsx                # Task 10：会话列表
    ├── AgentMain.tsx                   # Task 10：顶栏 + Chat + Composer
    ├── AgentTopBar.tsx                 # Task 10：模型 / 风格 / 上下文标签
    ├── CustomAgentChat.tsx             # Task 11：按 event.type 分发渲染
    ├── ToolTrace.tsx                   # Task 11：折叠小药丸
    ├── AgentComposer.tsx               # Task 11：自定义输入框 + 快捷 prompt
    ├── DecisionCard.tsx                # Task 12：决策卡 + 4 档色标 + 字段级 model_versions
    └── ContextDrawer.tsx               # Task 12：右侧抽屉（股票快卡 + 收藏决策）
```

---

## Task 1：合规解禁 + 物理隔离

**目标**：把 `chat.py` 物理改名为 `chat_legacy.py`，保留老 `/api/chat` 与 MCP server 可用；新建 `agents/prompts.py` 用解禁版 prompt（含「决策建议」第六维）；更新 `app.py:21` 与 `mcp_server.py:18` 的 import；重写 `AGENTS.md` 红线段为「个人本地部署」定位。

**Files:**
- Rename: `backend/chat.py` → `backend/chat_legacy.py`
- Create: `backend/agents/__init__.py`（空）
- Create: `backend/agents/prompts.py`
- Modify: `backend/app.py:21`
- Modify: `backend/mcp_server.py:18`
- Modify: `AGENTS.md`

- [ ] **Step 1.1：建 agents 包 + 占位 `__init__.py`**

```bash
mkdir -p backend/agents backend/agents/nodes
```

```python
# backend/agents/__init__.py
"""AI 原生 Agent 层 —— 决策/讨论/计划执行/主动扫描。

物理隔离：本目录所有 prompt 与逻辑均允许产出具体决策建议（目标价/止损/止盈/仓位节奏）。
客观数据层（astock.py / gstock.py / market.py / newsradar.py）不在此处修改。
"""
```

- [ ] **Step 1.2：物理改名 `chat.py` → `chat_legacy.py`**

```bash
cd /vol2/1000/code/stockclaw/backend
git mv chat.py chat_legacy.py
```

- [ ] **Step 1.3：改 `app.py:21` 导入**

修改 `backend/app.py` 第 21 行：

```python
# 改前
import chat as chat_layer
# 改后
import chat_legacy as chat_layer
```

- [ ] **Step 1.4：改 `mcp_server.py:18` 导入**

修改 `backend/mcp_server.py` 第 18 行：

```python
# 改前
import chat  # 复用 TOOLS 定义 + _exec_tool 执行逻辑（内含 astock）
# 改后
import chat_legacy as chat  # 复用 TOOLS 定义 + _exec_tool 执行逻辑（内含 astock）
```

- [ ] **Step 1.5：写 `agents/prompts.py`（解禁版）**

```python
# backend/agents/prompts.py
"""解禁版 Agent 提示词。

本 fork 已转为「个人本地部署的非投资建议风格」——可给具体决策建议（目标价/入场区/
止损/止盈/仓位节奏）。数字优先 quant 工具，工具不适用时 LLM 推理，必须标注依据类型。
"""

from __future__ import annotations

# 解禁版分析框架：原 5 维 + 第 6 维「决策建议」（目标价/入场区/止损/止盈/仓位节奏/依据类型）
ANALYSIS_FRAMEWORK_AGENT = """【投研分析框架】当用户要你分析个股、给判断或下结论时，按下面六个维度依次组织分析：
1. 估值：PE / PB / PS 的绝对水平 + 处在历史区间的高 / 中 / 低位 + 同业对比 + 机构一致预期的前向估值。
2. 资金面：主力资金流方向与强度 + 融资融券趋势 + 股东户数（筹码集中 / 分散）+ 龙虎榜 / 大宗异动。
3. 财报质量：营收与扣非净利增速是否匹配 + 经营现金流含金量 + 毛利 / 净利率趋势 + 资产负债率。
4. 行业景气：板块 / 概念归属 + 板块近期强弱 + 行业内相对排名 + 关联热门概念热度。
5. 事件催化与风险：重要公告 + 解禁 + 分红 + 舆情，分列「催化」与「风险」两栏。
6. 决策建议：目标价 / 入场区（区间价）/ 止损价 / 止盈价 / 仓位节奏（分批计划：第一批 X% 立即、第二批 X% 回踩、第三批 X% 突破）+ 依据类型（model / model_fallback / llm_reasoning / hybrid）。

输出组织：
- 结论先行：一句话概括当前状态 + 关键数据速览。
- 每个维度用「**加粗小标题** + 一小段展开」，有对比上小表格。
- 第六维「决策建议」务必调 quant 工具拿数字；工具不适用时（美港股 / 数据缺失）由你推导目标价且必须列依据数据点，止损价等硬性字段一律用工具 fallback 值。
- 末尾分列「关键观察」与「风险点」两栏。
（简单事实性问题——如"现价多少"——直接答，不必套用整个框架。）"""

# 系统提示：private-investment-analyst 角色，可给具体决策建议
SYSTEM_PROMPT_AGENT = f"""你是用户的私人投资分析师，部署在用户本机。你的任务是给出**可执行的具体决策建议**——
包括目标价、入场区、止损价、止盈价、仓位节奏（分批计划）。这不是投资建议风格——是私人决策辅助。

工具调用原则（硬约束）：
- 数字优先调 quant 工具（atr_stop / forward_pe_target / pe_percentile_revert / pyramid_buy / ...）拿客观数值。
- 工具不适用（美港股无历史 K 线、无一致 EPS、事件驱动股、重组股）时，工具会自动降级为 model_fallback；
  此时若你也无法给出有意义的推理，仅可在「目标价」字段作 LLM 推理，且必须列出依据的数据点。
- 止损价 / 入场区 / 止盈价 / 仓位百分比等硬性字段，一律用 quant 工具或 model_fallback 值，**禁止你凭空生成**。
- 每个数字必须能追溯到工具调用或显式假设；不要编造 ATR / 历史分位等关键参数。

依据类型标注：
- model：quant 工具完整公式（A 股数据齐全）
- model_fallback：工具因数据不足走简化公式（如固定 -8% 止损 / 近 60 日最低点）
- llm_reasoning：仅 target_price 字段允许，必须列推导依据
- hybrid：model 出基础值 + LLM 微调，必须列出调整项

{ANALYSIS_FRAMEWORK_AGENT}

当前页面上下文：
{{context}}"""

# 用于 LLM 调用前 .format(context=...)；保留 {context} 占位符
DECISION_NODE_PROMPT = """你正在生成一张结构化决策卡。基于已调用的 quant 工具结果，按下面 JSON Schema 输出（不要在 Markdown 内夹 JSON，由 Decision Node 统一拼装）：

必填字段：target_price, entry_low, entry_high, stop_loss, take_profit, cadence[{batch, pct, trigger, price}], explanation
依据字段：每个数字字段必须能映射到调过的某个 quant 工具的 model_version（Decision Node 自动填 model_versions_json）。

禁止：
- 不要自己编 ATR 值 / 历史分位值；这些只能从工具结果里读。
- target_price 是唯一允许你推理调整的字段，调整必须在 explanation 里列出依据数据点。
"""
```

- [ ] **Step 1.6：重写 `AGENTS.md` 红线段**

在 `/vol2/1000/code/stockclaw/AGENTS.md` 把「Compliance red lines」整段（约第 88-96 行）替换为：

```markdown
## 定位（个人本地部署）

本仓库（stockclaw 当前 fork）= **个人本地部署的 AI 原生投资分析平台**。是非投资建议风格——可给具体决策建议（目标价/止损/止盈/仓位节奏）。公开 main 仓库保持原「不荐股」红线，互不污染。

## 安全红线（不是合规红线）

- **本地部署，不分发、不开源此 fork**（公开 main 仓库是另一回事）
- **API 密钥本地化**：`VR_API_KEY` 等只存本地 `.env`，不入 git、不上传
- **不接真实券商 API**：仅做分析，不自动下单
- **决策卡内容含个人投资决策**：不入 git、不上传（`backend/.cache/` 已 gitignore）
- **客观数据层不污染**：`astock.py / gstock.py / market.py / newsradar.py` 永远只返回客观数据；决策建议只在 `agents/` 层产生。这条边界焊死——便于未来再分叉客观版本
```

同时把 `AGENTS.md` 顶部的「**never recommends, predicts, or scores**」段（约第 7 行）改为：

```markdown
Open-source personal AI investment-research dashboard fork, now upgraded to a **personal local AI-native investment platform**: A-share (primary) plus **US / HK / Korea** stocks. This fork gives **concrete decision recommendations** (target price / stop-loss / take-profit / position cadence). Public `main` branch keeps the original "never recommends" stance; this fork is personal/local only.
```

- [ ] **Step 1.7：验证老接口仍能 import**

```bash
cd /vol2/1000/code/stockclaw/backend
.venv/bin/python -c "import chat_legacy; print('chat_legacy ok'); import app; print('app ok')"
```

期望输出：
```
chat_legacy ok
app ok
```

如果报 `ModuleNotFoundError: No module named 'chat'`——说明 Step 1.3 / 1.4 漏改，回去补。

- [ ] **Step 1.8：验证 MCP server 仍能启动**

```bash
cd /vol2/1000/code/stockclaw/backend
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05"}}' | .venv/bin/python mcp_server.py
```

期望输出包含 `"name":"vibe-research"` 的 JSON-RPC response。

- [ ] **Step 1.9：commit**

```bash
cd /vol2/1000/code/stockclaw
git add backend/chat_legacy.py backend/agents/__init__.py backend/agents/prompts.py backend/app.py backend/mcp_server.py AGENTS.md
git status  # 确认 chat.py 重命名被识别为 rename
git commit -m "feat(agents): 物理隔离 chat.py → chat_legacy.py；新建 agents/prompts.py 解禁版提示词

- chat.py 物理改名为 chat_legacy.py，老 /api/chat 与 MCP server 仍可用
- app.py:21 / mcp_server.py:18 导入改为 chat_legacy
- 新建 agents/prompts.py：解禁版 SYSTEM_PROMPT_AGENT + 第六维「决策建议」+ DECISION_NODE_PROMPT
- AGENTS.md 红线段重写为「个人本地部署」定位（不再是合规红线，是安全红线）
- 不加 from chat_legacy import * 兼容垫片（保留物理隔离边界）"
```

---

## Task 2：Rate Limiter（cool-down 节流器）

**目标**：实现 `EastmoneyRateLimiter`——cool-down 1.0s，锁的 acquire 在 `__aenter__`、release 在 `__aexit__`，确保锁横跨整个业务逻辑。5 个并发 acquire 总耗时 ≥ 4s。

**Files:**
- Create: `backend/agents/rate_limiter.py`
- Test: `backend/tests/test_rate_limiter.py`

- [ ] **Step 2.1：写失败的并发压测**

```python
# backend/tests/test_rate_limiter.py
"""Rate Limiter 单测——验证 cool-down 节流，不是 Semaphore。

验收点（来自 spec §9 Phase 1 #2）：
- 锁必须横跨业务逻辑（acquire 在 __aenter__、release 在 __aexit__）
- 并发压测 5 个 acquire 总耗时 ≥ 4s
- 期间无两个 HTTP 请求时间重叠
"""
import asyncio
import time

import pytest

from agents.rate_limiter import EastmoneyRateLimiter


@pytest.mark.asyncio
async def test_sequential_5_acquires_takes_at_least_4_seconds():
    """5 个串行 acquire + 1.0s cool-down：第 1 个立即过，后续 4 个各等 1s → ≥ 4s。"""
    limiter = EastmoneyRateLimiter(cool_down=1.0)
    start = time.monotonic()

    async def task():
        async with limiter:
            await asyncio.sleep(0.05)  # 模拟业务请求耗时

    await asyncio.gather(*(task() for _ in range(5)))
    elapsed = time.monotonic() - start
    # 5 个 acquire × (1.0s cool-down + 0.05s 业务) ≈ 5.25s；放宽到 ≥ 4.0s 防止机器抖动
    assert elapsed >= 4.0, f"5 个串行 acquire 仅耗时 {elapsed:.2f}s，cool-down 失效（疑似 Semaphore 行为）"


@pytest.mark.asyncio
async def test_lock_held_during_business_logic():
    """锁必须在 __aexit__ 才释放——业务执行期间另一 task 拿不到锁。"""
    limiter = EastmoneyRateLimiter(cool_down=0.0)  # 关掉 cool-down，纯验锁语义
    held_during_business = False

    async def first():
        nonlocal held_during_business
        async with limiter:
            # 此时 second() 应该被卡住——它给的 holder_event 没收到
            await asyncio.sleep(0.1)
            held_during_business = True

    async def second():
        # 等 first() 进临界区后再尝试 acquire
        await asyncio.sleep(0.02)
        async with limiter:
            # 走到这里说明锁已释放——但若 first() 还在业务中，说明锁被过早释放
            pass

    await asyncio.gather(first(), second())
    assert held_during_business, "锁在业务结束前被释放（疑似 async with self._lock 立即释放 bug）"
```

- [ ] **Step 2.2：跑测试验证它失败（ImportError）**

```bash
cd /vol2/1000/code/stockclaw/backend
.venv/bin/python -m pytest tests/test_rate_limiter.py -v
```

期望：`ModuleNotFoundError: No module named 'agents.rate_limiter'`

- [ ] **Step 2.3：实现 Rate Limiter**

```python
# backend/agents/rate_limiter.py
"""东财数据源 cool-down 节流器（不是 Semaphore）。

东财 push2.eastmoney.com 等数据源有 ~1s 速率限制。`asyncio.Semaphore(1)` 只控并发度，
5 个 panel 串行下仍能在 200ms 内打完 5 个请求触发 403。

正确做法：__aenter__ 拿锁 + sleep cool-down；__aexit__ 才 release。锁的持有横跨整个业务
逻辑——不能用 `async with self._lock`（那会在 __aenter__ return 时立即释放，业务请求期间
锁已不在，限流形同虚设）。
"""
from __future__ import annotations

import asyncio
import os


class EastmoneyRateLimiter:
    """cool-down 节流器：__aenter__ acquire 锁 + sleep，__aexit__ 释放。"""

    def __init__(self, cool_down: float | None = None):
        # env 覆盖默认值（spec §8 .env：VR_AGENT_RATE_LIMIT_COOLDOWN=1.0）
        if cool_down is None:
            cool_down = float(os.environ.get("VR_AGENT_RATE_LIMIT_COOLDOWN", "1.0"))
        self._lock = asyncio.Lock()
        self._cool_down = cool_down
        self._last_release = 0.0  # monotonic 时间戳；0 表示从未释放过

    async def __aenter__(self) -> "EastmoneyRateLimiter":
        # 1. acquire，不进 with 块——锁释放延后到 __aexit__
        await self._lock.acquire()
        # 2. cool-down 等待：距上次 release 不足 cool_down 秒就补足
        now = asyncio.get_event_loop().time()
        wait = max(0.0, self._last_release + self._cool_down - now)
        if wait > 0:
            await asyncio.sleep(wait)
        return self

    async def __aexit__(self, *exc) -> None:
        try:
            # 3. 业务结束才更新 last_release
            self._last_release = asyncio.get_event_loop().time()
        finally:
            # 4. 确保锁一定被释放——用 try/finally 防 last_release 赋值抛异常时锁泄漏
            self._lock.release()


# 全局单例：所有 @tool 调东财数据源时共享
eastmoney_limiter = EastmoneyRateLimiter()
```

- [ ] **Step 2.4：跑测试验证通过**

```bash
cd /vol2/1000/code/stockclaw/backend
.venv/bin/python -m pytest tests/test_rate_limiter.py -v
```

期望：2 个测试通过。注意第一个测试要跑 ~5s（cool-down 真生效）。

如果第一个测试 < 4s 失败——说明你的实现用了 `async with self._lock`（return 时立即释放）。改回显式 `acquire()` + `release()`。

- [ ] **Step 2.5：commit**

```bash
git add backend/agents/rate_limiter.py backend/tests/test_rate_limiter.py
git commit -m "feat(agents): EastmoneyRateLimiter cool-down 节流器（不是 Semaphore）

- 锁横跨业务逻辑：__aenter__ acquire + sleep，__aexit__ 才 release
- 全局单例 eastmoney_limiter；cool_down 默认 1.0s，env VR_AGENT_RATE_LIMIT_COOLDOWN 可改
- 并发压测 5 个 acquire ≥ 4s，防止 5 个 panel 串行打东财 403"
```

---

## Task 3：quant.valuation（前向 PE 目标价 + 历史分位回复）

**目标**：3 个纯 Python 函数（无 LLM）——`forward_pe_target` / `pe_percentile_revert` / `pb_percentile_revert`。统一返回 `{tool, inputs, outputs, basis_type, model_version, model_assumptions, citations, explanation}`。美港股或数据缺失抛 `DataUnavailable`。

**Files:**
- Create: `backend/quant/__init__.py`
- Create: `backend/quant/valuation.py`
- Test: `backend/tests/test_quant_valuation.py`

- [ ] **Step 3.1：写失败的 forward_pe_target 单测**

```python
# backend/tests/test_quant_valuation.py
"""quant.valuation 单测——纯逻辑、无网络（mock astock.full_valuation）。"""
from unittest.mock import patch

import pytest

import quant.valuation as v


def _mock_full_valuation(price=1685.0, pe_ttm=18.0, eps_26e=85.0, eps_27e=95.0):
    """构造 astock.full_valuation 的 mock 返回。"""
    return {
        "code": "600519", "name": "贵州茅台", "price": price,
        "pe_ttm": pe_ttm, "pb": 6.0,
        "eps_26e": eps_26e, "eps_27e": eps_27e,
        "pe_26e": price / eps_26e if eps_26e else None,
        "cagr_pct": 0.15, "peg": None, "digest_years": None,
        "analyst_count": 30, "mcap_yi": 15000,
    }


def test_forward_pe_target_basic():
    """前向 PE × 一致 EPS = 目标价。"""
    with patch("quant.valuation.astock.full_valuation", return_value=_mock_full_valuation(price=1685.0, eps_27e=95.0)):
        result = v.forward_pe_target("600519", target_pe=20.0, eps_year="27e")
    assert result["tool"] == "forward_pe_target"
    assert result["basis_type"] == "model"
    assert result["model_version"] == "forward_pe_target.v1"
    assert result["outputs"]["target_price"] == pytest.approx(20.0 * 95.0, rel=1e-3)  # 1900
    assert result["outputs"]["current_price"] == 1685.0
    assert "citations" in result and result["citations"][0]["source"] == "astock.full_valuation"


def test_forward_pe_target_data_unavailable_when_no_eps():
    """一致 EPS 缺失（None）→ 抛 DataUnavailable，由上层降级。"""
    with patch("quant.valuation.astock.full_valuation", return_value=_mock_full_valuation(eps_27e=None)):
        with pytest.raises(v.DataUnavailable) as exc_info:
            v.forward_pe_target("600519", target_pe=20.0, eps_year="27e")
        assert "一致 EPS" in str(exc_info.value)


def test_pe_percentile_revert_basic():
    """PE 处于历史 80 分位 → 回复到 50 分位的目标价。"""
    mock_pct = {
        "metrics": {
            "pe_ttm": {
                "current": 30.0, "percentile": 0.80,
                "p20": 15.0, "p50": 22.0, "p80": 32.0, "min": 10.0, "max": 40.0, "n": 1200,
            }
        }
    }
    mock_quote = {"600519": {"price": 1685.0, "pe_ttm": 30.0, "name": "贵州茅台"}}
    with patch("quant.valuation.astock.valuation_percentile", return_value=mock_pct), \
         patch("quant.valuation.astock.tencent_quote", return_value=mock_quote):
        result = v.pe_percentile_revert("600519", revert_to=0.50)
    assert result["basis_type"] == "model"
    # 目标价 = 当前价 × (50 分位 PE / 当前 PE) = 1685 × (22 / 30) ≈ 1235.67
    assert result["outputs"]["target_price"] == pytest.approx(1685.0 * 22.0 / 30.0, rel=1e-3)
    assert result["outputs"]["current_percentile"] == 0.80
    assert result["outputs"]["revert_to"] == 0.50
```

- [ ] **Step 3.2：跑测试验证它失败**

```bash
cd /vol2/1000/code/stockclaw/backend
.venv/bin/python -m pytest tests/test_quant_valuation.py -v
```

期望：`ModuleNotFoundError: No module named 'quant.valuation'`

- [ ] **Step 3.3：建 quant 包 + 实现 valuation**

```python
# backend/quant/__init__.py
"""定量工具层（纯 Python 函数，无 LLM）。

每个函数返回统一 contract：
{
    "tool": "atr_stop",
    "inputs": {...},
    "outputs": {...},
    "basis_type": "model" | "model_fallback" | "llm_reasoning" | "hybrid",
    "model_version": "atr_stop.v1",
    "model_assumptions": ["..."],
    "citations": [{"source": "astock.kline", "code": "...", "range": "..."}],
    "explanation": "..."
}

数据源单一：只调 astock.py / gstock.py；不直接打 HTTP。
"""
```

```python
# backend/quant/valuation.py
"""估值类工具：前向 PE 目标价 + 历史分位回复。

纯 Python 函数，无 LLM。A 股数据齐走完整公式（model）；美港股或数据缺失抛 DataUnavailable，
由调用方（agents.tools）决定走 model_fallback 还是 llm_reasoning。
"""
from __future__ import annotations

from typing import Any

import astock


class DataUnavailable(Exception):
    """quant 工具因数据不足无法走完整公式。调用方应降级为 model_fallback 或 llm_reasoning。"""


def _contract(tool: str, inputs: dict, outputs: dict, model_version: str,
              assumptions: list[str], citations: list[dict], explanation: str,
              basis_type: str = "model") -> dict[str, Any]:
    """统一 contract 拼装。"""
    return {
        "tool": tool,
        "inputs": inputs,
        "outputs": outputs,
        "basis_type": basis_type,
        "model_version": model_version,
        "model_assumptions": assumptions,
        "citations": citations,
        "explanation": explanation,
    }


def forward_pe_target(code: str, target_pe: float = 20.0, eps_year: str = "27e") -> dict:
    """前向 PE × 一致 EPS = 目标价。

    code: A 股 6 位代码
    target_pe: 目标前向 PE（用户/LLM 给定，如行业均值 20x）
    eps_year: 用哪年的一致 EPS（"26e" / "27e"）

    返回 outputs: {target_price, current_price, current_pe, eps_used, target_pe}
    抛 DataUnavailable：当一致 EPS 缺失时（美港股 / A 股无覆盖）。
    """
    fv = astock.full_valuation(code)
    eps = fv.get(f"eps_{eps_year}")
    if not eps:
        raise DataUnavailable(f"{code} 缺 {eps_year} 一致 EPS，前向 PE 目标价不可计算")
    target_price = target_pe * eps
    return _contract(
        tool="forward_pe_target",
        inputs={"code": code, "target_pe": target_pe, "eps_year": eps_year},
        outputs={
            "target_price": round(target_price, 2),
            "current_price": fv.get("price"),
            "current_pe": fv.get("pe_ttm"),
            "eps_used": eps,
            "target_pe": target_pe,
        },
        model_version="forward_pe_target.v1",
        assumptions=[f"目标前向 PE = {target_pe}x", f"一致 EPS（{eps_year}）= {eps}"],
        citations=[{"source": "astock.full_valuation", "code": code}],
        explanation=f"目标价 {target_price:.2f} = 目标 PE {target_pe}x × 一致 EPS {eps}（{eps_year}）",
    )


def pe_percentile_revert(code: str, revert_to: float = 0.50, period: str = "近五年") -> dict:
    """PE 历史分位回复：当前 PE 处于 X 分位 → 回复到 revert_to 分位的目标价。

    code: A 股 6 位代码
    revert_to: 目标分位（0.0-1.0），默认 0.50（中位数）
    period: 历史窗口（"近五年" / "近十年"）

    返回 outputs: {target_price, current_price, current_percentile, revert_to, current_pe, revert_pe}
    抛 DataUnavailable：当历史分位数据不可用时。
    """
    pct = astock.valuation_percentile(code, period=period)
    pe_metric = (pct.get("metrics") or {}).get("pe_ttm")
    if not pe_metric or pe_metric.get("current") is None:
        raise DataUnavailable(f"{code} 缺 PE 历史分位数据")

    # 选目标分位对应的 PE：用线性插值近似（p20/p50/p80 是已知锚点）
    current_pe = pe_metric["current"]
    current_pct = pe_metric.get("percentile", 0.5)
    p20, p50, p80 = pe_metric.get("p20"), pe_metric.get("p50"), pe_metric.get("p80")
    revert_pe = _interp_percentile(revert_to, p20, p50, p80, pe_metric.get("min"), pe_metric.get("max"))
    if revert_pe is None:
        raise DataUnavailable(f"{code} PE 分位锚点不足，无法插值到 {revert_to}")

    quote = astock.tencent_quote([code])
    current_price = quote.get(code, {}).get("price")
    if not current_price:
        raise DataUnavailable(f"{code} 当前价缺失")

    # 目标价 = 当前价 × (目标 PE / 当前 PE)
    target_price = current_price * revert_pe / current_pe

    return _contract(
        tool="pe_percentile_revert",
        inputs={"code": code, "revert_to": revert_to, "period": period},
        outputs={
            "target_price": round(target_price, 2),
            "current_price": current_price,
            "current_percentile": current_pct,
            "revert_to": revert_to,
            "current_pe": current_pe,
            "revert_pe": revert_pe,
        },
        model_version="pe_percentile_revert.v1",
        assumptions=[f"目标分位 {revert_to}（{period}）", f"线性插值（p20={p20} / p50={p50} / p80={p80}）"],
        citations=[
            {"source": "astock.valuation_percentile", "code": code, "range": period},
            {"source": "astock.tencent_quote", "code": code},
        ],
        explanation=f"当前 PE {current_pe}（{current_pct:.0%} 分位），回复到 {revert_to:.0%} 分位 PE {revert_pe:.2f}，目标价 {target_price:.2f}",
    )


def pb_percentile_revert(code: str, revert_to: float = 0.50, period: str = "近五年") -> dict:
    """PB 历史分位回复：同上，但用 PB。重资产行业（银行/钢铁）适用。"""
    pct = astock.valuation_percentile(code, period=period)
    pb_metric = (pct.get("metrics") or {}).get("pb")
    if not pb_metric or pb_metric.get("current") is None:
        raise DataUnavailable(f"{code} 缺 PB 历史分位数据")

    current_pb = pb_metric["current"]
    current_pct = pb_metric.get("percentile", 0.5)
    p20, p50, p80 = pb_metric.get("p20"), pb_metric.get("p50"), pb_metric.get("p80")
    revert_pb = _interp_percentile(revert_to, p20, p50, p80, pb_metric.get("min"), pb_metric.get("max"))
    if revert_pb is None:
        raise DataUnavailable(f"{code} PB 分位锚点不足")

    quote = astock.tencent_quote([code])
    current_price = quote.get(code, {}).get("price")
    if not current_price:
        raise DataUnavailable(f"{code} 当前价缺失")

    target_price = current_price * revert_pb / current_pb

    return _contract(
        tool="pb_percentile_revert",
        inputs={"code": code, "revert_to": revert_to, "period": period},
        outputs={
            "target_price": round(target_price, 2),
            "current_price": current_price,
            "current_percentile": current_pct,
            "revert_to": revert_to,
            "current_pb": current_pb,
            "revert_pb": revert_pb,
        },
        model_version="pb_percentile_revert.v1",
        assumptions=[f"目标分位 {revert_to}（{period}）"],
        citations=[
            {"source": "astock.valuation_percentile", "code": code, "range": period},
            {"source": "astock.tencent_quote", "code": code},
        ],
        explanation=f"当前 PB {current_pb}（{current_pct:.0%} 分位），回复到 {revert_to:.0%} 分位 PB {revert_pb:.2f}，目标价 {target_price:.2f}",
    )


def _interp_percentile(target: float, p20, p50, p80, p_min, p_max) -> float | None:
    """对分位目标做线性插值（用已知锚点）。

    target ∈ [0, 1]。锚点：min=0%, p20=20%, p50=50%, p80=80%, max=100%。
    缺锚点则用相邻已知点线性外推。
    """
    anchors = []
    if p_min is not None: anchors.append((0.0, p_min))
    if p20 is not None: anchors.append((0.20, p20))
    if p50 is not None: anchors.append((0.50, p50))
    if p80 is not None: anchors.append((0.80, p80))
    if p_max is not None: anchors.append((1.0, p_max))
    if len(anchors) < 2:
        return None
    anchors.sort()
    # 边界外推
    if target <= anchors[0][0]:
        x0, y0 = anchors[0]; x1, y1 = anchors[1]
    elif target >= anchors[-1][0]:
        x0, y0 = anchors[-2]; x1, y1 = anchors[-1]
    else:
        for i in range(len(anchors) - 1):
            if anchors[i][0] <= target <= anchors[i + 1][0]:
                x0, y0 = anchors[i]; x1, y1 = anchors[i + 1]
                break
        else:
            return None
    if x1 == x0:
        return y0
    return y0 + (y1 - y0) * (target - x0) / (x1 - x0)
```

- [ ] **Step 3.4：跑测试验证通过**

```bash
cd /vol2/1000/code/stockclaw/backend
.venv/bin/python -m pytest tests/test_quant_valuation.py -v
```

期望：3 个测试通过。

- [ ] **Step 3.5：commit**

```bash
git add backend/quant/__init__.py backend/quant/valuation.py backend/tests/test_quant_valuation.py
git commit -m "feat(quant): valuation 工具——forward_pe_target + pe/pb_percentile_revert

- 统一 contract：tool / inputs / outputs / basis_type / model_version / citations / explanation
- 一致 EPS 缺失 / 历史分位缺失时抛 DataUnavailable（由上层降级）
- 纯 Python，无 LLM；单测 mock astock 不联网"
```

---

## Task 4：quant.stops（ATR 止损 + 结构止损 + 风险反推仓位）

**目标**：3 个函数——`atr_stop`（A 股主路径 model；无 K 线时降级为 model_fallback：固定 -8% 或近 60 日最低点）/ `structure_stop`（近期低点）/ `risk_based_position`（按止损距离反推仓位，可选读 `portfolio.json::totals`）。

**Files:**
- Create: `backend/quant/stops.py`
- Test: `backend/tests/test_quant_stops.py`

- [ ] **Step 4.1：写失败的 atr_stop 单测**

```python
# backend/tests/test_quant_stops.py
"""quant.stops 单测——含 A 股主路径 + 美港股 model_fallback 降级链。"""
from unittest.mock import patch

import pytest

import quant.stops as s


def _mock_kline(rows=30, base=1685.0):
    """构造 astock.kline 的 mock 返回（rows 条日 K，含 high/low/close）。"""
    out = []
    for i in range(rows):
        # 模拟波动：日 high/low 上下浮动
        out.append({
            "date": f"2026-06-{i+1:02d}",
            "open": base - 5, "close": base + (i % 3 - 1) * 3,
            "high": base + 20, "low": base - 25,
            "vol": 100000, "amount": 168500000.0,
        })
    return out


def test_atr_stop_main_path_a_share():
    """A 股主路径：14 日 ATR × 2.0 倍数 → stop_price。basis_type: model。"""
    with patch("quant.stops.astock.kline", return_value=_mock_kline(rows=30)):
        result = s.atr_stop("600519", period=14, multiplier=2.0)
    assert result["basis_type"] == "model"
    assert result["model_version"] == "atr_stop.v1"
    assert "stop_price" in result["outputs"]
    assert "current_price" in result["outputs"]
    assert "distance_pct" in result["outputs"]
    assert result["outputs"]["stop_price"] < result["outputs"]["current_price"]


def test_atr_stop_no_kline_falls_back_to_fixed_pct():
    """K 线数据不足 → model_fallback：当前价 × (1 - 0.08)。"""
    # 模拟空 K 线（mootdx 未装或返回空）
    with patch("quant.stops.astock.kline", return_value=[]), \
         patch("quant.stops.astock.tencent_quote", return_value={"600519": {"price": 100.0, "name": "测试"}}):
        result = s.atr_stop("600519", period=14, multiplier=2.0)
    assert result["basis_type"] == "model_fallback"
    assert result["outputs"]["stop_price"] == pytest.approx(92.0, rel=1e-3)  # 100 × 0.92
    assert "fallback_reason" in result["outputs"]
    assert result["outputs"]["fallback_reason"]


def test_atr_stop_us_stock_falls_back():
    """美港股代码（非 6 位数字）→ 直接走 model_fallback。"""
    with patch("quant.stops.astock.tencent_quote", return_value={"AAPL": {"price": 200.0, "name": "Apple"}}):
        result = s.atr_stop("AAPL", period=14, multiplier=2.0)
    assert result["basis_type"] == "model_fallback"
    assert result["outputs"]["stop_price"] == pytest.approx(184.0, rel=1e-3)


def test_structure_stop_uses_recent_low():
    """结构止损 = 近 60 日最低价。"""
    with patch("quant.stops.astock.kline", return_value=_mock_kline(rows=60)):
        result = s.structure_stop("600519", lookback=60)
    assert result["basis_type"] == "model"
    assert "stop_price" in result["outputs"]
    # _mock_kline 的 low 都是 base - 25 = 1660
    assert result["outputs"]["stop_price"] == pytest.approx(1660.0, rel=1e-3)


def test_risk_based_position_basic():
    """按止损距离反推仓位：风险 1% × 总净值 / 单股止损距离 = 股数。"""
    result = s.risk_based_position(
        entry_price=100.0, stop_price=92.0,
        total_equity=100000.0, risk_tolerance_pct=0.01,
    )
    assert result["basis_type"] == "model"
    # 单股风险 = 100 - 92 = 8；总风险 = 100000 × 0.01 = 1000；股数 = 1000 / 8 = 125
    assert result["outputs"]["shares"] == pytest.approx(125.0, rel=1e-3)
    assert result["outputs"]["position_value"] == pytest.approx(12500.0, rel=1e-3)


def test_risk_based_position_falls_back_to_pct_when_no_cash():
    """无 cash/equity → 用比例表达（30%/30%/40%），不算绝对股数。"""
    result = s.risk_based_position(entry_price=100.0, stop_price=92.0)
    assert result["basis_type"] == "model_fallback"
    assert "shares" not in result["outputs"] or result["outputs"]["shares"] is None
    assert "position_pct" in result["outputs"]
```

- [ ] **Step 4.2：跑测试验证失败**

```bash
cd /vol2/1000/code/stockclaw/backend
.venv/bin/python -m pytest tests/test_quant_stops.py -v
```

期望：`ModuleNotFoundError: No module named 'quant.stops'`

- [ ] **Step 4.3：实现 stops**

```python
# backend/quant/stops.py
"""止损类工具：ATR 止损 / 结构止损 / 风险反推仓位。

A 股主路径走完整公式（model）；K 线缺失或美港股代码 → Python 简化公式降级（model_fallback）。
不让 LLM 编 ATR / 历史分位——硬约束：stop_loss 字段必须由 Python 算。
"""
from __future__ import annotations

import json
import math
import os
from typing import Any

import astock


# portfolio.json 路径（spec §8：backend/.cache/portfolio.json）
_HERE = os.path.dirname(os.path.abspath(__file__))
_PF_FILE = os.path.normpath(os.path.join(_HERE, "..", ".cache", "portfolio.json"))

# 默认 fallback 止损百分比（spec §6 约束 1：8% 固定止损）
_FALLBACK_STOP_PCT = 0.08


def _is_a_share_code(code: str) -> bool:
    return code.isdigit() and len(code) == 6


def _atr_stop_main_path(klines: list[dict], period: int, multiplier: float) -> tuple[float, float] | None:
    """计算 ATR × multiplier 止损距离。返回 (atr, stop_distance)。失败返回 None。

    ATR = True Range 的 N 日均值。TR = max(high-low, |high-prev_close|, |low-prev_close|)。
    """
    if len(klines) < period + 1:
        return None
    trs = []
    for i in range(1, len(klines)):
        prev_close = klines[i - 1].get("close")
        high = klines[i].get("high")
        low = klines[i].get("low")
        if prev_close is None or high is None or low is None:
            continue
        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close),
        )
        trs.append(tr)
    if len(trs) < period:
        return None
    atr = sum(trs[-period:]) / period
    return atr, atr * multiplier


def atr_stop(code: str, period: int = 14, multiplier: float = 2.0) -> dict[str, Any]:
    """ATR 止损：A 股主路径走 K 线计算，K 线缺失/美港股降级为 model_fallback（固定 -8%）。

    code: 股票代码（A 股 6 位 / 美股字母 / 港股数字）
    period: ATR 周期（默认 14）
    multiplier: ATR 倍数（保守 2.0、激进 1.5）

    返回 outputs: {stop_price, current_price, distance_pct, basis_type, atr?, fallback_reason?}
    """
    # L1 model：A 股代码 + K 线齐全
    if _is_a_share_code(code):
        try:
            klines = astock.kline(code, category=4, offset=period + 30)
        except Exception:
            klines = []
        result = _atr_stop_main_path(klines, period, multiplier)
        if result is not None:
            atr, stop_distance = result
            quote = astock.tencent_quote([code])
            current = quote.get(code, {}).get("price")
            if current:
                stop_price = round(current - stop_distance, 2)
                return _contract(
                    tool="atr_stop",
                    inputs={"code": code, "period": period, "multiplier": multiplier},
                    outputs={
                        "stop_price": stop_price,
                        "current_price": current,
                        "distance_pct": round((stop_price - current) / current * 100, 2),
                        "atr": round(atr, 4),
                    },
                    model_version="atr_stop.v1",
                    assumptions=[f"{period}-day ATR", f"{multiplier}x multiplier"],
                    citations=[{"source": "astock.kline", "code": code, "range": f"近 {period + 30} 日"}],
                    explanation=f"基于 {period} 日 ATR={atr:.2f}，乘以 {multiplier}x 倍数，止损价 {current:.2f} - {stop_distance:.2f} = {stop_price:.2f}",
                )

    # L2 model_fallback：固定百分比止损
    quote = astock.tencent_quote([code]) if _is_a_share_code(code) else _global_current_price(code)
    current = quote if isinstance(quote, (int, float)) else (quote.get(code, {}) if quote else {}).get("price")
    if not current:
        # L4 直接拒答：连当前价都拿不到
        raise DataUnavailable(f"{code} 当前价缺失，无法计算止损")

    stop_price = round(current * (1 - _FALLBACK_STOP_PCT), 2)
    fallback_reason = "no_kline" if _is_a_share_code(code) else "non_a_share_code"
    return _contract(
        tool="atr_stop",
        inputs={"code": code, "period": period, "multiplier": multiplier},
        outputs={
            "stop_price": stop_price,
            "current_price": current,
            "distance_pct": round((stop_price - current) / current * 100, 2),
            "fallback_reason": fallback_reason,
        },
        model_version="atr_stop.v1",
        assumptions=[f"数据不足降级：固定 -{_FALLBACK_STOP_PCT:.0%} 止损"],
        citations=[{"source": "astock.tencent_quote", "code": code}],
        explanation=f"K 线/历史数据不足（{fallback_reason}），降级为固定 -{_FALLBACK_STOP_PCT:.0%} 止损，止损价 {stop_price}",
        basis_type="model_fallback",
    )


def _global_current_price(code: str) -> float | None:
    """美港股 / 韩股当前价（走 gstock）。"""
    try:
        import gstock
        data = gstock.us_hk_stock(code)
        return data.get("quote", {}).get("price") if data else None
    except Exception:
        return None


def structure_stop(code: str, lookback: int = 60) -> dict[str, Any]:
    """结构止损：近 lookback 日最低价。

    无 K 线时降级为 model_fallback（固定 -8%）。
    """
    if _is_a_share_code(code):
        try:
            klines = astock.kline(code, category=4, offset=lookback)
        except Exception:
            klines = []
        lows = [k.get("low") for k in klines if k.get("low") is not None]
        if lows:
            stop = min(lows)
            quote = astock.tencent_quote([code])
            current = quote.get(code, {}).get("price")
            if current:
                return _contract(
                    tool="structure_stop",
                    inputs={"code": code, "lookback": lookback},
                    outputs={
                        "stop_price": stop,
                        "current_price": current,
                        "distance_pct": round((stop - current) / current * 100, 2),
                    },
                    model_version="structure_stop.v1",
                    assumptions=[f"近 {lookback} 日最低价"],
                    citations=[{"source": "astock.kline", "code": code, "range": f"近 {lookback} 日"}],
                    explanation=f"结构止损 = 近 {lookback} 日最低价 {stop}",
                )

    # 降级
    return atr_stop(code, period=14, multiplier=2.0)  # 复用 atr_stop 的 fallback


def risk_based_position(entry_price: float, stop_price: float,
                        total_equity: float | None = None,
                        risk_tolerance_pct: float | None = None) -> dict[str, Any]:
    """按止损距离反推仓位：单笔风险 = 总净值 × 风险容忍度；股数 = 单笔风险 / 单股止损距离。

    未传 total_equity / risk_tolerance_pct 时，尝试从 portfolio.json::totals 读。
    两者都缺 → 返回 model_fallback，仅给比例（30%/30%/40%），不算绝对股数。
    """
    # 读 portfolio.json::totals（若未显式传参）
    if total_equity is None or risk_tolerance_pct is None:
        totals = _read_portfolio_totals()
        if total_equity is None:
            total_equity = totals.get("total_equity_override") or totals.get("available_cash")
        if risk_tolerance_pct is None:
            risk_tolerance_pct = totals.get("risk_tolerance_pct", 0.01)

    per_share_risk = entry_price - stop_price
    if per_share_risk <= 0:
        raise DataUnavailable(f"止损价 {stop_price} ≥ 入场价 {entry_price}，单股风险非正")

    if not total_equity:
        # 降级：比例表达
        return _contract(
            tool="risk_based_position",
            inputs={"entry_price": entry_price, "stop_price": stop_price},
            outputs={
                "position_pct": [0.30, 0.30, 0.40],  # 默认 3 批：30/30/40
                "per_share_risk": round(per_share_risk, 4),
                "fallback_reason": "no_total_equity",
            },
            model_version="risk_based_position.v1",
            assumptions=["缺总净值/可用现金，降级为默认比例 30%/30%/40%"],
            citations=[{"source": "portfolio.json::totals", "note": "available_cash / risk_tolerance_pct 未设"}],
            explanation="portfolio.json::totals 未配 available_cash，无法算绝对股数；按 30%/30%/40% 比例表达",
            basis_type="model_fallback",
        )

    if risk_tolerance_pct is None:
        risk_tolerance_pct = 0.01
    total_risk = total_equity * risk_tolerance_pct
    shares = total_risk / per_share_risk
    return _contract(
        tool="risk_based_position",
        inputs={
            "entry_price": entry_price, "stop_price": stop_price,
            "total_equity": total_equity, "risk_tolerance_pct": risk_tolerance_pct,
        },
        outputs={
            "shares": round(shares, 0),
            "position_value": round(shares * entry_price, 2),
            "position_pct_of_equity": round(shares * entry_price / total_equity, 4),
            "per_share_risk": round(per_share_risk, 4),
            "total_risk": round(total_risk, 2),
        },
        model_version="risk_based_position.v1",
        assumptions=[f"单笔风险容忍 = 总净值 × {risk_tolerance_pct:.1%}", f"单股止损距离 = {per_share_risk:.2f}"],
        citations=[{"source": "portfolio.json::totals", "fields": "available_cash / risk_tolerance_pct"}],
        explanation=f"总风险预算 {total_risk:.0f}（总净值 {total_equity:.0f} × {risk_tolerance_pct:.1%}）/ 单股风险 {per_share_risk:.2f} = {shares:.0f} 股",
    )


def _read_portfolio_totals() -> dict:
    """读 portfolio.json::totals（向后兼容老 JSON 无此字段时返回 {})."""
    try:
        with open(_PF_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data.get("totals") or {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _contract(tool, inputs, outputs, model_version, assumptions, citations, explanation, basis_type="model"):
    return {
        "tool": tool, "inputs": inputs, "outputs": outputs,
        "basis_type": basis_type, "model_version": model_version,
        "model_assumptions": assumptions, "citations": citations, "explanation": explanation,
    }


class DataUnavailable(Exception):
    """止损价等硬性字段算不出（如当前价缺失）——直接拒答，不输出半成品。"""
```

- [ ] **Step 4.4：跑测试验证通过**

```bash
cd /vol2/1000/code/stockclaw/backend
.venv/bin/python -m pytest tests/test_quant_stops.py -v
```

期望：6 个测试通过。

- [ ] **Step 4.5：commit**

```bash
git add backend/quant/stops.py backend/tests/test_quant_stops.py
git commit -m "feat(quant): stops 工具——atr_stop + structure_stop + risk_based_position

- atr_stop：A 股主路径 14 日 ATR × 2.0 倍数；K 线缺失/美港股自动降级为 model_fallback（固定 -8%）
- structure_stop：近 60 日最低价；无 K 线时复用 atr_stop fallback
- risk_based_position：按止损距离反推股数；无 cash/equity 时降级为 30%/30%/40% 比例
- 统一 contract：basis_type / model_version / citations / explanation / fallback_reason"
```

---

## Task 5：quant.cadence（金字塔加仓 + 分批建仓 + 定投）

**目标**：3 个纯 Python 函数——`pyramid_buy`（金字塔加仓：底部大、顶部小）/ `batch_build`（分批建仓：等额 N 批 + 触发条件）/ `dca_plan`（定投：固定周期 + 固定金额）。

**Files:**
- Create: `backend/quant/cadence.py`
- Test: `backend/tests/test_quant_cadence.py`

- [ ] **Step 5.1：写失败的 cadence 单测**

```python
# backend/tests/test_quant_cadence.py
"""quant.cadence 单测——纯逻辑、无网络。"""
import pytest

import quant.cadence as c


def test_pyramid_buy_basic():
    """金字塔加仓：3 批，比例 40%/30%/30%，触发价递增。"""
    result = c.pyramid_buy(
        current_price=100.0,
        total_budget=100000.0,
        batches=3,
        ratios=[0.40, 0.30, 0.30],
        triggers=["immediate", "pullback_to:95", "breakout_above:105"],
    )
    assert result["basis_type"] == "model"
    plan = result["outputs"]["plan"]
    assert len(plan) == 3
    assert plan[0]["batch"] == 1
    assert plan[0]["pct"] == 0.40
    assert plan[0]["amount"] == pytest.approx(40000.0, rel=1e-3)
    assert plan[0]["trigger"] == "immediate"
    assert plan[0]["ref_price"] == 100.0
    assert plan[1]["trigger"] == "pullback_to:95"
    assert plan[1]["ref_price"] == 95.0
    assert plan[2]["trigger"] == "breakout_above:105"
    assert plan[2]["ref_price"] == 105.0


def test_pyramid_buy_rejects_invalid_ratios():
    """比例之和 != 1.0 → 抛 ValueError。"""
    with pytest.raises(ValueError, match="比例之和"):
        c.pyramid_buy(
            current_price=100.0, total_budget=100000.0, batches=3,
            ratios=[0.5, 0.3, 0.3],  # 和 = 1.1
            triggers=["immediate", "pullback_to:95", "breakout_above:105"],
        )


def test_batch_build_basic():
    """分批建仓：等额 4 批，每周一批。"""
    result = c.batch_build(
        total_budget=80000.0, batches=4,
        schedule="weekly", start_price=100.0,
    )
    assert result["basis_type"] == "model"
    plan = result["outputs"]["plan"]
    assert len(plan) == 4
    for i, batch in enumerate(plan):
        assert batch["pct"] == pytest.approx(0.25, rel=1e-3)
        assert batch["amount"] == pytest.approx(20000.0, rel=1e-3)
        assert batch["trigger"] == f"day_offset:{i*7}"


def test_dca_plan_basic():
    """定投：12 周，每周 5000 元。"""
    result = c.dca_plan(
        periodic_amount=5000.0, periods=12,
        schedule="weekly",
    )
    assert result["basis_type"] == "model"
    plan = result["outputs"]["plan"]
    assert len(plan) == 12
    assert plan[0]["amount"] == 5000.0
    assert plan[0]["trigger"] == "day_offset:0"
    assert plan[11]["trigger"] == "day_offset:77"  # 11 周 × 7 天
    assert result["outputs"]["total_invested"] == 60000.0
```

- [ ] **Step 5.2：跑测试验证失败**

```bash
cd /vol2/1000/code/stockclaw/backend
.venv/bin/python -m pytest tests/test_quant_cadence.py -v
```

期望：`ModuleNotFoundError: No module named 'quant.cadence'`

- [ ] **Step 5.3：实现 cadence**

```python
# backend/quant/cadence.py
"""仓位节奏类工具：金字塔加仓 / 分批建仓 / 定投。

纯 Python 函数，无 LLM。返回统一 contract（basis_type 永远是 model——这些是策略规则，
不依赖外部数据可用性；具体执行时的价格由 Decision Node 在合并时给定）。
"""
from __future__ import annotations

from typing import Any


def _contract(tool, inputs, outputs, model_version, assumptions, explanation, basis_type="model"):
    return {
        "tool": tool, "inputs": inputs, "outputs": outputs,
        "basis_type": basis_type, "model_version": model_version,
        "model_assumptions": assumptions,
        "citations": [{"source": "internal.strategy", "note": "用户配置的策略规则"}],
        "explanation": explanation,
    }


def _parse_trigger_price(trigger: str, default: float) -> float:
    """从 trigger 字符串解析参考价。

    支持 "immediate" / "pullback_to:95" / "breakout_above:105" / "day_offset:7"
    """
    if ":" in trigger:
        try:
            return float(trigger.split(":", 1)[1])
        except (ValueError, IndexError):
            return default
    return default


def pyramid_buy(current_price: float, total_budget: float, batches: int,
                ratios: list[float], triggers: list[str]) -> dict[str, Any]:
    """金字塔加仓：底部仓位大、顶部小；触发价递增。

    current_price: 当前价（决定第一批 ref_price）
    total_budget: 总预算
    batches: 批次数（必须 == len(ratios) == len(triggers)）
    ratios: 每批占比，和必须 = 1.0（如 [0.40, 0.30, 0.30]）
    triggers: 触发条件（如 ["immediate", "pullback_to:95", "breakout_above:105"]）

    返回 outputs: {plan: [{batch, pct, amount, trigger, ref_price}, ...]}
    """
    if not (len(ratios) == batches == len(triggers)):
        raise ValueError(f"批次数 / ratios / triggers 长度不一致：{batches} / {len(ratios)} / {len(triggers)}")
    if abs(sum(ratios) - 1.0) > 1e-6:
        raise ValueError(f"比例之和必须 = 1.0，当前 = {sum(ratios):.4f}")

    plan = []
    for i, (ratio, trigger) in enumerate(zip(ratios, triggers)):
        ref_price = current_price if i == 0 else _parse_trigger_price(trigger, current_price)
        plan.append({
            "batch": i + 1,
            "pct": round(ratio, 4),
            "amount": round(total_budget * ratio, 2),
            "trigger": trigger,
            "ref_price": ref_price,
        })

    return _contract(
        tool="pyramid_buy",
        inputs={"current_price": current_price, "total_budget": total_budget, "batches": batches,
                "ratios": ratios, "triggers": triggers},
        outputs={"plan": plan},
        model_version="pyramid_buy.v1",
        assumptions=[f"{batches} 批金字塔", f"比例 {ratios}", "触发条件见 plan"],
        explanation=f"金字塔加仓：底部 {ratios[0]:.0%}、递增触发；总预算 {total_budget} 拆为 {batches} 批",
    )


def batch_build(total_budget: float, batches: int, schedule: str, start_price: float) -> dict[str, Any]:
    """分批建仓：等额 N 批，按周期触发（不择时，平摊成本）。

    schedule: "weekly" / "biweekly" / "monthly"
    """
    if batches <= 0:
        raise ValueError("批次数必须 > 0")
    schedule_days = {"weekly": 7, "biweekly": 14, "monthly": 30}
    days = schedule_days.get(schedule, 7)
    per = total_budget / batches
    plan = []
    for i in range(batches):
        plan.append({
            "batch": i + 1,
            "pct": round(1.0 / batches, 4),
            "amount": round(per, 2),
            "trigger": f"day_offset:{i * days}",
            "ref_price": start_price,  # 不择时，按当时市价
        })

    return _contract(
        tool="batch_build",
        inputs={"total_budget": total_budget, "batches": batches, "schedule": schedule, "start_price": start_price},
        outputs={"plan": plan},
        model_version="batch_build.v1",
        assumptions=[f"等额 {batches} 批", f"{schedule} 触发", "不择时，平摊成本"],
        explanation=f"分批建仓：{total_budget} 拆为 {batches} 批 × {per:.2f}，每 {days} 天一批",
    )


def dca_plan(periodic_amount: float, periods: int, schedule: str) -> dict[str, Any]:
    """定投：固定周期 + 固定金额。

    periodic_amount: 每次定投金额
    periods: 期数
    schedule: "weekly" / "biweekly" / "monthly"
    """
    if periodic_amount <= 0 or periods <= 0:
        raise ValueError("金额和期数必须 > 0")
    schedule_days = {"weekly": 7, "biweekly": 14, "monthly": 30}
    days = schedule_days.get(schedule, 7)
    plan = []
    for i in range(periods):
        plan.append({
            "batch": i + 1,
            "amount": periodic_amount,
            "trigger": f"day_offset:{i * days}",
        })

    return _contract(
        tool="dca_plan",
        inputs={"periodic_amount": periodic_amount, "periods": periods, "schedule": schedule},
        outputs={"plan": plan, "total_invested": round(periodic_amount * periods, 2)},
        model_version="dca_plan.v1",
        assumptions=[f"每 {schedule} 投 {periodic_amount}", f"共 {periods} 期"],
        explanation=f"定投：每 {days} 天投 {periodic_amount}，共 {periods} 期，总投入 {periodic_amount * periods:.2f}",
    )
```

- [ ] **Step 5.4：跑测试验证通过**

```bash
cd /vol2/1000/code/stockclaw/backend
.venv/bin/python -m pytest tests/test_quant_cadence.py -v
```

期望：4 个测试通过。

- [ ] **Step 5.5：commit**

```bash
git add backend/quant/cadence.py backend/tests/test_quant_cadence.py
git commit -m "feat(quant): cadence 工具——pyramid_buy + batch_build + dca_plan

- pyramid_buy：金字塔加仓，比例和必须 = 1.0；trigger 解析 ref_price
- batch_build：等额分批，weekly/biweekly/monthly
- dca_plan：定投固定金额 + 期数
- 纯策略规则函数，basis_type 恒为 model"
```

---

## Task 6：portfolio.json 字段扩展

**目标**：`portfolio.py::get_portfolio()` 返回的 `totals` 新增 `available_cash` / `risk_tolerance_pct` / `total_equity_override` 字段，向后兼容老 JSON（缺这些字段时给默认值）。

**Files:**
- Modify: `backend/portfolio.py:99-137`（`get_portfolio` 函数）
- Test: `backend/tests/test_portfolio_totals.py`

- [ ] **Step 6.1：写失败的单测**

```python
# backend/tests/test_portfolio_totals.py
"""portfolio.json totals 字段扩展——向后兼容老 JSON。"""
import json
from unittest.mock import patch

import portfolio as pf


def _mock_portfolio_file(tmp_path, data):
    """patch PF_FILE 到 tmp_path/portfolio.json。"""
    f = tmp_path / "portfolio.json"
    f.write_text(json.dumps(data, ensure_ascii=False))
    return str(f)


def test_get_portfolio_returns_totals_with_new_fields(tmp_path):
    """totals 必须含 available_cash / risk_tolerance_pct / total_equity_override。"""
    pf_file = tmp_path / "portfolio.json"
    pf_file.write_text(json.dumps({
        "holdings": [],
        "totals": {
            "available_cash": 100000.0,
            "risk_tolerance_pct": 0.01,
            "total_equity_override": None,
        },
    }))
    with patch.object(pf, "PF_FILE", str(pf_file)):
        result = pf.get_portfolio()
    t = result["totals"]
    assert t["available_cash"] == 100000.0
    assert t["risk_tolerance_pct"] == 0.01
    assert "total_equity_override" in t  # None 也算有


def test_get_portfolio_backward_compat_old_json_without_totals(tmp_path):
    """老 JSON 没有 totals 字段 → defaults 兼容填充。"""
    pf_file = tmp_path / "portfolio.json"
    pf_file.write_text(json.dumps({"holdings": [], "last_refresh": None}))
    with patch.object(pf, "PF_FILE", str(pf_file)):
        result = pf.get_portfolio()
    t = result["totals"]
    # 老字段保留
    assert "market_value" in t and "cost" in t and "pnl" in t
    # 新字段给默认值
    assert t["available_cash"] == 0.0
    assert t["risk_tolerance_pct"] == 0.01  # 默认 1%
    assert t["total_equity_override"] is None


def test_get_portfolio_partial_totals_only_some_fields(tmp_path):
    """只填了 available_cash 没 risk_tolerance_pct → 后者用默认。"""
    pf_file = tmp_path / "portfolio.json"
    pf_file.write_text(json.dumps({
        "holdings": [],
        "totals": {"available_cash": 50000.0},
    }))
    with patch.object(pf, "PF_FILE", str(pf_file)):
        result = pf.get_portfolio()
    t = result["totals"]
    assert t["available_cash"] == 50000.0
    assert t["risk_tolerance_pct"] == 0.01
    assert t["total_equity_override"] is None
```

- [ ] **Step 6.2：跑测试验证失败**

```bash
cd /vol2/1000/code/stockclaw/backend
.venv/bin/python -m pytest tests/test_portfolio_totals.py -v
```

期望：3 个测试都 `KeyError: 'available_cash'`。

- [ ] **Step 6.3：修改 `portfolio.py::get_portfolio()`**

在 `backend/portfolio.py` 的 `get_portfolio()` 函数（约第 99-137 行）中，把 `totals` 字典的构造改为：

```python
def get_portfolio() -> dict:
    """读持仓 + 实时行情，算每笔与汇总的市值/浮动盈亏。

    totals 字段含 spec §8 的扩展字段：
    - available_cash：可用现金（用户手输，给 risk_based_position 用）
    - risk_tolerance_pct：单笔风险容忍度，默认 1%
    - total_equity_override：手动覆盖总净值（含港股美股时填）
    老无这些字段的 JSON 兼容默认值。
    """
    with _LOCK:
        d = _load()
    # 用户在 portfolio.json 手填的 totals（spec §8 扩展字段）；老 JSON 无此键 → 空字典兼容
    user_totals = d.get("totals") or {}
    hs = d.get("holdings", [])
    rows, tmv, tcost = [], 0.0, 0.0
    if hs:
        try:
            quotes = astock.tencent_quote([h["code"] for h in hs])
        except Exception:
            quotes = {}
        for h in hs:
            q = quotes.get(h["code"], {})
            price = q.get("price", 0.0)
            mv = price * h["shares"]
            cv = h["cost"] * h["shares"]
            pnl = mv - cv
            rows.append({
                "code": h["code"], "name": q.get("name", h["code"]),
                "price": price, "shares": h["shares"], "cost": h["cost"],
                "market_value": round(mv, 2), "pnl": round(pnl, 2),
                "pnl_pct": round(pnl / cv * 100, 2) if cv else 0.0,
            })
            tmv += mv
            tcost += cv
    total_pnl = tmv - tcost
    closed = d.get("closed", [])
    return {
        "holdings": rows,
        "totals": {
            "market_value": round(tmv, 2), "cost": round(tcost, 2),
            "pnl": round(total_pnl, 2),
            "pnl_pct": round(total_pnl / tcost * 100, 2) if tcost else 0.0,
            # spec §8 漏洞五修补：账户基础字段（risk_based_position 用）
            "available_cash": float(user_totals.get("available_cash", 0.0) or 0.0),
            "risk_tolerance_pct": float(user_totals.get("risk_tolerance_pct", 0.01) or 0.01),
            "total_equity_override": user_totals.get("total_equity_override"),
        },
        "closed": closed,
        "realized_pnl": round(sum(c.get("pnl", 0) for c in closed), 2),
        "updated": _now(),
        "last_refresh": d.get("last_refresh"),
    }
```

注意：`_load()` 函数也需改，使其在老 JSON 缺 `totals` 时不报错（已有 `try/except FileNotFoundError, json.JSONDecodeError` 兜底，但 `_load` 当前返回 `{"holdings": [], "last_refresh": None}` 不含 totals——这是 OK 的，因为 `get_portfolio` 用 `d.get("totals") or {}` 兼容）。

- [ ] **Step 6.4：跑测试验证通过**

```bash
cd /vol2/1000/code/stockclaw/backend
.venv/bin/python -m pytest tests/test_portfolio_totals.py -v
```

期望：3 个测试通过。同时跑全量回归确保没破老接口：

```bash
.venv/bin/python -m pytest -m "not live" -v
```

期望：所有老测试仍通过。

- [ ] **Step 6.5：commit**

```bash
git add backend/portfolio.py backend/tests/test_portfolio_totals.py
git commit -m "feat(portfolio): totals 扩 available_cash / risk_tolerance_pct / total_equity_override

- get_portfolio() 返回的 totals 含三个新字段，给 risk_based_position 工具用
- 老 JSON 无这些字段时给默认值（available_cash=0, risk_tolerance_pct=0.01, override=None）
- 不污染客观数据层：portfolio.json 是用户自维护的本地输入"
```

---

## Task 7：agents 核心（state / graph / orchestrator / decision / tools / prompts 合并）

**目标**：搭建 LangGraph agent runtime——`AgentState` TypedDict + orchestrator 节点（分类意图）+ decision 节点（合并 quant 工具结果为决策卡 + `basis_type` 归并规则）+ `tools.py`（`@tool` 包装，所有同步数据层用 `asyncio.to_thread` 卸载）。

**Files:**
- Create: `backend/agents/state.py`
- Create: `backend/agents/tools.py`
- Create: `backend/agents/nodes/__init__.py`
- Create: `backend/agents/nodes/orchestrator.py`
- Create: `backend/agents/nodes/decision.py`
- Create: `backend/agents/graph.py`
- Test: `backend/tests/test_agents_decision.py`

- [ ] **Step 7.1：写失败的 basis_type 归并单测**

```python
# backend/tests/test_agents_decision.py
"""Decision Node 关键测试——basis_type 归并规则（最大不确定性优先）+ 字段级 model_versions_json。"""
import pytest

from agents.nodes.decision import merge_basis_type, build_decision_card


def test_merge_basis_type_max_uncertainty_wins():
    """归并规则：llm_reasoning > hybrid > model_fallback > model。"""
    assert merge_basis_type(["model", "model", "model"]) == "model"
    assert merge_basis_type(["model", "model_fallback"]) == "model_fallback"
    assert merge_basis_type(["model", "hybrid"]) == "hybrid"
    assert merge_basis_type(["model", "llm_reasoning"]) == "llm_reasoning"
    assert merge_basis_type(["model_fallback", "hybrid", "llm_reasoning"]) == "llm_reasoning"


def test_merge_basis_type_empty_returns_model():
    assert merge_basis_type([]) == "model"


def test_build_decision_card_basic():
    """合并工具结果为决策卡：字段级 model_versions_json + cadence 数组。"""
    tool_results = {
        "target": {  # 来自 forward_pe_target
            "tool": "forward_pe_target", "basis_type": "model",
            "model_version": "forward_pe_target.v1",
            "outputs": {"target_price": 1900.0, "current_price": 1685.0},
        },
        "stop": {  # 来自 atr_stop（fallback）
            "tool": "atr_stop", "basis_type": "model_fallback",
            "model_version": "atr_stop.v1",
            "outputs": {"stop_price": 1550.2, "current_price": 1685.0, "fallback_reason": "no_kline"},
        },
        "entry": {  # 来自 pe_percentile_revert
            "tool": "pe_percentile_revert", "basis_type": "model",
            "model_version": "pe_percentile_revert.v1",
            "outputs": {"target_price": 1900.0, "current_price": 1685.0},
        },
        "position": {  # 来自 risk_based_position
            "tool": "risk_based_position", "basis_type": "model",
            "model_version": "risk_based_position.v1",
            "outputs": {"shares": 125.0, "position_pct_of_equity": 0.21},
        },
    }
    card = build_decision_card(
        code="600519", name="贵州茅台",
        current_price=1685.0,
        target_price=1900.0, entry_low=1685.0, entry_high=1720.0,
        stop_loss=1550.2, take_profit=2080.0,
        cadence=[
            {"batch": 1, "pct": 0.40, "trigger": "immediate", "price": 1685.0},
            {"batch": 2, "pct": 0.30, "trigger": "pullback_to:1650", "price": 1650.0},
            {"batch": 3, "pct": 0.30, "trigger": "breakout_above:1780", "price": 1780.0},
        ],
        tool_results=tool_results,
        explanation="基于 forward PE 目标价 1900 + ATR fallback 止损 1550",
    )
    # 整卡 basis_type = model_fallback（含一个 model_fallback 字段）
    assert card["basis_type"] == "model_fallback"
    # 字段级 model_versions_json
    mv = card["model_versions_json"]
    assert "target_price" in mv and "forward_pe_target.v1" in mv["target_price"]
    assert "stop_loss" in mv and "atr_stop.v1" in mv["stop_loss"]
    assert "model_fallback" in mv["stop_loss"] or "fallback" in mv["stop_loss"].lower()
    # cadence 是数组
    assert isinstance(card["cadence"], list) and len(card["cadence"]) == 3
    # citations 来自所有工具
    assert len(card["citations"]) >= 1
    # code / name / current_price
    assert card["code"] == "600519"
    assert card["name"] == "贵州茅台"
    assert card["current_price"] == 1685.0
```

- [ ] **Step 7.2：跑测试验证失败**

```bash
cd /vol2/1000/code/stockclaw/backend
.venv/bin/python -m pytest tests/test_agents_decision.py -v
```

期望：`ModuleNotFoundError: No module named 'agents.nodes.decision'`

- [ ] **Step 7.3：建 nodes 包 + 实现 state.py**

```bash
mkdir -p backend/agents/nodes
```

```python
# backend/agents/nodes/__init__.py
"""Agent 节点：orchestrator / decision / panel_*（Phase 2）/ planner（Phase 2）/ proactive（Phase 3）。"""
```

```python
# backend/agents/state.py
"""LangGraph AgentState 定义。

messages: OpenAI 消息序列（system + user + assistant + tool）
intent: orchestrator 分类结果（"decision" | "research" | "general"）
context_codes: 用户从 ContextDrawer 注入的股票代码（A 股 6 位 / 美股字母 / 港股数字）
style: 风格预设（"conservative" | "balanced" | "aggressive"），Phase 1 不生效，预留接口
artifacts: 累积的结构化产物（Decision Node 输出追加到此）
thread_id: 会话 ID（持久化用）
"""
from __future__ import annotations

from typing import Annotated, TypedDict

from langgraph.graph.message import add_messages


class AgentState(TypedDict, total=False):
    messages: Annotated[list, add_messages]
    intent: str
    context_codes: list[str]
    style: str
    artifacts: list[dict]
    thread_id: str
    # 流式输出用：runner 维护，不进 graph 传递
    decision_card: dict | None
```

- [ ] **Step 7.4：实现 Decision Node 的归并逻辑**

```python
# backend/agents/nodes/decision.py
"""Decision Node：合并 quant 工具结果为决策卡。

核心职责：
1. basis_type 归并规则（最大不确定性优先）：llm_reasoning > hybrid > model_fallback > model
2. 字段级 model_versions_json：记录每个决策字段来自哪个工具版本
3. cadence 数组组装（来自 cadence 工具或 LLM 提议）
"""
from __future__ import annotations

from typing import Any

# 归并优先级（越大越优先）
_BASIS_PRIORITY = {
    "model": 0,
    "model_fallback": 1,
    "hybrid": 2,
    "llm_reasoning": 3,
}


def merge_basis_type(field_basis: list[str]) -> str:
    """归并多个字段的 basis_type 为整卡 basis_type。

    规则（spec §6 约束 3）：
    - 任意字段为 llm_reasoning → 整卡 llm_reasoning
    - 否则，任意字段为 hybrid → 整卡 hybrid
    - 否则，任意字段为 model_fallback → 整卡 model_fallback
    - 全部为 model → 整卡 model
    空列表 → "model"（默认）
    """
    if not field_basis:
        return "model"
    valid = [b for b in field_basis if b in _BASIS_PRIORITY]
    if not valid:
        return "model"
    return max(valid, key=lambda b: _BASIS_PRIORITY[b])


def _version_label(tool_result: dict) -> str:
    """生成字段级版本标签，如 'model(forward_pe_target.v1)' 或 'model_fallback(atr_stop.v1, reason=no_kline)'."""
    basis = tool_result.get("basis_type", "model")
    version = tool_result.get("model_version", "unknown")
    outputs = tool_result.get("outputs") or {}
    reason = outputs.get("fallback_reason")
    if reason:
        return f"{basis}({version}, reason={reason})"
    return f"{basis}({version})"


def build_decision_card(
    code: str,
    name: str,
    current_price: float,
    target_price: float,
    entry_low: float,
    entry_high: float,
    stop_loss: float,
    take_profit: float,
    cadence: list[dict],
    tool_results: dict[str, dict],
    explanation: str,
) -> dict[str, Any]:
    """组装决策卡，含字段级 model_versions_json + 归并后的 basis_type。

    tool_results 形如 {"target": forward_pe_target 结果, "stop": atr_stop 结果, ...}
    """
    # 字段级 model_versions_json：每个数字字段记录来源工具
    target_tool = tool_results.get("target", {})
    entry_tool = tool_results.get("entry", target_tool)
    stop_tool = tool_results.get("stop", {})
    take_profit_tool = tool_results.get("take_profit", target_tool)
    position_tool = tool_results.get("position", {})

    model_versions_json = {
        "target_price": _version_label(target_tool) if target_tool else "unknown",
        "entry_low": _version_label(entry_tool) if entry_tool else "unknown",
        "entry_high": _version_label(entry_tool) if entry_tool else "unknown",
        "stop_loss": _version_label(stop_tool) if stop_tool else "unknown",
        "take_profit": _version_label(take_profit_tool) if take_profit_tool else "unknown",
    }
    if position_tool:
        model_versions_json["cadence[0].pct"] = _version_label(position_tool)

    # 收集所有字段 basis_type 用于归并
    field_basis = [r.get("basis_type") for r in tool_results.values() if r.get("basis_type")]
    merged_basis = merge_basis_type(field_basis)

    # 收集所有 citations
    citations = []
    for r in tool_results.values():
        for c in (r.get("citations") or []):
            if c not in citations:
                citations.append(c)

    # 收集所有 model_assumptions
    assumptions = []
    for r in tool_results.values():
        for a in (r.get("model_assumptions") or []):
            if a not in assumptions:
                assumptions.append(a)

    return {
        "code": code,
        "name": name,
        "current_price": current_price,
        "target_price": target_price,
        "entry_low": entry_low,
        "entry_high": entry_high,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "cadence": cadence,
        "basis_type": merged_basis,
        "model_versions_json": model_versions_json,
        "assumptions": assumptions,
        "citations": citations,
        "explanation": explanation,
    }
```

- [ ] **Step 7.5：实现 tools.py（@tool 包装，asyncio.to_thread 卸载同步数据层）**

```python
# backend/agents/tools.py
"""LangChain @tool 包装，连接 astock / gstock / quant 工具。

硬约束（焊死）：所有调 astock.*/gstock.*/market.*/newsradar.* 的 @tool 必须用
`await asyncio.to_thread(fn, ...)` 包装。原因：
- astock.kline() 走 mootdx 同步阻塞 TCP
- astock.tencent_quote() 走 urllib.request.urlopen 同步
- astock.em_get() 走 requests 同步
- quant.* 全部基于这些同步 API
直接 await sync 函数会 TypeError；直接调用会冻结 event loop，Rate Limiter 的 sleep 无法调度。

Phase 1 验收：grep -E "await (astock|gstock|market|newsradable)" backend/agents/tools.py 必须无命中。
"""
from __future__ import annotations

import asyncio
import functools
import os
from typing import Any

from langchain_core.tools import tool

import astock
import quant.valuation as q_val
import quant.stops as q_stops
import quant.cadence as q_cad
from agents.rate_limiter import eastmoney_limiter


async def _run_sync(fn, *args, **kwargs):
    """把同步函数卸载到默认线程池，且 Rate Limiter 横跨整个调用。"""
    async with eastmoney_limiter:
        return await asyncio.to_thread(fn, *args, **kwargs)


# ---------------------------------------------------------------------------
# 数据查询工具（直接调 astock / gstock）
# ---------------------------------------------------------------------------

@tool
async def query_quote(codes: list[str]) -> dict:
    """查 A 股实时行情：现价/涨跌/PE/PB/市值/换手/涨跌停。可批量。

    codes: 6 位 A 股代码列表，如 ['600519', '000858']
    """
    return await _run_sync(astock.tencent_quote, codes)


@tool
async def query_global_stock(symbol: str) -> dict:
    """查美股/港股/韩股个股行情 + 关键财务指标。

    symbol: 美股字母（AAPL）/ 港股数字（00700）/ 韩股 6 位.KS（005930.KS）
    """
    import gstock
    return await _run_sync(gstock.us_hk_stock, symbol)


@tool
async def query_valuation(code: str) -> dict:
    """查 A 股完整估值：行情 + 一致预期 EPS + 前向 PE/PEG/消化年数。

    code: 6 位 A 股代码
    """
    return await _run_sync(astock.full_valuation, code)


@tool
async def query_kline(code: str, days: int = 60) -> list[dict]:
    """查 A 股日 K 线（用于 ATR 计算 / 结构止损）。

    code: 6 位 A 股代码
    days: 拉取天数（默认 60）
    """
    return await _run_sync(astock.kline, code, 4, days)


# ---------------------------------------------------------------------------
# quant 工具（纯 Python 函数，本身不调网络；但内部调 astock，故也走 Rate Limiter + to_thread）
# ---------------------------------------------------------------------------

@tool
async def forward_pe_target(code: str, target_pe: float = 20.0, eps_year: str = "27e") -> dict:
    """前向 PE × 一致 EPS = 目标价。A 股数据齐走完整公式（model）。

    一致 EPS 缺失（美港股 / 无覆盖）时抛 DataUnavailable——上层 Decision Node 会走 model_fallback 或 llm_reasoning。
    code: 6 位 A 股代码
    target_pe: 目标前向 PE（用户/LLM 给定，如行业均值 20x）
    eps_year: 用哪年的一致 EPS（"26e" / "27e"）
    返回：{tool, inputs, outputs, basis_type, model_version, citations, explanation}
    """
    return await _run_sync(q_val.forward_pe_target, code, target_pe, eps_year)


@tool
async def pe_percentile_revert(code: str, revert_to: float = 0.50) -> dict:
    """PE 历史分位回复：当前 PE 处于 X 分位 → 回复到目标分位的目标价。

    code: 6 位 A 股代码
    revert_to: 目标分位（0.0-1.0），默认 0.50（中位数）
    """
    return await _run_sync(q_val.pe_percentile_revert, code, revert_to)


@tool
async def atr_stop(code: str, period: int = 14, multiplier: float = 2.0) -> dict:
    """ATR 止损价。A 股主路径走 14 日 ATR × 倍数；K 线缺失或美港股自动降级为 model_fallback（固定 -8%）。

    code: 股票代码（A 股 6 位 / 美股字母 / 港股数字）
    period: ATR 周期（默认 14）
    multiplier: ATR 倍数（保守 2.0、激进 1.5）
    """
    return await _run_sync(q_stops.atr_stop, code, period, multiplier)


@tool
async def structure_stop(code: str, lookback: int = 60) -> dict:
    """结构止损：近 lookback 日最低价。无 K 线时复用 atr_stop fallback。

    code: 6 位 A 股代码
    lookback: 回看天数（默认 60）
    """
    return await _run_sync(q_stops.structure_stop, code, lookback)


@tool
async def risk_based_position(entry_price: float, stop_price: float) -> dict:
    """按止损距离反推仓位：股数 = 总净值 × 风险容忍度 / 单股止损距离。

    entry_price / stop_price: 入场价 / 止损价
    总净值 / 风险容忍度未传时，自动读 portfolio.json::totals
    两者都缺 → 返回 model_fallback，仅给比例（30%/30%/40%）
    """
    return await _run_sync(q_stops.risk_based_position, entry_price, stop_price)


@tool
async def pyramid_buy(current_price: float, total_budget: float,
                      batches: int, ratios: list[float], triggers: list[str]) -> dict:
    """金字塔加仓：底部仓位大、顶部小；触发价递增。

    ratios: 每批占比，和必须 = 1.0（如 [0.40, 0.30, 0.30]）
    triggers: 触发条件（如 ["immediate", "pullback_to:95", "breakout_above:105"]）
    """
    return await _run_sync(q_cad.pyramid_buy, current_price, total_budget, batches, ratios, triggers)


@tool
async def batch_build(total_budget: float, batches: int, schedule: str, start_price: float) -> dict:
    """分批建仓：等额 N 批，按周期触发（不择时，平摊成本）。

    schedule: "weekly" / "biweekly" / "monthly"
    """
    return await _run_sync(q_cad.batch_build, total_budget, batches, schedule, start_price)


@tool
async def dca_plan(periodic_amount: float, periods: int, schedule: str) -> dict:
    """定投：固定周期 + 固定金额。

    periodic_amount: 每次定投金额
    periods: 期数
    schedule: "weekly" / "biweekly" / "monthly"
    """
    return await _run_sync(q_cad.dca_plan, periodic_amount, periods, schedule)


# Phase 1 暴露给 LLM 的全部工具列表
ALL_TOOLS = [
    query_quote, query_global_stock, query_valuation, query_kline,
    forward_pe_target, pe_percentile_revert,
    atr_stop, structure_stop, risk_based_position,
    pyramid_buy, batch_build, dca_plan,
]
```

- [ ] **Step 7.6：跑 Decision Node 单测验证通过**

```bash
cd /vol2/1000/code/stockclaw/backend
.venv/bin/python -m pytest tests/test_agents_decision.py -v
```

期望：3 个测试通过。

- [ ] **Step 7.7：实现 Orchestrator 节点**

```python
# backend/agents/nodes/orchestrator.py
"""Orchestrator 节点：分类用户意图并路由。

Phase 1 简化版：基于规则分类（不调 LLM）
- "目标价" / "止损" / "止盈" / "仓位" / "决策" / "建仓" → intent="decision"
- 含 A 股代码或美股字母代码 + 一般问句 → intent="decision"（默认走决策）
- 其他 → intent="general"（直接调 LLM，不走 Decision Node）
"""
from __future__ import annotations

import re

from agents.state import AgentState


_DECISION_KEYWORDS = {"目标价", "止损", "止盈", "仓位", "决策", "建仓", "入场", "买点", "卖点", "节奏"}
# A 股 6 位 / 美股 1-5 字母 / 港股 4-5 位数字
_CODE_PATTERNS = [
    re.compile(r"\b\d{6}\b"),
    re.compile(r"\b[A-Z]{1,5}\b"),
    re.compile(r"\b\d{4,5}\b"),
]


def classify_intent(state: AgentState) -> str:
    """根据最后一条 user 消息分类意图。返回 'decision' | 'research' | 'general'。"""
    msgs = state.get("messages") or []
    if not msgs:
        return "general"
    last_user = ""
    for m in reversed(msgs):
        if isinstance(m, dict) and m.get("role") == "user":
            last_user = m.get("content", "") or ""
            break
        # LangChain BaseMessage 兼容
        role = getattr(m, "type", None) or getattr(m, "role", None)
        if role == "user":
            last_user = getattr(m, "content", "") or ""
            break

    # 关键词触发
    if any(kw in last_user for kw in _DECISION_KEYWORDS):
        return "decision"
    # 代码 + 求分析类问句
    has_code = any(p.search(last_user) for p in _CODE_PATTERNS)
    has_analysis = any(k in last_user for k in ["分析", "看看", "怎么样", "如何"])
    if has_code and has_analysis:
        return "decision"
    return "general"


def orchestrator_node(state: AgentState) -> dict:
    """LangGraph 节点：分类意图，写回 state['intent']。"""
    intent = classify_intent(state)
    return {"intent": intent}
```

- [ ] **Step 7.8：实现 Decision 节点 graph 包装**

```python
# backend/agents/nodes/decision.py 末尾追加（接续 Task 7.4 已有内容）
import json
from typing import Any

from agents.state import AgentState
from agents.tools import (
    forward_pe_target, pe_percentile_revert, atr_stop, structure_stop,
    risk_based_position, pyramid_buy, batch_build, dca_plan,
)


async def _invoke(tool, **kwargs) -> dict | None:
    """安全调用 tool，失败返回 None。"""
    try:
        return await tool.ainvoke(kwargs)
    except Exception as e:
        return {"error": f"{tool.name} failed: {e}", "basis_type": "model_fallback",
                "model_version": f"{tool.name}.v1", "outputs": {}, "citations": [],
                "model_assumptions": [f"工具失败：{e}"]}


async def decision_node(state: AgentState) -> dict:
    """LangGraph 节点：调 quant 工具集 + 合并决策卡。

    Phase 1 简化版：固定调 forward_pe_target + atr_stop + risk_based_position + batch_build。
    Phase 2 改为 LLM 决定调哪些工具。
    """
    context_codes = state.get("context_codes") or []
    msgs = state.get("messages") or []
    code = context_codes[0] if context_codes else _extract_code_from_messages(msgs)
    if not code:
        return {"decision_card": None}

    # 并发调工具
    target_r = await _invoke(forward_pe_target, code=code, target_pe=20.0, eps_year="27e")
    stop_r = await _invoke(atr_stop, code=code, period=14, multiplier=2.0)
    entry_r = await _invoke(pe_percentile_revert, code=code, revert_to=0.50)

    # 取当前价 + 名称
    current_price = (target_r or {}).get("outputs", {}).get("current_price") or \
                    (stop_r or {}).get("outputs", {}).get("current_price") or 0.0
    name = _lookup_name(code)

    target_price = (target_r or {}).get("outputs", {}).get("target_price") or current_price * 1.15
    stop_loss = (stop_r or {}).get("outputs", {}).get("stop_price") or current_price * 0.92
    entry_low = current_price * 0.98
    entry_high = current_price * 1.02
    take_profit = current_price * 1.20

    # 仓位 + 节奏
    pos_r = await _invoke(risk_based_position, entry_price=current_price, stop_price=stop_loss)
    cad_r = await _invoke(batch_build, total_budget=100000.0, batches=3, schedule="weekly", start_price=current_price)
    cadence = (cad_r or {}).get("outputs", {}).get("plan") or []

    # 合并
    card = build_decision_card(
        code=code, name=name, current_price=current_price,
        target_price=target_price, entry_low=entry_low, entry_high=entry_high,
        stop_loss=stop_loss, take_profit=take_profit,
        cadence=cadence,
        tool_results={
            "target": target_r or {}, "entry": entry_r or {},
            "stop": stop_r or {}, "position": pos_r or {},
            "take_profit": target_r or {},  # 复用 target
        },
        explanation=f"基于前向 PE 目标价 {target_price:.2f} + ATR 止损 {stop_loss:.2f} + 分批 3 期建仓",
    )
    return {"decision_card": card}


def _extract_code_from_messages(msgs) -> str | None:
    """从消息文本里抓 6 位 A 股代码 / 美股字母代码。"""
    import re
    pat = re.compile(r"\b(\d{6}|[A-Z]{1,5})\b")
    for m in reversed(msgs):
        text = m.get("content", "") if isinstance(m, dict) else getattr(m, "content", "")
        hit = pat.search(text or "")
        if hit:
            return hit.group(1)
    return None


def _lookup_name(code: str) -> str:
    """从 astock.tencent_quote 查股票名（不在线调，缓存层面交 astock）。"""
    if code.isdigit() and len(code) == 6:
        try:
            import astock
            q = astock.tencent_quote([code])
            return q.get(code, {}).get("name", code)
        except Exception:
            pass
    return code
```

- [ ] **Step 7.9：实现 graph.py（LangGraph 主图）**

```python
# backend/agents/graph.py
"""LangGraph 主图构建。

Phase 1 简化版：
  START → orchestrator → {decision: decision_node, general: tool_calling_llm}
  decision_node → END
  tool_calling_llm → END

注：Phase 2 会把 general 路径改成 LangGraph 标准 ReAct agent（多轮工具调用）。
"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from agents.nodes.decision import decision_node
from agents.nodes.orchestrator import orchestrator_node
from agents.state import AgentState


def _route_intent(state: AgentState) -> str:
    """orchestrator 之后的条件路由：'decision' | 'general'。"""
    return state.get("intent", "general")


def _general_passthrough(state: AgentState) -> dict:
    """general 路径占位：Phase 1 不调 LLM，由 runner 自己处理（runner 调 OpenAI 流式 + tools）。

    此节点存在是为了让 graph 结构完整；实际 LLM 调用在 runner.py。
    """
    return {}


def build_agent_graph():
    """构建并编译 agent 主图。"""
    g = StateGraph(AgentState)
    g.add_node("orchestrator", orchestrator_node)
    g.add_node("decision", decision_node)
    g.add_node("general", _general_passthrough)

    g.add_edge(START, "orchestrator")
    g.add_conditional_edges(
        "orchestrator",
        _route_intent,
        {"decision": "decision", "general": "general"},
    )
    g.add_edge("decision", END)
    g.add_edge("general", END)

    return g.compile()


# 全局编译好的图（runner 复用）
agent_graph = build_agent_graph()
```

- [ ] **Step 7.10：编译验证 + 验证 asyncio.to_thread 硬约束**

```bash
cd /vol2/1000/code/stockclaw/backend
.venv/bin/python -c "from agents.graph import agent_graph; print('graph compiled:', agent_graph)"
```

期望：无报错输出，print 出 LangGraph 编译对象。

```bash
# 硬约束验收：tools.py 里不应有 "await astock.xxx" / "await gstock.xxx" 等直接 await 同步数据层
grep -E "await (astock|gstock|market|newsradable)" backend/agents/tools.py
```

期望：**无任何输出**（无命中）。如果命中，说明有 `@tool` 直接 `await astock.xxx(...)` 而非 `await asyncio.to_thread(astock.xxx, ...)`——必须改。

- [ ] **Step 7.11：跑全部 agents + quant 测试**

```bash
cd /vol2/1000/code/stockclaw/backend
.venv/bin/python -m pytest tests/test_agents_decision.py tests/test_quant_valuation.py tests/test_quant_stops.py tests/test_quant_cadence.py tests/test_rate_limiter.py -v
```

期望：全部通过。

- [ ] **Step 7.12：commit**

```bash
git add backend/agents/state.py backend/agents/tools.py backend/agents/nodes/ backend/agents/graph.py backend/tests/test_agents_decision.py
git commit -m "feat(agents): LangGraph 主图 + orchestrator + decision + tools

- state.py: AgentState TypedDict（messages + intent + context_codes + artifacts）
- nodes/orchestrator.py: 规则分类意图（关键词 + 代码模式匹配）
- nodes/decision.py: basis_type 归并（llm_reasoning > hybrid > model_fallback > model）
                    + 字段级 model_versions_json + 决策卡组装
- tools.py: @tool 包装 astock/gstock/quant，全部 asyncio.to_thread 卸载同步数据层
- graph.py: START → orchestrator → {decision | general} → END
- 硬约束验收：grep 'await (astock|gstock|...)' tools.py 无命中"
```

---

## Task 8：runner + NDJSON endpoint

**目标**：`runner.py` 暴露 `run_agent(req) -> AsyncGenerator[dict]`，吐 NDJSON 事件（`text_delta` / `tool_trace` / `decision_artifact` / `citations` / `done` / `error`）。`app.py` 加 `/api/agent/chat` 路由，按 spec §7 协议：CLI 模式 400、鉴权失败 401 短路（不挂 SSE）。

**Files:**
- Create: `backend/runner.py`
- Modify: `backend/app.py`（加 `/api/agent/chat` 路由）
- Modify: `backend/.env.example`（加 VR_AGENT_* env）
- Test: `backend/tests/test_agent_endpoint.py`
- Test: `backend/tests/test_runner.py`

- [ ] **Step 8.1：写失败的 endpoint 测试**

```python
# backend/tests/test_agent_endpoint.py
"""/api/agent/chat 端点协议测试——CLI 拒绝、鉴权 401 短路、NDJSON 流。"""
import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("VR_API_KEY", raising=False)
    import app
    return TestClient(app.app)


def _llm_api_cfg():
    return {"provider": "", "baseURL": "https://api.example.com", "apiKey": "test-key", "model": "gpt-4o"}


def test_cli_mode_rejected_with_400(client):
    """provider=cli-* → 400 + JSON 错误体（不是 SSE 流）。"""
    resp = client.post("/api/agent/chat", json={
        "messages": [{"role": "user", "content": "分析茅台"}],
        "llm": {"provider": "cli-claude", "baseURL": "", "apiKey": "", "model": "claude"},
    })
    assert resp.status_code == 400
    assert resp.headers["content-type"].startswith("application/json")
    body = resp.json()
    assert "cli" in body["detail"].lower() or "API" in body["detail"]


def test_auth_required_returns_401_not_sse(monkeypatch):
    """设了 VR_API_KEY 但请求不带 → 401 + JSON，不挂 SSE 流。"""
    monkeypatch.setenv("VR_API_KEY", "secret-key")
    import importlib, app
    importlib.reload(app)
    client = TestClient(app.app)
    resp = client.post("/api/agent/chat", json={
        "messages": [{"role": "user", "content": "分析"}],
        "llm": _llm_api_cfg(),
    })
    assert resp.status_code == 401
    assert resp.headers["content-type"].startswith("application/json")


def test_request_body_validation_missing_messages(client):
    """缺 messages → 422。"""
    resp = client.post("/api/agent/chat", json={"llm": _llm_api_cfg()})
    assert resp.status_code == 422


def test_ndjson_stream_emits_text_delta_and_done(client):
    """正常请求 → text/x-ndjson 流；至少含 text_delta + done 事件。"""
    # mock runner.run_agent 直接吐预设事件
    async def fake_run_agent(req):
        yield {"type": "text_delta", "text": "分析中"}
        yield {"type": "decision_artifact", "decision_id": "test-did",
               "data": {"code": "600519", "name": "茅台", "target_price": 1900.0,
                        "basis_type": "model", "cadence": [], "model_versions_json": {},
                        "current_price": 1685.0, "entry_low": 1685.0, "entry_high": 1720.0,
                        "stop_loss": 1550.0, "take_profit": 2080.0,
                        "assumptions": [], "citations": [], "explanation": "测试"}}
        yield {"type": "done", "summary": {"rounds": 1}}

    with patch("app.run_agent", fake_run_agent):
        resp = client.post("/api/agent/chat", json={
            "thread_id": None,
            "messages": [{"role": "user", "content": "分析茅台 给目标价"}],
            "context_codes": ["600519"],
            "llm": _llm_api_cfg(),
            "style": "balanced",
        })
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/x-ndjson")
    body = resp.text
    lines = [l for l in body.split("\n") if l.strip()]
    types = [json.loads(l)["type"] for l in lines]
    assert "text_delta" in types
    assert "decision_artifact" in types
    assert "done" in types
    # 每行严格以 \n 结尾（spec §9 Phase 1 #8）
    assert body.endswith("\n")
```

- [ ] **Step 8.2：写 runner 单测**

```python
# backend/tests/test_runner.py
"""runner.run_agent 单测——NDJSON 事件流结构。"""
import asyncio
from unittest.mock import patch

import pytest

import runner


@pytest.mark.asyncio
async def test_run_agent_emits_decision_artifact_for_decision_intent():
    """decision 路径 → 至少含 text_delta + decision_artifact + done。"""
    async def fake_graph_ainvoke(input_state):
        return {"intent": "decision", "decision_card": {
            "code": "600519", "name": "茅台", "current_price": 1685.0,
            "target_price": 1900.0, "entry_low": 1685.0, "entry_high": 1720.0,
            "stop_loss": 1550.0, "take_profit": 2080.0, "cadence": [],
            "basis_type": "model", "model_versions_json": {},
            "assumptions": [], "citations": [], "explanation": "测试"
        }}

    with patch("runner.agent_graph.ainvoke", side_effect=fake_graph_ainvoke), \
         patch("runner._stream_llm_text", new=_fake_stream_text):
        req = runner.AgentChatReq(
            thread_id=None,
            messages=[{"role": "user", "content": "分析茅台 给目标价"}],
            context_codes=["600519"],
            llm={"provider": "", "baseURL": "https://api.example.com",
                 "apiKey": "k", "model": "gpt-4o"},
            style="balanced",
        )
        events = []
        async for ev in runner.run_agent(req):
            events.append(ev)

    types = [e["type"] for e in events]
    assert "text_delta" in types
    assert "decision_artifact" in types
    assert "done" in types


async def _fake_stream_text(*args, **kwargs):
    yield "分析中"


@pytest.mark.asyncio
async def test_run_agent_no_decision_card_for_general_intent():
    """general 路径 → 只有 text_delta + done，无 decision_artifact。"""
    async def fake_graph_ainvoke(input_state):
        return {"intent": "general", "decision_card": None}

    with patch("runner.agent_graph.ainvoke", side_effect=fake_graph_ainvoke), \
         patch("runner._stream_llm_text", new=_fake_stream_text):
        req = runner.AgentChatReq(
            thread_id=None,
            messages=[{"role": "user", "content": "你好"}],
            llm={"provider": "", "baseURL": "https://api.example.com",
                 "apiKey": "k", "model": "gpt-4o"},
        )
        events = []
        async for ev in runner.run_agent(req):
            events.append(ev)

    types = [e["type"] for e in events]
    assert "decision_artifact" not in types
    assert "text_delta" in types
    assert "done" in types
```

- [ ] **Step 8.3：跑测试验证失败**

```bash
cd /vol2/1000/code/stockclaw/backend
.venv/bin/python -m pytest tests/test_agent_endpoint.py tests/test_runner.py -v
```

期望：`ModuleNotFoundError: No module named 'runner'` + `/api/agent/chat` 路由 404。

- [ ] **Step 8.4：实现 runner.py**

```python
# backend/runner.py
"""Agent 运行入口——FastAPI /api/agent/chat 调它。

输出 NDJSON 事件流（spec §7 协议）：
- text_delta: 助手回答文本增量
- tool_trace: 工具调用记录（status: running/ok/error）
- decision_artifact: Decision Node 生成的决策卡
- citations: 数据出处批量上报
- done: 流正常结束
- error: 异常上报，流提前终止
"""
from __future__ import annotations

import json
import uuid
from typing import Any, AsyncGenerator

import requests
from pydantic import BaseModel

from agents.graph import agent_graph
from agents.prompts import SYSTEM_PROMPT_AGENT


class LLMConfig(BaseModel):
    provider: str = ""
    baseURL: str = ""
    apiKey: str = ""
    model: str


class AgentChatReq(BaseModel):
    """spec §7 NDJSON 协议请求体。"""
    thread_id: str | None = None
    messages: list[dict]
    context_codes: list[str] = []
    llm: LLMConfig
    style: str = "balanced"


async def _stream_llm_text(cfg: dict, system_prompt: str, user_messages: list[dict],
                           context_codes: list[str]) -> AsyncGenerator[str, None]:
    """调上游 OpenAI 兼容端点的流式接口，逐 chunk yield 文本 delta。

    Phase 1 简化版：不接 function-calling（决策路径由 graph 直接调 quant 工具）。
    Phase 2 改为接 tools 参数走 ReAct 多轮。
    """
    base = cfg["baseURL"].rstrip("/")
    if not base.endswith(("/v1", "/v3", "/api/v3")):
        base = base + "/v1"
    context_str = "；".join(context_codes) if context_codes else "（无）"
    messages = [{"role": "system", "content": system_prompt.format(context=context_str)}]
    messages.extend(user_messages)

    resp = requests.post(
        f"{base}/chat/completions",
        headers={"Authorization": f"Bearer {cfg['apiKey']}", "Content-Type": "application/json"},
        json={"model": cfg["model"], "messages": messages, "temperature": 0.3, "stream": True},
        timeout=120, stream=True,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"模型接口 HTTP {resp.status_code}: {resp.text[:300]}")

    # 复用 chat_legacy._iter_sse_deltas 的 SSE 解析逻辑
    buf = b""
    for chunk in resp.iter_content(chunk_size=None):
        if not chunk:
            continue
        buf += chunk
        while b"\n" in buf:
            raw, buf = buf.split(b"\n", 1)
            line = raw.decode("utf-8", errors="replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                return
            try:
                j = json.loads(data)
            except json.JSONDecodeError:
                continue
            choices = j.get("choices") or []
            if choices:
                delta = choices[0].get("delta") or {}
                text = delta.get("content")
                if text:
                    yield text


async def run_agent(req: AgentChatReq) -> AsyncGenerator[dict, None]:
    """主入口：跑 graph + 流式输出 NDJSON 事件。"""
    decision_id = uuid.uuid4().hex
    try:
        # Step 1：并发跑 graph（拿 decision_card）+ 流式 LLM 文本
        graph_state = {
            "messages": req.messages,
            "context_codes": req.context_codes,
            "style": req.style,
            "thread_id": req.thread_id or decision_id,
        }

        # 先调 graph（同步等结果，Phase 2 改并发）
        graph_result = await agent_graph.ainvoke(graph_state)
        decision_card = graph_result.get("decision_card")

        # 流式 LLM 文本（用决策卡作 context 加强）
        if decision_card:
            summary = (
                f"基于工具结果：目标价 {decision_card.get('target_price')}，"
                f"止损 {decision_card.get('stop_loss')}，止盈 {decision_card.get('take_profit')}，"
                f"依据 {decision_card.get('basis_type')}。"
            )
            enhanced_messages = list(req.messages) + [{
                "role": "assistant", "content": f"[工具结果摘要] {summary}"
            }]
        else:
            enhanced_messages = req.messages

        async for text in _stream_llm_text(
            req.llm.model_dump(), SYSTEM_PROMPT_AGENT, enhanced_messages, req.context_codes,
        ):
            yield {"type": "text_delta", "text": text}

        # 推决策卡 artifact
        if decision_card:
            yield {
                "type": "decision_artifact",
                "decision_id": decision_id,
                "data": decision_card,
            }
            # 推 citations（spec §7：消息结束前一次性发）
            yield {
                "type": "citations",
                "items": decision_card.get("citations") or [],
            }

        yield {"type": "done", "summary": {"thread_id": req.thread_id or decision_id}}

    except Exception as e:
        yield {"type": "error", "message": f"agent 运行失败：{e}"}
```

- [ ] **Step 8.5：在 `app.py` 加 `/api/agent/chat` 路由**

在 `backend/app.py` 顶部 import 区（约第 27 行后）加：

```python
import runner
from runner import AgentChatReq
```

然后在 `chat` 路由（`/api/chat`，约第 89-120 行）之后追加：

```python
@app.post("/api/agent/chat")
async def agent_chat(req: AgentChatReq):
    """AI 原生 Agent 工作台入口——NDJSON 流（text_delta / tool_trace / decision_artifact / done / error）。

    鉴权失败：复用 _require_api_key 中间件，自动 401 + JSON（不进 SSE 流）。
    CLI 模式（provider=cli-*）：返回 400 + JSON，不挂 SSE（提示前端配 API 模型）。
    """
    if not req.messages:
        raise HTTPException(400, "messages 不能为空")
    if not req.llm.model:
        raise HTTPException(400, "缺少模型配置")

    is_cli = req.llm.provider.startswith("cli-")
    if is_cli:
        # CLI 模式不支持 function-calling 与流式 agent 路由——直接 400 拒绝
        raise HTTPException(
            400,
            "Agent 工作台需要 API 接入的模型工具链。请前往「接入 AI」页配置 API Key 或更换模型。",
        )
    if not req.llm.apiKey or not req.llm.baseURL:
        raise HTTPException(400, "缺少 Base URL 或 API Key，请先在「接入 AI」里填写")

    async def gen():
        try:
            async for ev in runner.run_agent(req):
                yield json.dumps(ev, ensure_ascii=False) + "\n"
        except Exception as e:  # noqa: BLE001 — 流内异常以 error 事件上报
            yield json.dumps({"type": "error", "message": f"agent 流失败：{e}"}, ensure_ascii=False) + "\n"

    return StreamingResponse(gen(), media_type="text/x-ndjson")
```

- [ ] **Step 8.6：扩展 `.env.example`**

在 `backend/.env.example` 末尾追加：

```bash
# ── AI native agent 层（Phase 1 起） ─────────────────────────────────
# SQLite 路径（默认 backend/.cache/stockclaw.db，已 gitignore）。可改 ~/.stockclaw/stockclaw.db
VR_AGENT_DB=backend/.cache/stockclaw.db
# 默认主驾模型（用户在前端「接入 AI」里可覆盖）
VR_AGENT_MODEL=glm-5.2
# 主动 agent cron（Phase 3 生效，Phase 1 预留）
VR_AGENT_PROACTIVE_CRON=0 9 * * *
# plan-execute / 多 agent 最大轮数硬上限（Phase 2 生效）
VR_AGENT_MAX_ITERATIONS=8
# 东财 cool-down 秒数（Rate Limiter）
VR_AGENT_RATE_LIMIT_COOLDOWN=1.0
# 强缓存 TTL（秒，默认 30 分钟）
VR_AGENT_RATE_LIMIT_CACHE_TTL=1800
```

- [ ] **Step 8.7：跑 endpoint + runner 测试验证通过**

```bash
cd /vol2/1000/code/stockclaw/backend
.venv/bin/python -m pytest tests/test_agent_endpoint.py tests/test_runner.py -v
```

期望：5 个测试通过。

- [ ] **Step 8.8：curl 烟测（可选，需真实 LLM 端点）**

```bash
# 启动后端
cd /vol2/1000/code/stockclaw/backend
.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8900 &

# 发请求（替换为真实可用的 OpenAI 兼容端点）
curl -N -X POST http://127.0.0.1:8900/api/agent/chat \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [{"role":"user","content":"分析 600519 给目标价止损止盈"}],
    "context_codes": ["600519"],
    "llm": {"provider":"","baseURL":"https://open.bigmodel.cn/api/paas/v4","apiKey":"YOUR_KEY","model":"glm-4-plus"},
    "style": "balanced"
  }'
```

期望：流式输出多行 NDJSON，至少含 `text_delta` + `decision_artifact` + `done` 三种事件类型。

- [ ] **Step 8.9：commit**

```bash
git add backend/runner.py backend/app.py backend/.env.example backend/tests/test_agent_endpoint.py backend/tests/test_runner.py
git commit -m "feat(agent): runner + /api/agent/chat NDJSON endpoint

- runner.run_agent: 跑 graph + 流式 LLM + 推 text_delta / decision_artifact / citations / done
- /api/agent/chat: AgentChatReq Pydantic 模型按 spec §7（thread_id / context_codes / style）
- CLI 模式直接 400 拒绝（不支持 function-calling），不挂 SSE 流
- 鉴权失败复用 _require_api_key 中间件，自动 401 + JSON
- 每行严格以 \\n 结尾；Content-Type: text/x-ndjson
- .env.example 加 VR_AGENT_* 6 项"
```

---

## Task 9：persistence（aiosqlite + WAL + PRAGMA user_version migration）

**目标**：本地 SQLite 持久化——3 张表（`threads` / `conversations` / `decisions`），WAL 模式 + busy_timeout 5s。用 `PRAGMA user_version` 做轻量 migration，多版脚本可重入幂等。所有读写 `async`，启动两次不报 schema 错误。

**Files:**
- Create: `backend/persistence/__init__.py`
- Create: `backend/persistence/db.py`
- Create: `backend/persistence/threads.py`
- Create: `backend/persistence/conversations.py`
- Create: `backend/persistence/decisions.py`
- Test: `backend/tests/test_persistence.py`

- [ ] **Step 9.1：写失败的持久化单测**

```python
# backend/tests/test_persistence.py
"""持久化层测试——aiosqlite + WAL + migration 幂等 + 表 schema + CRUD。"""
import os
import tempfile

import pytest


@pytest.fixture
async def db(tmp_path, monkeypatch):
    """每个测试一个临时 db 文件。"""
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("VR_AGENT_DB", str(db_path))
    # 延迟 import 让 env 生效
    from persistence import db as db_mod
    await db_mod.init_db()
    yield db_mod
    await db_mod.close_db()


@pytest.mark.asyncio
async def test_migration_is_idempotent(db):
    """连跑两次 init 不报错（CREATE TABLE IF NOT EXISTS 幂等）。"""
    await db.init_db()  # 第二次
    await db.init_db()  # 第三次
    # user_version 应该 == 1（不是叠加）
    version = await db.get_user_version()
    assert version == 1


@pytest.mark.asyncio
async def test_threads_crud(db):
    """threads 表 CRUD：create / list / rename / delete（含 ON DELETE CASCADE）。"""
    from persistence import threads, conversations
    tid = await threads.create_thread(title="测试会话", model="gpt-4o")
    assert tid
    # list
    items = await threads.list_threads()
    assert any(t["id"] == tid for t in items)
    # rename
    await threads.rename_thread(tid, "新标题")
    item = await threads.get_thread(tid)
    assert item["title"] == "新标题"
    # CASCADE 验证：先塞一条 conversation，再删 thread
    await conversations.append_message(tid, {"role": "user", "content": "hi"})
    await threads.delete_thread(tid)
    items2 = await threads.list_threads()
    assert not any(t["id"] == tid for t in items2)
    # conversations 也应被 CASCADE 清空
    msgs = await conversations.list_messages(tid)
    assert msgs == []


@pytest.mark.asyncio
async def test_conversations_crud(db):
    """conversations 表：append_message + list_messages（按时间排序）。"""
    from persistence import conversations
    tid = "test-thread-id"
    # 先建 thread（外键约束）
    from persistence import threads
    await threads.create_thread(tid=tid, title="t", model="m")

    await conversations.append_message(tid, {"role": "user", "content": "第一条"})
    await conversations.append_message(tid, {"role": "assistant", "content": "回复"})
    msgs = await conversations.list_messages(tid)
    assert len(msgs) == 2
    assert msgs[0]["role"] == "user"
    assert msgs[1]["role"] == "assistant"
    # tool_calls_json / artifacts_json 字段存在
    assert "tool_calls_json" in msgs[0]
    assert "artifacts_json" in msgs[0]


@pytest.mark.asyncio
async def test_decisions_crud(db):
    """decisions 表：create + list_by_code + 收益追踪字段。"""
    from persistence import decisions
    did = await decisions.create_decision(
        thread_id="t1", code="600519", name="茅台",
        target_price=1900.0, entry_low=1685.0, entry_high=1720.0,
        stop_loss=1550.0, take_profit=2080.0,
        cadence=[{"batch": 1, "pct": 0.4, "trigger": "immediate", "price": 1685.0}],
        basis_type="model",
        model_versions_json={"target_price": "model(forward_pe_target.v1)"},
        assumptions=["14-day ATR"],
        citations=[{"source": "astock.kline", "code": "600519"}],
        raw_artifact={"code": "600519"},
    )
    items = await decisions.list_by_code("600519")
    assert any(d["id"] == did for d in items)
    # 收益追踪字段
    item = await decisions.get_decision(did)
    assert item["status"] == "active"
    assert item["price_at_creation"] is None  # Phase 3 才填
    assert item["current_price"] is None
```

- [ ] **Step 9.2：跑测试验证失败**

```bash
cd /vol2/1000/code/stockclaw/backend
.venv/bin/python -m pytest tests/test_persistence.py -v
```

期望：`ModuleNotFoundError: No module named 'persistence'`。

- [ ] **Step 9.3：建 persistence 包 + 实现 db.py**

```bash
mkdir -p backend/persistence
```

```python
# backend/persistence/__init__.py
"""本地 SQLite 持久化层。

- 强制 aiosqlite，所有读写 async（不阻塞 FastAPI event loop）
- WAL 模式 + busy_timeout=5000ms（防 database is locked）
- PRAGMA user_version 做轻量 migration（无外部工具依赖）
- 决策卡含个人投资决策——不入 git、不上传（backend/.cache/ 已 gitignore）
"""
```

```python
# backend/persistence/db.py
"""SQLite 连接管理 + WAL 初始化 + PRAGMA user_version migration。"""
from __future__ import annotations

import os
from typing import Any

import aiosqlite

# 默认 backend/.cache/stockclaw.db；env 可改 ~/.stockclaw/stockclaw.db
_HERE = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_DB = os.path.normpath(os.path.join(_HERE, "..", ".cache", "stockclaw.db"))
_DB_PATH = os.environ.get("VR_AGENT_DB", _DEFAULT_DB)

_conn: aiosqlite.Connection | None = None


# Migration 脚本：按版本递增，每版一组 SQL。CREATE TABLE IF NOT EXISTS 保证幂等。
MIGRATIONS: dict[int, list[str]] = {
    1: [
        # threads 表（spec §8 schema）
        """CREATE TABLE IF NOT EXISTS threads (
            id          TEXT PRIMARY KEY,
            title       TEXT NOT NULL,
            model       TEXT NOT NULL,
            created_at  INTEGER NOT NULL,
            updated_at  INTEGER NOT NULL
        );""",
        "CREATE INDEX IF NOT EXISTS idx_threads_updated_at ON threads(updated_at);",

        # conversations 表（spec §8 schema，含 tool_calls_json / artifacts_json）
        """CREATE TABLE IF NOT EXISTS conversations (
            id              TEXT PRIMARY KEY,
            thread_id       TEXT NOT NULL,
            role            TEXT NOT NULL,
            content         TEXT,
            tool_calls_json TEXT,
            tool_call_id    TEXT,
            artifacts_json  TEXT,
            created_at      INTEGER NOT NULL,
            FOREIGN KEY (thread_id) REFERENCES threads(id) ON DELETE CASCADE
        );""",
        "CREATE INDEX IF NOT EXISTS idx_conversations_thread ON conversations(thread_id, created_at);",

        # decisions 表（spec §8 schema）
        """CREATE TABLE IF NOT EXISTS decisions (
            id              TEXT PRIMARY KEY,
            thread_id       TEXT NOT NULL,
            code            TEXT NOT NULL,
            name            TEXT,
            created_at      INTEGER NOT NULL,

            target_price    REAL,
            entry_low       REAL,
            entry_high      REAL,
            stop_loss       REAL,
            take_profit     REAL,
            cadence_json    TEXT,

            basis_type      TEXT NOT NULL,
            model_versions_json  TEXT,
            assumptions_json     TEXT,
            citations_json  TEXT,

            status              TEXT,
            linked_position_code TEXT,
            price_at_creation   REAL,
            current_price       REAL,
            pnl_pct             REAL,
            updated_at          INTEGER,

            raw_artifact_json   TEXT
        );""",
        "CREATE INDEX IF NOT EXISTS idx_decisions_code ON decisions(code);",
        "CREATE INDEX IF NOT EXISTS idx_decisions_status ON decisions(status);",
        "CREATE INDEX IF NOT EXISTS idx_decisions_thread_id ON decisions(thread_id);",
    ],
}


async def _connect() -> aiosqlite.Connection:
    """打开连接 + 设 PRAGMA。"""
    os.makedirs(os.path.dirname(_DB_PATH) or ".", exist_ok=True)
    conn = await aiosqlite.connect(_DB_PATH)
    # WAL 模式：写不阻塞读
    await conn.execute("PRAGMA journal_mode=WAL;")
    # 5s 等锁，防 database is locked
    await conn.execute("PRAGMA busy_timeout=5000;")
    # 开外键（默认关，需显式开才有 ON DELETE CASCADE）
    await conn.execute("PRAGMA foreign_keys=ON;")
    return conn


async def init_db() -> None:
    """初始化 + 跑未应用的 migration。幂等。"""
    global _conn
    if _conn is not None:
        # 已开过连接：只确认 schema 最新
        await _run_migrations(_conn)
        return
    _conn = await _connect()
    await _run_migrations(_conn)


async def _run_migrations(conn: aiosqlite.Connection) -> None:
    """对比 PRAGMA user_version 与 MIGRATIONS，顺次执行未应用的 SQL。

    每版执行完写回 user_version。
    """
    async with conn.execute("PRAGMA user_version;") as cur:
        row = await cur.fetchone()
    current = row[0] if row else 0

    for version in sorted(MIGRATIONS.keys()):
        if version <= current:
            continue
        for sql in MIGRATIONS[version]:
            await conn.execute(sql)
        await conn.commit()
        await conn.execute(f"PRAGMA user_version = {version};")
        await conn.commit()


async def get_user_version() -> int:
    """测试用：返回当前 schema 版本。"""
    if _conn is None:
        await init_db()
    async with _conn.execute("PRAGMA user_version;") as cur:
        row = await cur.fetchone()
    return row[0] if row else 0


async def close_db() -> None:
    global _conn
    if _conn is not None:
        await _conn.close()
        _conn = None


async def get_conn() -> aiosqlite.Connection:
    """供其他模块复用。"""
    if _conn is None:
        await init_db()
    return _conn
```

- [ ] **Step 9.4：实现 threads / conversations / decisions CRUD**

```python
# backend/persistence/threads.py
"""threads 表 CRUD（会话列表，用于前端 sidebar 高效渲染）。"""
from __future__ import annotations

import time
import uuid

from persistence.db import get_conn


async def create_thread(title: str = "新会话", model: str = "", tid: str | None = None) -> str:
    """新建会话；返回 thread_id。"""
    conn = await get_conn()
    tid = tid or uuid.uuid4().hex
    now = int(time.time())
    await conn.execute(
        "INSERT INTO threads (id, title, model, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        (tid, title, model, now, now),
    )
    await conn.commit()
    return tid


async def get_thread(tid: str) -> dict | None:
    conn = await get_conn()
    async with conn.execute("SELECT id, title, model, created_at, updated_at FROM threads WHERE id = ?", (tid,)) as cur:
        row = await cur.fetchone()
    if not row:
        return None
    return {"id": row[0], "title": row[1], "model": row[2],
            "created_at": row[3], "updated_at": row[4]}


async def list_threads(limit: int = 100) -> list[dict]:
    """按 updated_at 倒序拿会话列表。"""
    conn = await get_conn()
    async with conn.execute(
        "SELECT id, title, model, created_at, updated_at FROM threads ORDER BY updated_at DESC LIMIT ?",
        (limit,),
    ) as cur:
        rows = await cur.fetchall()
    return [{"id": r[0], "title": r[1], "model": r[2],
             "created_at": r[3], "updated_at": r[4]} for r in rows]


async def rename_thread(tid: str, title: str) -> None:
    conn = await get_conn()
    await conn.execute(
        "UPDATE threads SET title = ?, updated_at = ? WHERE id = ?",
        (title, int(time.time()), tid),
    )
    await conn.commit()


async def touch_thread(tid: str) -> None:
    """更新 updated_at（每次新增消息时调）。"""
    conn = await get_conn()
    await conn.execute("UPDATE threads SET updated_at = ? WHERE id = ?",
                       (int(time.time()), tid))
    await conn.commit()


async def delete_thread(tid: str) -> None:
    """删会话；conversations 走 ON DELETE CASCADE 自动清空。"""
    conn = await get_conn()
    await conn.execute("DELETE FROM threads WHERE id = ?", (tid,))
    await conn.commit()
```

```python
# backend/persistence/conversations.py
"""conversations 表 CRUD（每条消息一行）。"""
from __future__ import annotations

import json
import time
import uuid

from persistence.db import get_conn
from persistence.threads import touch_thread


async def append_message(
    thread_id: str,
    message: dict,
    tool_calls_json: str | None = None,
    tool_call_id: str | None = None,
    artifacts_json: list | None = None,
) -> str:
    """追加一条消息。返回 message id。

    message: OpenAI 消息 dict，必含 role + content。
    artifacts_json: 本条消息产出的 artifact 列表（决策卡 / 图表 / 表格）。
    """
    conn = await get_conn()
    mid = uuid.uuid4().hex
    now = int(time.time())
    arts = json.dumps(artifacts_json, ensure_ascii=False) if artifacts_json else None
    await conn.execute(
        """INSERT INTO conversations
           (id, thread_id, role, content, tool_calls_json, tool_call_id, artifacts_json, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (mid, thread_id, message.get("role", "user"), message.get("content"),
         tool_calls_json, tool_call_id, arts, now),
    )
    await conn.commit()
    await touch_thread(thread_id)
    return mid


async def list_messages(thread_id: str) -> list[dict]:
    """按 created_at 升序拿所有消息。"""
    conn = await get_conn()
    async with conn.execute(
        """SELECT id, thread_id, role, content, tool_calls_json, tool_call_id, artifacts_json, created_at
           FROM conversations WHERE thread_id = ? ORDER BY created_at ASC""",
        (thread_id,),
    ) as cur:
        rows = await cur.fetchall()
    return [{
        "id": r[0], "thread_id": r[1], "role": r[2], "content": r[3],
        "tool_calls_json": json.loads(r[4]) if r[4] else None,
        "tool_call_id": r[5],
        "artifacts_json": json.loads(r[6]) if r[6] else None,
        "created_at": r[7],
    } for r in rows]
```

```python
# backend/persistence/decisions.py
"""decisions 表 CRUD（决策卡归档 + 收益追踪字段）。"""
from __future__ import annotations

import json
import time
import uuid

from persistence.db import get_conn


async def create_decision(
    thread_id: str, code: str, name: str | None,
    target_price: float, entry_low: float, entry_high: float,
    stop_loss: float, take_profit: float,
    cadence: list[dict], basis_type: str,
    model_versions_json: dict, assumptions: list[str], citations: list[dict],
    raw_artifact: dict,
) -> str:
    conn = await get_conn()
    did = uuid.uuid4().hex
    now = int(time.time())
    await conn.execute(
        """INSERT INTO decisions (
            id, thread_id, code, name, created_at,
            target_price, entry_low, entry_high, stop_loss, take_profit, cadence_json,
            basis_type, model_versions_json, assumptions_json, citations_json,
            status, raw_artifact_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (did, thread_id, code, name, now,
         target_price, entry_low, entry_high, stop_loss, take_profit,
         json.dumps(cadence, ensure_ascii=False),
         basis_type,
         json.dumps(model_versions_json, ensure_ascii=False),
         json.dumps(assumptions, ensure_ascii=False),
         json.dumps(citations, ensure_ascii=False),
         "active",  # 默认 active；Phase 3 scheduler 改 hit_target / hit_stop / expired
         json.dumps(raw_artifact, ensure_ascii=False)),
    )
    await conn.commit()
    return did


async def get_decision(did: str) -> dict | None:
    conn = await get_conn()
    async with conn.execute("SELECT * FROM decisions WHERE id = ?", (did,)) as cur:
        row = await cur.fetchone()
    if not row:
        return None
    cols = [d[0] for d in cur.description]
    out = dict(zip(cols, row))
    # JSON 字段反序列化
    for k in ("cadence_json", "model_versions_json", "assumptions_json", "citations_json", "raw_artifact_json"):
        if out.get(k):
            try:
                out[k] = json.loads(out[k])
            except (TypeError, json.JSONDecodeError):
                pass
    return out


async def list_by_code(code: str, limit: int = 50) -> list[dict]:
    conn = await get_conn()
    async with conn.execute(
        "SELECT * FROM decisions WHERE code = ? ORDER BY created_at DESC LIMIT ?",
        (code, limit),
    ) as cur:
        rows = await cur.fetchall()
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in rows]


async def list_active() -> list[dict]:
    """Phase 3 scheduler 用：拿所有待追踪的 active 决策。"""
    conn = await get_conn()
    async with conn.execute("SELECT * FROM decisions WHERE status = 'active'", ) as cur:
        rows = await cur.fetchall()
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in rows]
```

- [ ] **Step 9.5：在 `app.py` startup 初始化 DB**

修改 `backend/app.py`，在 `pf.start_scheduler(1800)` 之后加：

```python
# 启动时初始化 SQLite（spec §9 Phase 1 #9）
from contextlib import asynccontextmanager


@asynccontextmanager
async def _lifespan(_app):
    from persistence import db
    await db.init_db()
    yield
    await db.close_db()


app = FastAPI(title="Vibe-Research API", version="0.1.1", lifespan=_lifespan)
```

注：原 `app = FastAPI(title="Vibe-Research API", version="0.1.1")` 行替换为带 lifespan 的版本。

- [ ] **Step 9.6：跑全部 persistence 测试**

```bash
cd /vol2/1000/code/stockclaw/backend
.venv/bin/python -m pytest tests/test_persistence.py -v
```

期望：5 个测试通过。

- [ ] **Step 9.7：压测 1000 次连续写入无 database locked**

```bash
cd /vol2/1000/code/stockclaw/backend
.venv/bin/python -c "
import asyncio, os, tempfile
async def stress():
    from persistence import db, threads, conversations
    await db.init_db()
    tid = await threads.create_thread(title='stress', model='m')
    for i in range(1000):
        await conversations.append_message(tid, {'role':'user','content':f'msg {i}'})
    print('OK: 1000 inserts')
    await db.close_db()
asyncio.run(stress())
"
```

期望：1s 内输出 `OK: 1000 inserts`，无 `database is locked` 异常。

- [ ] **Step 9.8：commit**

```bash
git add backend/persistence/ backend/app.py backend/tests/test_persistence.py
git commit -m "feat(persistence): aiosqlite + WAL + PRAGMA user_version migration

- db.py: WAL 模式 + busy_timeout=5000ms + foreign_keys=ON
- MIGRATIONS dict 按 version 递增；CREATE TABLE IF NOT EXISTS 幂等
- migration V1 建齐 threads + conversations + decisions 三张表
- ON DELETE CASCADE：删 thread 自动清空 conversations
- conversations 表含 tool_calls_json / artifacts_json 字段（避免从 Markdown 反向解析）
- 1000 次连续写入无 database is locked"
```

---

## Task 10：前端 /agent 路由 + CLI 模型拦截覆盖层

**目标**：新增 `/agent` 路由 + `AgentWorkspace` 三栏布局骨架 + CLI 模型配置时显示拦截覆盖层 + 侧栏加「股神」入口 + TS 类型定义文件 + zustand store。

**Files:**
- Create: `frontend/src/lib/types/agent.ts`
- Create: `frontend/src/lib/stores/agent.ts`
- Modify: `frontend/src/lib/api.ts`（加 `api.agent.*` 方法）
- Create: `frontend/src/pages/Agent.tsx`
- Create: `frontend/src/components/agent/AgentWorkspace.tsx`
- Create: `frontend/src/components/agent/AgentSidebar.tsx`
- Create: `frontend/src/components/agent/AgentMain.tsx`
- Create: `frontend/src/components/agent/AgentTopBar.tsx`
- Modify: `frontend/src/router.tsx`
- Modify: `frontend/src/components/layout/Layout.tsx`

- [ ] **Step 10.1：建 TS 类型定义**

```typescript
// frontend/src/lib/types/agent.ts

// NDJSON 事件类型（spec §7）
export type AgentEventType =
  | "text_delta"
  | "tool_trace"
  | "decision_artifact"
  | "chart_artifact"
  | "table_artifact"
  | "citations"
  | "done"
  | "error";

export interface TextDeltaEvent {
  type: "text_delta";
  text: string;
}

export interface ToolTraceEvent {
  type: "tool_trace";
  tool: string;
  status: "running" | "ok" | "error";
  args: Record<string, unknown>;
  summary?: string;
}

// 决策卡 basis_type 4 档色标（spec §6 约束 3）
export type BasisType = "model" | "model_fallback" | "llm_reasoning" | "hybrid";

export interface CadenceBatch {
  batch: number;
  pct: number;
  trigger: string;
  price?: number;
  amount?: number;
  ref_price?: number;
}

export interface DecisionCardData {
  code: string;
  name: string;
  current_price: number;
  target_price: number;
  entry_low: number;
  entry_high: number;
  stop_loss: number;
  take_profit: number;
  cadence: CadenceBatch[];
  basis_type: BasisType;
  model_versions_json: Record<string, string>;
  assumptions: string[];
  citations: { source: string; code?: string; range?: string; note?: string }[];
  explanation: string;
}

export interface DecisionArtifactEvent {
  type: "decision_artifact";
  decision_id: string;
  data: DecisionCardData;
}

export interface CitationsEvent {
  type: "citations";
  items: { source: string; code?: string; range?: string }[];
}

export interface DoneEvent {
  type: "done";
  summary: { thread_id?: string; rounds?: number };
}

export interface ErrorEvent {
  type: "error";
  message: string;
  code?: string;
}

export type AgentEvent =
  | TextDeltaEvent
  | ToolTraceEvent
  | DecisionArtifactEvent
  | CitationsEvent
  | DoneEvent
  | ErrorEvent;

// API 请求体
export interface AgentChatReq {
  thread_id: string | null;
  messages: { role: string; content: string }[];
  context_codes: string[];
  llm: { provider: string; baseURL: string; apiKey: string; model: string };
  style: "conservative" | "balanced" | "aggressive";
}

// 会话
export interface AgentThread {
  id: string;
  title: string;
  model: string;
  created_at: number;
  updated_at: number;
}

// 渲染用消息
export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  toolTraces: ToolTraceEvent[];
  decisionCard?: DecisionCardData;
  citations?: { source: string; code?: string }[];
  streaming?: boolean;
}
```

- [ ] **Step 10.2：建 zustand store**

```typescript
// frontend/src/lib/stores/agent.ts
import { create } from "zustand";
import type { AgentThread, ChatMessage, ToolTraceEvent, DecisionCardData } from "@/lib/types/agent";

interface AgentState {
  threads: AgentThread[];
  currentThreadId: string | null;
  messagesByThread: Record<string, ChatMessage[]>;
  streaming: { active: boolean; toolCalls: ToolTraceEvent[] };
  savedDecisions: DecisionCardData[];

  // actions
  setThreads: (threads: AgentThread[]) => void;
  setCurrentThread: (tid: string | null) => void;
  appendMessage: (tid: string, msg: ChatMessage) => void;
  appendTextDelta: (tid: string, msgId: string, text: string) => void;
  appendToolTrace: (tid: string, msgId: string, trace: ToolTraceEvent) => void;
  setDecisionCard: (tid: string, msgId: string, card: DecisionCardData) => void;
  setCitations: (tid: string, msgId: string, items: { source: string; code?: string }[]) => void;
  finishStreaming: (tid: string, msgId: string) => void;
  resetStreaming: () => void;
  saveDecision: (card: DecisionCardData) => void;
  removeSavedDecision: (decisionId: string) => void;
}

export const useAgentStore = create<AgentState>((set) => ({
  threads: [],
  currentThreadId: null,
  messagesByThread: {},
  streaming: { active: false, toolCalls: [] },
  savedDecisions: [],

  setThreads: (threads) => set({ threads }),
  setCurrentThread: (tid) => set({ currentThreadId: tid }),

  appendMessage: (tid, msg) =>
    set((s) => ({
      messagesByThread: {
        ...s.messagesByThread,
        [tid]: [...(s.messagesByThread[tid] || []), msg],
      },
    })),

  appendTextDelta: (tid, msgId, text) =>
    set((s) => {
      const msgs = s.messagesByThread[tid] || [];
      return {
        messagesByThread: {
          ...s.messagesByThread,
          [tid]: msgs.map((m) => (m.id === msgId ? { ...m, content: m.content + text } : m)),
        },
      };
    }),

  appendToolTrace: (tid, msgId, trace) =>
    set((s) => {
      const msgs = s.messagesByThread[tid] || [];
      return {
        messagesByThread: {
          ...s.messagesByThread,
          [tid]: msgs.map((m) =>
            m.id === msgId ? { ...m, toolTraces: [...m.toolTraces, trace] } : m,
          ),
        },
        streaming: { ...s.streaming, toolCalls: [...s.streaming.toolCalls, trace] },
      };
    }),

  setDecisionCard: (tid, msgId, card) =>
    set((s) => {
      const msgs = s.messagesByThread[tid] || [];
      return {
        messagesByThread: {
          ...s.messagesByThread,
          [tid]: msgs.map((m) => (m.id === msgId ? { ...m, decisionCard: card } : m)),
        },
      };
    }),

  setCitations: (tid, msgId, items) =>
    set((s) => {
      const msgs = s.messagesByThread[tid] || [];
      return {
        messagesByThread: {
          ...s.messagesByThread,
          [tid]: msgs.map((m) => (m.id === msgId ? { ...m, citations: items } : m)),
        },
      };
    }),

  finishStreaming: (tid, msgId) =>
    set((s) => {
      const msgs = s.messagesByThread[tid] || [];
      return {
        messagesByThread: {
          ...s.messagesByThread,
          [tid]: msgs.map((m) => (m.id === msgId ? { ...m, streaming: false } : m)),
        },
      };
    }),

  resetStreaming: () => set({ streaming: { active: false, toolCalls: [] } }),

  saveDecision: (card) =>
    set((s) =>
      s.savedDecisions.some((d) => d.code === card.code)
        ? s
        : { savedDecisions: [card, ...s.savedDecisions].slice(0, 100) },
    ),

  removeSavedDecision: (decisionId) =>
    set((s) => ({ savedDecisions: s.savedDecisions.filter((d) => d.code !== decisionId) })),
}));
```

- [ ] **Step 10.3：扩展 `api.ts` 加 agent 客户端方法**

在 `frontend/src/lib/api.ts` 末尾（`export const api = {...}` 之前）加：

```typescript
// ---- Agent 工作台 ----
import type { AgentChatReq, AgentThread, DecisionCardData } from "@/lib/types/agent";

// 注意：agent chat 走 NDJSON 流式，不在 `request<T>` 包装内——直接用 fetch + ReadableStream
// 见 hooks/useAgentStream.ts

export const agentApi = {
  listThreads: () => get<AgentThread[]>("/agent/threads"),
  createThread: (title: string, model: string) =>
    request<AgentThread>("/agent/threads", "POST", { title, model }),
  renameThread: (tid: string, title: string) =>
    request<{ ok: boolean }>(`/agent/threads/${tid}`, "PATCH", { title }),
  deleteThread: (tid: string) =>
    request<{ ok: boolean }>(`/agent/threads/${tid}`, "DELETE"),
  listMessages: (tid: string) => get<unknown[]>(`/agent/threads/${tid}/messages`),
  saveDecision: (card: DecisionCardData) =>
    request<{ id: string }>("/agent/decisions", "POST", card),
  listDecisions: (code?: string) =>
    get<unknown[]>(`/agent/decisions${code ? `?code=${code}` : ""}`),
};
```

注：Phase 1 的 `api.agent.threads.*` 等只是接口预留，后端 `/api/agent/threads` 等 CRUD 端点不在 Phase 1 实现范围（spec §9 Phase 1 只列了 #8 的 `/api/agent/chat`）。前端列表先用本地 zustand store 内存态，Phase 2 再补后端 CRUD。

- [ ] **Step 10.4：建 AgentWorkspace 三栏布局**

```tsx
// frontend/src/components/agent/AgentWorkspace.tsx
import { useEffect, useState } from "react";
import { AgentSidebar } from "./AgentSidebar";
import { AgentMain } from "./AgentMain";
import { ContextDrawer } from "./ContextDrawer";
import { useAgentStore } from "@/lib/stores/agent";
import { loadLlmConfig } from "@/lib/llm";
import { CliBlocker } from "./CliBlocker";

export function AgentWorkspace() {
  const [drawerOpen, setDrawerOpen] = useState(true);
  const setThreads = useAgentStore((s) => s.setThreads);
  const llm = loadLlmConfig();

  // CLI 模型拦截：function-calling 与流式 agent 不支持
  if (llm.provider.startsWith("cli-")) {
    return <CliBlocker />;
  }

  return (
    <div className="flex h-[calc(100vh-3rem)] gap-2 p-2">
      <AgentSidebar />
      <AgentMain />
      {drawerOpen && <ContextDrawer onClose={() => setDrawerOpen(false)} />}
    </div>
  );
}
```

```tsx
// frontend/src/components/agent/CliBlocker.tsx
import { Link } from "react-router-dom";
import { AlertCircle } from "lucide-react";

export function CliBlocker() {
  return (
    <div className="flex h-[calc(100vh-3rem)] items-center justify-center p-6">
      <div className="glass max-w-md rounded-2xl p-6 text-center">
        <AlertCircle className="mx-auto mb-3 h-10 w-10 text-amber-500" />
        <h2 className="text-lg font-bold">需要 API 接入的模型</h2>
        <p className="mt-2 text-sm text-muted-foreground">
          Agent 工作台需要 Function-Calling 与流式多轮 Agent 路由，订阅接入（CLI 模式）不支持。
          请前往「接入 AI」页配置 API Key 或更换为 API 接入模型。
        </p>
        <Link
          to="/settings"
          className="mt-4 inline-block rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground"
        >
          前往「接入 AI」
        </Link>
      </div>
    </div>
  );
}
```

- [ ] **Step 10.5：建 AgentSidebar（会话列表）**

```tsx
// frontend/src/components/agent/AgentSidebar.tsx
import { Plus, MessageSquare, Trash2 } from "lucide-react";
import { useAgentStore } from "@/lib/stores/agent";
import { cn } from "@/lib/utils";

export function AgentSidebar() {
  const { threads, currentThreadId, setCurrentThread } = useAgentStore();

  const newThread = () => {
    // Phase 2 调 api.agent.createThread；Phase 1 用本地 store
    const tid = `local-${Date.now()}`;
    useAgentStore.setState((s) => ({
      threads: [
        { id: tid, title: "新会话", model: "", created_at: Date.now(), updated_at: Date.now() },
        ...s.threads,
      ],
      currentThreadId: tid,
      messagesByThread: { ...s.messagesByThread, [tid]: [] },
    }));
  };

  return (
    <aside className="glass flex w-60 flex-col rounded-2xl">
      <button
        onClick={newThread}
        className="m-2 flex items-center justify-center gap-2 rounded-lg bg-primary/10 px-3 py-2 text-sm font-medium text-primary hover:bg-primary/20"
      >
        <Plus className="h-4 w-4" /> 新建会话
      </button>
      <div className="flex-1 overflow-auto p-2">
        {threads.length === 0 && (
          <p className="px-2 py-4 text-xs text-muted-foreground">暂无会话</p>
        )}
        {threads.map((t) => (
          <button
            key={t.id}
            onClick={() => setCurrentThread(t.id)}
            className={cn(
              "mb-1 flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-sm transition-colors",
              t.id === currentThreadId
                ? "bg-primary/15 font-medium text-primary"
                : "text-muted-foreground hover:bg-muted/50 hover:text-foreground",
            )}
          >
            <MessageSquare className="h-3.5 w-3.5 shrink-0" />
            <span className="flex-1 truncate">{t.title}</span>
          </button>
        ))}
      </div>
    </aside>
  );
}
```

- [ ] **Step 10.6：建 AgentTopBar + AgentMain**

```tsx
// frontend/src/components/agent/AgentTopBar.tsx
import { useState } from "react";

export function AgentTopBar({ contextCodes }: { contextCodes: string[] }) {
  const [style, setStyle] = useState<"conservative" | "balanced" | "aggressive">("balanced");
  return (
    <div className="flex items-center gap-3 border-b border-border/50 px-4 py-2 text-sm">
      <span className="font-medium">模型:</span>
      <span className="text-muted-foreground">当前模型</span>
      <span className="text-muted-foreground">·</span>
      <label className="text-muted-foreground">风格:</label>
      <select
        value={style}
        onChange={(e) => setStyle(e.target.value as typeof style)}
        className="rounded border border-border/50 bg-transparent px-2 py-0.5 text-xs"
      >
        <option value="conservative">保守</option>
        <option value="balanced">平衡</option>
        <option value="aggressive">激进</option>
      </select>
      <span className="text-muted-foreground">·</span>
      <span className="text-xs text-muted-foreground">
        上下文: {contextCodes.length > 0 ? contextCodes.join("、") : "无"}
      </span>
    </div>
  );
}
```

```tsx
// frontend/src/components/agent/AgentMain.tsx
import { useState } from "react";
import { AgentTopBar } from "./AgentTopBar";
import { CustomAgentChat } from "./CustomAgentChat";
import { AgentComposer } from "./AgentComposer";

export function AgentMain() {
  const [contextCodes] = useState<string[]>([]);
  return (
    <main className="glass flex flex-1 flex-col rounded-2xl">
      <AgentTopBar contextCodes={contextCodes} />
      <CustomAgentChat />
      <AgentComposer />
    </main>
  );
}
```

注：`CustomAgentChat` 和 `AgentComposer` 在 Task 11 创建——这里先用占位：

```tsx
// frontend/src/components/agent/CustomAgentChat.tsx（Task 11 替换为完整实现）
export function CustomAgentChat() {
  return (
    <div className="flex-1 overflow-auto p-4 text-sm text-muted-foreground">
      Agent 工作台骨架（Task 11 完成聊天 + 决策卡渲染）
    </div>
  );
}
```

```tsx
// frontend/src/components/agent/AgentComposer.tsx（Task 11 替换为完整实现）
export function AgentComposer() {
  return (
    <div className="border-t border-border/50 p-3">
      <input
        placeholder="输入消息（Task 11 完成发送逻辑）"
        className="w-full rounded-lg border border-border/50 bg-transparent px-3 py-2 text-sm"
        disabled
      />
    </div>
  );
}
```

```tsx
// frontend/src/components/agent/ContextDrawer.tsx（Task 12 完整实现）
export function ContextDrawer({ onClose }: { onClose: () => void }) {
  return (
    <aside className="glass w-72 rounded-2xl p-3 text-sm text-muted-foreground">
      <div className="flex items-center justify-between">
        <span className="font-medium">上下文</span>
        <button onClick={onClose} className="text-xs">收起</button>
      </div>
      <p className="mt-2 text-xs">Task 12 完整实现（股票快卡 + 收藏决策卡）</p>
    </aside>
  );
}
```

- [ ] **Step 10.7：建 Agent 页面 + 加路由**

```tsx
// frontend/src/pages/Agent.tsx
import { AgentWorkspace } from "@/components/agent/AgentWorkspace";

export function Agent() {
  return <AgentWorkspace />;
}
```

修改 `frontend/src/router.tsx`：

```tsx
import { createBrowserRouter, Navigate } from "react-router-dom";
import { Layout } from "@/components/layout/Layout";
import { DailyReview } from "@/pages/DailyReview";
import { Intel } from "@/pages/Intel";
import { Sectors } from "@/pages/Sectors";
import { SectorDetail } from "@/pages/SectorDetail";
import { Portfolio } from "@/pages/Portfolio";
import { StockData } from "@/pages/StockData";
import { Watchlist } from "@/pages/Watchlist";
import { MyReports } from "@/pages/MyReports";
import { Notes } from "@/pages/Notes";
import { Settings } from "@/pages/Settings";
import { Agent } from "@/pages/Agent";

export const router = createBrowserRouter([
  {
    element: <Layout />,
    children: [
      { path: "/", element: <Navigate to="/daily-review" replace /> },
      { path: "/daily-review", element: <DailyReview /> },
      { path: "/intel", element: <Intel /> },
      { path: "/sectors", element: <Sectors /> },
      { path: "/sectors/:key", element: <SectorDetail /> },
      { path: "/portfolio", element: <Portfolio /> },
      { path: "/stock-data", element: <StockData /> },
      { path: "/watchlist", element: <Watchlist /> },
      { path: "/my-reports", element: <MyReports /> },
      { path: "/notes", element: <Notes /> },
      { path: "/agent", element: <Agent /> },
      { path: "/settings", element: <Settings /> },
    ],
  },
]);
```

- [ ] **Step 10.8：侧栏加「股神 / Agent」入口**

修改 `frontend/src/components/layout/Layout.tsx`：

```tsx
import {
  Activity, Radar, LayoutGrid, Wallet, Settings, Search, NotebookPen,
  Moon, Sun, ChevronsLeft, ChevronsRight, LineChart, Github, UserRound,
  Cog, Cpu, Database, Cable, Rocket, FlaskConical, Star, FileText,
  Bot,  // 新增：Agent 图标
} from "lucide-react";

const NAV = [
  { to: "/daily-review", icon: Activity, label: "每日复盘" },
  { to: "/intel", icon: Radar, label: "资讯雷达" },
  { to: "/sectors", icon: LayoutGrid, label: "板块中心" },
  { to: "/stock-data", icon: Search, label: "个股数据" },
  { to: "/watchlist", icon: Star, label: "自选股" },
  { to: "/portfolio", icon: Wallet, label: "我的持仓" },
  { to: "/my-reports", icon: FileText, label: "我的研报" },
  { to: "/notes", icon: NotebookPen, label: "研究记录" },
  { to: "/agent", icon: Bot, label: "股神" },  // 新增
  { to: "/settings", icon: Settings, label: "接入 AI" },
];
```

同时把 Layout 的页脚那段「v0.1.1 · 不荐股 · 不预测 · 无倾向」改为：

```tsx
<p className="text-[11px] leading-relaxed text-muted-foreground/60">
  {APP_VERSION} · 个人本地部署 · 非投资建议风格
</p>
```

- [ ] **Step 10.9：类型检查 + 启动 dev server 手测**

```bash
cd /vol2/1000/code/stockclaw/frontend
npm run build  # tsc -b + vite build；验证类型正确
```

期望：build 成功无 TS 报错。

```bash
npm run dev  # 启动 dev server :55890
```

浏览器访问 `http://127.0.0.1:55890/agent`：
- 验证：页面可达，看到三栏布局骨架（侧栏 + 主区 + 抽屉）
- 验证：CLI 模型配置下显示 `<CliBlocker>` 覆盖层（先在「接入 AI」选 cli-claude 再访问 /agent）
- 验证：现有 10 页零破坏（导航 /daily-review /intel 等正常）

- [ ] **Step 10.10：commit**

```bash
git add frontend/src/lib/types/agent.ts frontend/src/lib/stores/agent.ts frontend/src/lib/api.ts frontend/src/pages/Agent.tsx frontend/src/components/agent/ frontend/src/router.tsx frontend/src/components/layout/Layout.tsx
git commit -m "feat(frontend): /agent 路由 + AgentWorkspace 三栏布局 + CLI 模型拦截

- TS 类型定义：NDJSON 事件 / DecisionCardData / AgentChatReq / ChatMessage
- zustand store：threads / messagesByThread / streaming / savedDecisions
- api.agent.threads.* / decisions.* 客户端方法（后端 CRUD 留 Phase 2）
- AgentWorkspace：侧栏 + Main + ContextDrawer 三栏
- CliBlocker：provider=cli-* 时展示拦截覆盖层（跳 Settings）
- Layout 侧栏加「股神 / Agent」入口；页脚改为「个人本地部署 · 非投资建议风格」
- 现有 10 页零改动；路由可达 /agent"
```

---

## Task 11：CustomAgentChat + useAgentStream（NDJSON line buffer）

**目标**：完整实现聊天 UI——`useAgentStream` hook 维护跨 chunk line buffer + `TextDecoder{stream:true}` + 单条坏帧不中断整流；`CustomAgentChat` 按 `event.type` 分发渲染；`ToolTrace` 折叠小药丸；`AgentComposer` 输入框 + 快捷 prompt。

**Files:**
- Create: `frontend/src/hooks/useAgentStream.ts`
- Modify: `frontend/src/components/agent/CustomAgentChat.tsx`
- Create: `frontend/src/components/agent/ToolTrace.tsx`
- Modify: `frontend/src/components/agent/AgentComposer.tsx`

- [ ] **Step 11.1：实现 useAgentStream hook（line buffer + TextDecoder{stream:true}）**

```typescript
// frontend/src/hooks/useAgentStream.ts
import { useCallback, useRef } from "react";
import { useAgentStore } from "@/lib/stores/agent";
import { authHeaders } from "@/lib/api";
import { loadLlmConfig } from "@/lib/llm";
import type { AgentEvent, AgentChatReq } from "@/lib/types/agent";

interface SendOpts {
  threadId: string | null;
  content: string;
  contextCodes: string[];
  style: "conservative" | "balanced" | "aggressive";
  onDone?: (summary: { thread_id?: string; rounds?: number }) => void;
  onError?: (message: string) => void;
}

export function useAgentStream() {
  const abortRef = useRef<AbortController | null>(null);
  const {
    appendMessage, appendTextDelta, appendToolTrace,
    setDecisionCard, setCitations, finishStreaming, resetStreaming,
  } = useAgentStore.getState();

  const dispatch = useCallback((tid: string, msgId: string, event: AgentEvent) => {
    switch (event.type) {
      case "text_delta":
        appendTextDelta(tid, msgId, event.text);
        break;
      case "tool_trace":
        appendToolTrace(tid, msgId, event);
        break;
      case "decision_artifact":
        setDecisionCard(tid, msgId, event.data);
        break;
      case "citations":
        setCitations(tid, msgId, event.items);
        break;
      case "done":
        finishStreaming(tid, msgId);
        break;
      case "error":
        finishStreaming(tid, msgId);
        break;
      // chart_artifact / table_artifact：Phase 2 处理
    }
  }, [appendTextDelta, appendToolTrace, setDecisionCard, setCitations, finishStreaming]);

  const send = useCallback(async (opts: SendOpts) => {
    const tid = opts.threadId || `local-${Date.now()}`;
    const userMsgId = `u-${Date.now()}`;
    const assistantMsgId = `a-${Date.now() + 1}`;

    // 1. 写入用户消息 + 占位 assistant 消息（streaming: true）
    appendMessage(tid, {
      id: userMsgId, role: "user", content: opts.content, toolTraces: [],
    });
    appendMessage(tid, {
      id: assistantMsgId, role: "assistant", content: "", toolTraces: [], streaming: true,
    });
    useAgentStore.setState({ currentThreadId: tid });
    resetStreaming();
    useAgentStore.setState({ streaming: { active: true, toolCalls: [] } });

    // 2. 构造请求
    const llm = loadLlmConfig();
    const body: AgentChatReq = {
      thread_id: opts.threadId,
      messages: [{ role: "user", content: opts.content }],
      context_codes: opts.contextCodes,
      llm,
      style: opts.style,
    };

    abortRef.current = new AbortController();
    let response: Response;
    try {
      response = await fetch("/api/agent/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify(body),
        signal: abortRef.current.signal,
      });
    } catch (e) {
      opts.onError?.(`连接失败：${e instanceof Error ? e.message : "未知错误"}`);
      finishStreaming(tid, assistantMsgId);
      return;
    }

    // 3. 鉴权失败 / CLI 拒绝：HTTP 4xx + JSON（不是 SSE 流）
    if (!response.ok) {
      let detail = `HTTP ${response.status}`;
      try {
        const j = await response.json();
        detail = j.detail || detail;
      } catch { /* 非 JSON 响应 */ }
      opts.onError?.(detail);
      finishStreaming(tid, assistantMsgId);
      return;
    }

    // 4. 流式解析 NDJSON：跨 chunk line buffer + TextDecoder{stream:true}
    const reader = response.body?.getReader();
    if (!reader) {
      opts.onError?.("无响应体");
      finishStreaming(tid, assistantMsgId);
      return;
    }

    const decoder = new TextDecoder();
    let lineBuffer = "";
    let doneSummary: { thread_id?: string; rounds?: number } | undefined;

    try {
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        // stream:true 保证多字节 UTF-8 字符跨 chunk 不被截断
        lineBuffer += decoder.decode(value, { stream: true });
        const lines = lineBuffer.split("\n");
        // 弹出最后一个（可能不完整的）行，留到下次拼接
        lineBuffer = lines.pop() ?? "";

        for (const line of lines) {
          const trimmed = line.trim();
          if (!trimmed) continue;
          try {
            const event = JSON.parse(trimmed) as AgentEvent;
            if (event.type === "done") {
              doneSummary = event.summary;
            }
            dispatch(tid, assistantMsgId, event);
          } catch (e) {
            // 单条坏帧不中断整流——只 console.error
            console.error("NDJSON 帧解析失败:", trimmed, e);
          }
        }
      }
      // flush 缓冲区里最后残留的一行
      if (lineBuffer.trim()) {
        try {
          const event = JSON.parse(lineBuffer.trim()) as AgentEvent;
          if (event.type === "done") doneSummary = event.summary;
          dispatch(tid, assistantMsgId, event);
        } catch (e) {
          console.error("NDJSON flush 帧解析失败:", lineBuffer, e);
        }
      }
    } finally {
      finishStreaming(tid, assistantMsgId);
      useAgentStore.setState({ streaming: { active: false, toolCalls: [] } });
      opts.onDone?.(doneSummary || {});
    }
  }, [appendMessage, dispatch, finishStreaming, resetStreaming]);

  const abort = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  return { send, abort };
}
```

- [ ] **Step 11.2：实现 ToolTrace 折叠小药丸**

```tsx
// frontend/src/components/agent/ToolTrace.tsx
import { useState } from "react";
import { ChevronDown, ChevronRight, Loader2, CheckCircle, XCircle } from "lucide-react";
import type { ToolTraceEvent } from "@/lib/types/agent";
import { cn } from "@/lib/utils";

export function ToolTrace({ trace }: { trace: ToolTraceEvent }) {
  const [open, setOpen] = useState(false);
  const Icon = trace.status === "running" ? Loader2
    : trace.status === "ok" ? CheckCircle
    : XCircle;
  const color = trace.status === "running" ? "text-amber-500"
    : trace.status === "ok" ? "text-emerald-500"
    : "text-red-500";

  return (
    <div className="my-1 rounded-md border border-border/40 bg-muted/30 text-xs">
      <button
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-1.5 px-2 py-1.5 text-left"
      >
        {open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
        <Icon className={cn("h-3 w-3", color, trace.status === "running" && "animate-spin")} />
        <span className="font-mono">{trace.tool}</span>
        {trace.summary && <span className="text-muted-foreground"> · {trace.summary}</span>}
      </button>
      {open && (
        <pre className="border-t border-border/40 px-2 py-1.5 font-mono text-[11px] text-muted-foreground overflow-auto">
          {JSON.stringify(trace.args, null, 2)}
        </pre>
      )}
    </div>
  );
}
```

- [ ] **Step 11.3：完整实现 CustomAgentChat**

```tsx
// frontend/src/components/agent/CustomAgentChat.tsx
import { useEffect, useRef } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useAgentStore } from "@/lib/stores/agent";
import { ToolTrace } from "./ToolTrace";
import { DecisionCard } from "./DecisionCard";  // Task 12 实现但本任务先 import
import { Bot, User } from "lucide-react";

export function CustomAgentChat() {
  const currentThreadId = useAgentStore((s) => s.currentThreadId);
  const messages = useAgentStore((s) =>
    currentThreadId ? s.messagesByThread[currentThreadId] || [] : [],
  );
  const scrollRef = useRef<HTMLDivElement>(null);

  // 新消息时自动滚到底
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  if (!currentThreadId) {
    return (
      <div className="flex flex-1 items-center justify-center p-6 text-sm text-muted-foreground">
        点击左侧「+ 新建会话」开始
      </div>
    );
  }

  return (
    <div ref={scrollRef} className="flex-1 overflow-auto px-4 py-3">
      {messages.map((m) => (
        <div key={m.id} className="mb-4 flex gap-3">
          <div className={["h-7 w-7 shrink-0 rounded-full flex items-center justify-center",
            m.role === "user" ? "bg-primary/20 text-primary" : "glass text-muted-foreground"].join(" ")}>
            {m.role === "user" ? <User className="h-3.5 w-3.5" /> : <Bot className="h-3.5 w-3.5" />}
          </div>
          <div className="flex-1 min-w-0">
            <div className="text-xs text-muted-foreground mb-1">
              {m.role === "user" ? "你" : "Agent"}
            </div>
            {m.content && (
              <div className="prose prose-sm dark:prose-invert max-w-none">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {m.content}
                </ReactMarkdown>
              </div>
            )}
            {m.toolTraces.map((t, i) => (
              <ToolTrace key={`${t.tool}-${i}`} trace={t} />
            ))}
            {m.decisionCard && <DecisionCard card={m.decisionCard} />}
            {m.citations && m.citations.length > 0 && (
              <div className="mt-2 text-[11px] text-muted-foreground">
                数据出处：{m.citations.map((c) => c.source).join("、")}
              </div>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}
```

注：`DecisionCard` 在 Task 12 实现。先用占位避免 import 失败：

```tsx
// frontend/src/components/agent/DecisionCard.tsx（Task 12 替换）
export function DecisionCard({ card }: { card: any }) {
  return (
    <div className="mt-2 rounded-md border border-primary/30 bg-primary/5 p-3 text-xs">
      决策卡 · {card.code} · 目标价 {card.target_price}（Task 12 完整实现）
    </div>
  );
}
```

- [ ] **Step 11.4：完整实现 AgentComposer**

```tsx
// frontend/src/components/agent/AgentComposer.tsx
import { useState } from "react";
import { Send, Loader2 } from "lucide-react";
import { useAgentStore } from "@/lib/stores/agent";
import { useAgentStream } from "@/hooks/useAgentStream";

const QUICK_PROMPTS = [
  "分析茅台 给目标价止损止盈仓位节奏",
  "宁德时代 现在能买吗",
  "帮我对比下光伏板块几只龙头",
];

export function AgentComposer() {
  const [content, setContent] = useState("");
  const [style, setStyle] = useState<"conservative" | "balanced" | "aggressive">("balanced");
  const { send, abort } = useAgentStream();
  const streaming = useAgentStore((s) => s.streaming.active);
  const currentThreadId = useAgentStore((s) => s.currentThreadId);

  const submit = () => {
    if (!content.trim() || streaming) return;
    send({
      threadId: currentThreadId,
      content: content.trim(),
      contextCodes: [],
      style,
    });
    setContent("");
  };

  return (
    <div className="border-t border-border/50 p-3">
      <div className="mb-2 flex gap-1.5 flex-wrap">
        {QUICK_PROMPTS.map((q) => (
          <button
            key={q}
            onClick={() => setContent(q)}
            disabled={streaming}
            className="rounded-full bg-muted/40 px-2.5 py-1 text-[11px] text-muted-foreground hover:bg-muted/70 disabled:opacity-50"
          >
            {q}
          </button>
        ))}
      </div>
      <div className="flex items-end gap-2">
        <textarea
          value={content}
          onChange={(e) => setContent(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              submit();
            }
          }}
          placeholder="输入消息；Enter 发送，Shift+Enter 换行"
          rows={2}
          className="flex-1 resize-none rounded-lg border border-border/50 bg-transparent px-3 py-2 text-sm outline-none focus:border-primary/50"
          disabled={streaming}
        />
        <select
          value={style}
          onChange={(e) => setStyle(e.target.value as typeof style)}
          className="rounded-lg border border-border/50 bg-transparent px-2 py-2 text-xs"
        >
          <option value="conservative">保守</option>
          <option value="balanced">平衡</option>
          <option value="aggressive">激进</option>
        </select>
        {streaming ? (
          <button
            onClick={abort}
            className="rounded-lg bg-red-500/10 px-3 py-2 text-sm text-red-500 hover:bg-red-500/20"
          >
            <Loader2 className="h-4 w-4 animate-spin" />
          </button>
        ) : (
          <button
            onClick={submit}
            disabled={!content.trim()}
            className="rounded-lg bg-primary px-3 py-2 text-sm font-medium text-primary-foreground disabled:opacity-40"
          >
            <Send className="h-4 w-4" />
          </button>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 11.5：类型检查 + dev server 手测**

```bash
cd /vol2/1000/code/stockclaw/frontend
npm run build
```

期望：build 成功无 TS 报错。

```bash
npm run dev
```

浏览器手测：
- 访问 `/agent` → 新建会话 → 输入"分析 600519 给目标价" → 看到流式 text_delta + tool_trace + decision_artifact
- 用 Chrome DevTools Network → 找 `/api/agent/chat` → Response 标签页应能看到逐行 NDJSON
- 主动构造一条坏帧（mock 后端发坏 JSON）—— 验证前端不崩、只 console.error

- [ ] **Step 11.6：commit**

```bash
git add frontend/src/hooks/useAgentStream.ts frontend/src/components/agent/CustomAgentChat.tsx frontend/src/components/agent/ToolTrace.tsx frontend/src/components/agent/AgentComposer.tsx frontend/src/components/agent/DecisionCard.tsx
git commit -m "feat(frontend): CustomAgentChat + useAgentStream + ToolTrace

- useAgentStream: line buffer 跨 chunk 拼接 NDJSON；TextDecoder{stream:true} 防多字节 UTF-8 截断
  单条坏帧 try/catch 不中断整流；最后 flush 残留行
- CustomAgentChat: 按 event.type 分发（text_delta / tool_trace / decision_artifact / citations / done / error）
  react-markdown + remark-gfm 渲染；新消息自动滚到底
- ToolTrace: 折叠小药丸（running/ok/error 三态图标 + 参数 JSON 展开）
- AgentComposer: 输入框 + 快捷 prompt + 风格切换 + Abort 按钮
- 鉴权失败/CLI 拒绝走 HTTP 4xx + JSON，不挂 SSE 流"
```

---

## Task 12：DecisionCard + ContextDrawer（basis_type 4 档色标 + 字段级 model_versions）

**目标**：决策卡 UI 完整实现——目标价/入场区/止损/止盈/仓位节奏结构化展示 + 4 档 `basis_type` 色标（蓝/黄/橙/灰）+ 「依据」展开按字段级 `model_versions_json` 显示来源 + 收藏按钮。ContextDrawer 完整实现——股票快卡 + 收藏的决策卡列表 + 快速跳转。

**Files:**
- Modify: `frontend/src/components/agent/DecisionCard.tsx`（替换 Task 11 的占位）
- Modify: `frontend/src/components/agent/ContextDrawer.tsx`（替换 Task 10 的占位）

- [ ] **Step 12.1：完整实现 DecisionCard**

```tsx
// frontend/src/components/agent/DecisionCard.tsx
import { useState } from "react";
import { Heart, Copy, RefreshCw, ChevronDown, ChevronRight } from "lucide-react";
import type { DecisionCardData, BasisType } from "@/lib/types/agent";
import { useAgentStore } from "@/lib/stores/agent";
import { cn } from "@/lib/utils";

// basis_type 4 档色标（spec §6 约束 3）
const BASIS_COLORS: Record<BasisType, { bg: string; text: string; label: string }> = {
  model: { bg: "bg-blue-500/15", text: "text-blue-500", label: "model" },
  model_fallback: { bg: "bg-amber-500/15", text: "text-amber-500", label: "model_fallback" },
  hybrid: { bg: "bg-orange-500/15", text: "text-orange-500", label: "hybrid" },
  llm_reasoning: { bg: "bg-zinc-500/15", text: "text-zinc-500", label: "llm_reasoning" },
};

const BASIS_DESC: Record<BasisType, string> = {
  model: "A 股数据齐全，走完整公式（最可信）",
  model_fallback: "数据不足，Python 简化公式降级",
  hybrid: "model 出基础值 + LLM 微调",
  llm_reasoning: "仅 LLM 推理（target_price 字段）",
};

export function DecisionCard({ card }: { card: DecisionCardData }) {
  const [showBasis, setShowBasis] = useState(false);
  const [saved, setSaved] = useState(false);
  const saveDecision = useAgentStore((s) => s.saveDecision);

  const basis = BASIS_COLORS[card.basis_type];
  const changePct = ((card.target_price - card.current_price) / card.current_price) * 100;

  const handleSave = () => {
    saveDecision(card);
    setSaved(true);
  };

  const handleCopy = () => {
    const lines = [
      `${card.name}（${card.code}） 决策卡`,
      `目标价 ¥${card.target_price.toFixed(2)}（${changePct >= 0 ? "+" : ""}${changePct.toFixed(1)}%）`,
      `入场区 ¥${card.entry_low.toFixed(2)} – ¥${card.entry_high.toFixed(2)}`,
      `止损 ¥${card.stop_loss.toFixed(2)}`,
      `止盈 ¥${card.take_profit.toFixed(2)}`,
      "",
      "仓位节奏：",
      ...card.cadence.map((c) =>
        `  第${c.batch}批 ${Math.round(c.pct * 100)}% ${c.trigger} ¥${(c.price || c.ref_price || 0).toFixed(2)}`,
      ),
      "",
      `依据：${basis.label} - ${BASIS_DESC[card.basis_type]}`,
      card.explanation,
    ];
    navigator.clipboard.writeText(lines.join("\n"));
  };

  return (
    <div className="mt-2 rounded-xl border border-primary/30 bg-primary/5 p-4">
      <div className="flex items-start justify-between">
        <div>
          <h3 className="text-base font-bold">
            决策卡 · {card.code} {card.name}
          </h3>
          <p className="text-xs text-muted-foreground mt-0.5">
            目标价 <span className="font-mono text-primary">¥{card.target_price.toFixed(2)}</span>
            <span className={cn("ml-1", changePct >= 0 ? "text-red-500" : "text-emerald-500")}>
              ({changePct >= 0 ? "+" : ""}{changePct.toFixed(1)}%)
            </span>
          </p>
        </div>
        <div className="flex gap-1">
          <button
            onClick={handleSave}
            className={cn("rounded p-1.5 hover:bg-muted/50", saved && "text-red-500")}
            title={saved ? "已收藏" : "收藏"}
          >
            <Heart className={cn("h-4 w-4", saved && "fill-current")} />
          </button>
          <button onClick={handleCopy} className="rounded p-1.5 hover:bg-muted/50" title="复制">
            <Copy className="h-4 w-4" />
          </button>
          <button className="rounded p-1.5 hover:bg-muted/50" title="复盘追踪（Phase 3）">
            <RefreshCw className="h-4 w-4" />
          </button>
        </div>
      </div>

      <div className="mt-3 grid grid-cols-2 gap-3 text-sm">
        <div>
          <div className="text-xs text-muted-foreground">入场区</div>
          <div className="font-mono">¥{card.entry_low.toFixed(2)} – ¥{card.entry_high.toFixed(2)}</div>
        </div>
        <div>
          <div className="text-xs text-muted-foreground">止损</div>
          <div className="font-mono text-red-500/80">¥{card.stop_loss.toFixed(2)}</div>
        </div>
        <div>
          <div className="text-xs text-muted-foreground">止盈</div>
          <div className="font-mono text-emerald-500/80">¥{card.take_profit.toFixed(2)}</div>
        </div>
        <div>
          <div className="text-xs text-muted-foreground">仓位节奏</div>
          <div className="font-mono">{card.cadence.length} 批</div>
        </div>
      </div>

      {card.cadence.length > 0 && (
        <div className="mt-3">
          <div className="text-xs text-muted-foreground mb-1">分批计划</div>
          <div className="space-y-1">
            {card.cadence.map((c) => (
              <div key={c.batch} className="flex items-center gap-3 text-xs">
                <span className="w-12 text-muted-foreground">第 {c.batch} 批</span>
                <span className="w-12 font-mono">{Math.round(c.pct * 100)}%</span>
                <span className="flex-1 text-muted-foreground">{c.trigger}</span>
                <span className="font-mono">¥{(c.price || c.ref_price || 0).toFixed(2)}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 依据：4 档色标 + 字段级 model_versions_json 展开 */}
      <div className="mt-3 border-t border-border/40 pt-2">
        <button
          onClick={() => setShowBasis(!showBasis)}
          className="flex w-full items-center gap-1 text-xs"
        >
          {showBasis ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
          <span className="text-muted-foreground">依据</span>
          <span className={cn("ml-1 rounded-full px-2 py-0.5 text-[11px] font-mono", basis.bg, basis.text)}>
            ● {basis.label}
          </span>
          <span className="ml-auto text-muted-foreground/60">{BASIS_DESC[card.basis_type]}</span>
        </button>
        {showBasis && (
          <div className="mt-2 pl-4 text-[11px] text-muted-foreground">
            <div className="mb-1 font-medium">字段级来源：</div>
            <ul className="space-y-0.5 font-mono">
              {Object.entries(card.model_versions_json).map(([field, ver]) => (
                <li key={field}>
                  <span className="text-foreground/80">{field}</span>
                  <span className="mx-2">←</span>
                  <span>{ver}</span>
                </li>
              ))}
            </ul>
            {card.assumptions.length > 0 && (
              <>
                <div className="mb-1 mt-2 font-medium">假设：</div>
                <ul className="list-disc pl-4">
                  {card.assumptions.map((a, i) => <li key={i}>{a}</li>)}
                </ul>
              </>
            )}
            {card.explanation && (
              <p className="mt-2 border-t border-border/30 pt-1">{card.explanation}</p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 12.2：完整实现 ContextDrawer**

```tsx
// frontend/src/components/agent/ContextDrawer.tsx
import { Link } from "react-router-dom";
import { X, Star, Wallet, Search } from "lucide-react";
import { useAgentStore } from "@/lib/stores/agent";

export function ContextDrawer({ onClose }: { onClose: () => void }) {
  const { savedDecisions, removeSavedDecision } = useAgentStore();

  return (
    <aside className="glass w-72 rounded-2xl p-3 text-sm flex flex-col">
      <div className="flex items-center justify-between border-b border-border/40 pb-2">
        <span className="font-medium">上下文</span>
        <button onClick={onClose} className="text-muted-foreground hover:text-foreground">
          <X className="h-3.5 w-3.5" />
        </button>
      </div>

      <div className="mt-3">
        <div className="text-xs text-muted-foreground mb-1">快速跳转</div>
        <div className="space-y-1">
          <Link to="/watchlist" className="flex items-center gap-2 rounded-lg px-2 py-1.5 text-xs hover:bg-muted/50">
            <Star className="h-3 w-3" /> 自选股
          </Link>
          <Link to="/portfolio" className="flex items-center gap-2 rounded-lg px-2 py-1.5 text-xs hover:bg-muted/50">
            <Wallet className="h-3 w-3" /> 我的持仓
          </Link>
          <Link to="/stock-data" className="flex items-center gap-2 rounded-lg px-2 py-1.5 text-xs hover:bg-muted/50">
            <Search className="h-3 w-3" /> 个股数据
          </Link>
        </div>
      </div>

      <div className="mt-4 flex-1 overflow-auto">
        <div className="text-xs text-muted-foreground mb-2">收藏的决策卡（{savedDecisions.length}）</div>
        {savedDecisions.length === 0 ? (
          <p className="text-[11px] text-muted-foreground/60">在决策卡上点 ♡ 收藏</p>
        ) : (
          <div className="space-y-2">
            {savedDecisions.map((d, i) => (
              <div key={`${d.code}-${i}`} className="rounded-md border border-border/40 bg-muted/20 p-2 text-xs">
                <div className="flex items-center justify-between">
                  <span className="font-medium">{d.name}（{d.code}）</span>
                  <button
                    onClick={() => removeSavedDecision(d.code)}
                    className="text-muted-foreground hover:text-red-500"
                  >
                    <X className="h-3 w-3" />
                  </button>
                </div>
                <div className="mt-1 font-mono text-muted-foreground">
                  目标 ¥{d.target_price.toFixed(0)} · 止损 ¥{d.stop_loss.toFixed(0)}
                </div>
                <div className="text-[10px] text-muted-foreground/70 mt-0.5">
                  依据：{d.basis_type}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </aside>
  );
}
```

- [ ] **Step 12.3：类型检查 + 完整端到端手测**

```bash
cd /vol2/1000/code/stockclaw/frontend
npm run build
```

期望：build 成功无 TS 报错。

启动前后端：

```bash
# Terminal 1：后端
cd /vol2/1000/code/stockclaw/backend
.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8900

# Terminal 2：前端
cd /vol2/1000/code/stockclaw/frontend
npm run dev
```

浏览器访问 `http://127.0.0.1:55890/agent`：

- 新建会话 → 输入"分析茅台（600519）给目标价止损止盈仓位节奏"
- 验证：流式文本 + 决策卡出现，4 档 `basis_type` 色标显示
- 验证：点决策卡「依据」展开 → 看到字段级 `model_versions_json`（如 `target_price ← model(forward_pe_target.v1)`）
- 验证：点 ♡ 收藏 → ContextDrawer 出现该决策卡
- 验证：点决策卡「复制」→ 粘贴到任意文本框，看到结构化文本

**美港股降级验证**：
- 输入"分析苹果（AAPL）" → 决策卡出现，`basis_type: model_fallback`（黄色标）+ `fallback_reason: non_a_share_code` 在字段级 model_versions 中可见

**Rate Limiter 端到端验证**（spec §9 Phase 1 出口）：
- 输入"对比 5 只股票：600519 / 000858 / 300750 / 002594 / 000651"
- 5 个并发工具请求总耗时 ≥ 4s（可在 Network 标签看到 tool_trace 时间戳间隔）

- [ ] **Step 12.4：commit**

```bash
git add frontend/src/components/agent/DecisionCard.tsx frontend/src/components/agent/ContextDrawer.tsx
git commit -m "feat(frontend): DecisionCard 完整 + ContextDrawer 完整

- DecisionCard：4 档 basis_type 色标（蓝/黄/橙/灰）+ 字段级 model_versions_json 展开
  收藏（♡）写入 zustand；复制为结构化文本；仓位节奏分批列表
- ContextDrawer：快速跳转（/watchlist /portfolio /stock-data）+ 收藏决策卡列表
- 美港股决策卡显示 basis_type=model_fallback + 字段级 fallback_reason
- Phase 1 出口：用户能拿到带色标的决策卡，能收藏、能复制"
```

---

## Self-Review（plan 作者自查清单）

### 1. Spec 覆盖（spec §9 Phase 1 #1-#12 全部覆盖）

| Spec # | 交付物 | 本 plan 落点 |
|---|---|---|
| 1 | 合规解禁 + 物理隔离 | Task 1（chat.py → chat_legacy.py + agents/prompts.py + AGENTS.md 重写 + app.py:21 + mcp_server.py:18） |
| 2 | Rate Limiter | Task 2（EastmoneyRateLimiter cool-down 1.0s；__aenter__/__aexit__ 锁横跨业务；并发压测 ≥ 4s） |
| 3 | quant.valuation | Task 3（forward_pe_target + pe_percentile_revert + pb_percentile_revert；DataUnavailable） |
| 4 | quant.stops（含 fallback） | Task 4（atr_stop A 股主路径 + model_fallback；structure_stop；risk_based_position 读 portfolio.json::totals） |
| 5 | quant.cadence | Task 5（pyramid_buy + batch_build + dca_plan） |
| 6 | portfolio 字段扩展 | Task 6（available_cash / risk_tolerance_pct / total_equity_override；老 JSON 向后兼容） |
| 7 | agents 核心 | Task 7（state/graph/orchestrator/decision/tools/prompts；basis_type 归并；字段级 model_versions_json；asyncio.to_thread 硬约束） |
| 8 | runner + NDJSON endpoint | Task 8（AgentChatReq Pydantic；401 短路不挂 SSE；CLI 模式 400；每行 \n 结尾） |
| 9 | persistence（aiosqlite） | Task 9（WAL + busy_timeout 5000 + PRAGMA user_version migration；V1 建齐 threads + conversations + decisions） |
| 10 | 前端 /agent 路由 + 模型校验 | Task 10（router.tsx + AgentWorkspace 三栏 + CliBlocker 拦截覆盖层） |
| 11 | CustomAgentChat + 事件分发 | Task 11（useAgentStream line buffer + TextDecoder{stream:true} + 单条坏帧不中断整流） |
| 12 | DecisionCard + ContextDrawer | Task 12（4 档色标 + 字段级 model_versions 展开 + 收藏） |

### 2. 阶段性 hardening 验收点（spec §9 Phase 1 「出口」段）

- ✅ `/agent` 输入"分析茅台 给目标价止损止盈仓位节奏" → 拿到带 `basis_type` 的决策卡（Task 12 手测）
- ✅ 能收藏、能复制（Task 12 DecisionCard）
- ✅ 美港股请求自动走 `model_fallback` 或 `llm_reasoning`，决策卡明确标降级依据（Task 12 手测 + Task 4 单测）
- ✅ 5 个并发工具请求总耗时 ≥ 4s，不触发东财 403（Task 2 单测 + Task 12 手测）
- ✅ SQLite 1000 次连续写入无 `database is locked`（Task 9 Step 9.7 压测）
- ✅ 决策卡内的批次计划等价于"分批建仓"建议本身（Task 5 + Task 7 cadence 字段）

### 3. 类型一致性检查

- ✅ `DecisionCardData`（TS）= Python decision_card 字段一一对应（Task 7 build_decision_card 输出 vs Task 10 TS 类型）
- ✅ `AgentEvent`（TS）= NDJSON 协议表（spec §7）的事件类型一一对应（text_delta / tool_trace / decision_artifact / citations / done / error）
- ✅ `basis_type` 取值四档（model / model_fallback / llm_reasoning / hybrid）在 Python `merge_basis_type` 和 TS `BasisType` 完全对齐
- ✅ `AgentChatReq` Pydantic（Task 8）字段与 TS `AgentChatReq`（Task 10）一一对应（thread_id / messages / context_codes / llm / style）
- ✅ `EastmoneyRateLimiter.__aenter__/__aexit__` 在 Task 2 实现，Task 7 `tools.py::_run_sync` 用 `async with eastmoney_limiter` 复用同一锁语义

### 4. 硬约束验证（每个 task 完成后自查）

- ✅ Task 7 Step 7.10：`grep -E "await (astock|gstock|market|newsradable)" backend/agents/tools.py` 必须无命中
- ✅ Task 2 单测：锁横跨业务（`async with self._lock` 的 trap test）
- ✅ Task 8 单测：鉴权失败 401 短路 + JSON（不挂 SSE）
- ✅ Task 9 单测：migration 幂等（连跑三次 `user_version` 仍 == 1）

### 5. 不做的事（Phase 1 YAGNI）

- ❌ 多 agent 讨论（panel_valuation / panel_funds / panel_earnings / panel_industry / panel_events）—— Phase 2
- ❌ plan-execute planner / replanner —— Phase 2
- ❌ 回测（backtest_strategy / signal_backtest / walk_forward / monte_carlo）—— Phase 2
- ❌ 主动 agent / `/today` —— Phase 3
- ❌ ChartRenderer / TableRenderer —— Phase 2（chart_artifact / table_artifact 事件协议已在 Task 10 TS 类型预留）
- ❌ MCP server 升级（暴露 agents.graph）—— Phase 4
- ❌ 风格切换实际生效（保守/平衡/激进 → 影响 multiplier / 仓位百分比）—— Phase 4（Phase 1 只在前端 store 预留）

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-08-ai-native-agent-phase-1.md`. Two execution options:

**1. Subagent-Driven (recommended)** — 每个 task 派一个 fresh subagent；两阶段 review（subagent 自检 + 主 agent 验收）；fast iteration；适合 12 个相对独立的 task。

**2. Inline Execution** — 在当前 session 内顺序执行；checkpoint review；适合需要密集调试 / 跨 task 调整架构时。

**Which approach?**

如果选 1：调用 superpowers:subagent-driven-development，主 agent 每个 task 派 subagent，2 阶段 review（自检 + 验收）。

如果选 2：调用 superpowers:executing-plans，按 Task 1-12 顺序执行，每个 task 完成 commit 后 checkpoint。

---

## Task 13：对抗性评审回修（2026-07-09）

**评审文档**：`docs/superpowers/review/adversarial-review-phase1-plan-2026-07-09.md`
**评审结论**：Approved with 8 Issues
**回修策略**：8 项全部采纳——逐项给出处置 + 替换代码。执行时按 Task 1→12 顺序，但每个 task 用本节对应 patch 替换原 step 的代码块。**本节 patch 优先级高于原 step 代码——出现冲突时以本节为准。**

### 回修总览

| # | 评审漏洞 | 风险 | 处置 | 影响步骤 |
|---|---|---|---|---|
| R1 | runner.py 用同步 requests 阻塞 event loop | 严重 | ✅ 采纳：换 httpx.AsyncClient | Task 8 Step 8.4 |
| R2 | decision_node 不发 tool_trace 事件 | 中 | ✅ 采纳：graph state 收集 + runner 推送 | Task 7 Step 7.8 + Task 8 Step 8.4 |
| R3 | `.format(context=...)` 花括号脆弱 | 中 | ✅ 采纳：改 `.replace("{context}", ...)` | Task 8 Step 8.4 |
| R4 | agentApi 调用不存在的 CRUD 端点 | 中 | ✅ 采纳方案 1：补后端路由 | 新增 Task 9 Step 9.5 |
| R5 | runner.py 不写持久化 | 中 | ✅ 采纳：runner 内嵌 threads/conversations/decisions 写入 | Task 8 Step 8.4 |
| R6 | _DB_PATH 模块级读 env，测试不可靠 | 低 | ✅ 采纳：改 `_get_db_path()` 函数 | Task 9 Step 9.3 |
| R7 | `loadLlmConfig` 不存在（实为 `loadLlm` + 返回可空） | 低（但编译必挂） | ✅ 采纳：改 import + null 检查 | Task 10 Step 10.4 + Task 11 Step 11.1 |
| R8 | `DataUnavailable` + `_contract` 重复定义 | 低 | ✅ 采纳：提取到 `quant/__init__.py` | Task 3 Step 3.3 + Task 4 Step 4.3 |

**零拒绝。** 评审的 8 项发现均与 spec / 代码事实直接对应，无主观偏好项。

---

### R1：runner.py 改用 httpx.AsyncClient（替换 Task 8 Step 8.4 中 `_stream_llm_text`）

**Why**：`requests` 是同步阻塞库。在 `async def _stream_llm_text` 内用 `requests.post(stream=True) + iter_content` 会冻结整个 FastAPI event loop——Rate Limiter 的 `asyncio.sleep` 无法调度，其他并发请求（`/api/quote` 等）全部排队，用户在浏览器看到长时间无反应。这违反了 plan 自己在 Task 7 全局硬约束 #2 写的"asyncio.to_thread 卸载同步层"原则。

**替换代码**（替换 Task 8 Step 8.4 中 `_stream_llm_text` 函数，以及顶部 `import requests` 改为 `import httpx`）：

```python
# runner.py 顶部 import 区
import httpx  # 替换 import requests
```

```python
# runner.py 中 _stream_llm_text 完整替换为：
async def _stream_llm_text(cfg: dict, system_prompt: str, user_messages: list[dict],
                           context_codes: list[str]) -> AsyncGenerator[str, None]:
    """调上游 OpenAI 兼容端点的流式接口，逐 chunk yield 文本 delta。

    httpx.AsyncClient 走真异步流式；不阻塞 event loop，不干扰 Rate Limiter 的 sleep。
    Phase 1 不接 function-calling（决策路径由 graph 直接调 quant 工具）。
    """
    base = cfg["baseURL"].rstrip("/")
    if not base.endswith(("/v1", "/v3", "/api/v3")):
        base = base + "/v1"
    context_str = "；".join(context_codes) if context_codes else "（无）"
    # 用 replace 而非 format，防 prompt 文本里其他花括号引发 KeyError（见 R3）
    system_content = system_prompt.replace("{context}", context_str)
    messages = [{"role": "system", "content": system_content}]
    messages.extend(user_messages)

    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
        async with client.stream(
            "POST",
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {cfg['apiKey']}", "Content-Type": "application/json"},
            json={"model": cfg["model"], "messages": messages, "temperature": 0.3, "stream": True},
        ) as resp:
            if resp.status_code != 200:
                body = await resp.aread()
                raise RuntimeError(f"模型接口 HTTP {resp.status_code}: {body[:300]}")
            async for line in resp.aiter_lines():
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    return
                try:
                    j = json.loads(data)
                except json.JSONDecodeError:
                    continue
                choices = j.get("choices") or []
                if choices:
                    delta = choices[0].get("delta") or {}
                    text = delta.get("content")
                    if text:
                        yield text
```

**新增依赖**（pyproject.toml `[project] dependencies` 加一行）：

```toml
"httpx>=0.24",  # runner._stream_llm_text 用真异步流式（不阻塞 event loop）
```

执行：

```bash
cd /vol2/1000/code/stockclaw/backend
uv pip install httpx
```

---

### R2：decision_node 收集 tool_traces + runner 推送（替换 Task 7 Step 7.8 decision_node + Task 8 Step 8.4 runner.run_agent 相关段）

**Why**：spec §7 NDJSON 协议表明确要求 `tool_trace` 事件（status: running/ok/error），Phase 1 #11 验收也要求前端展示"atr_stop 运行中"小药丸。原 plan 在 `decision_node` 里调了 4 个工具但从未把 trace 写入 state——前端 `ToolTrace` 组件（Task 11）永远收不到事件。

#### Step 7.8.1：在 `_invoke` helper 里收集 trace

替换 Task 7 Step 7.8 的 `_invoke` helper + `decision_node` 主体：

```python
# backend/agents/nodes/decision.py 末尾追加（接续 Task 7.4 已有内容）

async def _invoke(tool, traces_out: list, **kwargs) -> dict:
    """安全调用 tool，失败返回 fallback contract。同时把 trace 写入 traces_out。

    traces_out：list 引用，调用方传入；本函数 append 一条 trace dict。
    """
    trace_entry = {"tool": tool.name, "status": "running", "args": kwargs, "summary": None}
    traces_out.append(trace_entry)
    try:
        result = await tool.ainvoke(kwargs)
        trace_entry["status"] = "ok"
        # 摘要：basis_type / 关键输出
        if isinstance(result, dict):
            basis = result.get("basis_type", "")
            outs = result.get("outputs") or {}
            if "stop_price" in outs:
                trace_entry["summary"] = f"{basis} stop={outs['stop_price']}"
            elif "target_price" in outs:
                trace_entry["summary"] = f"{basis} target={outs['target_price']}"
            elif "shares" in outs:
                trace_entry["summary"] = f"shares={outs.get('shares')}"
            elif "plan" in outs:
                trace_entry["summary"] = f"{len(outs['plan'])} batches"
        return result
    except Exception as e:
        trace_entry["status"] = "error"
        trace_entry["summary"] = str(e)[:120]
        return {
            "error": f"{tool.name} failed: {e}",
            "basis_type": "model_fallback",
            "model_version": f"{tool.name}.v1",
            "outputs": {}, "citations": [],
            "model_assumptions": [f"工具失败：{e}"],
        }


async def decision_node(state: AgentState) -> dict:
    """LangGraph 节点：调 quant 工具集 + 合并决策卡。

    Phase 1 简化版：固定调 forward_pe_target + atr_stop + risk_based_position + batch_build。
    Phase 2 改为 LLM 决定调哪些工具。

    返回 state patch：{decision_card, tool_traces}——runner 从 tool_traces 推 NDJSON 事件。
    """
    context_codes = state.get("context_codes") or []
    msgs = state.get("messages") or []
    code = context_codes[0] if context_codes else _extract_code_from_messages(msgs)
    if not code:
        return {"decision_card": None, "tool_traces": []}

    # 工具调用追踪列表（会被 _invoke append；最终写入 graph state）
    traces: list[dict] = []

    # 串行调工具（Rate Limiter cool-down 1.0s 强制串行；并发反而触发东财 403）
    target_r = await _invoke(forward_pe_target, traces, code=code, target_pe=20.0, eps_year="27e")
    stop_r = await _invoke(atr_stop, traces, code=code, period=14, multiplier=2.0)
    entry_r = await _invoke(pe_percentile_revert, traces, code=code, revert_to=0.50)

    # 取当前价 + 名称
    current_price = (target_r or {}).get("outputs", {}).get("current_price") or \
                    (stop_r or {}).get("outputs", {}).get("current_price") or 0.0
    name = _lookup_name(code)

    target_price = (target_r or {}).get("outputs", {}).get("target_price") or current_price * 1.15
    stop_loss = (stop_r or {}).get("outputs", {}).get("stop_price") or current_price * 0.92
    entry_low = current_price * 0.98
    entry_high = current_price * 1.02
    take_profit = current_price * 1.20

    pos_r = await _invoke(risk_based_position, traces, entry_price=current_price, stop_price=stop_loss)
    cad_r = await _invoke(batch_build, traces, total_budget=100000.0, batches=3,
                          schedule="weekly", start_price=current_price)
    cadence = (cad_r or {}).get("outputs", {}).get("plan") or []

    card = build_decision_card(
        code=code, name=name, current_price=current_price,
        target_price=target_price, entry_low=entry_low, entry_high=entry_high,
        stop_loss=stop_loss, take_profit=take_profit,
        cadence=cadence,
        tool_results={
            "target": target_r or {}, "entry": entry_r or {},
            "stop": stop_r or {}, "position": pos_r or {},
            "take_profit": target_r or {},  # 复用 target
        },
        explanation=f"基于前向 PE 目标价 {target_price:.2f} + ATR 止损 {stop_loss:.2f} + 分批 3 期建仓",
    )
    # 把 traces 也写到 state，runner 提取后推 NDJSON tool_trace 事件
    return {"decision_card": card, "tool_traces": traces}


def _extract_code_from_messages(msgs) -> str | None:
    """从消息文本里抓 6 位 A 股代码 / 美股字母代码。"""
    import re
    pat = re.compile(r"\b(\d{6}|[A-Z]{1,5})\b")
    for m in reversed(msgs):
        text = m.get("content", "") if isinstance(m, dict) else getattr(m, "content", "")
        hit = pat.search(text or "")
        if hit:
            return hit.group(1)
    return None


def _lookup_name(code: str) -> str:
    """从 astock.tencent_quote 查股票名（缓存层面交 astock）。"""
    if code.isdigit() and len(code) == 6:
        try:
            import astock
            q = astock.tencent_quote([code])
            return q.get(code, {}).get("name", code)
        except Exception:
            pass
    return code
```

注：顶部 import 区追加（已有就别重复）：

```python
from agents.tools import (
    forward_pe_target, pe_percentile_revert, atr_stop, structure_stop,
    risk_based_position, pyramid_buy, batch_build, dca_plan,
)
```

#### Step 8.4 补丁：runner.py 推送 tool_trace 事件

见下面 R5 的完整 runner.py 替换版（包含 R1+R2+R3+R5 全部修复）。

---

### R3：runner.py 用 `.replace()` 替代 `.format()`（已在 R1 替换代码里融合）

**Why**：原 `system_prompt.format(context=context_str)` 是脆弱设计——`SYSTEM_PROMPT_AGENT` 是 f-string 定义，`{{context}}` 在定义时已被降级为字面 `{context}`，但任何在 `ANALYSIS_FRAMEWORK_AGENT` 文本里出现的裸 `{` 都会让 `.format()` 抛 KeyError。改用 `.replace("{context}", context_str)` 完全避开命名参数解析。

**处置**：已在 R1 替换代码中融合——`_stream_llm_text` 内改为：

```python
system_content = system_prompt.replace("{context}", context_str)
messages = [{"role": "system", "content": system_content}]
```

Task 1 Step 1.5 的 `agents/prompts.py` 中 `SYSTEM_PROMPT_AGENT = f"""...{{context}}"""` 保持不变——f-string 在定义时已把 `{{context}}` 转为字面 `{context}`，运行时 `.replace("{context}", ...)` 能正确匹配。

---

### R4：补后端 CRUD 端点（新增 Task 9 Step 9.5）

**Why**：Task 10 Step 10.3 的 `agentApi` 客户端方法调 `/api/agent/threads` 和 `/api/agent/decisions` 等 8 个端点，但原 Phase 1 plan 只实现了 `/api/agent/chat`。Task 9 的 persistence CRUD 是死代码——runner.py 也不写持久化（R5）→ 前端发请求一律 404 + ApiError，刷新页面全部会话丢失。Task 9 persistence 已实现，只差路由注册，工作量小、收益大（让 Sidebar 真正可持久化）。采纳评审建议方案 1。

**位置**：插入 Task 9 Step 9.5 之前作为新的 Step 9.4.5（在 db.py / threads.py / conversations.py / decisions.py 已实现 CRUD 之后，app.py lifespan 之前）。

- [ ] **Step 9.4.5：在 app.py 注册 /api/agent/threads + /api/agent/decisions 路由**

在 `backend/app.py` 文件末尾追加（在所有现有路由之后）：

```python
# ---------------------------------------------------------------------------
# Agent 工作台持久化路由（spec §7 AgentSidebar：会话列表从 SQLite 同步）
# 复用 persistence.{threads,conversations,decisions} CRUD 层。
# ---------------------------------------------------------------------------

from persistence import threads as _threads_store
from persistence import conversations as _convos_store
from persistence import decisions as _dec_store


class ThreadIn(BaseModel):
    title: str = "新会话"
    model: str = ""


class ThreadRename(BaseModel):
    title: str


@app.post("/api/agent/threads")
async def agent_create_thread(t: ThreadIn):
    """新建会话；返回 thread 对象。"""
    tid = await _threads_store.create_thread(title=t.title, model=t.model)
    return {"data": await _threads_store.get_thread(tid)}


@app.get("/api/agent/threads")
async def agent_list_threads():
    """列会话（按 updated_at 倒序）。"""
    return {"data": await _threads_store.list_threads()}


@app.get("/api/agent/threads/{tid}")
async def agent_get_thread(tid: str):
    item = await _threads_store.get_thread(tid)
    if not item:
        raise HTTPException(404, "会话不存在")
    return {"data": item}


@app.patch("/api/agent/threads/{tid}")
async def agent_rename_thread(tid: str, body: ThreadRename):
    await _threads_store.rename_thread(tid, body.title)
    return {"data": {"ok": True}}


@app.delete("/api/agent/threads/{tid}")
async def agent_delete_thread(tid: str):
    """删会话；conversations 走 ON DELETE CASCADE 自动清空（spec §9 #9）。"""
    await _threads_store.delete_thread(tid)
    return {"data": {"ok": True}}


@app.get("/api/agent/threads/{tid}/messages")
async def agent_list_messages(tid: str):
    return {"data": await _convos_store.list_messages(tid)}


@app.post("/api/agent/decisions")
async def agent_save_decision(card: dict):
    """收藏决策卡到 decisions 表。

    card: DecisionCardData 完整 dict（spec §7 NDJSON decision_artifact.data）。
    """
    did = await _dec_store.create_decision(
        thread_id=card.get("_thread_id", ""),  # 可选——前端收藏时可能未关联 thread
        code=card["code"], name=card.get("name"),
        target_price=card["target_price"], entry_low=card["entry_low"], entry_high=card["entry_high"],
        stop_loss=card["stop_loss"], take_profit=card["take_profit"],
        cadence=card.get("cadence", []),
        basis_type=card["basis_type"],
        model_versions_json=card.get("model_versions_json", {}),
        assumptions=card.get("assumptions", []),
        citations=card.get("citations", []),
        raw_artifact=card,
    )
    return {"data": {"id": did}}


@app.get("/api/agent/decisions")
async def agent_list_decisions(code: str | None = None):
    """列收藏的决策卡；可选按 code 过滤。"""
    if code:
        return {"data": await _dec_store.list_by_code(code)}
    return {"data": await _dec_store.list_active()}
```

注：`save_decision` 的 `thread_id` 是可选字段——前端从决策卡 UI 收藏时不一定知道当前 thread。schema 上 `thread_id TEXT NOT NULL`，所以这里给个空串兜底；若需强约束，可改成 `thread_id: str` 必填、前端收藏时传当前 thread。

**测试**：在 `backend/tests/test_persistence.py` 末尾追加一个端到端测试：

```python
@pytest.mark.asyncio
async def test_thread_endpoints_crud(tmp_path, monkeypatch):
    """验证 /api/agent/threads CRUD 路由可走通。"""
    monkeypatch.setenv("VR_AGENT_DB", str(tmp_path / "ep.db"))
    from persistence import db as db_mod
    await db_mod.init_db()
    import importlib, app
    importlib.reload(app)
    from fastapi.testclient import TestClient
    client = TestClient(app.app)

    # create
    resp = client.post("/api/agent/threads", json={"title": "测试", "model": "m"})
    assert resp.status_code == 200
    tid = resp.json()["data"]["id"]
    # list
    resp = client.get("/api/agent/threads")
    assert any(t["id"] == tid for t in resp.json()["data"])
    # rename
    resp = client.patch(f"/api/agent/threads/{tid}", json={"title": "新"})
    assert resp.status_code == 200
    # delete
    resp = client.delete(f"/api/agent/threads/{tid}")
    assert resp.status_code == 200
    await db_mod.close_db()
```

---

### R5：runner.py 写持久化（替换 Task 8 Step 8.4 的 `run_agent` 完整实现）

**Why**：原 `run_agent` 全程不写 SQLite——用户消息、助手回复、决策卡都只在内存态。Task 9 辛苦实现的 CRUD 是死代码；刷新页面全部丢失。这违反 spec §7「会话列表（从 SQLite 同步）」与 §8 持久化层设计。

**完整替换**（融合 R1 + R2 + R3 + R5 全部修复——替换 Task 8 Step 8.4 中 `run_agent` 函数主体）：

```python
# runner.py 顶部 import 追加
import time
from persistence import threads as _threads
from persistence import conversations as _convos
from persistence import decisions as _decisions


async def run_agent(req: AgentChatReq) -> AsyncGenerator[dict, None]:
    """主入口：跑 graph + 流式输出 NDJSON 事件 + 写持久化。

    事件序列：
    1. tool_trace × N（每个 quant 工具一条，含 running/ok/error）
    2. text_delta × M（LLM 流式文本）
    3. decision_artifact（若 graph 出 decision_card）
    4. citations（决策卡数据出处批量上报）
    5. done {summary: {thread_id, rounds}}

    持久化：
    - thread_id 为 None 时新建 thread（spec §7 续聊语义）
    - 用户消息追加到 conversations
    - 流式结束后保存助手消息（含 decision_card artifact）
    - 若有 decision_card，写入 decisions 表
    """
    decision_id = uuid.uuid4().hex

    # —— 1. 解析或新建 thread ——
    thread_id = req.thread_id
    if not thread_id:
        # 用第一条 user 消息前 20 字作标题
        first_user = next((m.get("content", "") for m in req.messages
                           if m.get("role") == "user"), "新会话")
        title = (first_user[:20] + "…") if len(first_user) > 20 else first_user
        thread_id = await _threads.create_thread(title=title or "新会话", model=req.llm.model)

    # —— 2. 持久化 user 消息（续聊只发新增——见 spec §7 续聊语义） ——
    for msg in req.messages:
        if msg.get("role") == "user":
            await _convos.append_message(thread_id, msg)

    try:
        # —— 3. 跑 graph 拿 decision_card + tool_traces ——
        graph_state = {
            "messages": req.messages,
            "context_codes": req.context_codes,
            "style": req.style,
            "thread_id": thread_id,
        }
        graph_result = await agent_graph.ainvoke(graph_state)
        decision_card = graph_result.get("decision_card")
        tool_traces = graph_result.get("tool_traces") or []

        # —— 4. 推 tool_trace 事件（在 text_delta 之前——展示"工具运行中"） ——
        for trace in tool_traces:
            yield {
                "type": "tool_trace",
                "tool": trace["tool"],
                "status": trace["status"],
                "args": trace.get("args", {}),
                "summary": trace.get("summary"),
            }

        # —— 5. 流式 LLM 文本（用决策卡作 context 加强） ——
        if decision_card:
            summary = (
                f"基于工具结果：目标价 {decision_card.get('target_price')}，"
                f"止损 {decision_card.get('stop_loss')}，止盈 {decision_card.get('take_profit')}，"
                f"依据 {decision_card.get('basis_type')}。"
            )
            enhanced_messages = list(req.messages) + [{
                "role": "assistant", "content": f"[工具结果摘要] {summary}"
            }]
        else:
            enhanced_messages = req.messages

        accumulated_text: list[str] = []
        async for text in _stream_llm_text(
            req.llm.model_dump(), SYSTEM_PROMPT_AGENT, enhanced_messages, req.context_codes,
        ):
            accumulated_text.append(text)
            yield {"type": "text_delta", "text": text}

        # —— 6. 推决策卡 artifact（如果有） ——
        if decision_card:
            yield {
                "type": "decision_artifact",
                "decision_id": decision_id,
                "data": decision_card,
            }
            yield {
                "type": "citations",
                "items": decision_card.get("citations") or [],
            }

            # 写入 decisions 表（spec §8 持久化）
            try:
                await _decisions.create_decision(
                    thread_id=thread_id,
                    code=decision_card["code"],
                    name=decision_card.get("name"),
                    target_price=decision_card["target_price"],
                    entry_low=decision_card["entry_low"],
                    entry_high=decision_card["entry_high"],
                    stop_loss=decision_card["stop_loss"],
                    take_profit=decision_card["take_profit"],
                    cadence=decision_card.get("cadence", []),
                    basis_type=decision_card["basis_type"],
                    model_versions_json=decision_card.get("model_versions_json", {}),
                    assumptions=decision_card.get("assumptions", []),
                    citations=decision_card.get("citations", []),
                    raw_artifact=decision_card,
                )
            except Exception as e:  # noqa: BLE001 — 决策卡写入失败不挂流
                yield {"type": "error", "message": f"决策卡持久化失败：{e}", "code": "persist_fail"}

        # —— 7. 持久化助手回复（content + artifacts_json） ——
        assistant_content = "".join(accumulated_text)
        await _convos.append_message(
            thread_id,
            {"role": "assistant", "content": assistant_content},
            artifacts_json=[decision_card] if decision_card else None,
        )

        # —— 8. done 事件（带 thread_id，前端记下供续聊） ——
        yield {"type": "done", "summary": {"thread_id": thread_id, "rounds": 1}}

    except Exception as e:
        yield {"type": "error", "message": f"agent 运行失败：{e}"}
```

注：`runner.py` 顶部 imports 现为：

```python
from __future__ import annotations

import json
import time
import uuid
from typing import Any, AsyncGenerator

import httpx  # R1：替代 requests
from pydantic import BaseModel

from agents.graph import agent_graph
from agents.prompts import SYSTEM_PROMPT_AGENT
from persistence import threads as _threads
from persistence import conversations as _convos
from persistence import decisions as _decisions
```

`AgentChatReq` Pydantic 模型（顶部）保持不变。

**单测 patch**：在 `backend/tests/test_runner.py` 顶部追加 persistence mock：

```python
import asyncio
from unittest.mock import patch, AsyncMock

import pytest

import runner


@pytest.fixture(autouse=True)
def _stub_persistence(monkeypatch):
    """runner.py 现在写持久化；测试 stub 掉避免依赖真实 SQLite。"""
    async def _no_thread(*args, **kwargs):
        return "test-tid"
    async def _no_op(*args, **kwargs):
        return None
    monkeypatch.setattr("runner._threads.create_thread", _no_thread)
    monkeypatch.setattr("runner._convos.append_message", _no_op)
    monkeypatch.setattr("runner._decisions.create_decision", _no_op)
```

`test_run_agent_emits_decision_artifact_for_decision_intent` 现在还要验证 tool_trace 被推送：

```python
async def test_run_agent_emits_decision_artifact_for_decision_intent(_stub_persistence):
    """decision 路径 → 至少含 tool_trace × N + text_delta + decision_artifact + done。"""
    async def fake_graph_ainvoke(input_state):
        return {
            "intent": "decision",
            "decision_card": {
                "code": "600519", "name": "茅台", "current_price": 1685.0,
                "target_price": 1900.0, "entry_low": 1685.0, "entry_high": 1720.0,
                "stop_loss": 1550.0, "take_profit": 2080.0, "cadence": [],
                "basis_type": "model", "model_versions_json": {},
                "assumptions": [], "citations": [], "explanation": "测试"
            },
            "tool_traces": [
                {"tool": "forward_pe_target", "status": "ok", "args": {"code": "600519"}, "summary": "target=1900"},
                {"tool": "atr_stop", "status": "ok", "args": {"code": "600519"}, "summary": "stop=1550"},
            ],
        }

    with patch("runner.agent_graph.ainvoke", side_effect=fake_graph_ainvoke), \
         patch("runner._stream_llm_text", new=_fake_stream_text):
        req = runner.AgentChatReq(
            thread_id=None,
            messages=[{"role": "user", "content": "分析茅台 给目标价"}],
            context_codes=["600519"],
            llm={"provider": "", "baseURL": "https://api.example.com",
                 "apiKey": "k", "model": "gpt-4o"},
            style="balanced",
        )
        events = []
        async for ev in runner.run_agent(req):
            events.append(ev)

    types = [e["type"] for e in events]
    assert "tool_trace" in types          # 新增验收
    assert "text_delta" in types
    assert "decision_artifact" in types
    assert "done" in types
    # tool_trace 应在 decision_artifact 之前
    assert types.index("tool_trace") < types.index("decision_artifact")
```

---

### R6：db.py 用 `_get_db_path()` 函数（替换 Task 9 Step 9.3 中 `_DB_PATH` 常量段）

**Why**：`_DB_PATH = os.environ.get("VR_AGENT_DB", _DEFAULT_DB)` 在模块加载时读 env。pytest 同 session 内多个测试用 `monkeypatch.setenv` + 延迟 import 只在**首次** import 时生效；后续测试模块已缓存，不会重新执行顶层代码，`_DB_PATH` 仍是首次值——测试间串扰。

**替换 Task 9 Step 9.3 中 db.py 顶部 `_DB_PATH` 段为**：

```python
import os
from typing import Any

import aiosqlite

# 默认 backend/.cache/stockclaw.db；env 可改 ~/.stockclaw/stockclaw.db
_HERE = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_DB = os.path.normpath(os.path.join(_HERE, "..", ".cache", "stockclaw.db"))

_conn: aiosqlite.Connection | None = None


def _get_db_path() -> str:
    """每次调用时读 env——避免模块级常量在 pytest 多测试间缓存（R6 修复）。"""
    return os.environ.get("VR_AGENT_DB", _DEFAULT_DB)
```

并把 `_connect()` 函数中的 `_DB_PATH` 引用改为 `_get_db_path()`：

```python
async def _connect() -> aiosqlite.Connection:
    """打开连接 + 设 PRAGMA。"""
    db_path = _get_db_path()
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    conn = await aiosqlite.connect(db_path)
    await conn.execute("PRAGMA journal_mode=WAL;")
    await conn.execute("PRAGMA busy_timeout=5000;")
    await conn.execute("PRAGMA foreign_keys=ON;")
    return conn
```

**单测 fixture patch**：在 `test_persistence.py` 的 `db` fixture 顶部加 `importlib.reload`：

```python
@pytest.fixture
async def db(tmp_path, monkeypatch):
    """每个测试一个临时 db 文件 + 强制 reload 模块（防 _DB_PATH 缓存）。"""
    import importlib
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("VR_AGENT_DB", str(db_path))
    from persistence import db as db_mod
    importlib.reload(db_mod)  # R6：强制重新读 env
    await db_mod.init_db()
    yield db_mod
    await db_mod.close_db()
    importlib.reload(db_mod)  # 还原，防影响其他测试
```

---

### R7：`loadLlmConfig` → `loadLlm` + null 检查（替换 Task 10 Step 10.4 AgentWorkspace + Task 11 Step 11.1 useAgentStream）

**Why**：实际 `frontend/src/lib/llm.ts` 导出的函数名是 `loadLlm`（不是 `loadLlmConfig`），返回 `LlmConfig | null`（用户未配置时返回 null）。原 plan 的 `loadLlmConfig()` 调用 TS 编译必挂；直接 `llm.provider.startsWith("cli-")` 在未配置时抛 `TypeError`。

#### Step 10.4 patch：AgentWorkspace.tsx

替换 Task 10 Step 10.4 中 `AgentWorkspace.tsx` 完整代码：

```tsx
// frontend/src/components/agent/AgentWorkspace.tsx
import { useState } from "react";
import { AgentSidebar } from "./AgentSidebar";
import { AgentMain } from "./AgentMain";
import { ContextDrawer } from "./ContextDrawer";
import { CliBlocker } from "./CliBlocker";
import { loadLlm } from "@/lib/llm";  // R7：函数名 loadLlm，不是 loadLlmConfig

export function AgentWorkspace() {
  const [drawerOpen, setDrawerOpen] = useState(true);
  const llm = loadLlm();

  // R7：未配置 LLM 或 CLI 模式都走拦截层
  if (!llm || llm.provider.startsWith("cli-")) {
    return <CliBlocker />;
  }

  return (
    <div className="flex h-[calc(100vh-3rem)] gap-2 p-2">
      <AgentSidebar />
      <AgentMain />
      {drawerOpen && <ContextDrawer onClose={() => setDrawerOpen(false)} />}
    </div>
  );
}
```

`CliBlocker.tsx` 文案微调，让它在「未配置」和「CLI 模式」两种情况下都合理：

```tsx
// frontend/src/components/agent/CliBlocker.tsx
import { Link } from "react-router-dom";
import { AlertCircle } from "lucide-react";
import { loadLlm } from "@/lib/llm";

export function CliBlocker() {
  const llm = loadLlm();
  const isCli = llm?.provider.startsWith("cli-");
  return (
    <div className="flex h-[calc(100vh-3rem)] items-center justify-center p-6">
      <div className="glass max-w-md rounded-2xl p-6 text-center">
        <AlertCircle className="mx-auto mb-3 h-10 w-10 text-amber-500" />
        <h2 className="text-lg font-bold">
          {isCli ? "需要 API 接入的模型" : "请先接入 AI"}
        </h2>
        <p className="mt-2 text-sm text-muted-foreground">
          {isCli
            ? "Agent 工作台需要 Function-Calling 与流式多轮 Agent 路由，订阅接入（CLI 模式）不支持。请前往「接入 AI」页配置 API Key 或更换为 API 接入模型。"
            : "Agent 工作台需要一个 API 接入的模型才能开始。请前往「接入 AI」页配置。"}
        </p>
        <Link
          to="/settings"
          className="mt-4 inline-block rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground"
        >
          前往「接入 AI」
        </Link>
      </div>
    </div>
  );
}
```

#### Step 11.1 patch：useAgentStream.ts

替换 Task 11 Step 11.1 中 `useAgentStream.ts` 内 `loadLlmConfig` 调用：

```typescript
// useAgentStream.ts 顶部 import 改为：
import { loadLlm } from "@/lib/llm";

// send() 内的 LLM 配置段改为：
    const llm = loadLlm();
    if (!llm) {
      opts.onError?.("尚未接入 AI，请先在「接入 AI」里配置");
      finishStreaming(tid, assistantMsgId);
      return;
    }
    const body: AgentChatReq = {
      thread_id: opts.threadId,
      messages: [{ role: "user", content: opts.content }],
      context_codes: opts.contextCodes,
      llm,  // loadLlm() 返回的 LlmConfig 已含 provider/baseURL/apiKey/model 四字段
      style: opts.style,
    };
```

---

### R8：`DataUnavailable` + `contract` 提取到 `quant/__init__.py`

**Why**：原 plan 在 `quant/valuation.py` 和 `quant/stops.py` 各自定义 `DataUnavailable` 与 `_contract`——两个独立类。`agents/tools.py::_invoke` 用 `except Exception` 兜底没问题，但任何未来代码尝试 `except quant.valuation.DataUnavailable` 不会捕获 `quant.stops.DataUnavailable`。统一到 `quant/__init__.py` 后续易维护。

#### Step 3.3 patch：替换 quant/__init__.py

替换 Task 3 Step 3.3 中 `backend/quant/__init__.py` 为：

```python
# backend/quant/__init__.py
"""定量工具层（纯 Python 函数，无 LLM）。

每个函数返回统一 contract：
{
    "tool": "atr_stop",
    "inputs": {...},
    "outputs": {...},
    "basis_type": "model" | "model_fallback" | "llm_reasoning" | "hybrid",
    "model_version": "atr_stop.v1",
    "model_assumptions": ["..."],
    "citations": [{"source": "astock.kline", "code": "...", "range": "..."}],
    "explanation": "..."
}

数据源单一：只调 astock.py / gstock.py；不直接打 HTTP。
"""


class DataUnavailable(Exception):
    """quant 工具因数据不足无法走完整公式。调用方应降级为 model_fallback 或 llm_reasoning。"""


def contract(tool: str, inputs: dict, outputs: dict, model_version: str,
             assumptions: list[str], citations: list[dict], explanation: str,
             basis_type: str = "model") -> dict:
    """统一 contract 拼装——子模块共享（R8：消除 valuation/stops 重复定义）。"""
    return {
        "tool": tool,
        "inputs": inputs,
        "outputs": outputs,
        "basis_type": basis_type,
        "model_version": model_version,
        "model_assumptions": assumptions,
        "citations": citations,
        "explanation": explanation,
    }
```

#### Step 3.3 patch：valuation.py 删除本地定义、改 import

把 `backend/quant/valuation.py` 顶部的本地 `class DataUnavailable` 和 `def _contract(...)` 删除，import 改为：

```python
# backend/quant/valuation.py 顶部
from __future__ import annotations

from typing import Any

import astock
from quant import DataUnavailable, contract as _contract  # R8：从 __init__ 导入
```

`forward_pe_target` / `pe_percentile_revert` / `pb_percentile_revert` 函数体内的 `_contract(...)` 调用不变（alias 已建好）。

#### Step 4.3 patch：stops.py 同上

把 `backend/quant/stops.py` 顶部的本地 `class DataUnavailable` 和 `def _contract(...)` 删除，import 改为：

```python
# backend/quant/stops.py 顶部
from __future__ import annotations

import json
import math
import os
from typing import Any

import astock
from quant import DataUnavailable, contract as _contract  # R8：从 __init__ 导入
```

#### tools.py 单测兼容（无需改动）

`agents/tools.py::_invoke` 用 `except Exception`，统一兜底——R8 不会破坏现有测试。`tests/test_quant_valuation.py` 和 `test_quant_stops.py` 中的 `from quant.valuation import DataUnavailable` / `from quant.stops import DataUnavailable` 仍能工作（子模块 `import from quant` 重导出后，子模块的命名空间内可见 `DataUnavailable`）。

**但更干净的写法**：测试也改成 `from quant import DataUnavailable`：

```python
# tests/test_quant_valuation.py 和 tests/test_quant_stops.py 顶部
from quant import DataUnavailable  # R8：统一从 __init__ 导入
```

---

### 回修后验收清单（在原 Self-Review §2 基础上追加）

- ✅ R1：`grep -E "^import requests|^from requests" backend/runner.py` 必须无命中（runner 全部走 httpx）
- ✅ R2：`backend/tests/test_runner.py::test_run_agent_emits_decision_artifact_for_decision_intent` 验证 `tool_trace` 事件被推送，且在 `decision_artifact` 之前
- ✅ R3：runner.py 中 `grep "\.format(context=" backend/runner.py` 必须无命中
- ✅ R4：`curl -X POST http://127.0.0.1:8900/api/agent/threads -d '{"title":"测试"}'` 返回 200 + `{"data":{"id":...}}`
- ✅ R5：跑一次 `/api/agent/chat` 后，`sqlite3 backend/.cache/stockclaw.db "SELECT COUNT(*) FROM conversations"` 至少 2（user + assistant）
- ✅ R6：跑 `pytest tests/test_persistence.py -v` 两次（同 session），第二次全部仍通过（_DB_PATH 不缓存）
- ✅ R7：`npm run build`（tsc 严格模式）build 成功；`loadLlm()` 返回 null 时显示 `<CliBlocker>`（未配置文案）
- ✅ R8：`grep -c "class DataUnavailable" backend/quant/` 仅 `__init__.py:1` 命中（valuation/stops 都改为 import）

### 回修 commit 建议

把回修打成两个 commit（不要混在原 task commit 里，便于 review 追溯）：

```bash
# Commit 1：后端回修（R1+R2+R3+R4+R5+R6+R8）
git add backend/runner.py backend/agents/nodes/decision.py backend/persistence/db.py \
        backend/quant/__init__.py backend/quant/valuation.py backend/quant/stops.py \
        backend/app.py backend/pyproject.toml backend/tests/
git commit -m "fix(agent): 对抗性评审回修（R1+R2+R3+R4+R5+R6+R8）

R1: runner.py 改 httpx.AsyncClient（原 requests 阻塞 event loop）
R2: decision_node 收集 tool_traces；runner 推 NDJSON tool_trace 事件
R3: SYSTEM_PROMPT_AGENT 用 .replace 替代 .format（防花括号冲突）
R4: app.py 注册 /api/agent/threads + /api/agent/decisions CRUD 路由
R5: runner.run_agent 写持久化（threads/conversations/decisions）
R6: db.py _DB_PATH → _get_db_path() 函数（防测试间缓存）
R8: DataUnavailable + contract 提取到 quant/__init__.py"

# Commit 2：前端回修（R7）
git add frontend/src/components/agent/AgentWorkspace.tsx \
        frontend/src/components/agent/CliBlocker.tsx \
        frontend/src/hooks/useAgentStream.ts
git commit -m "fix(frontend): loadLlmConfig → loadLlm + null 检查（R7）

- 函数名修正：实际 llm.ts 导出的是 loadLlm，不是 loadLlmConfig
- 未配置 LLM 时显示 CliBlocker（"请先接入 AI\" 文案）
- useAgentStream.ts 在 send 前 null-check，避免 TypeError"
```

---

## 最终执行顺序（含 Task 13 patch）

执行人按下面顺序操作（每个 Task 完成后 commit，最后再打 R 回修 commit）：

1. Task 1（Step 1.1-1.9）→ commit
2. Task 2（Step 2.1-2.5）→ commit
3. Task 3（Step 3.1-3.5，**用 R8 patch 后的 quant/__init__.py 与 valuation.py**）→ commit
4. Task 4（Step 4.1-4.5，**用 R8 patch 后的 stops.py**）→ commit
5. Task 5（Step 5.1-5.5）→ commit
6. Task 6（Step 6.1-6.5）→ commit
7. Task 7（Step 7.1-7.12，**用 R2 patch 后的 decision_node**）→ commit
8. Task 8（Step 8.1-8.9，**用 R1+R3+R5 patch 后的 runner.py**）→ commit
9. Task 9（Step 9.1-9.8，**用 R6 patch 后的 db.py + 新增 Step 9.4.5 的 R4 路由**）→ commit
10. Task 10（Step 10.1-10.10，**用 R7 patch 后的 AgentWorkspace.tsx**）→ commit
11. Task 11（Step 11.1-11.6，**用 R7 patch 后的 useAgentStream.ts**）→ commit
12. Task 12（Step 12.1-12.4）→ commit
13. **跑全部测试**：`pytest -m "not live" -v` + `npm run build`
14. **手测端到端**：spec §9 Phase 1 出口 5 项验收
15. **回修 commit**：按 Task 13 末尾建议的两个 commit message 提交（可分可合）

---

## 最终执行选择

**Plan + 回修完整，可启动实施。两种执行方案：**

**1. Subagent-Driven（推荐）** — 主 agent 每个 task 派 fresh subagent；subagent 拿到 plan 的对应 task 段 + Task 13 patch 后实施；2 阶段 review（subagent 自检 + 主 agent 验收）。

**2. Inline Execution** — 在当前 session 内 Task 1→12 顺序执行，每 task commit 后 checkpoint；Task 13 回修随对应 task 落地（不必单独再 commit）。

**Which approach?**

---

## Task 14：Round 2 对抗性评审回修（R9-R12，2026-07-09）

**评审文档**：`docs/superpowers/review/adversarial-review-2026-07-09.md`（Round 2）
**评审结论**：4 项关键架构 / 逻辑遗漏
**回修策略**：4 项全部采纳——逐项给出处置 + 替换代码。优先级与 Task 13 同级——执行人按「最终执行顺序」段把 R9-R12 patch 与对应 task 同步落地。

### Round 2 回修总览

| # | 评审漏洞 | 风险 | 处置 | 影响步骤 |
|---|---|---|---|---|
| R9 | runner.py 不加载历史消息 → 多轮对话失忆 | 严重 | ✅ 采纳：thread_id 非空时 `_convos.list_messages()` 拼到 messages 前 | Task 8 Step 8.4（叠加在 R5 patch 之上） |
| R10 | AgentSidebar 用 ephemeral ID，刷新丢历史 | 高 | ✅ 采纳：mount 时 `agentApi.listThreads()`；新建走 `agentApi.createThread()`；切换加载 `listMessages()` | Task 10 Step 10.4 + Step 10.5 |
| R11 | decision_node 串行调工具（注释误导） | 中（perf + 误导） | ✅ 采纳：恢复 `asyncio.gather`；Rate Limiter 的锁已保证 eastmoney 间隔；修注释 | Task 7 Step 7.8（叠加在 R2 patch 之上） |
| R12 | target_price / take_profit hardcode `* 1.15` / `* 1.20` 绕过 LLM 推理 | 中（违反 spec L3） | ✅ 采纳：tool 失败时字段 = None；basis_type field-level 升 `llm_reasoning`；卡 UI 显示「见分析」 | Task 7 Step 7.4 + 7.8 + Task 12 DecisionCard + Task 10 类型 |

**零拒绝。** 评审的 4 项发现均与 spec / 已有 plan 代码直接对应，无主观项。

---

### R9：runner.py 加载历史消息（叠加在 R5 patch 的 `run_agent` 之上）

**Why**：spec §7 续聊语义明确——"`thread_id` 非空时，后端从 `conversations` 表加载历史消息拼到 `messages` 前；前端发送时**只发本次新增的消息**"。R5 patch 让 runner 写持久化但**没读**——续聊时 LLM 完全失忆，把每次对话当全新会话处理。

**替换 Task 13 R5 patch 中 `run_agent` 函数的前半部分**（从「—— 1. 解析或新建 thread ——」到「—— 3. 跑 graph 拿 decision_card + tool_traces ——」之前的整段）：

```python
    # —— 1. 解析或新建 thread ——
    thread_id = req.thread_id
    is_continuation = thread_id is not None  # R9：续聊场景标志

    if not thread_id:
        # 用第一条 user 消息前 20 字作标题
        first_user = next((m.get("content", "") for m in req.messages
                           if m.get("role") == "user"), "新会话")
        title = (first_user[:20] + "…") if len(first_user) > 20 else first_user
        thread_id = await _threads.create_thread(title=title or "新会话", model=req.llm.model)

    # —— 2. R9：续聊时加载历史消息拼到 messages 前（spec §7 续聊语义） ——
    historical_messages: list[dict] = []
    if is_continuation:
        rows = await _convos.list_messages(thread_id)
        # 只取 user / assistant 的 content，过滤 tool / system 消息避免上下文污染
        historical_messages = [
            {"role": r["role"], "content": r["content"] or ""}
            for r in rows
            if r["role"] in ("user", "assistant") and r["content"]
        ]

    # —— 3. 持久化本次 user 消息（续聊只发新增——见 spec §7） ——
    for msg in req.messages:
        if msg.get("role") == "user":
            await _convos.append_message(thread_id, msg)

    # —— 4. 拼接完整 messages：历史 + 本次新增 ——
    all_messages = historical_messages + req.messages

    try:
        # —— 5. 跑 graph 拿 decision_card + tool_traces ——
        graph_state = {
            "messages": all_messages,  # R9：用拼接后的全量历史
            "context_codes": req.context_codes,
            "style": req.style,
            "thread_id": thread_id,
        }
        graph_result = await agent_graph.ainvoke(graph_state)
        decision_card = graph_result.get("decision_card")
        tool_traces = graph_result.get("tool_traces") or []
```

后续段落（推 tool_trace 事件 + 流式 LLM + 推 decision_artifact + 持久化 assistant 回复 + done 事件）**保持 R5 patch 不变**——但 `_stream_llm_text` 调用的 `enhanced_messages` 改为基于 `all_messages`：

```python
        # —— R9：流式 LLM 文本时也要带历史消息 ——
        if decision_card:
            summary = (
                f"基于工具结果：目标价 {decision_card.get('target_price')},"
                f"止损 {decision_card.get('stop_loss')},止盈 {decision_card.get('take_profit')},"
                f"依据 {decision_card.get('basis_type')}。"
            )
            enhanced_messages = all_messages + [{
                "role": "assistant", "content": f"[工具结果摘要] {summary}"
            }]
        else:
            enhanced_messages = all_messages
```

**单测 patch**：在 `backend/tests/test_runner.py` 新增续聊测试：

```python
@pytest.mark.asyncio
async def test_run_agent_continuation_loads_history(_stub_persistence, monkeypatch):
    """R9：thread_id 非空时，runner 从 conversations 加载历史拼到 messages 前。"""
    loaded_messages: list[dict] = []

    async def fake_graph_ainvoke(input_state):
        # 捕获传给 graph 的 messages——验证历史被拼上了
        loaded_messages.extend(input_state["messages"])
        return {"intent": "general", "decision_card": None, "tool_traces": []}

    # mock _convos.list_messages 返回 2 条历史
    async def fake_list_messages(tid):
        return [
            {"id": "h1", "thread_id": tid, "role": "user", "content": "历史问 1",
             "tool_calls_json": None, "tool_call_id": None, "artifacts_json": None, "created_at": 1},
            {"id": "h2", "thread_id": tid, "role": "assistant", "content": "历史答 1",
             "tool_calls_json": None, "tool_call_id": None, "artifacts_json": None, "created_at": 2},
        ]

    monkeypatch.setattr("runner._convos.list_messages", fake_list_messages)

    with patch("runner.agent_graph.ainvoke", side_effect=fake_graph_ainvoke), \
         patch("runner._stream_llm_text", new=_fake_stream_text):
        req = runner.AgentChatReq(
            thread_id="existing-tid",  # R9：续聊
            messages=[{"role": "user", "content": "本次新增问题"}],
            llm={"provider": "", "baseURL": "https://api.example.com",
                 "apiKey": "k", "model": "gpt-4o"},
        )
        async for _ in runner.run_agent(req):
            pass

    # graph 应该收到 3 条 messages：2 条历史 + 1 条新增
    assert len(loaded_messages) == 3
    assert loaded_messages[0]["content"] == "历史问 1"
    assert loaded_messages[1]["content"] == "历史答 1"
    assert loaded_messages[2]["content"] == "本次新增问题"
```

---

### R10：AgentSidebar 走 agentApi 持久化（替换 Task 10 Step 10.4 AgentWorkspace.tsx + Step 10.5 AgentSidebar.tsx）

**Why**：R4 patch 加了后端 CRUD 路由，但原 AgentSidebar 仍用 `local-${Date.now()}` ephemeral ID，且 mount 时不拉历史。结果：刷新页面 sidebar 空，新建的会话也只活在内存。R4 + R5 + R9 把后端链路打通了，前端必须接上才闭环。

#### Step 10.4 patch：AgentWorkspace.tsx mount 时拉历史

替换 Task 10 Step 10.4 AgentWorkspace.tsx 完整代码（含 R7 patch；拆内组件避免违反 Hooks 规则）：

```tsx
// frontend/src/components/agent/AgentWorkspace.tsx
import { useEffect, useState } from "react";
import { AgentSidebar } from "./AgentSidebar";
import { AgentMain } from "./AgentMain";
import { ContextDrawer } from "./ContextDrawer";
import { CliBlocker } from "./CliBlocker";
import { loadLlm } from "@/lib/llm";  // R7：函数名 loadLlm
import { agentApi } from "@/lib/api";
import { useAgentStore } from "@/lib/stores/agent";

export function AgentWorkspace() {
  // R7：未配置 LLM 或 CLI 模式都走拦截层（early return 必须在 Hooks 之前——拆内组件）
  const llm = loadLlm();
  if (!llm || llm.provider.startsWith("cli-")) {
    return <CliBlocker />;
  }
  return <AgentWorkspaceInner />;
}

function AgentWorkspaceInner() {
  const [drawerOpen, setDrawerOpen] = useState(true);
  const setThreads = useAgentStore((s) => s.setThreads);

  // R10：mount 时从后端拉历史会话列表（刷新后 sidebar 不空）
  useEffect(() => {
    let mounted = true;
    (async () => {
      try {
        const threads = await agentApi.listThreads();
        if (mounted) setThreads(threads);
      } catch (e) {
        // 后端 404 / 网络错时不阻塞 UI，用空列表（用户仍可新建本地会话）
        console.warn("加载会话列表失败:", e);
      }
    })();
    return () => { mounted = false; };
  }, [setThreads]);

  return (
    <div className="flex h-[calc(100vh-3rem)] gap-2 p-2">
      <AgentSidebar />
      <AgentMain />
      {drawerOpen && <ContextDrawer onClose={() => setDrawerOpen(false)} />}
    </div>
  );
}
```

#### Step 10.5 patch：AgentSidebar.tsx 走 agentApi

替换 Task 10 Step 10.5 AgentSidebar.tsx 完整代码：

```tsx
// frontend/src/components/agent/AgentSidebar.tsx
import { Plus, MessageSquare, Trash2 } from "lucide-react";
import { useAgentStore } from "@/lib/stores/agent";
import { agentApi } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { ChatMessage } from "@/lib/types/agent";

export function AgentSidebar() {
  const { threads, currentThreadId, setCurrentThread } = useAgentStore();

  // R10：新建会话走后端 agentApi.createThread 拿真实 ID
  const newThread = async () => {
    try {
      const t = await agentApi.createThread("新会话", "");
      useAgentStore.setState((s) => ({
        threads: [t, ...s.threads.filter((x) => x.id !== t.id)],
        currentThreadId: t.id,
        messagesByThread: { ...s.messagesByThread, [t.id]: [] },
      }));
    } catch (e) {
      // 兜底：用本地 ID（后端 down 时不阻塞用户）
      console.warn("创建会话失败，用本地 ID:", e);
      const tid = `local-${Date.now()}`;
      useAgentStore.setState((s) => ({
        threads: [
          { id: tid, title: "新会话", model: "", created_at: Date.now(), updated_at: Date.now() },
          ...s.threads,
        ],
        currentThreadId: tid,
        messagesByThread: { ...s.messagesByThread, [tid]: [] },
      }));
    }
  };

  // R10：切换会话时按需拉历史消息
  const selectThread = async (tid: string) => {
    setCurrentThread(tid);
    if (useAgentStore.getState().messagesByThread[tid]) return;  // 已缓存不重复拉
    try {
      const rows = await agentApi.listMessages(tid);
      const chatMsgs: ChatMessage[] = (rows as any[]).map((r) => ({
        id: r.id,
        role: r.role === "user" ? "user" : "assistant",
        content: r.content || "",
        toolTraces: [],  // Phase 2 补 tool_calls_json 反序列化
        // artifacts_json 也含决策卡——Phase 2 反序列化为 decisionCard
      }));
      useAgentStore.setState((s) => ({
        messagesByThread: { ...s.messagesByThread, [tid]: chatMsgs },
      }));
    } catch (e) {
      console.warn("加载会话消息失败:", e);
      useAgentStore.setState((s) => ({
        messagesByThread: { ...s.messagesByThread, [tid]: [] },
      }));
    }
  };

  // R10：删会话走后端
  const deleteThread = async (tid: string, e: React.MouseEvent) => {
    e.stopPropagation();
    try {
      await agentApi.deleteThread(tid);
    } catch (err) {
      console.warn("删除会话失败:", err);
    }
    useAgentStore.setState((s) => {
      const { [tid]: _removed, ...rest } = s.messagesByThread;
      return {
        threads: s.threads.filter((t) => t.id !== tid),
        messagesByThread: rest,
        currentThreadId: s.currentThreadId === tid ? null : s.currentThreadId,
      };
    });
  };

  return (
    <aside className="glass flex w-60 flex-col rounded-2xl">
      <button
        onClick={newThread}
        className="m-2 flex items-center justify-center gap-2 rounded-lg bg-primary/10 px-3 py-2 text-sm font-medium text-primary hover:bg-primary/20"
      >
        <Plus className="h-4 w-4" /> 新建会话
      </button>
      <div className="flex-1 overflow-auto p-2">
        {threads.length === 0 && (
          <p className="px-2 py-4 text-xs text-muted-foreground">暂无会话</p>
        )}
        {threads.map((t) => (
          <div
            key={t.id}
            onClick={() => selectThread(t.id)}
            className={cn(
              "group mb-1 flex w-full cursor-pointer items-center gap-2 rounded-lg px-2.5 py-2 text-left text-sm transition-colors",
              t.id === currentThreadId
                ? "bg-primary/15 font-medium text-primary"
                : "text-muted-foreground hover:bg-muted/50 hover:text-foreground",
            )}
          >
            <MessageSquare className="h-3.5 w-3.5 shrink-0" />
            <span className="flex-1 truncate">{t.title}</span>
            <button
              onClick={(e) => deleteThread(t.id, e)}
              className="opacity-0 transition-opacity group-hover:opacity-100"
              title="删除"
            >
              <Trash2 className="h-3 w-3 text-muted-foreground hover:text-red-500" />
            </button>
          </div>
        ))}
      </div>
    </aside>
  );
}
```

#### Step 10.3 patch：agentApi 类型修正

Task 10 Step 10.3 中 `agentApi.listMessages` 返回类型是 `unknown[]`——R10 patch 需明确类型。把 Task 10 Step 10.3 的 `agentApi` 改为：

```typescript
import type { AgentChatReq, AgentThread, DecisionCardData, PersistedMessage } from "@/lib/types/agent";

export const agentApi = {
  listThreads: () => get<AgentThread[]>("/agent/threads"),
  createThread: (title: string, model: string) =>
    request<AgentThread>("/agent/threads", "POST", { title, model }),
  renameThread: (tid: string, title: string) =>
    request<{ ok: boolean }>(`/agent/threads/${tid}`, "PATCH", { title }),
  deleteThread: (tid: string) =>
    request<{ ok: boolean }>(`/agent/threads/${tid}`, "DELETE"),
  listMessages: (tid: string) => get<PersistedMessage[]>(`/agent/threads/${tid}/messages`),
  saveDecision: (card: DecisionCardData) =>
    request<{ id: string }>("/agent/decisions", "POST", card),
  listDecisions: (code?: string) =>
    get<unknown[]>(`/agent/decisions${code ? `?code=${code}` : ""}`),
};
```

`PersistedMessage` 类型加到 `frontend/src/lib/types/agent.ts`：

```typescript
// 持久化在 SQLite 的消息（persistence/conversations.py::list_messages 返回格式）
export interface PersistedMessage {
  id: string;
  thread_id: string;
  role: string;
  content: string | null;
  tool_calls_json: unknown | null;
  tool_call_id: string | null;
  artifacts_json: unknown | null;
  created_at: number;
}
```

---

### R11：decision_node 恢复 `asyncio.gather` 并发（叠加在 R2 patch 之上）

**Why**：Rate Limiter（Task 2）已用 `asyncio.Lock` + cool-down 1.0s 保证 eastmoney 调用间隔——这是 spec §6 约束 4 的设计意图。在 application 层再串行是 redundant + 误导（原注释"Rate Limiter cool-down 1.0s 强制串行；并发反而触发东财 403"错误）。`asyncio.gather` 让非 eastmoney 工具（如 `pyramid_buy` / `batch_build` / `dca_plan` 纯策略函数）并行执行，省 cool-down 时间。

**替换 Task 13 R2 patch 中 `decision_node` 主体**——把串行 `await _invoke(...)` 改为 `asyncio.gather`，注释改为正确描述，并融合 R12 patch（target_price / take_profit 可空）：

```python
# backend/agents/nodes/decision.py 顶部 import 追加
import asyncio


async def decision_node(state: AgentState) -> dict:
    """LangGraph 节点：调 quant 工具集 + 合并决策卡。

    R11：用 asyncio.gather 并发调工具——Rate Limiter 的锁（Task 2）已保证
    eastmoney 调用间隔 ≥ 1.0s，application 层不需要再串行。
    非 eastmoney 工具（纯策略函数）会自然并行，不浪费 cool-down 时间。

    R12：target_price / take_profit 不再 hardcode——工具失败时字段 = None，
    整卡 basis_type 通过归并规则升 llm_reasoning（spec §6 L3）。
    """
    context_codes = state.get("context_codes") or []
    msgs = state.get("messages") or []
    code = context_codes[0] if context_codes else _extract_code_from_messages(msgs)
    if not code:
        return {"decision_card": None, "tool_traces": []}

    traces: list[dict] = []

    # R11：第一批 3 个独立工具并发——traces 列表的 append 由 GIL 保证原子；
    # 顺序非确定但语义无影响（tool_traces 顺序仅信息展示）
    target_r, stop_r, entry_r = await asyncio.gather(
        _invoke(forward_pe_target, traces, code=code, target_pe=20.0, eps_year="27e"),
        _invoke(atr_stop, traces, code=code, period=14, multiplier=2.0),
        _invoke(pe_percentile_revert, traces, code=code, revert_to=0.50),
    )

    # 取当前价 + 名称
    current_price = (target_r or {}).get("outputs", {}).get("current_price") or \
                    (stop_r or {}).get("outputs", {}).get("current_price") or 0.0
    name = _lookup_name(code)

    # R12：target_price / take_profit 不再 hardcode
    target_price = (target_r or {}).get("outputs", {}).get("target_price")  # 可能为 None
    stop_loss = (stop_r or {}).get("outputs", {}).get("stop_price")
    # entry_low / entry_high 是入场区间宽度（相对当前价的偏移），不算决策数字
    entry_low = current_price * 0.98
    entry_high = current_price * 1.02
    take_profit = None  # R12：Phase 1 无独立 take_profit 工具，留空交 LLM 推理

    # stop_loss 也彻底失败时（连当前价都拿不到）→ 整体拒答（spec §6 L4）
    if stop_loss is None or current_price == 0:
        return {"decision_card": None, "tool_traces": traces}

    # 第二批：position / cadence 依赖 stop_loss / current_price，单独 gather
    pos_r, cad_r = await asyncio.gather(
        _invoke(risk_based_position, traces, entry_price=current_price, stop_price=stop_loss),
        _invoke(batch_build, traces, total_budget=100000.0, batches=3,
                schedule="weekly", start_price=current_price),
    )
    cadence = (cad_r or {}).get("outputs", {}).get("plan") or []

    card = build_decision_card(
        code=code, name=name, current_price=current_price,
        target_price=target_price,            # R12: 可能 None
        entry_low=entry_low, entry_high=entry_high,
        stop_loss=stop_loss, take_profit=take_profit,  # R12: 可能 None
        cadence=cadence,
        tool_results={
            "target": target_r or {}, "entry": entry_r or {},
            "stop": stop_r or {}, "position": pos_r or {},
            "take_profit": target_r or {},
        },
        explanation=_build_explanation(target_r, stop_r, pos_r, stop_loss, target_price),
    )
    return {"decision_card": card, "tool_traces": traces}


def _build_explanation(target_r, stop_r, pos_r, stop_loss, target_price):
    """R12：解释里诚实陈述 target_price 是否可用。"""
    parts = []
    if target_price is not None:
        parts.append(f"前向 PE 目标价 {target_price:.2f}")
    else:
        parts.append("前向 PE 工具不可用（数据不足），目标价留空，由 LLM 在分析中推导")
    parts.append(f"ATR 止损 {stop_loss:.2f}")
    if (pos_r or {}).get("outputs", {}).get("shares"):
        parts.append(f"按风险反推 {pos_r['outputs']['shares']:.0f} 股")
    parts.append("分批 3 期建仓")
    return "基于 " + " + ".join(parts)
```

**Race condition 说明**：`traces.append(...)` 在多个并发 `_invoke` 中调用。Python `list.append` 是原子操作（GIL 保护），但顺序非确定。tool_traces 顺序仅用于前端展示，无语义影响——可接受。如果前端需要严格按调用顺序展示，可在 trace 里加 `seq` 字段（这里不做）。

**单测 patch**：`test_run_agent_emits_decision_artifact_for_decision_intent` 已经验证 `tool_trace` 事件被推送——R11 改并发后该测试仍通过（gather 完成后 traces 内容相同，顺序可能不同但 `assert "tool_trace" in types` 不依赖顺序）。

---

### R12：移除 `* 1.15` / `* 1.20` 硬编码（叠加在 R2 patch 之上 + Task 7 Step 7.4 + Task 12 DecisionCard）

**Why**：原 plan 在 `decision_node` 里 hardcode `target_price = current_price * 1.15`、`take_profit = current_price * 1.20`——任意 +15% / +20% 是凭空捏造的数字。spec §6 约束 1 L3 明确：当 model 和 model_fallback 都无效时，**只有 target_price 允许 LLM 推理**，且必须列依据。`* 1.15` 直接绕过了这条降级链——前端看到 basis_type 含 model 字段，但实际 target_price 是 +15% 捏造，违反 spec §6 约束 1。

R12 的代码改动已在 R11 patch 内融合（target_price / take_profit 改 None）。下面补充 `build_decision_card` 与 DecisionCard UI 的兼容改动。

#### Step 7.4 patch：build_decision_card 允许 Optional 字段

替换 Task 7 Step 7.4 中 `build_decision_card` 函数签名 + `model_versions_json` 构造：

```python
def build_decision_card(
    code: str,
    name: str,
    current_price: float,
    target_price: float | None,           # R12: 可空（工具失败 / 数据不足）
    entry_low: float,
    entry_high: float,
    stop_loss: float,                     # 硬约束：必填，否则整体拒答
    take_profit: float | None,            # R12: 可空
    cadence: list[dict],
    tool_results: dict[str, dict],
    explanation: str,
) -> dict[str, Any]:
    """组装决策卡。

    R12：target_price / take_profit 允许 None——当 quant 工具不可用时，
    字段留空交给 LLM 在 prose 中推导（spec §6 L3 llm_reasoning）。
    stop_loss / entry_low / entry_high 必填（Python model_fallback 兜底）。
    """
    target_tool = tool_results.get("target", {})
    entry_tool = tool_results.get("entry", target_tool)
    stop_tool = tool_results.get("stop", {})
    take_profit_tool = tool_results.get("take_profit", target_tool)
    position_tool = tool_results.get("position", {})

    # R12：字段级 model_versions——target_price / take_profit 为空时标 llm_reasoning
    def _field_label(tool_result: dict, value) -> str:
        if value is None:
            return "llm_reasoning(data_unavailable)"
        return _version_label(tool_result) if tool_result else "unknown"

    model_versions_json = {
        "target_price": _field_label(target_tool, target_price),
        "entry_low": _version_label(entry_tool) if entry_tool else "unknown",
        "entry_high": _version_label(entry_tool) if entry_tool else "unknown",
        "stop_loss": _version_label(stop_tool) if stop_tool else "unknown",
        "take_profit": _field_label(take_profit_tool, take_profit),
    }
    if position_tool:
        model_versions_json["cadence[0].pct"] = _version_label(position_tool)

    # 收集 basis_type——R12：含 None 字段时该字段 basis_type 升 llm_reasoning
    field_basis: list[str] = []
    for r in tool_results.values():
        if r.get("basis_type"):
            field_basis.append(r["basis_type"])
    # R12：target_price / take_profit 为空 → 隐含 llm_reasoning 字段
    if target_price is None or take_profit is None:
        field_basis.append("llm_reasoning")
    merged_basis = merge_basis_type(field_basis)

    citations = []
    for r in tool_results.values():
        for c in (r.get("citations") or []):
            if c not in citations:
                citations.append(c)

    assumptions = []
    for r in tool_results.values():
        for a in (r.get("model_assumptions") or []):
            if a not in assumptions:
                assumptions.append(a)

    return {
        "code": code,
        "name": name,
        "current_price": current_price,
        "target_price": target_price,        # 可能 None
        "entry_low": entry_low,
        "entry_high": entry_high,
        "stop_loss": stop_loss,
        "take_profit": take_profit,          # 可能 None
        "cadence": cadence,
        "basis_type": merged_basis,
        "model_versions_json": model_versions_json,
        "assumptions": assumptions,
        "citations": citations,
        "explanation": explanation,
    }
```

#### Step 12.1 patch：DecisionCard.tsx 兼容 None 字段

替换 Task 12 Step 12.1 中 `DecisionCard.tsx` 顶部 `changePct` 计算 + 价格显示段：

```tsx
export function DecisionCard({ card }: { card: DecisionCardData }) {
  const [showBasis, setShowBasis] = useState(false);
  const [saved, setSaved] = useState(false);
  const saveDecision = useAgentStore((s) => s.saveDecision);

  const basis = BASIS_COLORS[card.basis_type];

  // R12：target_price 可能为 null——changePct 计算需 guard
  const changePct = card.target_price != null
    ? ((card.target_price - card.current_price) / card.current_price) * 100
    : null;

  const handleSave = () => { saveDecision(card); setSaved(true); };

  const handleCopy = () => {
    const lines = [
      `${card.name}（${card.code}） 决策卡`,
      card.target_price != null
        ? `目标价 ¥${card.target_price.toFixed(2)}（${changePct! >= 0 ? "+" : ""}${changePct!.toFixed(1)}%）`
        : `目标价 暂无（数据不足，见分析）`,
      `入场区 ¥${card.entry_low.toFixed(2)} – ¥${card.entry_high.toFixed(2)}`,
      `止损 ¥${card.stop_loss.toFixed(2)}`,
      card.take_profit != null
        ? `止盈 ¥${card.take_profit.toFixed(2)}`
        : `止盈 暂无`,
      "",
      "仓位节奏：",
      ...card.cadence.map((c) =>
        `  第${c.batch}批 ${Math.round(c.pct * 100)}% ${c.trigger} ¥${(c.price || c.ref_price || 0).toFixed(2)}`,
      ),
      "",
      `依据：${basis.label} - ${BASIS_DESC[card.basis_type]}`,
      card.explanation,
    ];
    navigator.clipboard.writeText(lines.join("\n"));
  };

  return (
    <div className="mt-2 rounded-xl border border-primary/30 bg-primary/5 p-4">
      <div className="flex items-start justify-between">
        <div>
          <h3 className="text-base font-bold">决策卡 · {card.code} {card.name}</h3>
          <p className="text-xs text-muted-foreground mt-0.5">
            目标价{" "}
            {card.target_price != null ? (
              <>
                <span className="font-mono text-primary">¥{card.target_price.toFixed(2)}</span>
                <span className={cn("ml-1", changePct! >= 0 ? "text-red-500" : "text-emerald-500")}>
                  ({changePct! >= 0 ? "+" : ""}{changePct!.toFixed(1)}%)
                </span>
              </>
            ) : (
              <span className="font-mono text-amber-500">数据不足 · 见分析</span>
            )}
          </p>
        </div>
        {/* 收藏/复制/复盘按钮区保持 Task 12 原实现不变 */}
        {/* ... */}
      </div>

      <div className="mt-3 grid grid-cols-2 gap-3 text-sm">
        <div>
          <div className="text-xs text-muted-foreground">入场区</div>
          <div className="font-mono">¥{card.entry_low.toFixed(2)} – ¥{card.entry_high.toFixed(2)}</div>
        </div>
        <div>
          <div className="text-xs text-muted-foreground">止损</div>
          <div className="font-mono text-red-500/80">¥{card.stop_loss.toFixed(2)}</div>
        </div>
        <div>
          <div className="text-xs text-muted-foreground">止盈</div>
          {card.take_profit != null ? (
            <div className="font-mono text-emerald-500/80">¥{card.take_profit.toFixed(2)}</div>
          ) : (
            <div className="font-mono text-amber-500">—</div>
          )}
        </div>
        <div>
          <div className="text-xs text-muted-foreground">仓位节奏</div>
          <div className="font-mono">{card.cadence.length} 批</div>
        </div>
      </div>

      {/* 仓位节奏列表 + 依据展开（4 档色标 + 字段级 model_versions）保持 Task 12 原实现 */}
      {/* ... */}
    </div>
  );
}
```

注：上面用 `{/* ... */}` 标注的省略段（仓位节奏列表、依据展开、收藏/复制按钮）保持 Task 12 原实现不变——只需保证 `card.target_price` / `card.take_profit` 在用前判断 `!= null`。

#### TS 类型 patch：DecisionCardData 改 Optional

替换 Task 10 Step 10.1 中 `DecisionCardData` 类型定义的相关字段：

```typescript
export interface DecisionCardData {
  code: string;
  name: string;
  current_price: number;
  target_price: number | null;       // R12：可空
  entry_low: number;
  entry_high: number;
  stop_loss: number;
  take_profit: number | null;        // R12：可空
  cadence: CadenceBatch[];
  basis_type: BasisType;
  model_versions_json: Record<string, string>;
  assumptions: string[];
  citations: { source: string; code?: string; range?: string; note?: string }[];
  explanation: string;
}
```

#### 单测 patch：test_agents_decision 加 None 字段用例

在 `backend/tests/test_agents_decision.py` 末尾追加：

```python
def test_build_decision_card_target_none_marks_llm_reasoning():
    """R12：target_price 为 None 时，整卡 basis_type 升 llm_reasoning。"""
    tool_results = {
        "target": {  # 工具失败 fallback contract（无 target_price）
            "tool": "forward_pe_target", "basis_type": "model_fallback",
            "model_version": "forward_pe_target.v1",
            "outputs": {},  # 空 outputs
        },
        "stop": {
            "tool": "atr_stop", "basis_type": "model",
            "model_version": "atr_stop.v1",
            "outputs": {"stop_price": 1550.0, "current_price": 1685.0},
        },
        "entry": {"tool": "pe_percentile_revert", "basis_type": "model",
                  "model_version": "pe_percentile_revert.v1", "outputs": {}},
        "position": {"tool": "risk_based_position", "basis_type": "model",
                     "model_version": "risk_based_position.v1", "outputs": {}},
    }
    card = build_decision_card(
        code="600519", name="茅台", current_price=1685.0,
        target_price=None,  # R12：target 工具失败
        entry_low=1685.0, entry_high=1720.0,
        stop_loss=1550.0, take_profit=None,  # R12：take_profit 也空
        cadence=[], tool_results=tool_results, explanation="测试",
    )
    # 整卡 basis_type 应升 llm_reasoning（target_price / take_profit 任一 None）
    assert card["basis_type"] == "llm_reasoning"
    # 字段级 model_versions：target_price 标 llm_reasoning
    assert "llm_reasoning" in card["model_versions_json"]["target_price"]
    assert "llm_reasoning" in card["model_versions_json"]["take_profit"]
```

---

### Round 2 回修后追加验收清单

- ✅ R9：续聊测试——首次对话后用返回的 `thread_id` 发续聊请求，验证 graph 收到的 messages 包含首次的 user + assistant 历史消息
- ✅ R10：刷新 `/agent` 页面，sidebar 仍能看到上次创建的会话；新建会话后 DevTools Network 看到 `POST /api/agent/threads` 调用
- ✅ R11：`grep "强制串行\|并发反而触发" backend/agents/nodes/decision.py` 必须无命中（注释已改）；`asyncio.gather` 调工具——非 eastmoney 工具应能并行
- ✅ R12：`grep -E "current_price \* (1\.15|1\.20|0\.92)" backend/agents/nodes/decision.py` 必须无命中（hardcode 已移除）；mock forward_pe_target 失败 → `decision_card.target_price is None` 且 `basis_type == "llm_reasoning"`

---

### Round 2 commit 建议

```bash
# 后端 R9 + R11 + R12
git add backend/runner.py backend/agents/nodes/decision.py backend/tests/
git commit -m "fix(agent): Round 2 评审回修（R9+R11+R12）

R9: runner.run_agent 续聊时加载 conversations 历史消息拼到 messages 前（spec §7）
R11: decision_node 用 asyncio.gather 并发调工具；Rate Limiter 锁已保证 eastmoney 间隔
R12: 移除 target_price * 1.15 / take_profit * 1.20 硬编码——工具失败时字段=None，
     basis_type field-level 升 llm_reasoning；build_decision_card 允许 Optional 字段"

# 前端 R10 + R12 类型
git add frontend/src/components/agent/AgentWorkspace.tsx \
        frontend/src/components/agent/AgentSidebar.tsx \
        frontend/src/components/agent/DecisionCard.tsx \
        frontend/src/lib/api.ts frontend/src/lib/types/agent.ts
git commit -m "fix(frontend): Round 2 评审回修（R10+R12 UI）

R10: AgentWorkspace mount 时 agentApi.listThreads() 拉历史；
     AgentSidebar 新建/切换/删除全走 agentApi（持久化闭环）
R12: DecisionCard 兼容 target_price / take_profit 为 null——
     显示「数据不足 · 见分析」琥珀色提示；TS 类型改 Optional"
```

---

## 最终执行顺序（含 Task 13 + Task 14 patch）

执行人按下面顺序操作（每个 Task 完成后 commit，最后再打 R 回修 commit）：

1. Task 1（Step 1.1-1.9）→ commit
2. Task 2（Step 2.1-2.5）→ commit
3. Task 3（Step 3.1-3.5，**用 R8 patch 后的 quant/__init__.py 与 valuation.py**）→ commit
4. Task 4（Step 4.1-4.5，**用 R8 patch 后的 stops.py**）→ commit
5. Task 5（Step 5.1-5.5）→ commit
6. Task 6（Step 6.1-6.5）→ commit
7. Task 7（Step 7.1-7.12，**用 R2 + R11 + R12 patch 后的 decision.py + build_decision_card**）→ commit
8. Task 8（Step 8.1-8.9，**用 R1+R3+R5+R9 patch 后的 runner.py**）→ commit
9. Task 9（Step 9.1-9.8，**用 R6 patch 后的 db.py + 新增 Step 9.4.5 的 R4 路由**）→ commit
10. Task 10（Step 10.1-10.10，**用 R7 + R10 patch 后的 AgentWorkspace.tsx + AgentSidebar.tsx + types + api**）→ commit
11. Task 11（Step 11.1-11.6，**用 R7 patch 后的 useAgentStream.ts**）→ commit
12. Task 12（Step 12.1-12.4，**用 R12 patch 后的 DecisionCard.tsx（兼容 Optional）**）→ commit
13. **跑全部测试**：`pytest -m "not live" -v` + `npm run build`
14. **手测端到端**：
    - spec §9 Phase 1 出口 5 项验收
    - **R9 续聊**：首次对话 → 拿 thread_id → 不刷新页面继续问"刚才那只股票的止损是多少" → Agent 应能引用上下文
    - **R10 持久化**：刷新页面 → sidebar 仍有上次会话 → 点开 → 历史消息回来
    - **R11 perf**：DevTools Network 看 `/api/agent/chat` 流式响应总耗时，4 个工具 trace 时间戳间隔 ≈ 1s（Rate Limiter），但非 eastmoney 工具应见缝插针并行
    - **R12 美港股**：mock `forward_pe_target` 失败 → 决策卡 target_price 显示「数据不足 · 见分析」琥珀色，basis_type = llm_reasoning
15. **回修 commit**：按 Task 13 + Task 14 末尾建议的 commit message 提交

---

## 最终执行选择（重申）

**Plan + Round 1 (R1-R8) + Round 2 (R9-R12) 完整，可启动实施。两种执行方案：**

**1. Subagent-Driven（推荐）** — 主 agent 每个 task 派 fresh subagent；subagent 拿 plan 的对应 task 段 + Task 13/14 patch 后实施；2 阶段 review。

**2. Inline Execution** — 当前 session 内 Task 1→12 顺序执行；R1-R12 patch 随对应 task 落地，最后整体跑测试 + commit。

**Which approach?**

---

## Task 15：Round 3 对抗性评审回修（R13-R15，2026-07-09）

**评审文档**：`docs/superpowers/review/adversarial-review-2026-07-09-round3.md`
**评审结论**：No（需再做修复）——R12 patch 把 `take_profit` 也留空交 LLM 推理，违反 spec §6 L3 硬约束
**回修策略**：3 项全部采纳——R13 是 R12 的过度修正回归，R14/R15 是代码卫生项

### Round 3 回修总览

| # | 评审漏洞 | 风险 | 处置 | 影响步骤 |
|---|---|---|---|---|
| R13 | R12 把 `take_profit` 也留 None 交 LLM 推理——违反 spec §6 L3 | 严重（违反 spec 硬约束） | ✅ 采纳：take_profit 走 Python fallback（2:1 R/R 基于 stop_loss 距离），明确标 `model_fallback` | Task 7 Step 7.8（叠加在 R11+R12 patch 之上） |
| R14 | entry_low/high 是 fixed spread（±2%）却继承 `target_tool` 的 `basis_type: model`——误导 | 重要 | ✅ 采纳：tool_results 加显式 `entry` 条目，标 `model_fallback(fixed_spread_2pct.v1)` | Task 7 Step 7.8 |
| R15 | `_invoke` 静默吞所有异常——bug 难调试 | 次要 | ✅ 采纳：`except` 块加 `logging.exception()` 打真实堆栈 | Task 7 Step 7.8（_invoke helper） |

**零拒绝。** 3 项发现均与 spec / 已有 plan 代码直接对应。**R13 是关键修复**——R12 patch 在移除 `* 1.20` hardcode 时矫枉过正，把本应 Python fallback 的 take_profit 也归入 LLM 推理路径。

---

### R13：take_profit 走 Python fallback（修复 R12 的过度修正）

**Why**：spec §6 约束 1 L3 明确——"LLM 仅可调整 target_price 且必须列出依据的数据点。**stop_loss / entry_* / take_profit 等硬性价位字段一律由 L2 fallback 值兜底，LLM 不得生成**"。R12 patch 把 `take_profit` 也设为 `None` + `_field_label` 标 `llm_reasoning(data_unavailable)`——直接违反此硬约束。target_price 是唯一允许 LLM 推理的字段；take_profit 必须有 Python 兜底值。

**修复策略**：take_profit 用 **2:1 风险回报比**（基于已算出的 stop_loss 距离）——这是有依据的交易规则，不是凭空数字：
- 单股风险 = `current_price - stop_loss`
- `take_profit = current_price + 单股风险 × 2`（2:1 R/R）
- 标 `basis_type: model_fallback` + `model_version: risk_reward_2to1.v1`

#### Step 7.8 patch（叠加在 R11+R12 patch 之上）：decision_node + _invoke + build_decision_card

替换 Task 14 R11 patch 中 `decision_node` 主体（融合 R13 take_profit fallback + R14 entry 显式 fallback contract + R15 logging）：

```python
# backend/agents/nodes/decision.py 顶部 import 追加
import asyncio
import logging

logger = logging.getLogger(__name__)


async def _invoke(tool, traces_out: list, **kwargs) -> dict:
    """安全调用 tool，失败返回 fallback contract。同时把 trace 写入 traces_out。

    R15：except 块加 logging.exception() 打真实堆栈——DataUnavailable 走降级是
    正常路径，但 TypeError / KeyError 等代码 bug 必须能在日志里看到。
    """
    trace_entry = {"tool": tool.name, "status": "running", "args": kwargs, "summary": None}
    traces_out.append(trace_entry)
    try:
        result = await tool.ainvoke(kwargs)
        trace_entry["status"] = "ok"
        if isinstance(result, dict):
            basis = result.get("basis_type", "")
            outs = result.get("outputs") or {}
            if "stop_price" in outs:
                trace_entry["summary"] = f"{basis} stop={outs['stop_price']}"
            elif "target_price" in outs:
                trace_entry["summary"] = f"{basis} target={outs['target_price']}"
            elif "shares" in outs:
                trace_entry["summary"] = f"shares={outs.get('shares')}"
            elif "plan" in outs:
                trace_entry["summary"] = f"{len(outs['plan'])} batches"
        return result
    except Exception as e:
        # R15：真实堆栈进日志——DataUnavailable 是预期降级，但其他异常可能是 bug
        logger.exception("Tool %s failed", tool.name)
        trace_entry["status"] = "error"
        trace_entry["summary"] = str(e)[:120]
        return {
            "error": f"{tool.name} failed: {e}",
            "basis_type": "model_fallback",
            "model_version": f"{tool.name}.v1",
            "outputs": {}, "citations": [],
            "model_assumptions": [f"工具失败：{e}"],
        }


async def decision_node(state: AgentState) -> dict:
    """LangGraph 节点：调 quant 工具集 + 合并决策卡。

    R11：asyncio.gather 并发调工具——Rate Limiter 锁已保证 eastmoney 间隔。
    R12：target_price 工具失败时为 None（spec L3 允许 LLM 推理 target_price）。
    R13：take_profit 走 Python fallback（2:1 R/R）——spec L3 禁止 LLM 生成 take_profit。
    R14：entry_low/high 显式标 model_fallback——fixed spread 不是 model 输出。
    """
    context_codes = state.get("context_codes") or []
    msgs = state.get("messages") or []
    code = context_codes[0] if context_codes else _extract_code_from_messages(msgs)
    if not code:
        return {"decision_card": None, "tool_traces": []}

    traces: list[dict] = []

    # 第一批 3 个独立工具并发
    target_r, stop_r, entry_r = await asyncio.gather(
        _invoke(forward_pe_target, traces, code=code, target_pe=20.0, eps_year="27e"),
        _invoke(atr_stop, traces, code=code, period=14, multiplier=2.0),
        _invoke(pe_percentile_revert, traces, code=code, revert_to=0.50),
    )

    current_price = (target_r or {}).get("outputs", {}).get("current_price") or \
                    (stop_r or {}).get("outputs", {}).get("current_price") or 0.0
    name = _lookup_name(code)

    # R12：target_price 工具失败时为 None（spec L3 允许 LLM 推理此字段）
    target_price = (target_r or {}).get("outputs", {}).get("target_price")

    stop_loss = (stop_r or {}).get("outputs", {}).get("stop_price")
    if stop_loss is None or current_price == 0:
        return {"decision_card": None, "tool_traces": traces}

    # R14：entry_low/high 是 fixed spread（±2%），不是 model 输出——显式标 model_fallback
    entry_low = current_price * 0.98
    entry_high = current_price * 1.02
    entry_tool_explicit = {
        "tool": "fixed_spread",
        "basis_type": "model_fallback",
        "model_version": "fixed_spread_2pct.v1",
        "outputs": {"entry_low": entry_low, "entry_high": entry_high, "current_price": current_price},
        "model_assumptions": ["入场区 = 当前价 ± 2%（无独立 entry 工具的简化策略）"],
        "citations": [],
        "explanation": f"入场区 [{entry_low:.2f}, {entry_high:.2f}] = 当前价 {current_price:.2f} ± 2%",
    }

    # R13：take_profit 走 Python fallback（2:1 R/R）——spec §6 L3 禁止 LLM 生成此字段
    risk_per_share = current_price - stop_loss
    take_profit_value = current_price + risk_per_share * 2  # 2:1 风险回报比
    take_profit_tool_explicit = {
        "tool": "risk_reward_2to1",
        "basis_type": "model_fallback",
        "model_version": "risk_reward_2to1.v1",
        "outputs": {"take_profit": take_profit_value, "current_price": current_price,
                    "stop_loss": stop_loss, "risk_per_share": risk_per_share},
        "model_assumptions": ["2:1 风险回报比", "风险 = 当前价 - 止损价"],
        "citations": [{"source": "internal.strategy", "note": "2:1 R/R 基于 atr_stop 结果"}],
        "explanation": f"止盈 {take_profit_value:.2f} = 当前价 {current_price:.2f} + 单股风险 {risk_per_share:.2f} × 2",
    }

    # 第二批：position / cadence 依赖 stop_loss / current_price
    pos_r, cad_r = await asyncio.gather(
        _invoke(risk_based_position, traces, entry_price=current_price, stop_price=stop_loss),
        _invoke(batch_build, traces, total_budget=100000.0, batches=3,
                schedule="weekly", start_price=current_price),
    )
    cadence = (cad_r or {}).get("outputs", {}).get("plan") or []

    card = build_decision_card(
        code=code, name=name, current_price=current_price,
        target_price=target_price,            # R12: 可能 None（LLM 推理）
        entry_low=entry_low, entry_high=entry_high,
        stop_loss=stop_loss,
        take_profit=take_profit_value,        # R13: 始终有 Python fallback 值
        cadence=cadence,
        tool_results={
            "target": target_r or {},
            "entry": entry_tool_explicit,             # R14: 显式 fallback contract
            "stop": stop_r or {},
            "position": pos_r or {},
            "take_profit": take_profit_tool_explicit, # R13: 显式 fallback contract
        },
        explanation=_build_explanation(target_r, stop_r, pos_r, current_price,
                                       stop_loss, target_price, take_profit_value),
    )
    return {"decision_card": card, "tool_traces": traces}


def _build_explanation(target_r, stop_r, pos_r, current_price, stop_loss,
                       target_price, take_profit):
    """R12+R13：解释里诚实陈述各字段来源。"""
    parts = []
    if target_price is not None:
        parts.append(f"前向 PE 目标价 {target_price:.2f}（model 或 model_fallback）")
    else:
        parts.append("前向 PE 工具不可用，目标价留空交 LLM 在分析中推导（spec L3 llm_reasoning）")
    parts.append(f"ATR 止损 {stop_loss:.2f}")
    parts.append(f"止盈 {take_profit:.2f}（2:1 R/R Python fallback）")
    if (pos_r or {}).get("outputs", {}).get("shares"):
        parts.append(f"按风险反推 {pos_r['outputs']['shares']:.0f} 股")
    parts.append("入场区 ±2% / 分批 3 期建仓")
    return "基于 " + " + ".join(parts)
```

#### Step 7.4 patch（叠加在 R12 patch 之上）：build_decision_card 收紧 None 升级规则

替换 Task 14 R12 patch 中 `build_decision_card` 的 `field_basis` 收集段——**只有 target_price 为 None 时才升 llm_reasoning**（take_profit 现在永远有 Python fallback 值）：

```python
    # 收集 basis_type——R12+R13：只有 target_price 为 None 时升 llm_reasoning
    # （take_profit / entry_* / stop_loss 都走 Python fallback，不会是 None）
    field_basis: list[str] = []
    for r in tool_results.values():
        if r.get("basis_type"):
            field_basis.append(r["basis_type"])
    # R13 收紧：仅 target_price 为 None 触发 llm_reasoning 升级
    if target_price is None:
        field_basis.append("llm_reasoning")
    merged_basis = merge_basis_type(field_basis)
```

注：build_decision_card 其他段（`_field_label`、`model_versions_json` 构造）保持 R12 patch 不变——`_field_label` 现在只在 `target_price is None` 时返回 `"llm_reasoning(data_unavailable)"`；take_profit 因永远有值，会走正常的 `_version_label(take_profit_tool)` → `"model_fallback(risk_reward_2to1.v1)"`。

#### 单测 patch：test_agents_decision 加 R13 用例

在 `backend/tests/test_agents_decision.py` 末尾追加：

```python
def test_build_decision_card_take_profit_never_llm_reasoning():
    """R13：take_profit 永远不为 None——spec §6 L3 禁止 LLM 生成此字段。"""
    tool_results = {
        "target": {"tool": "forward_pe_target", "basis_type": "model",
                   "model_version": "forward_pe_target.v1",
                   "outputs": {"target_price": 1900.0, "current_price": 1685.0}},
        "stop": {"tool": "atr_stop", "basis_type": "model",
                 "model_version": "atr_stop.v1",
                 "outputs": {"stop_price": 1550.0, "current_price": 1685.0}},
        "entry": {"tool": "fixed_spread", "basis_type": "model_fallback",
                  "model_version": "fixed_spread_2pct.v1",
                  "outputs": {"entry_low": 1651.3, "entry_high": 1718.7}},
        "position": {"tool": "risk_based_position", "basis_type": "model",
                     "model_version": "risk_based_position.v1", "outputs": {}},
        "take_profit": {"tool": "risk_reward_2to1", "basis_type": "model_fallback",
                        "model_version": "risk_reward_2to1.v1",
                        "outputs": {"take_profit": 1820.0}},
    }
    card = build_decision_card(
        code="600519", name="茅台", current_price=1685.0,
        target_price=1900.0, entry_low=1651.3, entry_high=1718.7,
        stop_loss=1550.0, take_profit=1820.0,
        cadence=[], tool_results=tool_results, explanation="测试",
    )
    # take_profit 字段标 model_fallback，不是 llm_reasoning
    assert "model_fallback" in card["model_versions_json"]["take_profit"]
    assert "llm_reasoning" not in card["model_versions_json"]["take_profit"]
    # entry_low/high 同样标 model_fallback（R14）
    assert "model_fallback" in card["model_versions_json"]["entry_low"]
    # 整卡 basis_type = model_fallback（含 entry + take_profit 两档 fallback）
    assert card["basis_type"] == "model_fallback"


def test_build_decision_card_only_target_none_triggers_llm_reasoning():
    """R13 收紧：target_price=None 触发 llm_reasoning；take_profit 仍走 Python fallback。"""
    tool_results = {
        "target": {"tool": "forward_pe_target", "basis_type": "model_fallback",
                   "model_version": "forward_pe_target.v1", "outputs": {}},  # 工具失败
        "stop": {"tool": "atr_stop", "basis_type": "model",
                 "model_version": "atr_stop.v1",
                 "outputs": {"stop_price": 1550.0, "current_price": 1685.0}},
        "entry": {"tool": "fixed_spread", "basis_type": "model_fallback",
                  "model_version": "fixed_spread_2pct.v1", "outputs": {}},
        "position": {"tool": "risk_based_position", "basis_type": "model",
                     "model_version": "risk_based_position.v1", "outputs": {}},
        "take_profit": {"tool": "risk_reward_2to1", "basis_type": "model_fallback",
                        "model_version": "risk_reward_2to1.v1",
                        "outputs": {"take_profit": 1820.0}},  # R13: 永远有 Python 值
    }
    card = build_decision_card(
        code="600519", name="茅台", current_price=1685.0,
        target_price=None,  # R12: 唯一允许 None 的字段
        entry_low=1651.3, entry_high=1718.7,
        stop_loss=1550.0, take_profit=1820.0,  # R13: 不允许 None
        cadence=[], tool_results=tool_results, explanation="测试",
    )
    # 整卡 basis_type = llm_reasoning（target_price 触发）
    assert card["basis_type"] == "llm_reasoning"
    # 但 take_profit 字段级仍是 model_fallback，不是 llm_reasoning
    assert "model_fallback" in card["model_versions_json"]["take_profit"]
    assert "llm_reasoning" not in card["model_versions_json"]["take_profit"]
    # target_price 字段级是 llm_reasoning
    assert "llm_reasoning" in card["model_versions_json"]["target_price"]
```

注：原 R12 patch 加的 `test_build_decision_card_target_none_marks_llm_reasoning` 测试需要更新——把 `take_profit=None` 改为 `take_profit=1820.0`（R13 不允许 None）。具体改动：

```python
# 把原 R12 测试中的：
#     stop_loss=1550.0, take_profit=None,  # R12：take_profit 也空
# 改为：
#     stop_loss=1550.0, take_profit=1820.0,  # R13：take_profit 不允许 None
```

且把 `tool_results` 里加显式 `take_profit` 条目：

```python
# 在 tool_results 字典中追加：
        "take_profit": {"tool": "risk_reward_2to1", "basis_type": "model_fallback",
                        "model_version": "risk_reward_2to1.v1",
                        "outputs": {"take_profit": 1820.0}},
```

并更新原断言：

```python
# 原：assert "llm_reasoning" in card["model_versions_json"]["take_profit"]
# 改为：assert "model_fallback" in card["model_versions_json"]["take_profit"]
```

---

### R14：entry_low/high 显式标 model_fallback（已在 R13 patch 内融合）

**Why**：原 plan 在 `build_decision_card` 里 `entry_tool = tool_results.get("entry", target_tool)`——若 tool_results 没 "entry" 键，回退到 target_tool（forward_pe_target），其 `basis_type: model`。结果 `entry_low = current_price * 0.98` 这种简单 ±2% spread 被错误标为 `model(forward_pe_target.v1)`——前端色标显示蓝色（最可信），掩盖了它其实是降级值。

**修复策略**：在 `decision_node` 里把 entry 显式构造为 `fixed_spread_2pct.v1` 的 fallback contract，放入 `tool_results["entry"]`。这样 `build_decision_card` 拿到的 entry_tool 是显式 fallback contract，`_version_label` 会正确返回 `"model_fallback(fixed_spread_2pct.v1)"`。

**代码已融合在 R13 patch 内**——见上面 `entry_tool_explicit` 字典 + `tool_results["entry"] = entry_tool_explicit`。

---

### R15：_invoke 加 logging.exception（已在 R13 patch 内融合）

**Why**：原 `_invoke` 的 `except Exception` 静默吞所有异常，直接返回 fallback contract。DataUnavailable 是预期降级路径（正常），但 TypeError / KeyError / AttributeError 等代码 bug 也会被吞——开发者只看到决策卡显示 `model_fallback`，不知道是工具真不可用还是代码挂了。

**修复策略**：`except` 块先 `logger.exception()` 打真实堆栈到日志，再返回 fallback contract。trace UI 仍显示简短错误信息（业务兜底不变），但 server 日志有完整 traceback 用于调试。

**代码已融合在 R13 patch 内**——见上面 `_invoke` 的 `except Exception as e:` 块：

```python
    except Exception as e:
        # R15：真实堆栈进日志——DataUnavailable 是预期降级，但其他异常可能是 bug
        logger.exception("Tool %s failed", tool.name)
        trace_entry["status"] = "error"
        ...
```

---

### Round 3 回修后追加验收清单

- ✅ R13：`grep "take_profit = None" backend/agents/nodes/decision.py` 必须无命中；`test_build_decision_card_take_profit_never_llm_reasoning` 通过——验证 take_profit 字段永远不为 None 且 model_versions 标 model_fallback
- ✅ R14：`grep "tool_results.get..entry..target_tool" backend/agents/nodes/decision.py` 必须无命中（不再用 target_tool 兜底）；mock 决策生成后 `card.model_versions_json.entry_low` 应含 `"model_fallback"` 与 `"fixed_spread_2pct.v1"`
- ✅ R15：故意制造一个 `_invoke` 内的 TypeError（如 mock tool 抛 `TypeError("test")`）→ server 日志能看到完整 traceback；决策流仍正常降级返回 fallback contract（不挂流）

---

### Round 3 commit 建议

```bash
# 后端 R13 + R14 + R15（融合在一个 commit，因为 patch 互相耦合）
git add backend/agents/nodes/decision.py backend/tests/test_agents_decision.py
git commit -m "fix(agent): Round 3 评审回修（R13+R14+R15）

R13: take_profit 走 Python fallback（2:1 R/R based on stop_loss），
     不再 None——spec §6 L3 禁止 LLM 生成 take_profit 等硬性价位字段。
     build_decision_card 收紧 None 升级规则：仅 target_price 为 None 触发 llm_reasoning
R14: entry_low/high 显式标 model_fallback(fixed_spread_2pct.v1)——
     不再错误继承 target_tool 的 basis_type=model
R15: _invoke except 块加 logging.exception()——DataUnavailable 走降级正常，
     但 TypeError/KeyError 等代码 bug 必须能在日志看到完整堆栈"
```

---

## 最终执行顺序（含 Task 13 + 14 + 15 patch）

执行人按下面顺序操作（每个 Task 完成后 commit，最后再打 R 回修 commit）：

1. Task 1（Step 1.1-1.9）→ commit
2. Task 2（Step 2.1-2.5）→ commit
3. Task 3（Step 3.1-3.5，**用 R8 patch 后的 quant/__init__.py 与 valuation.py**）→ commit
4. Task 4（Step 4.1-4.5，**用 R8 patch 后的 stops.py**）→ commit
5. Task 5（Step 5.1-5.5）→ commit
6. Task 6（Step 6.1-6.5）→ commit
7. Task 7（Step 7.1-7.12，**用 R2 + R11 + R12 + R13 + R14 + R15 patch 后的 decision.py + build_decision_card**）→ commit
8. Task 8（Step 8.1-8.9，**用 R1+R3+R5+R9 patch 后的 runner.py**）→ commit
9. Task 9（Step 9.1-9.8，**用 R6 patch 后的 db.py + 新增 Step 9.4.5 的 R4 路由**）→ commit
10. Task 10（Step 10.1-10.10，**用 R7 + R10 patch 后的 AgentWorkspace.tsx + AgentSidebar.tsx + types + api**）→ commit
11. Task 11（Step 11.1-11.6，**用 R7 patch 后的 useAgentStream.ts**）→ commit
12. Task 12（Step 12.1-12.4，**用 R12 patch 后的 DecisionCard.tsx（兼容 Optional target_price；take_profit 仍可能为 null 但实际不会发生）**）→ commit
13. **跑全部测试**：`pytest -m "not live" -v` + `npm run build`
14. **手测端到端**：
    - spec §9 Phase 1 出口 5 项验收
    - **R9 续聊** / **R10 持久化** / **R11 perf**（见 Task 14 手测段）
    - **R12+R13 美港股**：mock `forward_pe_target` 失败 → 决策卡 `target_price = "数据不足 · 见分析"`（琥珀色，basis_type = llm_reasoning），但 `take_profit` 仍正常显示数值（model_fallback，琥珀色标）——验证 take_profit 走 Python 2:1 R/R fallback，不被 LLM 接管
    - **R14 入场区色标**：决策卡「依据」展开后，`entry_low / entry_high` 字段级 model_versions 显示 `model_fallback(fixed_spread_2pct.v1)`，色标为黄（model_fallback）而非蓝（model）
    - **R15 日志**：故意触发工具异常 → `tail -f backend/.cache/server.log`（或 stderr）能看到完整 Python traceback
15. **回修 commit**：按 Task 13 + 14 + 15 末尾建议的 commit message 提交

---

## 最终执行选择（再次重申）

**Plan + Round 1 (R1-R8) + Round 2 (R9-R12) + Round 3 (R13-R15) 完整，可启动实施。**

累计 15 项评审回修全部采纳，零拒绝。Plan 总长 ~6900 行。两种执行方案：

**1. Subagent-Driven（推荐）** — 主 agent 每个 task 派 fresh subagent；subagent 拿 plan 的对应 task 段 + Task 13/14/15 patch 后实施；2 阶段 review。

**2. Inline Execution** — 当前 session 内 Task 1→12 顺序执行；R1-R15 patch 随对应 task 落地，最后整体跑测试 + commit。

**Which approach?**
