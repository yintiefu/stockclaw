<p align="center"><b>简体中文</b> | <a href="README_en.md">English</a></p>

<h1 align="center">Vibe-Research · 个人 AI 投研系统（A股/美股/港股）</h1>

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![React 19](https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=white)](https://react.dev/)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![GitHub stars](https://img.shields.io/github/stars/simonlin1212/Vibe-Research?style=social)](https://github.com/simonlin1212/Vibe-Research/stargazers)
[![官网 viberesearch.wiki](https://img.shields.io/badge/🌐_官网-viberesearch.wiki-F35D2B?style=flat)](https://viberesearch.wiki)
[![English README](https://img.shields.io/badge/📖_English-README-1F6FEB?style=flat)](README_en.md)

<p align="center">
  <a href="https://viberesearch.wiki">官网</a> ·
  <a href="#产品预览">产品预览</a> ·
  <a href="#功能">功能</a> ·
  <a href="#数据源data-sources">数据源</a> ·
  <a href="#快速开始">快速开始</a> ·
  <a href="#设置">设置</a> ·
  <a href="#合规">合规</a> ·
  <a href="#相关生态">相关生态</a>
</p>

> **Vibe-Research: Your Personal Trading Research Agent** · A股 / 美股 / 港股 的个人投研 Agent。
>
> 每日复盘、资讯雷达、个股数据、自选股、板块中心、我的持仓、我的研报、研究记录。把数据和功能配齐，由**你自己的 AI** 驱动投资研究。

Vibe-Research 是一个开源的「个人 AI 投研看板」，**主推 A 股、兼看美股 / 港股**（A 股常要看隔夜外围脸色，数据配上更全）。它不替你做决定——把行情、研报、估值、财务、公告、资金面、资讯都配齐，放进一个干净的看板，再留一个能接入**你自己的 AI** 的接口。方向和结论，交给你自己配置的模型 / agent。

> *Vibe-Research: Your Personal Trading Research Agent. An open dashboard for China A-share (plus US / HK): it wires up all the data and plugs into **your own AI / agent** — it never recommends a stock. You bring the model, it brings the data.*

## 产品预览

**每日复盘** — 大盘 / 短线情绪(连板股 · 成交额 TOP20) / 板块资金一屏看全，一键交给你的 AI 复盘

![Vibe-Research 每日复盘](docs/screenshots/daily-review.png)

<table>
<tr>
<td width="50%">

**个股数据** — 财报速览 + 估值分位 + 资金面一屏看穿

![个股数据](docs/screenshots/stock-detail.png)

</td>
<td width="50%">

**资讯雷达** — 12 赛道 108 个公开源，一键提炼今日要点

![资讯雷达](docs/screenshots/intel.png)

</td>
</tr>
</table>

---

## 功能

每个页面的具体模块：

| 页面 | 包含的模块 / 能力 |
|---|---|
| 📊&nbsp;**每&#8288;日&#8288;复&#8288;盘** | 大盘指数 · **全球市场**（隔夜美股道指 / 标普 / 纳指 + 港股恒指 / 恒生科技）· 关注股票（自选实时行情）· **短线情绪**（连板股 / 最高连板 / 连板梯队 / 封板率 / 炸板率 / 晋级率）· **全市场成交额 TOP20** · 市场情绪（大盘宽度 / 题材投机 / 涨跌停）· 板块资金趋势榜 · 资金轮动 · AI 当日复盘 |
| 📡&nbsp;**资&#8288;讯&#8288;雷&#8288;达** | 12 赛道 108 个公开 RSS 源 · AI 一键提炼「今日要点」· A 股公告 / 公开新闻（挂钩你的关注列表）|
| 🔍&nbsp;**个&#8288;股&#8288;数&#8288;据** | **A 股**：行情 · 估值矩阵（前向 PE / PEG）· **财报速览** · 估值历史分位 · 财务关键指标 · 研报 · 公告 · 新闻 · **资金面**（融资融券 / 股东户数 / 主力资金流 / 分红 / 大宗交易）· 龙虎榜 · 限售解禁 · 板块归属 · 热门概念 · 互动易问答。**美股 / 港股 / 韩股**（输 `AAPL` / `00700` / `005930.KS`）：行情 · 总市值 · 关键财务指标（营收 / 净利 / EPS / ROE / 毛利率 / 负债率；韩股仅行情）|
| ⚔️&nbsp;**多&#8288;空&#8288;辩&#8288;论** | **多 agent**：后端先拉一份客观事实底稿（13 项数据），再让**多方研究员 / 空方研究员**基于同一份数据各自立论（可选交叉反驳），最后由**中立主持**归纳「双方共识 / 真正的分歧点 / 验证清单 / 数据缺口」。**刻意不产出买卖结论**。<br>⏱ 比问答重：一轮约 100 秒 / 3 次模型调用，**跑之前先看下方「一次辩论的开销」** |
| ⭐&nbsp;**自&#8288;选&#8288;股** | **批量粘贴一串代码即加**（逗号 / 空格 / 换行都行）· 一屏表格总览（现价 / 涨跌 / PE / PB / 换手）· **实时行情开关**（右上角，默认关；开了在交易时段每 3 秒自动刷新，非交易时段与页面切走时自动暂停）· 一键交给 AI 读。只存本地 |
| 🧩&nbsp;**板&#8288;块&#8288;中&#8288;心** | 板块 + 产业链环节骨架 |
| 💼&nbsp;**我&#8288;的&#8288;持&#8288;仓** | 录入即实时盈亏 · 已清仓记录（只存本地、不上传）|
| 📄&nbsp;**我&#8288;的&#8288;研&#8288;报** | **拖拽 / 多选上传**自己的研报（PDF / Word / txt / 表格 / 图片）· 按文件名**自动分行业**归档 · 下载 / 删除。**只存本地部署目录、不上传、不进仓库** |
| 📝&nbsp;**研&#8288;究&#8288;记&#8288;录** | 复盘 / 今日要点 / 问 AI / 辩论结果本地沉淀，随时回看 · **反思审计**：让 AI 回头审这段推理——哪些结论有数据撑着、哪些是脑补、最脆弱的一环在哪、要验证得看什么 |
| 🔌&nbsp;**接&#8288;入&nbsp;AI** | 只读状态页：模型只配置在服务端 `settings.json`（权限 0600）· 缺配置时一键复制模板 + 启动指引 · MCP（挂进 Claude Code 等 agent）|

> **投研分析框架**：让 AI 分析个股时，自动按 估值 / 资金面 / 财报质量 / 行业景气 / 事件催化与风险 五维组织结论——框架只规定「怎么读数据」、不规定买卖，方向仍由你自己的 AI 决定。
>
> 连板股 / 成交额榜等均为**客观公开榜单数据，只呈现事实、不推荐、不预测**。

## 数据源（Data Sources）

Vibe-Research 把三套公开数据源**直接集成进仓库**——`git clone` 下来**开箱即用，无需另外下载、接线**。

### A 股全栈数据 · AStockData

- **就在本仓库的 [`a-stock-data/`](a-stock-data/) 文件夹里**（v3.6.0）。十层数据架构、47 个端点、15 个数据源，`a-stock-data/SKILL.md` **内嵌全部调用代码**，自包含、零第三方数据封装依赖，东财接口已内置限流防封，主源被封还能降级到备用源。
- **覆盖**：行情 / K线 / 研报 / 一致预期 / 估值 / 历史分位 / 财务三表 / 公告 / 龙虎榜 / 融资融券 / 大宗交易 / 股东户数 / 分红 / 资金流 / 解禁 / 概念板块 / 打板情绪 / ETF 期权 / 互动易 / 全市场行业排名 …
- **给 agent 用**：用 Claude Code 等 agent 跑本仓库时，要调 A 股数据就看 [`a-stock-data/SKILL.md`](a-stock-data/SKILL.md)——每个接口都有 copy-paste 即用的代码。Vibe-Research 后端的数据层（`backend/astock.py`）也是从它移植的。
- **运行依赖**：`pip install mootdx requests pandas stockstats`（自包含，v3.0 起已移除 akshare 依赖）。
- **更新 / 上游**：<https://github.com/simonlin1212/a-stock-data> —— 想跟进最新端点、扩数据源，去这里看；**但即便你不更新，仓库自带的这份也是固定可用的快照，可以一直用。**

### 美股 / 港股数据 · global-stock-data

- **就在本仓库的 [`global-stock-data/`](global-stock-data/) 文件夹里**（v2.0.3）。13 层数据架构、30+ 个端点、11 个数据源、零鉴权，覆盖美港股行情 / K线 / 技术指标 / 三表财报 / 资金流 / 期权（CBOE 官方期权链含完整希腊字母与 0DTE 流）/ FINRA 空头成交量 / SEC EDGAR 申报流与全市场筛选。每个数据源都标注了合规级别。
- 后端 `backend/gstock.py` 移植了**东财域内的合规子集**：全球指数（每日复盘「全球市场」栏）+ 美港股个股行情 & 关键财务指标（个股页输 `AAPL` / `00700` 即用）。东财调用复用 `astock.em_get`（直连优先，避开科学上网代理挂国内站）。
- **韩股**：东财已覆盖，个股页输 6 位代码**加 `.KS` 后缀**即可（如三星 `005930.KS`、SK 海力士 `000660.KS`）。⚠️ 韩股代码与 A 股同为 6 位数字，**必须带 `.KS` 后缀**才能被识别为韩股（否则按 A 股处理）；东财对韩股仅给行情、无财务。台股走美股 ADR（如台积电 `TSM`）。
- **上游**：<https://github.com/simonlin1212/global-stock-data> —— 想要 K线 / 技术指标 / 期权 / SEC 等全量端点，去这里看。

### 全球资讯 · investment-news

- 12 赛道 108 个公开 RSS 源，已并入 `backend/newsradar.py` + `backend/news_sources.json`：纯标准库、零 key、已按合规词表过滤（剔除赌 / 预测市场 / 加密等）。
- **上游**：<https://github.com/simonlin1212/investment-news>

> 数据均来自公开源。Vibe-Research 只做客观信息整理与公开榜单呈现（连板股 / 成交额榜等，与东财 / 同花顺同款客观数据），**只呈现事实、不推荐个股、不预测涨跌、不给买卖时机、不做主观评分**；用这些数据做什么分析、看什么方向，由你和你自己的 AI 决定。

## 架构

一套数据层 + 两条 AI 出口：

```
Vibe-Research/
├── a-stock-data/      A 股全栈数据工具箱（数据源，v3.6.0，自带即用）
├── global-stock-data/ 美股 / 港股数据工具箱（数据源，v2.0.3，自带即用）
├── backend/           FastAPI :8900（数据/业务 API）+ 本地 LangGraph Server :2024（全部 AI）
│   ├── astock.py        A 股数据（移植自 a-stock-data）
│   ├── gstock.py        美股 / 港股数据（移植自 global-stock-data）
│   ├── newsradar.py     资讯雷达（移植自 investment-news）
│   ├── market.py        市场情绪 + 板块资金流 + 全球指数
│   ├── portfolio.py     持仓 + 已清仓（存本地用户目录）
│   ├── tools.py         AI 工具层（24 个数据工具，全部 Graph 与 MCP 共用）
│   ├── mcp_server.py    MCP server（给 Claude Code 等 agent）
│   ├── langgraph.json   六个 LangGraph 图注册（agent / embedded_agent / debate /
│   │                    reflection / daily_review / news_digest）
│   └── agent/           统一 AI 运行时：模型工厂 + 中立策略 + 工具执行器 + Skill 库
│                        + 工作流 YAML 加载/编译（agent/workflows/*.yaml）
└── frontend/          Vite + React 19 + TS + Tailwind（玻璃暖橙主题）:5899
```

**分级依赖**：行情（腾讯）+ 研报 / 公告（东财）**秒装可用**；akshare / mootdx 惰性导入，缺失时对应端点返回 501 + 安装提示，不拖垮服务。

## 快速开始

需要 **Python 3.11+**（Agent 工作台的 LangGraph 运行时要求）。`langgraph dev` 不会自动安装依赖，
务必先 `pip install -r requirements.txt`。

```bash
# 数据 / 业务后端（FastAPI :8900，客观行情、持仓、研报等）
cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8900

# AI 运行时（本地 LangGraph Server :2024，六个图；只用 127.0.0.1，绝不绑局域网/公网地址）
.venv/bin/langgraph dev --host 127.0.0.1 --port 2024 --no-browser

# 前端（Vite :5899，/api 代理到 8900、/agent-api 代理到 2024）
cd ../frontend && npm install && npm run dev
# 浏览器打开 http://localhost:5899
```

## 设置

所有模型调用统一走**本地 LangGraph Server**（`127.0.0.1:2024`）上的六个图：
`agent`（工作台）、`embedded_agent`（页面问 AI）、`debate`（多空辩论）、
`reflection`（反思审计）、`daily_review`（每日复盘）、`news_digest`（资讯提炼）。
模型与密钥**只**来自一份本地静态配置文件（默认 `~/.vibe-research/agent/settings.json`，
可用 `VR_AGENT_SETTINGS` 覆盖），Agent Server 启动时读取一次，修改后需重启：

```bash
mkdir -p ~/.vibe-research/agent/skills
# 把下面模板写到 ~/.vibe-research/agent/settings.json（apiKey 填你自己的）
chmod 600 ~/.vibe-research/agent/settings.json
```

```json
{
  "model": {
    "provider": "openai",
    "name": "your-model",
    "apiKey": "YOUR_API_KEY",
    "baseURL": "https://your-provider.example/v1",
    "temperature": 0.2,
    "thinking": false
  },
  "skills": { "path": "~/.vibe-research/agent/skills" },
  "mcpServers": {}
}
```

要点：

- **浏览器不再保存任何模型密钥**。前端「AI 接入状态」页只读展示脱敏摘要
  （模型名 / 主机 / Skill 与 MCP 数量 / 配置路径 + 重启指引），模板里的
  `YOUR_API_KEY` 只是占位符。旧版浏览器里的 `vr-llm` / `vr-askai-chat:*`
  值属于迁移前数据：新代码不读取、不迁移、也不删除。
- **FastAPI 只保留数据 / 业务 API**（行情、持仓、研报、资讯雷达、只读
  `/api/agent/status`）。公网部署设 `VR_API_KEY` 保护的是 FastAPI；
  它**不**保护 LangGraph——`/agent-api` 只是本机开发代理，公网部署
  绝不要把它反代出去。
- **MCP（给 Claude Code / 高手 agent）**：把后端挂成 MCP server，agent 用自己的
  订阅额度调 Vibe-Research 的数据工具、多步分析。命令见
  [`backend/README.md`](backend/README.md)。要更全量的 A 股数据端点，用根目录
  [`a-stock-data/`](a-stock-data/SKILL.md) 工具箱。
- **会话与历史就在本机**：问 AI / 辩论 / 反思 / 复盘 / 提炼的历史保存在 LangGraph
  的本地检查点里（页面刷新可恢复，各业务页面自己的历史区可查看 / 重新运行 /
  显式删除）；旧浏览器对话键不迁移。

## 多 agent 是怎么设计的

开源的多 agent 金融框架（TradingAgents、ai-hedge-fund 等）流程末端都有一个 trader /
portfolio_manager 角色，产出「买 / 卖 / 仓位多少」。**本项目刻意不做那一层。**

这里的多 agent 终点是**分歧**，不是结论：

```
① 事实底稿   后端按固定清单拉 13 项客观数据（不经 LLM）
              ↓  多空双方吵的是同一份数据，谁也不能靠编数字赢
② 多方研究员  基于底稿立论：核心论点 + 支撑证据 + 逻辑成立的前提
③ 空方研究员  基于底稿质疑：核心质疑 + 风险证据 + 逻辑成立的前提
   （可选）    交叉反驳：逐条回应对方，承认的明说，能反驳的给数据
④ 中立主持    双方共识 / 真正的分歧点（是数据不足还是解读不同）/ 验证清单 / 数据缺口
```

几个刻意的约束：

- **底稿先行，不让模型自己想起来调哪个工具**。数据确定、可复现，缺项会如实写进底稿并要求「不得臆测」。
- **每条论点必须标出所依据的具体数据**，没有数据支撑的要自己标注「无数据支撑」。
- **中立主持不裁决谁对**，也不给评级或倾向——它的产出是「你接下来该去看什么」。
- 走限流接口的数据项**保持串行抓取**：`em_get` 的防封节流靠时间戳而非锁，并发会击穿它。

### 一次辩论的开销（**跑之前先看这里**）

辩论比「问 AI」重得多——它要跑一整套流程，而且**每个角色都会带上完整底稿**。实测数据：

| | 一轮（各自陈述） | 两轮（加交叉反驳） |
|---|---|---|
| 模型调用次数 | **3 次** | **5 次** |
| 送进模型的内容 | 约 3.5 万字 | 约 6 万字 |
| 模型产出 | 约 4 千字 | 约 7 千字 |
| 耗时 | **约 100–120 秒** | 约 3 分钟 |

其中拉底稿约占 35 秒（打十几个公开数据接口，**不消耗任何 token**），剩下是模型生成时间。

**想省钱 / 省额度，按这个顺序：**

1. **默认用一轮就够**。两轮只在你真想看双方对轰时用——它把开销直接翻倍。
2. **辩论不需要贵模型**。数据已经在底稿里备齐了，模型只做组织和表达，中档模型足够；
   把预算留给你自己的深度提问。
3. **别连续狂点**。底稿要打十几个公开接口且带节流，短时间反复触发既慢也容易被上游限流。

> 换算成 token 大致是：一轮约 3–4 万输入 + 4–5 千输出（中文按 1 字≈1 token 粗估，
> 实际随模型 tokenizer 浮动）。按主流模型的价格，一轮通常在几分钱到几毛钱之间——
> 但如果你用的是高价模型或跑两轮，成本会明显上去，心里有个数。

### 反思审计

同一个思路的延伸：对一段已写好的分析做推理审计，挑出「听起来合理但没有依据」的部分。
实测能揪出诸如「获得机构广泛认可」（用三家推断整体）、「频繁上调预期」（未量化）这类似是而非的表述。

开销小得多——**只有 1 次模型调用**，输入就是你选中的那段文本（超过 1.2 万字会自动截断并提示）。

## α-mind 与统一 AI 工作流

「α-mind」和全部 AI 对话 / 分析工作流都由同一个**本地 LangGraph Server**
（`127.0.0.1:2024`，与 FastAPI 分离启动）驱动：线程、运行、检查点与人工审批（HITL）
全部由它原生持久化。α-mind 是自由对话 + MCP / Skills 的通用 agent；页面内「问 AI」、
多空辩论、反思审计、每日复盘、资讯提炼是五个独立图，各自隔离持久化——工作流历史只在
对应业务页面查看（辩论在辩论页、反思在研究记录页、复盘 / 提炼在各自页面），不进 α-mind
会话列表，也没有全局历史页。

Agent 按轮调用你配置的模型与工具（内置行情/资讯工具 + 你接入的 MCP 服务器，配合本地
Skills 库）。它和整个产品一样**只整理客观数据与分析框架，不给买卖结论**——辩论止于
分歧点，α-mind 止于「可核对的事实」。配置文件见上文「设置」。

要点：

- **密钥只在本地设置文件**：模型/MCP 密钥以明文保存在 `settings.json`，请执行
  `chmod 600 ~/.vibe-research/agent/settings.json`（权限过宽时服务启动会在 stderr 提醒）。
  它们不进入线程元数据、检查点、日志或前端请求；旧版浏览器里的 `vr-llm` 等键不再被读取（保留在磁盘、无人使用）。
- **会话数据在用户目录**：`scripts/dev` 托管启动的 Agent Server 以
  `~/.vibe-research/agent/server/` 为工作目录——里面是自动生成的绝对路径
  `langgraph.json` 与 `.langgraph_api/` 会话数据（运行时把存储路径硬编码为
  cwd 下的 `.langgraph_api/`，无 env 覆盖项），仓库内不落任何运行时文件。
  若手动 `cd backend && langgraph dev` 裸跑，数据会落在 `backend/.langgraph_api/`
  （已 gitignore），与用户目录互不可见。
- **MCP 逐次审批**：外部 MCP 工具默认需要批准，每次只有「批准 / 拒绝」两种决定；
  被拒的调用以拒绝结果回到对话，不会静默执行。
- **Skills 只读且限制在配置根目录**：模型经 `ls` / `read_file` 渐进读取本地技能库
  （先见名称与描述，按需读全文），路径逃逸（`..` 越出根目录）会被拒绝。
- **CORS 边界（如实说明）**：Agent Server 的来源白名单只允许本地前端
  （`http://127.0.0.1:5899`）。它阻止无关网站**读取**响应，但不是鉴权、也不是 CSRF 防护——
  浏览器 simple request 仍可盲写线程/提交运行、消耗模型与数据源额度。此残余风险仅在
  loopback 单用户场景下接受，因此 Agent Server 绝不要绑定局域网/公网地址。
- **思考过程展示（thinking）**：`model.thinking` 设为 `true` 后（默认关闭），请求会带
  上游思考参数（智谱 GLM 系），模型的 `reasoning_content` 在对话中以「思考过程」折叠区
  展示。注意：思考计入 output tokens（更贵更慢）；含思考的历史消息回传上游前会自动剥离
  thinking 块（第三方接口对未知内容块会 400）；仅对支持 `reasoning_content` 的第三方
  OpenAI 兼容上游生效（官方 OpenAI 无此字段）；修改后需重启 Agent Server。
- 旧版自定义 Agent 会话（JSON 文件）**不迁移**：升级后工作台从空列表开始，原文件保留在磁盘上。
- **调用链路追踪（JSONL）**：默认开启（`trace.enabled`）。每个 run 的执行流程——模型调用
  （耗时、model、token 用量、tool_calls）、工具调用（入参、结果预览、状态）、HITL 拒绝——
  实时追加写入 `~/.vibe-research/agent/traces/<thread_id>.jsonl`（每线程一文件，可用
  `trace.dir` 覆盖），可 `tail -f` / `jq` 消费。终端查看：`scripts/dev trace`（列线程）与
  `scripts/dev trace show <thread_id>`（按 run 分组的时间线，`show --raw` 输出 jq-friendly
  原文，`--traces-dir` 覆盖目录）。注意：
  - 追踪文件含对话与工具入参/结果的**明文**，与 `settings.json` 同一隐私边界（本机、
    绝不提交仓库）；密钥不会写入（事件只含消息内容与工具数据）。
  - 自定义 baseURL 的流式上游常无 usage chunk，`input_tokens` / `output_tokens` 记
    `null` 是正常形态，消费方不得假定非空。
  - 被中断（等待审批）的 run 没有末尾 `run_end` 事件；恢复（resume）产生新 run_id。
  - 追踪写入失败绝不影响 agent 运行：首错熔断、整个进程只打一行 stderr 告警。
  - 只记录启用后的新 run，历史会话不补录。

## 测试

```bash
cd backend && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest -m "not live"   # 离线单测 + API 校验（快、稳，无需联网）
.venv/bin/pytest -m live          # 联网核对数据源 shape（升级 / 发布前跑一遍）

# 前端
cd ../frontend && npm install
npm test          # node --test 页面冒烟
npm run test:unit # vitest 组件/逻辑单测
npm run build     # tsc -b && vite build
```

### Agent 工作台浏览器测试（Playwright）

三个隔离服务（FastAPI :8873 + LangGraph :2873 + Vite :5873），LangGraph 用确定性脚本化模型与
stdio 假 MCP，数据根为每次运行新建的临时目录，**不触碰 8900/2024 端口的常规服务，也不读你的
`~/.vibe-research/`**：

```bash
cd frontend
npm run test:e2e:install   # 首次：安装 Chromium（受限网络可设 PLAYWRIGHT_DOWNLOAD_HOST 指向可达镜像）
npm run test:e2e          # 串行跑完整交互矩阵 + 桌面/移动截图 + 代理与 CORS 断言
```

## 更新日志

见 [CHANGELOG.md](./CHANGELOG.md)。版本号唯一来源是 `frontend/package.json`，后端 API / 前端界面 / MCP `serverInfo` 全部从它读取。

## 合规

- 只做客观数据整理与公开榜单呈现：**不荐股、不预测涨跌、不给买卖时机、不承诺收益、不做主观评分**；中立无倾向。
- 连板股 / 成交额榜等均为**客观公开榜单数据**（东财 / 同花顺同款），产品只如实呈现、不附带任何推荐或预测。
- 所有分析方向由你自己配置的 AI 给出，与本产品无关。UI 无买卖按钮；估值历史分位只标位置、不划买卖线。
- **持仓 / 关注股 / 上传的研报 / API key 只存本地，不上传、不进仓库。**
- 持仓与上传的研报默认存在**用户目录 `~/.vibe-research/`**（可用环境变量 `VR_DATA_DIR` 换根目录、`VR_REPORTS_DIR` 单独指定研报目录）——在项目文件夹之外，**重新下载 / 覆盖更新项目文件夹不会丢数据**；旧版本存在 `backend/.cache/` 的数据，新版首次启动自动迁移（复制，原文件保留）。

## 相关生态

Vibe-Research 用到的数据 / 工具，来自同一套自研开源体系（都在 [`simonlin1212`](https://github.com/simonlin1212)）：

| 仓库 | 定位 |
|---|---|
| [**a-stock-data**](https://github.com/simonlin1212/a-stock-data) | A 股全栈数据工具包（10 层 · 44 端点 · 15 数据源）—— 本项目的 A 股数据引擎 |
| [**global-stock-data**](https://github.com/simonlin1212/global-stock-data) | 美股 / 港股全栈数据工具包（13 层 · 30+ 端点 · 11 数据源） |
| [**investment-news**](https://github.com/simonlin1212/investment-news) | 全球产业链资讯看板（12 赛道一一对应 A 股板块）—— 本项目的资讯源 |
| [**Agent-Staff**](https://github.com/simonlin1212/Agent-Staff) | 把公司 Agent 化：每部门一个 AI agent + CEO 参谋长，常驻飞书 |

## 联系作者

作者 **Simon**，独立开发者。

- 🐦 X：[@linsizhen](https://x.com/linsizhen)
- ✉️ 邮箱：<simonlin0423@gmail.com>
- 💬 欢迎交流**企业 AI 落地方案**；项目相关问题也可提 [Issue](https://github.com/simonlin1212/Vibe-Research/issues)。

## 致谢

- A 股数据引擎：[a-stock-data](https://github.com/simonlin1212/a-stock-data)（作者：Simonlin1212）
- 美股 / 港股数据引擎：[global-stock-data](https://github.com/simonlin1212/global-stock-data)（作者：Simonlin1212）
- 资讯：[investment-news](https://github.com/simonlin1212/investment-news)（作者：Simonlin1212）
- 界面设计语言参考并致谢：[HKUDS/Vibe-Trading](https://github.com/HKUDS/Vibe-Trading)（作者：HKUDS · 仅借鉴 UI，底层为全新实现）

## 免责声明

本项目仅供学习与研究，**不构成任何投资建议**。看板只做客观数据整理与公开榜单呈现——不推荐个股、不预测涨跌、不给买卖时机、不承诺收益；所有分析方向由你自己配置的 AI 给出，与本产品无关。股市有风险，请独立决策、自行核实，风险自担。

## 赞赏

如果这个工具帮到了你，欢迎请作者喝杯咖啡。

<p align="center">
  <a href="https://buymeacoffee.com/simonlin1212"><img src="./assets/bmc-qr.png" width="180" alt="Buy Me a Coffee"></a>
</p>

## License

MIT
