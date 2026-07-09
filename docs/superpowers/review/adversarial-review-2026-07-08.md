# 对抗性评审报告：AI Native 投资分析平台改造设计

**文件名称**：`adversarial-review-2026-07-08.md`  
**评审日期**：2026-07-08  
**评审员**：Antigravity (AI 架构评审组)  
**评审状态**：Completed (建议针对设计漏洞进行修正补充后方可进入实施阶段)  
**针对的 spec 文件**：[2026-07-08-ai-native-agent-module-design.md](file:///vol2/1000/code/stockclaw/docs/superpowers/specs/2026-07-08-ai-native-agent-module-design.md)  

---

## 1. 评审概述

本评审报告针对将 `stockclaw` 项目改造为个人本地部署的 **AI 原生投资分析平台** 的设计方案（以下简称 "Spec"）进行对抗性评审。评审的核心目标是站在**工程稳定性、系统并发安全、数据一致性、LLM 幻觉控制、React 19 兼容性**等角度，寻找设计中潜在的“阿喀琉斯之踵”，并提出具体的修正建议。

### 评审基本结论
Spec 的核心方向（剥离合规红线、客观数据层与 Agent 建议层隔离、轻量化 React Chat + NDJSON SSE 替代 CopilotKit）是**合理且可行**的。然而，在**并发速率限制、美港股降级逻辑的数学逻辑闭环、SQLite 在 FastAPI 中的异步阻塞、SSE 结构化传输协议设计**四个核心领域存在明显的漏洞或未定义行为。如果不做修正，可能会在多 Agent 协同（Phase 2）或主动扫描（Phase 3）启动时遭遇接口高频封禁、UI 流式渲染崩溃或系统死锁。

---

## 2. 核心漏洞与对抗性批判

### 漏洞一：东财（East-Money）数据源频控与多 Agent 并发死锁
> **Spec 描述**：在 `backend/agents/tools.py` 引入全局 `asyncio.Semaphore(1)` 串行限制锁，以缓解东财 ~1s 的速率限制，防止 API 403 频控。

#### 对抗性分析：
1. **Semaphore 只能解决“并发度”，无法解决“频度”**：
   `asyncio.Semaphore(1)` 确保在任意时刻只有一个请求正在进行，但在 Phase 2 中，如果 5 个 Panel 节点（估值、资金、财报、行业、事件）同时苏醒，它们会排队发出 5 个请求。由于是串行，请求 1 结束后，请求 2 会**立即**发出。这 5 个请求可能在 200 毫秒内全部发完，依然会触发东财的 ~1s 频控。
2. **多 Worker/多进程下的 Semaphore 失效**：
   虽然本地通常运行单个 Uvicorn 实例，但如果用户使用 `--workers` 或在后台有 Proactive 扫描 Scheduler 同时运行，多进程或独立的事件循环（Event Loop）将无法共享同一个内存中的 `asyncio.Semaphore`。
3. **调用开销与积压**：
   如果排队的工具请求过多，加上 Semaphore 的串行化，会导致 Agent 的响应时延呈线性暴增，用户体验极差。

#### 建议修补方案：
- **引入带延迟的频率限制器（Rate Limiter）**：
  弃用简单的 Semaphore，改为一个具备全局冷却时间（Cool-down time，如 $1.0\text{s}$）的并发排队装饰器，在每次成功请求东财 API 后强制 `await asyncio.sleep(1.0)`。
- **冷热数据分类与强缓存机制**：
  在 `@tool` 封装中，除 A 股行情外，其他如股东人数、大宗交易等静态/低频数据必须强制读 30 分钟的缓存，绝不允许 Agent 在同一会话的工具路由中重复打穿到真实网络。

---

### 漏洞二：美港股降级逻辑下的决策卡数学逻辑撕裂
> **Spec 描述**：对于美港韩股，由于数据源限制无法获取历史 K 线和历史估值分位数，调用 `atr_stop` 等工具时抛出 `DataUnavailable`。Agent 自动转入 `llm_reasoning` 模式，提示用户“由于美港韩股历史数据受限，转为由大模型做定性分析与估算”。

#### 对抗性分析：
1. **首尾逻辑冲突**：
   Spec 在第 6 节中写道：“**严禁 LLM 自行计算/胡编 ATR、历史分位数等硬性量化指标**”。但在美港股场景下，如果量化工具不可用，决策卡却依然需要渲染 `stop_loss`（止损价）和 `target_price`（目标价）。如果 LLM 不能编造，那这些数值怎么填？如果让 LLM 瞎填，就违背了防幻觉的基本盘。
2. **决策卡公式的逻辑坍塌**：
   例如 `stops.py` 中的 `risk_based_position`（按止损距离反推仓位）是建立在 `stop_price` 基础上的。如果 `stop_price` 是 LLM 定性估出来的（比如拍脑袋说“我认为止损在 150”），那么以此算出来的仓位计划就失去了数学的严肃性，容易诱导用户做出错误决策。

#### 建议修补方案：
- **定义“降级量化公式”而非“大模型拍脑袋”**：
  当 `atr_stop` 发现数据不可用时，不应让大模型直接估算止损价。应当在 Python 代码层自动降级为简单的比例止损（如：收盘价的 $-10\%$ 或近 $N$ 日最高/最低价），并在 `basis_type` 中标记为 `model_fallback`。
- **限制 LLM Reasoning 的输出字段**：
  大模型仅被允许修改“目标价”（基于行业 PE 和 EPS 预测的合理乘积，需提供推导依据），对于“止损价”等硬性量化指标，必须使用 Python 计算出的 fallback 结果，禁止 LLM 自主决定。

---

### 漏洞三：SQLite 阻塞与本地文件并发写入风险
> **Spec 描述**：使用本地 SQLite 存储 threads, conversations, decisions, signals_log 等，并配置 WAL 模式；写入走单线程 queue。

#### 对抗性分析：
1. **异步架构中的同步阻塞**：
   FastAPI 本身是异步的（`async def`）。如果后端直接使用标准库的 `sqlite3`（同步库）进行频繁的 DB 写入和查询，每个 DB 读写请求都会阻塞 FastAPI 的事件循环（Event Loop），导致多并发请求时系统的响应性能急剧劣化。
2. **多线程并发冲突**：
   FastAPI 的普通路由函数（`def` 而非 `async def`）是在外部线程池中运行的。如果 `persistence/db.py` 没有正确配置连接池或全局锁，在多线程环境下频繁写入 SQLite 会直接导致 `sqlite3.OperationalError: database is locked`。

#### 建议修补方案：
- **强制使用 `aiosqlite`**：
  必须在 backend 引入 `aiosqlite` 异步 SQLite 驱动，所有数据库读写均使用 `async with` 和 `await`，彻底释放 FastAPI 的事件循环。
- **明确配置 WAL 模式的初始化时机**：
  在 `db.py` 初始化数据库时，显式执行 `PRAGMA journal_mode=WAL;` 和 `PRAGMA busy_timeout=5000;`，确保并发写操作时不会因瞬间锁定而导致崩溃。
- **数据库路径安全回退**：
  Spec 中指定的 `~/.stockclaw/stockclaw.db` 依赖于用户宿主机的家目录权限。在部分容器化环境（Docker）中可能会由于权限不足导致无法创建目录。建议默认路径设为项目内部（已在 `.gitignore` 中的 `backend/.cache/stockclaw.db`），并通过环境变量 `VR_AGENT_DB` 允许用户改写。

---

### 漏洞四：前端 Markdown 拦截机制的解析脆弱性
> **Spec 描述**：前端 `CustomChatArea` 不使用 CopilotKit，改用自定义轻量级 React Chat 组件。通过拦截 Markdown 中的特定标记（如 `:::decision-card JSON`）将其渲染为自定义决策卡组件。

#### 对抗性分析：
1. **流式 NDJSON SSE 的状态分裂**：
   在 SSE 流式传输过程中，LLM 是一字一字输出的。如果采用“拦截 Markdown 中的代码块”机制，在代码块只吐出了一半（例如 `{"target_price": 18` 且没有闭合）时，前端解析器会因为 JSON 不完整而频繁抛出 `SyntaxError`，导致界面闪烁，甚至使得后续输出无法渲染。
2. **Markdown 渲染器的干扰**：
   很多主流 Markdown 渲染库（如 `react-markdown`）在解析未完成的自定义语法块时，会将其当成普通的文本或损坏的 HTML 节点，导致流式输出过程中 UI 布局剧烈跳动。

#### 建议修补方案：
- **API 级的数据/文本分离（结构化流）**：
  弃用在 Markdown 正文中夹带 JSON 的原始方案。建议在后端 `/api/agent/chat` 吐出的 NDJSON SSE 流中定义明确的事件类型：
  - `{"type": "delta", "text": "..."}`：用于普通的流式文本展示。
  - `{"type": "tool_call", "tool": "..."}`：展示小药丸。
  - `{"type": "artifact", "data": { ... }}`：由 Agent 在最终决策节点生成并一次性发送的结构化 JSON，前端据此直接渲染在消息流下方或右侧抽屉。
- **若坚持 Markdown 拦截，必须设计稳健的“流式 JSON 宽限器”**：
  如果因架构限制必须在 markdown 中拦截，前端解析器必须能够包容截断的 JSON（利用 `json-repair` 或正则提取器），在未完全闭合时展示骨架屏（Skeleton），避免 UI 崩溃。

---

### 漏洞五：决策工具 `risk_based_position` 的数学闭环缺失（Cash 缺失）
> **Spec 描述**：`stops.py` 中规划了 `risk_based_position`（按止损距离反推仓位）工具。

#### 对抗性分析：
1. **账户资金（Cash）在数据层是缺失的**：
   系统仅记录了持仓标的 (`holdings`) 和已平仓标的 (`closed`)，**完全没有记录账户可用现金（Cash）或总资产（Total Equity）**。
2. **无法自动计算仓位股数**：
   没有可用现金和总资产，量化公式就无法计算诸如“使用总仓位的 $10\%$ 买入”或“单笔风险控制在总净值的 $1\%$”的具体股数。Agent 在调用此工具时，必须强制要求用户在 Prompt 中手动输入“我有 10 万元现金”或“我的总资产是 50 万”。这使得整个 Agent 的自动化体验大打折扣。

#### 建议修补方案：
- **在 `portfolio.json` 中追加账户基础财务字段**：
  在不污染客观数据层的大前提下，允许在本地 `portfolio.json` 的 `totals` 中由用户手动设置 `available_cash`（可用资金）和 `risk_tolerance_pct`（单笔风险容忍度，默认 1%）。
- **quant 契约升级**：
  将 `risk_based_position` 的入参增加 `total_equity` 或 `cash` 作为可选参数。若为空，则读取 `portfolio.py` 中的用户设置值，以此确保建议仓位的计算结果切实可用。

---

## 3. 阶段化路线图的可行性与冲突审查

Spec 将项目划分为 4 个阶段，以下对各阶段的核心冲突进行评估：

### Phase 1 审查（MVP 阶段）
*   **冲突点**：
    在 Phase 1 中，Spec 移除了 `chat.py` 的禁止条款并修改了 `AGENTS.md`。但与此同时，老接口 `/api/chat` 被要求保留。
*   **对抗风险**：
    如果老接口 `/api/chat` 的 system prompt 依然使用旧版的 `SYSTEM_PROMPT`（不含建议，焊死了合规红线），而新接口 `/api/agent/chat` 使用新版解禁后的 system prompt，后端的 `chat.py` 会面临**逻辑分裂**。
*   **修补建议**：
    显式将老的 `chat.py` 保留为 `chat_legacy.py`（专供老的数据个股页面的 AI 助手使用，维持原合规红线），而新版 Agent 工作台完全由 `agents/` 目录下的新逻辑接管。这能确保老用户和新平台互不污染。

### Phase 2 审查（多 Agent 讨论 + Plan-Execute）
*   **冲突点**：
    引入了 5 个 Panel 节点以及一个 Planner/Replanner 节点。
*   **对抗风险**：
    多节点环路在本地运行（通常是 CPU 环境）时，模型推理的 Token 消耗和网络延迟会极大增加。如果用户配置的是弱模型（如较弱的千问/Deepseek Lite），多 Agent 复杂的路由很容易导致死循环，耗尽用户 Token 甚至导致网络超时。
*   **修补建议**：
    在 `runner.py` 中强制设置 `max_iterations = 8`（最大轮数硬上限），一旦超出，Orchestrator 必须强制退出并利用当前上下文生成收尾的“安全版”总结，绝不能无限制循环。

### Phase 3 审查（主动 Agent + 今日看盘）
*   **冲突点**：
    引入 `APScheduler` 每日收盘后扫描 active 状态的 decisions，更新最新价、计算盈亏并更新 status。
*   **对抗风险**：
    如果用户的电脑在收盘时处于关机或睡眠状态，定时任务将无法执行。这会导致 decisions 的 status 和盈亏在前端页面展现时发生严重滞后，导致“复盘追踪”失效。
*   **修补建议**：
    不要单纯依赖每日特定时间的“定时触发”。应当采用**懒惰触发（Lazy Update）+ 定时同步双通道机制**：每次用户打开 `/agent` 页面或 `/portfolio` 页面时，前端或后端自动检测上一次更新时间。如果跨越了交易日收盘时间，则立即静默触发一次价格和状态的更新计算。

---

## 4. Spec 补充与修正设计建议 (Action Items)

为确保项目改造能够平稳运行，建议在开始编写 Phase 1 代码前，对架构方案进行如下细节微调：

### A. 后端接口响应 NDJSON SSE 协议细化
后端 `/api/agent/chat` 应统一采用结构化事件流：

| 事件类型 (`type`) | 数据载体字段 | 描述 | 前端渲染策略 |
| :--- | :--- | :--- | :--- |
| `text_delta` | `text` | 正常的助手回答文本流 | 追加到 Markdown 文本区域 |
| `tool_trace` | `tool`, `status`, `args` | 工具的执行记录（如 `atr_stop` 运行中/成功/失败） | 渲染为可折叠的小药丸（Badge） |
| `decision_artifact` | `decision_id`, `data` (JSON) | 最终生成的结构化决策卡数据 | 在流下方渲染为专用的 `<DecisionCard>` UI 交互组件，支持一键保存/收藏 |
| `error` | `message` | 发生错误时的异常上报 | 渲染红色警告框，流提前终止 |

### B. 数据库写入池与 Wal 初始化 (db.py 规范)
```python
# persistence/db.py 核心伪代码设计

import aiosqlite
import os

DB_PATH = os.path.expanduser(os.environ.get("VR_AGENT_DB", "backend/.cache/stockclaw.db"))

async def get_db_conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = await aiosqlite.connect(DB_PATH)
    # 强制启用 WAL 模式和写入忙等待，防止 database is locked
    await conn.execute("PRAGMA journal_mode=WAL;")
    await conn.execute("PRAGMA busy_timeout=5000;")
    conn.row_factory = aiosqlite.Row
    return conn
```

### C. quant 工具箱容错与 Fallback 契约
```python
# backend/quant/stops.py 容错设计

class DataUnavailable(Exception):
    """底层数据不完整或不支持该市场的异常"""
    pass

def atr_stop(code: str, period: int = 14, multiplier: float = 2.0) -> dict:
    try:
        # A股尝试调用 astock.kline
        # 美港股在 astock 中没有对应 6 位代码的数据会进入异常
        kline_data = get_kline_data(code) # 封装内部方法
    except Exception as e:
        # 捕获后向上抛出，让 node 层感知到以启动 fallback 方案
        raise DataUnavailable(f"无法获取股票 {code} 的 K 线数据，无法计算 ATR") from e
```

---

## 5. 评审结论

| 评估维度 | 评分 | 核心理由 |
|---|---|---|
| **架构合理性** | **8 / 10** | 客观数据层与 Agent 层分界线清晰，不污染原项目。 |
| **高并发稳定性** | **4 / 10** | 简单 Semaphore(1) 无法抵挡东财高频短间隔请求，极易导致 403。 |
| **可实施性 (Phase 1)** | **9 / 10** | 路由和数据契约非常明确，React 19 自定义 chat 的选型避开了 CopilotKit 的坑。 |
| **数据安全性** | **9.5 / 10** | 全本地 SQLite，且 db 路径已被 .gitignore 完美隔离。 |

> [!IMPORTANT]
> **结论：有条件批准 (Approved with Reservations)**。  
> 必须在启动具体代码编写前，对**漏洞一（频控延迟）**、**漏洞二（美港股量化降级）**和**漏洞四（SSE 数据/文本分离）**的修补建议予以确认，并将修改后的契约应用到后续的实施计划中。
