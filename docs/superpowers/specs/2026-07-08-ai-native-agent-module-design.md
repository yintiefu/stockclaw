# AI Native 投资分析平台改造设计

**日期**：2026-07-08
**作者**：用户与 Claude 共同 brainstorming
**状态**：Approved（等待用户对 spec 文档本身的复核）
**适用仓库**：当前 stockclaw 仓库（已是 fork 后的工作副本，main 公开仓库不在本次改造范围）

---

## 1. 背景与定位

当前项目（Vibe-Research / stockclaw）是一个开源 A 股 + 美港韩股研究看板，核心合规红线是"不荐股、不预测、不给买卖时机"。本设计把当前 fork 改造为**个人本地部署的 AI 原生投资分析平台**，所有核心功能围绕 AI agent 展开，并解除上述红线。

**定位声明**：
- 个人本地使用，不分发、不开源此 fork
- 非投资建议风格——可给出目标价/止损止盈/仓位节奏等具体决策建议
- 客观数据层保持客观，决策建议只在 agent 层产生

**为什么这么做**：用户需要的是真实可用的个人决策辅助，不只是更深入的研究工具；同时公开 main 分支保持原红线，互不污染。

---

## 2. 设计原则

1. **数据层不污染**：`astock.py / gstock.py / market.py` 等客观数据接口保持"只返回客观数据"。决策建议只在 agent 层产生。这条边界焊死，便于未来再分叉客观版本。
2. **数字优先模型，例外 LLM**：默认 quant 工具算数字；工具不适用时（美港股历史数据不足、事件驱动股、重组股、缺乏一致 EPS）LLM 推理，必须显式标注依据类型。
3. **轻量与高兼容度**：前端 chat 不使用臃肿且对 React 19 兼容性极差的 CopilotKit，改用自定义的轻量级 React Chat 组件加原生的 NDJSON SSE 流式解析；后端 runtime 可使用 LangGraph 或简化版 Agent 状态循环。
4. **结构化 NDJSON 事件流优先于 Markdown 拦截**：决策卡、图表、表格是结构化 artifact，**作为独立的 NDJSON SSE 事件**（`type: decision_artifact` / `chart_artifact` / `table_artifact`）发送到前端。**禁止在 LLM 输出的 Markdown 正文里夹带 JSON 块**——流式输出时未闭合 JSON 会让前端解析器反复崩溃。普通文本走 `type: text_delta`，结构化产物走对应 artifact 事件。
5. **频控与并发分离**：东财 ~1s 速率限制必须用 **Rate Limiter（带 cool-down 的节流器）** 解决，**不是** `Semaphore`。`Semaphore` 只控并发度，5 个 panel 串行下仍能在 200ms 内打完 5 个请求触发 403。Phase 1 即建立 cool-down 1.0s 的全局 Rate Limiter。
6. **美港股降级走 Python fallback，不走 LLM 编数**：当 quant 工具因数据不足（美港韩股无历史 K 线、无一致 EPS）抛 `DataUnavailable` 时，**先尝试 Python 层 fallback 公式**（如简单比例止损 `-8%`、近期高低点），标记 `basis_type: model_fallback`；fallback 仍不可行才转 `llm_reasoning`，且 LLM **只允许调整 target_price**（必须给推导），止损价等硬性指标一律用 fallback 值。
7. **隐私本地化**：所有决策、会话、信号日志存本地 SQLite，不入 git，不上传。
8. **现有页面零破坏**：原 10 个数据页保持可用，新功能以新模块形式接入。

---

## 3. 合规变更

| 项 | 现状 | 改造后 |
|---|---|---|
| `chat.py` SYSTEM_PROMPT | 禁止推荐/预测/买卖 | 删除禁止条款；改为"私人投资分析师"角色，可给目标价/入场区/止损止盈/仓位节奏；数字优先工具，工具不适用时可推理，必须标注依据类型 |
| `ANALYSIS_FRAMEWORK` 五维分析 | 末尾"不给买卖结论" | 保留五维结构作为分析底盘；追加第六维「决策建议」：目标价/入场区/止损/止盈/仓位节奏/依据类型 |
| 工具描述 | 客观、不带建议 | 加入 quant 工具描述，允许产出建议性输出 |
| `AGENTS.md` 红线段 | 合规红线（不荐股等） | 重写为"个人本地部署"定位；红线改为安全红线（密钥本地、不入 git、不含真实券商 API），不再是合规红线 |
| 前端 disclaimer | 无 | 不加 disclaimer。顶栏加一行小字"个人本地部署 · 非投资建议风格"作标识，纯提示用途 |
| `_validate()` 路径校验、CORS、`VR_API_KEY` | 保留 | 保留（安全层不动，针对 A 股代码依然作 6 位数字校验，全局代码走对应接口） |
| 数据接口（`astock.py` / `gstock.py`） | 客观 | 不变 |

**客观数据层不污染** 是边界红线：决策能力建立在可信数据之上；如果让数据层带建议，未来再分叉客观版本就回不去了。

---

## 4. 架构总览

```
┌─ 用户 ────────────────────────────────────────────────────┐
│   /agent  工作台（自定义 React Chat + 决策卡组件拦截渲染）  │
└───────────────────────────────────────────────────────────┘
                            ↓ 自定义 SSE (Server-Sent Events)
┌─ FastAPI /api/agent/chat ─────────────────────────────────┐
│   runner.py → Agent state pipeline / LangGraph            │
│                                                            │
│   Orchestrator (supervisor)                                │
│       ├─ Decision node    ─ 调 quant/{valuation,stops,     │
│       │                     cadence}                       │
│       ├─ Panel nodes × 5  ─ 估值/资金/财报/行业/事件        │
│       ├─ Planner node     ─ Plan-and-Execute               │
│       └─ Proactive node   ─ 后台 scheduler 驱动（Phase 3） │
│                                                            │
│   Tools layer: agents/tools.py                             │
│       └─ 调 astock / gstock / market / newsradar /         │
│          portfolio / quant/* (含 cool-down 1.0s Rate       │
│          Limiter + 30min 强缓存，防东财 403)               │
└───────────────────────────────────────────────────────────┘
                            ↓
┌─ 客观数据层（不动）──────────────────────────────────────┐
│   astock.py  gstock.py  market.py  newsradar.py            │
│   portfolio.py  myreports.py                               │
└───────────────────────────────────────────────────────────┘
                            ↓
┌─ quant 层（纯 Python 函数，无 LLM）───────────────────────┐
│   valuation  stops  cadence  backtest  signals  factors    │
└───────────────────────────────────────────────────────────┘
                            ↓
┌─ 持久化（本地 SQLite）────────────────────────────────────┐
│   threads  conversations  decisions  signals_log  artifacts│
└───────────────────────────────────────────────────────────┘
```

**轻量化运行机制**：放弃臃肿的 CopilotKit 依赖，前端向后端自定义的 `/api/agent/chat` 接口发送请求，通过 SSE 流式获取 NDJSON 响应。后端利用 LangGraph 或简单的 Python State 循环进行工具路由和状态合并。**所有打向东财/同花顺等带速率限制的数据源调用必经全局 Rate Limiter（cool-down 1.0s）**，配合 30 分钟 TTL 强缓存，单纯 `asyncio.Semaphore` 只控并发度不控频率、不足以防 403。

---

## 5. 后端目录结构

```
backend/
├── app.py                     # FastAPI 路由，新增 /api/agent/chat 等挂载点
├── astock.py / gstock.py / market.py / newsradar.py / portfolio.py / myreports.py
│                              # 数据层 —— 不动
├── chat.py                    # 保留为兼容入口（Phase 2 弃用）
│
├── quant/                     # 定量工具层（纯 Python 函数，无 LLM）
│   ├── __init__.py
│   ├── valuation.py           # forward_pe_target / pe_percentile_revert / pb_percentile_revert (注意：美港股只计算基础PE/PB，无历史分位时需优雅抛错)
│   ├── stops.py               # atr_stop / structure_stop / risk_based_position (注意：美港股无历史 K 线算 ATR 时优雅抛错)
│   ├── cadence.py             # pyramid_buy / batch_build / dca_plan
│   ├── backtest.py            # backtest_strategy / signal_backtest（Phase 2）
│   ├── signals.py             # return_diff / percentile_breakthrough / fund_flow_anomaly
│   └── factors.py             # relative_strength / beta / industry_alpha（Phase 4）
│
├── agents/                    # Agent 核心逻辑层
│   ├── __init__.py
│   ├── state.py               # AgentState: messages + intent + ctx + artifacts
│   ├── graph.py               # 主图/管道构建：supervisor + 工具节点 + sub-agent 节点
│   ├── nodes/
│   │   ├── orchestrator.py    # supervisor 节点：分类意图并路由
│   │   ├── decision.py        # 决策节点
│   │   ├── panel_valuation.py # 五维节点（Phase 2）
│   │   ├── panel_funds.py
│   │   ├── panel_earnings.py
│   │   ├── panel_industry.py
│   │   ├── panel_events.py
│   │   ├── planner.py         # plan-execute planner/replanner（Phase 2）
│   │   └── proactive.py       # 主动扫描节点（Phase 3）
│   ├── tools.py               # @tool 包装（接 astock/gstock/quant，含并发锁）
│   └── prompts.py             # 各节点 system prompt（解禁后）
│
├── persistence/               # 本地持久化
│   ├── __init__.py
│   ├── db.py                  # SQLite 连接 + migration
│   ├── conversations.py       # 历史对话/上下文持久化
│   ├── decisions.py           # 决策卡归档 + 收益追踪
│   └── signals_log.py         # 主动 agent 信号日志（Phase 3）
│
├── runner.py                  # 运行入口（FastAPI 调它）
└── mcp_server.py              # MCP server —— 暴露 agents.graph
```

---

## 6. quant 工具箱契约

### 统一输出契约

```python
{
    "tool": "atr_stop",
    "inputs": {"code": "600519", "period": 14, "multiplier": 2.0},
    "outputs": {"stop_price": 1580.0, "current_price": 1685.0, "distance_pct": -6.2},
    "basis_type": "model",  # model | model_fallback | llm_reasoning | hybrid
    "model_version": "atr_stop.v1",
    "model_assumptions": ["14-day ATR", "2.0x multiplier (conservative)"],
    "citations": [{"source": "astock.kline", "code": "600519", "range": "..."}],
    "explanation": "基于 14 日 ATR=52.5，乘以 2.0 倍数，止损价 1685 - 105 = 1580"
}
```

注：单工具返回的 `model_version` 是字符串；Decision Node 合并成决策卡时改为**字段级字典**写入 `model_versions_json`（见约束 3 归并规则）。

### 工具清单（按 Phase 划分）

| 模块 | Phase 1 必做 | Phase 2+ |
|---|---|---|
| `valuation.py` | `forward_pe_target`（前向 PE × 一致 EPS）<br>`pe_percentile_revert`（PE 历史分位回复）<br>`pb_percentile_revert` | `simple_dcf`、`peg_justified`、`ddm` |
| `stops.py` | `atr_stop`（ATR×倍数）<br>`structure_stop`（近期低点）<br>`risk_based_position`（按止损距离反推仓位） | `volatility_stop`、`chandelier_exit` |
| `cadence.py` | `pyramid_buy`（金字塔加仓）<br>`batch_build`（分批建仓）<br>`dca_plan`（定投） | `grid_plan`、`martingale`（谨慎） |
| `backtest.py` | — | `backtest_strategy`（一次性/定投/分批/网格）<br>`signal_backtest`（信号胜率按年/分位拆）<br>`walk_forward`、`monte_carlo` |
| `signals.py` | — | `return_diff`、`percentile_breakthrough`、`fund_flow_anomaly`、`ma_cross`、`divergence` |
| `factors.py` | — | `relative_strength`、`beta`、`industry_alpha` |

### 关键约束

1. **数据源单一与美港股优雅降级（四级降级链）**：所有 quant 工具只调 `astock.py` / `gstock.py`，不直接打 HTTP。降级链如下：
   - **L1 `model`**：主路径，A 股数据齐走完整公式（如 ATR×倍数）。
   - **L2 `model_fallback`**：A 股 K 线缺失 / 美港韩股历史 K 线不可用时，Python 层降级为简化公式（如 `current_price × 0.92` 固定 8% 止损，或近 60 日最低价）。**仍由 Python 算，不让 LLM 编**。返回 `basis_type: model_fallback` 并附 `fallback_reason`。
   - **L3 `llm_reasoning`**：L2 也无意义（如事件驱动股、重组股、缺乏任何基本面数据）时，LLM 仅可调整 `target_price` 且必须列出依据的数据点。**`stop_loss` / `entry_*` / `take_profit` 等硬性价位字段一律由 L2 fallback 值兜底，LLM 不得生成**。
   - **L4 直接拒答**：连当前价都拿不到时，决策卡整体返回 error，不输出半成品。
2. **不依赖 LLM**：每个 quant 函数纯输入输出，无 LLM 介入，可单独单测、缓存。
3. **`basis_type` 取值与多工具归并规则**：
   - 四档：`model` / `model_fallback` / `llm_reasoning` / `hybrid`（hybrid = model + LLM 显式微调，必须列调整项）
   - **单工具直接用工具返回的 basis_type**
   - **多工具合并为决策卡时按"最大不确定性优先"归并**（Decision Node 负责）：
     1. 任意字段为 `llm_reasoning` → 整卡 `llm_reasoning`
     2. 否则，任意字段为 `hybrid` → 整卡 `hybrid`
     3. 否则，任意字段为 `model_fallback` → 整卡 `model_fallback`
     4. 全部字段为 `model` → 整卡 `model`
   - **`model_versions_json` 改为字段级字典**，记录每个决策字段来自哪个工具版本：

     ```json
     {
       "target_price":   "model(pe_percentile_revert.v1)",
       "entry_low":      "model(pe_percentile_revert.v1)",
       "entry_high":     "model(pe_percentile_revert.v1)",
       "stop_loss":      "model_fallback(atr_stop.v1, reason='no_kline_for_us_stock')",
       "take_profit":    "hybrid(pe_percentile.v1 + llm_adjust=-30 for quality)",
       "cadence[0].pct": "model(risk_based_position.v1)"
     }
     ```

     前端 DecisionCard「依据」展开后可按字段显示来源，比扁平数组 `["atr_stop.v1"]` 信息量大得多。
4. **频控 = Rate Limiter（带 cool-down）+ 强缓存，不是 Semaphore**：
   - 东财数据源（`push2.eastmoney.com` / `push2his.eastmoney.com` 等）有 ~1s 速率限制
   - `asyncio.Semaphore(1)` 只控并发度，5 个 panel 串行下仍能在 200ms 内打完 5 个请求触发 403
   - **正确做法**：`agents/tools.py` 维护全局 `EastmoneyRateLimiter`，单实例 `acquire()` 后强制 `await asyncio.sleep(1.0)` cool-down，下次 `acquire()` 才放行
   - **强缓存**：行情 30min、研报 30min、股东户数/大宗 30min（低频静态数据强制走缓存，agent 同一会话内重复请求只读缓存）
   - 多进程/多 worker 下 Rate Limiter 失效（内存不共享）—— 本地默认单 worker，文档里写明此限制
5. **缓存**：quant 结果按 `(tool, inputs, data_snapshot_hash)` 缓存 5 分钟。astock 数据 TTL 到期 → quant 缓存链式失效。
6. **回测例外**：回测慢（30s+），单独走 `runner.py` 异步任务，前端订阅流式输出。

### LangGraph/Pipeline 工具暴露

```python
# agents/tools.py
import asyncio
from langchain_core.tools import tool
from agents.rate_limiter import eastmoney_limiter  # 全局 cool-down Rate Limiter

@tool
async def atr_stop(code: str, period: int = 14, multiplier: float = 2.0) -> dict:
    """计算 A 股 ATR 止损价（美港股自动降级为 model_fallback）。
    period=ATR 周期，multiplier=倍数（保守 2.0、激进 1.5）。
    返回 stop_price / current_price / distance_pct / basis_type / fallback_reason。"""
    async with eastmoney_limiter:
        # 关键：astock.kline() 走 mootdx（同步阻塞 TCP）；astock.tencent_quote()
        # 走 urllib.request.urlopen（同步）；astock.em_get() 走 requests（同步）。
        # quant.* 全部基于这些同步 API。直接 await sync 函数会 TypeError，
        # 直接调用会冻结 event loop（Rate Limiter 的 sleep 无法调度）。
        # 必须用 asyncio.to_thread 把同步调用卸载到默认线程池。
        return await asyncio.to_thread(quant.stops.atr_stop, code, period, multiplier)
```

> **硬约束（焊死）**：所有调 `astock.*` / `gstock.*` / `market.*` / `newsradar.*` 的 `@tool` 函数，必须用 `await asyncio.to_thread(fn, ...)` 包装。Phase 1 评审前用 `grep -E "await (astock|gstock|market|newsradable)"` 应**无命中**（命中即说明直接调用了同步函数）。Code review 必查这条。

工具 docstring 是给 LLM 看的，必须明确"什么场景用、参数含义、返回什么"、**市场适用性**与**降级行为**。

```python
# agents/rate_limiter.py（Phase 1 必须实现）
class EastmoneyRateLimiter:
    """cool-down 节流器：__aenter__ 拿锁 + sleep，__aexit__ 释放。
    锁的持有必须横跨调用者整个业务逻辑——锁释放点必须在 __aexit__，
    不能用 async with self._lock（那会在 __aenter__ return 时立即释放，
    导致业务请求期间锁已不在，限流彻底失效）。"""
    def __init__(self, cool_down: float = 1.0):
        self._lock = asyncio.Lock()
        self._cool_down = cool_down
        self._last_release = 0.0

    async def __aenter__(self):
        # 1. acquire，不进 with 块——锁释放延后到 __aexit__
        await self._lock.acquire()
        # 2. cool-down 等待
        now = asyncio.get_event_loop().time()
        wait = max(0, self._last_release + self._cool_down - now)
        if wait:
            await asyncio.sleep(wait)
        return self

    async def __aexit__(self, *exc):
        try:
            # 3. 业务结束才更新释放时间
            self._last_release = asyncio.get_event_loop().time()
        finally:
            # 4. 确保锁一定被释放，让下一个排队请求进入
            self._lock.release()

eastmoney_limiter = EastmoneyRateLimiter(cool_down=1.0)
```

---

## 7. 前端 `/agent` 模块

### 依赖新增

```
recharts
(不添加任何 CopilotKit 依赖，彻底消除 React 19 构建兼容性风险)
```

### 路由与模型限制

```
/agent       → AgentWorkspace（Phase 1 主战场）
/today       → TodayBriefing（Phase 3）
现有 /watchlist /portfolio /sectors ... → 全保留，零改动
```

**模型选择限制**：由于本地 CLI 接入模式不支持 Function-Calling 和流式多轮 Agent 路由，前端需要在进入 `/agent` 页面时做模型配置校验。如检测到配置为 `cli-` 订阅模式，展示友好覆盖层：“Agent 工作台需要 API 接入的模型工具链，请前往「接入 AI」页配置 API Key 或更换模型。”

### 组件树

```
<AgentWorkspace>
  ├─ <AgentSidebar>                  // 自定义左侧历史会话栏
  │   ├─ [+ 新建会话]
  │   └─ 会话列表（从 SQLite 同步，支持删除和重命名）
  │
  ├─ <AgentMain>
  │   ├─ <AgentTopBar>               // 模型▾  风格▾  上下文: 自选●3 持仓●2
  │   ├─ <CustomAgentChat>           // 自定义 React Chat 区
  │   │   └─ 按 NDJSON 事件 type 分发渲染 ↓
  │   │       ├─ text_delta           → Markdown 增量追加（react-markdown）
  │   │       ├─ tool_trace           → 可折叠小药丸（"atr_stop 运行中"）
  │   │       ├─ decision_artifact    → <DecisionCard> 一次性渲染
  │   │       ├─ chart_artifact       → <AgentChart> recharts (Phase 2)
  │   │       ├─ table_artifact       → <AgentTable> (Phase 2)
  │   │       └─ error                → 红色警告框，流终止
  │   └─ <AgentComposer>             // 自定义聊天输入框与快捷 Prompt 预设
  │
  └─ <ContextDrawer>                 // 右侧抽屉
      ├─ 当前股票快卡
      ├─ 收藏的决策卡（按股票/按时间）
      └─ 快速跳转：跳到 /watchlist /portfolio
</AgentWorkspace>
```

### NDJSON SSE 事件协议（漏洞四修补）

后端 `/api/agent/chat` 返回 `text/x-ndjson` 流，每行一个 JSON 事件，**禁止在 Markdown 正文内夹带 JSON**：

#### 请求体（`AgentChatReq`，Pydantic 模型）

```python
class AgentChatReq(BaseModel):
    thread_id: str | None = None        # None = 新建会话；非空 = 续聊
    messages: list[dict]                 # OpenAI 消息格式；至少含最新一条 user 消息
    context_codes: list[str] = []        # 用户从 ContextDrawer 注入的自选/持仓代码
                                         #   agent 自动调 astock/gstock 查行情与基本面
    llm: LLMConfig                       # 复用现有 chat.LLMConfig（兼容 OpenAI / CLI 两路）
    style: str = "balanced"              # conservative | balanced | aggressive
                                         #   Phase 4 才生效，Phase 1 接口预留
```

- **鉴权**：复用现有 `_require_api_key` 中间件（路径 `/api/agent/*` 已在 `/api/` 前缀下自动触发）。鉴权失败时**禁止挂起 SSE 连接**——直接返回 HTTP 401 + JSON 错误体（不带 `text/x-ndjson` Content-Type），前端 `fetch` 解析 res.ok 即可识别
- **续聊语义**：`thread_id` 非空时，后端从 `conversations` 表加载历史消息拼到 `messages` 前；前端发送时**只发本次新增的消息**，不重复发整段历史
- **CLI 模型拒绝**：`llm.mode == "cli"` 时直接返回 400 + 错误体，提示前端跳 Settings 配 API 模型（前端 `/agent` 路由层也会做预防性拦截，但后端兜底防绕过）

#### 响应事件类型（每行一个 NDJSON 帧）

| `type` | 载荷字段 | 描述 | 前端渲染 |
|---|---|---|---|
| `text_delta` | `text: string` | 助手回答文本增量（流式 token） | 追加到当前消息的 Markdown 区 |
| `tool_trace` | `tool, status, args, summary?` | 工具调用记录：`status ∈ {running, ok, error}` | 折叠小药丸 |
| `decision_artifact` | `decision_id, data: DecisionCardData` | 决策节点生成的完整结构化决策卡 | 在消息流下方插入 `<DecisionCard>` |
| `chart_artifact` | `data: { type, series, ... }` | 图表数据（Phase 2） | `<AgentChart>` recharts |
| `table_artifact` | `data: { headers, rows }` | 表格数据（Phase 2） | `<AgentTable>` |
| `citations` | `items: [{source, code, range}]` | 数据出处批量上报（消息结束前一次性发） | `<CitationsList>` |
| `done` | `summary` | 流正常结束 | 流关闭 |
| `error` | `message, code?` | 异常上报 | 红色警告框，流提前终止 |

前端用 `fetch + ReadableStream + TextDecoder` 按行解析（不依赖 `EventSource`，因 POST 不支持）。**即使流式文本区 Markdown 未闭合（未配对的 `**`、未闭合代码块）也只追加不解析**——结构化产物完全靠 artifact 事件，避免 LLM 一字字吐 JSON 时前端崩溃。

**必须维护跨 chunk 的 line buffer**——ReadableStream 单次 `read()` 返回的 chunk 边界与 `\n` 不对齐，一行 NDJSON 可能被切成两段：

```typescript
// hooks/useAgentStream.ts 标准解析实现
const response = await fetch('/api/agent/chat', { method: 'POST', ... });
const reader = response.body?.getReader();
const decoder = new TextDecoder();
let lineBuffer = '';

if (reader) {
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;

    // 拼接缓冲区，stream:true 保证多字节 UTF-8 字符跨 chunk 不被截断
    lineBuffer += decoder.decode(value, { stream: true });
    const lines = lineBuffer.split('\n');

    // 弹出最后一个（可能不完整的）行，留到下次拼接
    lineBuffer = lines.pop() ?? '';

    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      try {
        const event = JSON.parse(trimmed);
        dispatch(event);  // 按 event.type 路由到对应渲染器
      } catch (e) {
        console.error('NDJSON 帧解析失败:', trimmed, e);
        // 不抛出，继续下一行——单条坏帧不应中断整个流
      }
    }
  }
  // flush 缓冲区里最后残留的一行
  if (lineBuffer.trim()) {
    try { dispatch(JSON.parse(lineBuffer.trim())); } catch {}
  }
}
```

关键点：`TextDecoder` 必须传 `{ stream: true }`（避免多字节 UTF-8 字符在 chunk 边界被截断）；单条坏帧不中断整流（容错）。

### 决策卡组件（核心 UI 单元）

```
┌─ 决策卡 · 600519 茅台 ────────────────────────────────┐
│ 目标价  ¥1820   (+8.0%)                               │
│ ─────────────────────────────                         │
│ 入场区  ¥1650 – ¥1720                                 │
│ 止损    ¥1580   (-6.2%, ATR×2)                        │
│ 止盈    ¥1950   (+15.8%, PE 分位 75%)                 │
│                                                       │
│ 仓位节奏                                              │
│  第一批  30%  立即  ¥1680                             │
│  第二批  30%  回踩  ¥1700 ± 20                        │
│  第三批  40%  突破  ¥1780                             │
│                                                       │
│ 依据  ● model  [展开公式 ▾]  [查看数据 ▾]              │
│ 生成  2026-07-08 14:30 · atr_stop.v1 + pe_percentile  │
│                                                       │
│ [♡ 收藏]  [⟳ 复盘追踪]  [⧉ 复制]                       │
└───────────────────────────────────────────────────────┘
```

`basis_type` 色标（决策卡「依据」字段标识）：
- `● model` 蓝色（最可信，A股完整公式）
- `● model_fallback` 黄色（数据不足时 Python 简化公式降级，附 `fallback_reason`）
- `● hybrid` 橙色（model 出基础值 + LLM 微调，列出调整项）
- `● llm_reasoning` 灰色（仅 LLM 推理，仅 `target_price` 字段允许）

### 状态管理（zustand）

```ts
// src/lib/stores/agent.ts
{
  threads: AgentThread[]
  currentThreadId: string | null
  messagesByThread: Record<string, Message[]>
  streaming: { active: boolean; toolCalls: ToolCallTrace[] }
  // actions
  createThread(), sendMessage(content, ctx), abortStream(), forkThread(id)
}

// src/lib/stores/decisions.ts (持久化到 localStorage + 服务端 SQLite 双写)
{
  saved: SavedDecision[]
  linkedToPortfolio: Record<decisionId, positionCode>
}
```

### 与现有项目接入

| 现有 | 改动 |
|---|---|
| `src/lib/api.ts` | 新加 `api.agent.threads.*` 等辅助方法 |
| `src/components/layout/` | 侧栏加 "股神 / Agent" 入口（图标 + 文字） |
| `src/router.tsx` | 加 `/agent` 和 `/today` 路由 |
| 现有 10 页 | 零改动（Phase 1） |
| `tailwind.config` | 加 token：decision-card / chart-container / tool-trace |
| 主题适配 | 自定义组件直接使用现有 Tailwind token（`hsl(var(--primary))` 等），与 dashboard 自然统一，无第三方主题映射成本 |

### 与智谱清言样板的差异

- 不做"社区 / 技能市场 / 应用商店 / 日程"侧栏
- 顶栏不写"全球 / 产业 / 个股 / 金融工具 / 数据市场"——这些已经在现有页面里
- 模型切换复用现有 Settings 页的 LLM 配置

---

## 8. 数据流与持久化

### 持久化层（本地 SQLite）

```
backend/.cache/stockclaw.db  (默认；env VR_AGENT_DB 可改 ~/.stockclaw/stockclaw.db)
├─ threads         (会话列表，用于前端 sidebar 高效渲染)
├─ conversations   (历史对话与上下文详细记录)
├─ decisions       (决策卡归档 + 收益追踪)
├─ signals_log     (主动 agent 触发的信号日志，Phase 3)
└─ artifacts       (大型图表/表格 JSON，按 thread 归档)
```

现有 `portfolio.py` / `myreports.py` 不动（文件 JSON 形式）。决策卡通过 `linked_position_code` 软关联到持仓。

**`portfolio.json` 新增账户基础字段**（漏洞五修补，不污染客观数据层——这是用户自维护的本地输入）：

```json
{
  "holdings": [...],
  "closed": [...],
  "totals": {
    "available_cash": 100000.0,        // 可用现金（用户手输）
    "risk_tolerance_pct": 0.01,        // 单笔风险容忍度，默认 1%
    "total_equity_override": null      // 选填：手动覆盖总净值（含港股美股时）
  }
}
```

`risk_based_position` 工具入参 `total_equity` / `cash` 可选；未传则回退读 `portfolio.json::totals`。两者都缺时，工具返回 `basis_type: model_fallback` 且 `cadence` 用比例表达（30% / 30% / 40%），不算绝对股数。

### threads 表 schema
```sql
CREATE TABLE IF NOT EXISTS threads (
  id          TEXT PRIMARY KEY,         -- ULID / UUID
  title       TEXT NOT NULL,            -- 会话标题
  model       TEXT NOT NULL,            -- 使用的模型名称
  created_at  INTEGER NOT NULL,         -- 创建时间戳
  updated_at  INTEGER NOT NULL          -- 最后更新时间戳
);
CREATE INDEX IF NOT EXISTS idx_threads_updated_at ON threads(updated_at);
```

### conversations 表 schema（每条消息一行；Phase 1 #9 必须建此表）
```sql
CREATE TABLE IF NOT EXISTS conversations (
  id              TEXT PRIMARY KEY,        -- 消息 ID (ULID)
  thread_id       TEXT NOT NULL,           -- 所属会话
  role            TEXT NOT NULL,           -- system | user | assistant | tool
  content         TEXT,                    -- 消息正文（assistant 的 Markdown 文本）
  tool_calls_json TEXT,                    -- assistant 消息的工具调用序列化（OpenAI tool_calls 格式）
  tool_call_id    TEXT,                    -- role=tool 时关联的 tool_call_id
  artifacts_json  TEXT,                    -- 本条消息产出的 artifact 列表（决策卡/图表/表格）
  created_at      INTEGER NOT NULL,
  FOREIGN KEY (thread_id) REFERENCES threads(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_conversations_thread ON conversations(thread_id, created_at);
```

设计要点：
- `tool_calls_json` 与 OpenAI tool_calls 协议对齐，便于回放整个 agent 轨迹
- `artifacts_json` 单独存——避免下次加载会话时要从 `content` Markdown 反向解析 artifact
- `ON DELETE CASCADE`：删 thread 自动删消息，前端不用先删子表

### decisions 表 schema

```sql
CREATE TABLE decisions (
  id              TEXT PRIMARY KEY,         -- ULID
  thread_id       TEXT NOT NULL,
  code            TEXT NOT NULL,
  name            TEXT,
  created_at      INTEGER NOT NULL,

  -- 决策内容
  target_price    REAL,
  entry_low       REAL,
  entry_high      REAL,
  stop_loss       REAL,
  take_profit     REAL,
  cadence_json    TEXT,                     -- [{batch, pct, trigger, price}, ...]

  -- 依据
  basis_type      TEXT NOT NULL,            -- model | model_fallback | llm_reasoning | hybrid（按"最大不确定性优先"归并）
  model_versions_json  TEXT,                -- 字段级字典 {"target_price":"model(pe_percentile.v1)", "stop_loss":"model_fallback(atr_stop.v1, reason='no_kline')", ...}
  assumptions_json     TEXT,                -- 显式假设列表
  citations_json TEXT,                      -- 数据出处

  -- 复盘追踪（scheduler 定时更新）
  status              TEXT,                 -- active|hit_target|hit_stop|expired
  linked_position_code TEXT,
  price_at_creation   REAL,
  current_price       REAL,
  pnl_pct             REAL,
  updated_at          INTEGER,

  raw_artifact_json   TEXT                  -- 完整 AgentResult，便于复盘
);
CREATE INDEX idx_decisions_code ON decisions(code);
CREATE INDEX idx_decisions_status ON decisions(status);
CREATE INDEX idx_decisions_thread_id ON decisions(thread_id);
```

### signals_log 表 schema（Phase 3）

```sql
CREATE TABLE signals_log (
  id          TEXT PRIMARY KEY,
  created_at  INTEGER NOT NULL,
  source_node TEXT NOT NULL,                -- panel_valuation / proactive / ...
  code        TEXT,
  signal_type TEXT NOT NULL,                -- return_diff / percentile_break / fund_flow_anomaly
  severity    REAL,                         -- 0~1
  payload_json TEXT,
  handled     INTEGER DEFAULT 0
);
```

### 隐私与安全边界

| 项 | 现状 | 改造后 |
|---|---|---|
| SQLite db | 无 | **默认 `backend/.cache/stockclaw.db`**（容器/Docker 友好，`.cache` 已在 `.gitignore`）；env `VR_AGENT_DB` 可改 `~/.stockclaw/stockclaw.db` |
| SQLite 驱动 | — | **强制 `aiosqlite`**，所有读写走 `async with`，避免阻塞 FastAPI event loop |
| SQLite PRAGMA | — | 初始化时 `PRAGMA journal_mode=WAL;` + `PRAGMA busy_timeout=5000;`，防 `database is locked` |
| Schema 迁移 | — | 用 SQLite 内置 `PRAGMA user_version` 做轻量级可重入 migration（无外部工具依赖）。`MIGRATIONS: dict[int, list[str]]` 按版本递增，启动时比对当前版本顺次执行未应用的 SQL，每版执行完写回 `user_version`。`CREATE TABLE IF NOT EXISTS` 保证多次启动幂等。 |
| 决策卡内容 | 无 | 含个人投资决策——不入 git、不上传 |
| API keys | `.env` 本地 | 不变 |
| LangSmith tracing | — | 默认关；开启则 env 配本地 langsmith 或自建 |

### 新 `.env.example` 增加

```
# ── AI native agent 层 ──
VR_AGENT_DB=backend/.cache/stockclaw.db      # SQLite 路径（默认项目内，可改 ~/.stockclaw/）
VR_AGENT_MODEL=glm-5.2                        # 默认主驾模型
VR_AGENT_PROACTIVE_CRON=0 9 * * *             # 主动 agent 早 9 点扫
VR_AGENT_MAX_ITERATIONS=8                     # plan-execute / 多 agent 最大轮数硬上限
VR_AGENT_RATE_LIMIT_COOLDOWN=1.0              # 东财 cool-down 秒数
VR_AGENT_RATE_LIMIT_CACHE_TTL=1800            # 强缓存 TTL（秒，默认 30 分钟）
```

### 收益追踪机制（Phase 3）

- 后台 scheduler 每日收盘后扫 `decisions WHERE status='active'`
- 拉最新价 → 更新 `current_price` / `pnl_pct` / `status`
- 触及 `take_profit` → `status='hit_target'`；触及 `stop_loss` → `status='hit_stop'`
- `/portfolio` 页加"决策卡追踪"区，对照当初建议 vs 实际表现

---

## 9. 阶段化路线图

### Phase 1（MVP，2-3 周）— 决策卡闭环

| # | 交付物 | 文件 | 验收 |
|---|---|---|---|
| 1 | 合规解禁 + 物理隔离 | `chat.py` → `chat_legacy.py`（保留原 SYSTEM_PROMPT 给老 UI）；新建 `agents/prompts.py` 用解禁版；`AGENTS.md` 重写。**必须同步更新所有 `import chat` 的下游模块**（已确认的导入点：`app.py:21` `import chat as chat_layer`、`mcp_server.py:18` `import chat`） | grep `chat_legacy.py` 无残留禁止条款；新 prompt 文件含决策建议第六维；**老 `/api/chat` 与 MCP server 仍能启动并响应（不报 `ModuleNotFoundError`）**；**禁用 `from chat_legacy import *` 兼容垫片**（模糊物理隔离边界） |
| 2 | Rate Limiter | `backend/agents/rate_limiter.py` | `EastmoneyRateLimiter` cool-down 1.0s；**锁必须横跨业务逻辑（acquire 在 `__aenter__`、release 在 `__aexit__`，不得用 `async with`）**；并发压测 5 个 acquire 总耗时 ≥ 4s，期间无两个 HTTP 请求重叠 |
| 3 | quant.valuation | `backend/quant/valuation.py` | `forward_pe_target` + `pe_percentile_revert` 单测；美港股分支抛 `DataUnavailable` |
| 4 | quant.stops（含 fallback） | `backend/quant/stops.py` | `atr_stop` A 股主路径 + `model_fallback` 路径（无 K 线时返回 `-8%` 止损 + `basis_type: model_fallback`）；`risk_based_position` 读 `portfolio.json::totals` |
| 5 | quant.cadence | `backend/quant/cadence.py` | `pyramid_buy` + `batch_build` + `dca_plan` 单测 |
| 6 | portfolio 字段扩展 | `backend/portfolio.py` | `totals.available_cash` / `risk_tolerance_pct` 读写，向后兼容老 JSON |
| 7 | agents 核心 | `state.py` + `graph.py` + `nodes/orchestrator.py` + `nodes/decision.py` + `tools.py` + `prompts.py` | 编译通过；**Decision Node 实现 basis_type 归并规则**（llm_reasoning > hybrid > model_fallback > model）；输入茅台 → 出 decision_artifact 事件，`model_versions_json` 为字段级字典；**`tools.py` 所有调 astock/gstock/market/newsradar 的工具用 `asyncio.to_thread` 包装同步数据层**，`grep -E "await (astock|gstock|market|newsradable)" agents/tools.py` 无命中 |
| 8 | runner + NDJSON endpoint | `runner.py` + `app.py:/api/agent/chat` | curl POST 返回 `text/x-ndjson` 流；事件类型按协议表完整覆盖；**每行严格以 `\n` 结尾**；**`AgentChatReq` Pydantic 模型按 §7 定义（thread_id / context_codes / style 字段齐）**；**鉴权失败返回 HTTP 401 + JSON，不挂起 SSE 流**；**CLI 模式请求返回 400 + JSON** |
| 9 | persistence（aiosqlite） | `persistence/db.py` + `persistence/{threads,conversations,decisions}.py` | 全 `async`；WAL+busy_timeout 已设；**用 `PRAGMA user_version` 做轻量 migration，多版脚本可重入幂等**；**migration V1 必须建齐 `threads` + `conversations` 两张表**；启动两次不报 schema 错误；删除 thread 时 `ON DELETE CASCADE` 自动清空 conversations |
| 10 | 前端 /agent 路由 + 模型校验 | `router.tsx` + `pages/Agent.tsx` | 路由可达；CLI 模型配置时显示拦截覆盖层 |
| 11 | CustomAgentChat + 事件分发 | `components/agent/CustomAgentChat.tsx` + `hooks/useAgentStream.ts` | 按 `type` 分发 `text_delta` / `tool_trace` / `decision_artifact` / `error`，不解析 Markdown 内 JSON；**`useAgentStream` 维护 line buffer，跨 chunk 拼接 NDJSON**，多字节 UTF-8 字符 `{stream:true}` 解码；**单条坏帧不中断整流** |
| 12 | DecisionCard + ContextDrawer | `components/agent/{DecisionCard,ContextDrawer}.tsx` + layout 加链接 | decision_artifact 渲染正确；4 档 basis_type 色标；**展开「依据」按字段级 `model_versions_json` 显示来源**；收藏写入 SQLite |

**Phase 1 出口**：用户能在 `/agent` 输入"分析茅台 给目标价止损止盈仓位节奏"，拿到带 `basis_type` 的决策卡，能收藏、能复制。**美港股请求自动走 `model_fallback` 或 `llm_reasoning`，决策卡明确标降级依据**。**5 个并发工具请求总耗时 ≥ 4s，不会触发东财 403**。**SQLite 在 1000 次连续写入下无 `database is locked`**。**决策卡内的批次计划等价于样板 1 的"分批建仓"建议本身**；样板 1 的"3 策略净值曲线对比 + 收益回测表"需要 `backtest.py`，落在 Phase 2。

**Phase 1 不做**：多 agent 讨论（panel_*）、plan-execute planner、回测、主动 agent / /today、Chart/Table renderer、MCP server 升级。

### Phase 2（1-2 周）— 多 agent 讨论 + plan-execute

- panel_valuation / panel_funds / panel_earnings / panel_industry / panel_events
- planner + replanner（复杂请求的 plan-execute）
- **`max_iterations=8` 硬上限**（env 可调），超出后 orchestrator 强制退出并用当前上下文生成安全版收尾
- ChartRenderer / TableRenderer（回测对比、胜率拆分）
- 样板 2（40 日收益差胜率推导）的能力等价可达

### Phase 3（1-2 周）— 主动 agent + 今日看盘

- proactive 子图 + APScheduler（每日定时扫自选/持仓）
- signals_log 表 + WebSocket push
- `/today` 页（持仓异动 + 自选信号 + 多 agent 早会纪要 + 行动建议）
- 收益追踪 **双通道机制**：
  - **定时同步**：每日收盘后 scheduler 扫 `decisions WHERE status='active'` 更新最新价 / `pnl_pct` / `status`
  - **懒惰触发**：用户打开 `/agent` 或 `/portfolio` 时，若 `decisions.updated_at` 跨过最近交易日收盘时刻，静默同步一次——避免电脑关机漏更新

### Phase 4（持续）— 优化与扩展

- `quant.factors`（相对强弱、beta、行业 alpha）
- `quant.walk_forward` / `monte_carlo`
- 决策卡复盘页（`/portfolio` 集成）
- MCP server 升级（让 Claude Code 也能调 `agents.graph`）
- 风格切换（保守/平衡/激进 → 影响 multiplier、仓位百分比）

---

## 10. 风险与缓解

| 风险 | 缓解 |
|---|---|
| React 19 + CopilotKit 依赖地狱 | 不使用 CopilotKit，自定义轻量 React Chat + 原生 NDJSON SSE 解析 |
| 东财 ~1s 速率限制 / Phase 2 多 agent 瞬时高并发 | `EastmoneyRateLimiter` cool-down 1.0s（**非 Semaphore**）+ 30min 强缓存；本地默认单 worker，文档写明多进程下 Rate Limiter 失效 |
| quant 工具在美港韩股上无历史 K 线 / 无一致 EPS | 四级降级链：`model` → `model_fallback`（Python 简化公式）→ `llm_reasoning`（仅 target_price，列依据）→ 直接拒答 |
| LLM 编造 ATR / 历史分位数（明明工具不可用却瞎填） | 系统级硬约束：`stop_loss` / `entry_*` / `take_profit` 字段必须由 Python 算（model 或 model_fallback）；LLM 仅可调整 `target_price` 且必须列推导；前端按 `basis_type` 色标可视化警告 |
| SQLite 同步阻塞 FastAPI event loop / database locked | 强制 `aiosqlite`；初始化 `WAL` + `busy_timeout=5000`；默认路径 `backend/.cache/stockclaw.db` |
| 流式 Markdown 内嵌 JSON 解析崩溃 | NDJSON 结构化事件流：决策卡走独立 `decision_artifact` 事件，**禁止在 Markdown 内夹 JSON** |
| `risk_based_position` 算不出具体股数（缺 cash/equity） | `portfolio.json::totals` 新增 `available_cash` / `risk_tolerance_pct`；缺失时工具回退比例表达（30%/30%/40%） |
| Phase 2 弱模型 + 多 agent 死循环烧 Token | `VR_AGENT_MAX_ITERATIONS=8` 硬上限；超出后强制退出 + 安全版收尾总结 |
| Phase 3 定时任务依赖开机时间 | 双通道：APScheduler 定时同步 + 页面打开时懒惰触发 |
| 决策卡追踪误命中（涨跌停、停牌） | scheduler 跳过停牌；涨跌停时用上一交易日收盘价对比 |

---

## 11. 不做的事（YAGNI）

- ❌ 用户登录/多用户（个人本地）
- ❌ 移动端 App（个人本地用 Web 就够）
- ❌ AutoGen/CrewAI 风格的复杂对话编排（plan-execute + supervisor 已够）
- ❌ 真实券商 API 接入（仅做分析，不自动下单）
- ❌ 期权/期货/加密货币（Phase 4+ 视需要）
- ❌ region gating / disclaimer 弹窗（个人本地，不分发）
- ❌ 公开 main 仓库的同步（main 是上游，本 fork 是 fork，不回流）

---

## 12. 与 main 公开仓库的关系

- 本仓库（stockclaw 当前工作副本）= fork 后的工作副本
- 公开 main 仓库保持原红线（不荐股、不预测、不分发）
- 本 fork 不回流公开仓库；公开仓库的数据层修复可以 cherry-pick 到本 fork
- 三份私有文档（`VibeResearch-开发日志.md` / `方案定稿.md` / `专业化建议.md`）依然不入公开仓库

---

## 附录 A：样板对照

**样板 1（智谱清言"股神"）：20 万买中证红利，三种建仓策略对比**

对应能力：
- `quant/backtest.py::backtest_strategy`（一次性/定投/分批）→ 净值曲线 + 收益对比表
- `quant/cadence.py::batch_build` → 具体批次计划（第一批 8 万立即，第二批 6 万触发条件…）
- 决策卡组件渲染建议

**样板 2：40 日收益差胜率推导**

对应能力：
- `quant/signals.py::return_diff` → 40 日收益差时序
- `quant/backtest.py::signal_backtest` → 全样本 + 分年胜率拆分
- ChartRenderer（散点 + 柱状）+ TableRenderer
- Phase 2 落地

---

## 附录 B：对抗性评审响应（2026-07-08）

### 第一轮（8 项，全部采纳）

针对 `docs/superpowers/review/adversarial-review-2026-07-08.md` 的 8 项评审意见全部采纳：

| 评审漏洞 | 处置 | spec 落地位置 |
|---|---|---|
| 1. Semaphore 只控并发不控频控 | 引入 `EastmoneyRateLimiter` cool-down 1.0s（**非 Semaphore**）；30min 强缓存 | § 2 原则 5、§ 6 约束 4、§ 9 Phase 1 #2 |
| 2. 美港股降级 vs LLM 防幻觉矛盾 | 四级降级链：`model` → `model_fallback`（Python 简化公式）→ `llm_reasoning`（仅 `target_price`）→ 拒答；新增 `basis_type: model_fallback` | § 2 原则 6、§ 6 约束 1+3、决策卡色标 |
| 3. SQLite 同步阻塞 / database locked | 强制 `aiosqlite`；初始化 `WAL` + `busy_timeout=5000`；默认路径 `backend/.cache/stockclaw.db` | § 8 隐私与安全边界、§ 9 Phase 1 #9 |
| 4. 流式 Markdown 内嵌 JSON 崩溃 | NDJSON 结构化事件流：`text_delta` / `tool_trace` / `decision_artifact` / `chart_artifact` / `table_artifact` / `citations` / `done` / `error`，**禁止在 Markdown 内夹 JSON** | § 2 原则 4、§ 7 NDJSON 协议表、§ 9 Phase 1 #8+#11 |
| 5. `risk_based_position` 缺 cash/equity | `portfolio.json::totals` 加 `available_cash` + `risk_tolerance_pct`；缺失时回退比例表达 | § 8 portfolio 字段扩展、§ 9 Phase 1 #4+#6 |
| P1. chat.py 新旧 prompt 分裂 | `chat.py` → `chat_legacy.py` 物理隔离老接口；新逻辑全在 `agents/` | § 9 Phase 1 #1 |
| P2. 弱模型多 agent 死循环烧 Token | `VR_AGENT_MAX_ITERATIONS=8` 硬上限（env 可调），超出强制退出 + 安全版收尾 | § 8 .env、§ 9 Phase 2 |
| P3. 定时任务依赖开机时间 | 双通道：APScheduler 定时 + 页面打开懒惰触发 | § 9 Phase 3 |

### 第二轮（4 项，全部采纳）

针对 `docs/superpowers/review/adversarial-review-2026-07-08-round2.md` 的 4 项评审意见全部采纳：

| 评审漏洞 | 处置 | spec 落地位置 |
|---|---|---|
| R2-1. `EastmoneyRateLimiter.__aenter__` 中 `async with self._lock` 在 `return self` 时立即释放锁，业务请求期间锁已不在，限流形同虚设 | 改为 `await self._lock.acquire()` + 在 `__aexit__` 显式 `release()`；try/finally 保证锁一定释放 | § 6 Rate Limiter 完整代码、§ 9 Phase 1 #2 验收（"锁必须横跨业务逻辑"） |
| R2-2. 前端 ReadableStream 按 chunk 直接 `split('\n')` 会切断跨 chunk 的 NDJSON 帧，半截 JSON 抛 `SyntaxError` 中断整流 | `useAgentStream` 维护 `lineBuffer`，每次 chunk 拼接后按 `\n` 切，最后一行 pop 留作下次；`TextDecoder.decode(value, {stream:true})` 防多字节 UTF-8 跨 chunk 截断；单条坏帧 `try/catch` 不中断整流，最后 flush 残留行 | § 7 NDJSON 解析 line buffer 标准实现、§ 9 Phase 1 #11 |
| R2-3. 多工具合并为决策卡时整卡 `basis_type` 取值未定义；`model_versions_json` 扁平数组信息量不足 | Decision Node 按"最大不确定性优先"归并：`llm_reasoning` > `hybrid` > `model_fallback` > `model`；`model_versions_json` 改为字段级字典 `{target_price: "model(pe_percentile.v1)", stop_loss: "model_fallback(atr_stop.v1, reason='no_kline')", ...}` | § 6 约束 3 归并规则、§ 9 Phase 1 #7+#12 |
| R2-4. SQLite migration 需要可重入机制但不应引入外部工具 | 用 SQLite 内置 `PRAGMA user_version` + `MIGRATIONS: dict[int, list[str]]`；启动比对当前版本顺次执行未应用 SQL，每版完成写回 `user_version`；`CREATE TABLE IF NOT EXISTS` 保证幂等 | § 8 隐私与安全边界「Schema 迁移」行、§ 9 Phase 1 #9 |

### 第三轮（4 项 + 1 格式瑕疵，全部采纳）

针对 `docs/superpowers/review/adversarial-review-2026-07-08-round3.md` 的发现全部采纳（评审已验证全部前两轮 12 项无回归）：

| 评审发现 | 处置 | spec 落地位置 |
|---|---|---|
| R3-格式. 第 159 行孤立 ``` 闭合标记，无对应开启 | 移除该行 | § 6 quant 契约示例后的注释段 |
| R3-1. `chat.py → chat_legacy.py` 重命名会断 `app.py:21` `import chat as chat_layer` 与 `mcp_server.py:18` `import chat`，老 `/api/chat` 与 MCP server 立即 `ModuleNotFoundError` | Phase 1 #1 任务明确列出全部导入修改点；验收要求"老 /api/chat 与 MCP server 仍能启动"；**禁用 `from chat_legacy import *` 兼容垫片**（模糊物理隔离） | § 9 Phase 1 #1 |
| R3-2. `agents/tools.py` 用 `async def` 但 `astock.*` 全同步（`urllib` / `requests` / `mootdx` 均阻塞）；直接 `await` sync 函数会 TypeError；直接调用会冻结 event loop，Rate Limiter 的 sleep 无法调度 | **硬约束（焊死）**：所有调 astock/gstock/market/newsradar 的 `@tool` 必须用 `await asyncio.to_thread(fn, ...)` 包装；Phase 1 #7 验收用 `grep -E "await (astock|gstock|market|newsradable)" agents/tools.py` 必须无命中；code review 必查 | § 6 工具示例代码改为 `asyncio.to_thread` 版本 + 硬约束说明、§ 9 Phase 1 #7 |
| R3-3. `conversations` 表 schema 缺失（仅有目录结构提及） | § 8 新增完整 `conversations` 表 SQL（含 `tool_calls_json` / `tool_call_id` / `artifacts_json` 字段 + `ON DELETE CASCADE` + 索引）；Phase 1 #9 验收"migration V1 必须建齐 threads + conversations 两张表" | § 8 conversations schema 段、§ 9 Phase 1 #9 |
| R3-4. `/api/agent/chat` 请求体未定义；NDJSON 只定义了响应 | § 7 新增 `AgentChatReq` Pydantic 模型（`thread_id` / `messages` / `context_codes` / `llm` / `style`）；明确鉴权 401 + JSON 短路（不挂 SSE）、CLI 模式 400 拒绝、续聊只发新增消息 | § 7 NDJSON 协议「请求体」段、§ 9 Phase 1 #8 |

零拒绝。三轮共 17 项修订全部落地，spec 已达生产级，可进入 writing-plans 阶段。

---

**下一步**：本 spec 经用户复核后，调用 writing-plans skill 生成 Phase 1 的详细实施计划。
