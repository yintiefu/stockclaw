# Changelog

本项目的版本号唯一来源是 `frontend/package.json`；后端 HTTP API、`/api/health`、
前端界面与 MCP `serverInfo` 全部从它读取（见 `backend/version.py`）。

## 未发布 — 2026-08-27：精简 Agent 自定义面（重构迭代第一轮）

- 删除遗留死代码 `agent/client.py`（自研 SSE 客户端）及其测试：旧 FastAPI AI 路由
  （`/api/chat` 等）下线后已无任何生产调用方，前端直连 LangGraph Server。
- debate 底稿 13 项固定契约（工具、参数、空值策略、执行策略）从 `workflow_loader`
  的运行时校验外移为 pytest 契约测试：加载器只做通用 schema 校验，防回归不变量由
  `tests/agent/test_workflow_loader.py` 的契约比对在 CI 兜底；同时解除 loader 对
  `tool_executor` 的反向依赖。

## 未发布 — 2026-08-24：Agent 思考过程展示（thinking 开关）

- `settings.json` 的 `model.thinking`（默认 `false`）：开启后请求带上游思考参数，模型
  `reasoning_content` 增量转成 thinking content block，前端以「思考过程」折叠区实时展示。
- 新增 `ReasoningChatOpenAI`：`ChatOpenAI` 会丢弃第三方非标准字段 `reasoning_content`，
  子类在流式 chunk 转换处保留并转块；历史回传上游前 thinking 块自动剥离（智谱等对
  未知内容块返回 400）。
- 思考计入 output tokens；仅对支持 `reasoning_content` 的第三方 OpenAI 兼容上游生效。

## 未发布 — 2026-08-24：Agent 会话调用链路追踪（JSONL）

- 新增 `SessionTraceMiddleware`：每个 run 的模型调用（耗时 / token / tool_calls）、工具调用、
  HITL 拒绝实时追加写入按线程组织的 `~/.vibe-research/agent/traces/<thread_id>.jsonl`，
  可 `tail -f` / `jq`；`settings.json` 的 `trace.enabled` / `trace.dir` 可关可改。
- 终端查看：`scripts/dev trace`（列线程）与 `scripts/dev trace show <thread_id>`
  （时间线 / `--raw`）。
- 追踪写入失败不影响 agent 运行（首错熔断，仅一行 stderr 告警）；只记录启用后的新 run。

## 未发布 — 2026-08-24：Agent 工作台迁移到本地 LangGraph Server

Agent 工作台的自定义 FastAPI/AG-UI 运行时整体替换为 **assistant-ui + 本地 LangGraph Server**
（与 FastAPI 分离启动，`127.0.0.1:2024`，仅回环地址）：

- **原生线程 / 检查点 / 审批**：会话、运行、检查点与 MCP 人工审批（HITL）由 LangGraph Server
  原生持久化，进程重启后普通会话与待审批断点均可恢复；前端经 `useStreamRuntime` + LangGraph SDK
  线程适配器直连。
- **静态本地配置**：模型 / MCP / Skills 全部来自一份本地设置文件（默认
  `~/.vibe-research/agent/settings.json`，`VR_AGENT_SETTINGS` 可覆盖；含明文密钥，建议 `chmod 600`），
  Agent Server 启动时读取一次，修改后重启生效。请求级模型配置与请求头密钥转发已移除。
- **三个本地服务**：`uvicorn app:app`（:8900）+ `langgraph dev`（:2024）+ `npm run dev`（:5899）；
  Python 3.11+，`langgraph dev` 不会自动安装依赖。
- **旧会话不迁移**：旧版自定义 Agent JSON 会话不读取也不删除，升级后工作台从空列表开始。
- **移除的能力**：Inspector、Artifact / 来源标签、预算治理（Policy/会话配额）、MCP/Skills 管理
  界面与「本次/本会话」审批粒度——MCP 审批收敛为逐次「批准 / 拒绝」。
- **不变的部分**：传统 chat / debate / reflect 仍走 FastAPI 且保持请求级模型配置与 SSRF 行为；
  `tools.py` 的 24 个数据工具为全出口共用，Eastmoney 节流仍保持串行（进程级锁）。

## v0.3.1 — 2026-08-09

三个用户报告的 bug + 版本号治理。感谢 [@lihaoran0412](https://github.com/lihaoran0412)
一口气提了三份带根因和文件行号的报告，质量很高。

### 修复：`query_market scope=turnover` 字段全为 null（#28）

`tools.py` 按 `turnover` / `changePct` 取字段，而 `astock.market_turnover_rank()`
实际返回的是 `price` / `pct` / `amount` / `mcap` / `float_cap` / `industry`——键名对不上，
每条只剩 `name` 和 `code`，其余一片空白。已对齐字段名，实测 20 条全部有值。

### 修复：Windows 下 MCP server stdout GBK 编码崩溃（#27）

Windows 上 Python 的 stdio 默认编码是 GBK(cp936)。JSON-RPC 响应里带中文、RSS 正文里的
`\xa0`（不换行空格）等字符 GBK 编不出来，**整条响应写不出去**——客户端表现为工具调用
失败 + 反复重连；即便不崩，中文也会被按 UTF-8 解 GBK 字节，全是乱码。

`mcp_server.main()` 现在在读写任何协议内容之前把 stdio 钉死成 UTF-8。选择重配而不是
退让成 `ensure_ascii=True`：后者能防崩，但会把中文全变成转义序列、体积翻几倍，而 MCP
协议本身就要求 UTF-8。

### 改进：证券搜索加备用端点，并区分「接口不可用」与「查无此票」（#26）

报告者的环境下美股/港股/韩股查询全部失败，而产品只回一句「未找到对应代码」，
他只能自己逆向排查到底哪一步坏了。

⚠️ **该接口从我们这边实测是正常的**（AAPL / 00700 / TSLA 均能解析），所以更可能是
IP 风控或链路问题，而非接口下线。但暴露出的两个真问题已修：

- **`except Exception: return None` 把两种情况压成一个返回值**——"这只票不存在"和
  "接口请求失败"从此不可区分。现在后者抛 `SearchUnavailable`，带上真实的底层错误，
  并明说「这与查无此代码是两回事」。
- **单一端点故障会让整块功能瘫痪**——新增备用端点 `searchadapter.eastmoney.com`，
  主端点失败自动切换。**并且必须校验响应结构再收手**：主端点返回「合法 JSON 但没有
  `QuotationCodeTable`」（接口改版 / 风控页 / HTTP 错误页，`em_get` 不做
  `raise_for_status`）时会被误当成"查得到但没匹配"，备用端点根本轮不上——而这恰恰
  就是本 issue 描述的情形，不校验的话这次修复等于没修。

### 修复：MCP `serverInfo` 版本号仍写死 `0.2.2`（#20 补漏）

#20 只列了 3 处硬编码，照着改会漏掉第 4 处：MCP 客户端初始化拿到的还是旧版本。
现抽出 `backend/version.py` 作为唯一读取点，四处同源。

**刻意独立成模块而不是从 `app` 导入**：`app.py` 在导入时会 `pf.start_scheduler(1800)`
起后台线程，MCP 服务只想拿个版本号，不该承担那个副作用。读取失败的警告走 **stderr**——
MCP 的 stdout 专供 JSON-RPC，往那儿打一行警告会插在初始化响应之前，客户端可能拒收整条流。

### 测试

`backend/` 90 passed（新增 11 例），含三条反向边界：GBK stdout 下不修就必崩（先证明坑
真实存在）、接口正常但查无此票仍返回 None、读不到版本号时 stdout 必须为空。

---

## v0.3.0 及更早

本文件自 v0.3.1 起维护；更早的版本历史见
[Releases](https://github.com/simonlin1212/Vibe-Research/releases)。
