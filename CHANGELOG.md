# Changelog

本项目的版本号唯一来源是 `frontend/package.json`；后端 HTTP API、`/api/health`、
前端界面与 MCP `serverInfo` 全部从它读取（见 `backend/version.py`）。

## 未发布 — 2026-08-28：工作流 AI 层迁移 v2 流（破坏性重构）

- 阶段正文从 `StageResult.content` 迁到 `messages` 通道（`add_messages` 按 id 归并），
  `StageResult` 退化为状态机 + `message_id` 指针；`result` 状态键删除。
- `run_stage` 传播节点 config：token 增量原生进入 messages 通道；每 token 一个
  custom 事件的旧机制与 seq/cursor/轮询对账协议整体删除。
- 重试改为独立 `resume` 控制通道（`{resume: true}`，绝不覆写 `input`）+ 后端
  `auto_resume` 版本门控（写入式拒绝：拒绝原因落 checkpoint `errors`，run 正常收尾
  ——节点 raise 在 inmem 上既无 lifecycle failed 事件也无 run.error 文本，前端只能靠
  checkpoint 感知）；取消不再客户端回写状态。
- 已知上游缺口：langgraph 1.2.11 的 v3 流路径不转发 `get_stream_writer` custom 事件
  （实测 13 发 0 收），底稿收集期进度 UI 降级为静态状态行；custom 通道接线保留，
  升级 langgraph 后需复测。
- **config_version 升至 2（状态 schema 破坏性变更）**：迁移前需对**仍在运行旧代码的
  Agent** 执行一次定向清理：

  ```bash
  node scripts/prune-workflow-threads.mjs   # 删除 channel=workflow 线程
  ```

  dev pickle 为六个图共用（`$VR_AGENT_WORK_DIR/.langgraph_api/`）——**禁止直接删
  .pckl 文件**，否则 workspace/embedded 历史一并丢失；`scripts/dev` 的
  pickle-backups 仅能整体回滚。

## 未发布 — 2026-08-27：精简 Agent 自定义面（重构迭代第一轮）

- 删除遗留死代码 `agent/client.py`（自研 SSE 客户端）及其测试：旧 FastAPI AI 路由
  （`/api/chat` 等）下线后已无任何生产调用方，前端直连 LangGraph Server。
- debate 底稿 13 项固定契约（工具、参数、空值策略、执行策略）从 `workflow_loader`
  的运行时校验外移为 pytest 契约测试：加载器只做通用 schema 校验，防回归不变量由
  `tests/agent/test_workflow_loader.py` 的契约比对在 CI 兜底；同时解除 loader 对
  `tool_executor` 的反向依赖。
- 页面级「问 AI」抽屉的传输层从手写 SSE 消费切换为 `@langchain/react` 的
  `useStream`（v2 流协议，已对本地 langgraph-api 0.12.6 冒烟验证：token 级流式、
  `page_context` 注入、断开后 Server run 继续跑完均通过）：
  - `embedded-client.ts` 只保留域逻辑（scope 线程搜索/创建/删除、submit 输入构造、
    流式消息 → 抽屉消息映射），删除「流结束后再拉一次 getState」的双读与
    AbortController 手工管理；
  - `AskAiButton.tsx` 直接消费 hook 合并后的消息（乐观上屏 + 权威 checkpoint 自动
    归并），关抽屉/换 scope/卸载只断开本地订阅，语义与之前一致（从不取消 Server run）；
  - 六图 Playwright 验收（含嵌入式问答 scope 持久化/快照版本/恢复/隔离/删除）全过。

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
  `tools.py` 的 25 个数据工具为全出口共用，Eastmoney 节流仍保持串行（进程级锁）。

## v0.4.0 — 2026-08-20

### 新增：「产业信号」页 · 第一个小栏目「GPU 租金」

新侧栏分页 `/signals`：每期从公开零鉴权数据源移植一个「一句话产业信号」小栏目、
发一个版本，本期上线第一个——GPU 租金，算力供需冷热的价格侧证据（供应链数据只说明
「货在走」，租金说明「装了之后卖不卖得掉」）。三条腿：

- **近一年走势**：B200 / H100 SXM / A100 SXM4 逐日中位价折线（约 365 点/条），
  数据来自 500.farm（Vast.ai 公开市场数据的社区统计站，exporter 开源）。
  口径＝按机型档位分组统计后聚合的中位（切片等权，非逐张挂单的精确中位——
  页面与工具文案均如实标注）。
- **现货**：三型号现货卡**直接取走势曲线的最后一个点**（同一次请求、同一条数据，
  与曲线数字严格一致），另附当前可租/总挂单卡数与在租率做规模读数。
  刻意不用 Vast bundles 直取挂单中位——它与曲线的统计算法不同，同一时刻会算出
  两个对不上的「中位价」（实测同屏 $8.13 vs $6.95），同名不同算的数不同屏。
- **远期（图表化）**：Kalshi 公开事件合约（KXB200MS，按 **Ornn 跨平台指数的整月平均**
  结算）**覆盖全部在市结算月**（当前 13 个月 / 123 张合约，日 K 小并发拉取 + 单档重试，
  约 40s）。顶部一张**预期曲线**：已结算月的实际落点（实线）接各结算月的市场预期中位
  （虚线）——同为 Ornn 月均口径，远期是涨是跌一眼看清；每月再给**概率分布柱图 +
  隐含预期中位价 + 最可能落点区间**，配一句话（预期多少钱、概率多少）。
  刻意**不**把远期画进 Vast 历史曲线——两者是不同市场、不同时间口径
  （「此刻挂单价」vs「整月均价」），拼在一条线上会误导。
- **随仓库分发数据快照**（`backend/data/signals_gpu_seed.json`，发版前刷新一份）：
  clone 打开就有截至发布日的完整历史与远期数据，不必先等 40 秒刷新；用户点刷新后
  以自己拉取的最新数据（`.cache/`）为准。
- **四条口径边界直接印在页面上**：历史曲线是每日定时采样、现货卡是「此刻」挂单，
  同源但不必逐点相等；现货与远期口径不同、数值不能直接对减；撮合市场看中位数、
  「无在租报价」是市场状态不是故障；前沿卡紧与旧卡松可以同时为真。
- **AI 工具层同步 +1**：`query_gpu_rent`（chat / MCP / 多空辩论三条出口共用，现共 25 个工具）。
- 后端 `signals.py`：纯标准库、零鉴权；缓存原子落盘 + 结构版本号；**失败纪律**——
  部分数据源失败时回填上一次的好数据并标 `stale` + 在页面出声，全部失败绝不覆盖好缓存。
- 前端新增按需引入的 ECharts 容器组件（`components/ui/EChart.tsx`）。
- 新增 38 项离线测试（解析 / 三分判别 / 概率推导 / 失败回填 / 分页 / API 契约）。

### 其它：侧栏导航组 + 内嵌数据源同步至上游最新

- **侧栏导航组**：资讯雷达 / 产业信号 / 板块中心三组均带小三角展开收起（状态按组记忆），
  子栏目缩进排列并有直达 URL（`/intel/:tab`、`/signals/:tab`）。资讯雷达子栏目顺序调整为
  Investment News → 公开新闻 → A股公告 → 事件概率。
- **`a-stock-data/` 同步至上游 v3.7.1**（十一层 54 端点 19 数据源：新增筹码分布 CYQ /
  估值历史 / 复权因子等，详见其 CHANGELOG）。
- **资讯雷达同步上游 investment-news v1.0.3**：① 源清单清掉 2 个跨栏重复源（108 → 106）；
  ② 移植**条目层去重**——同栏内同一 URL（剥跟踪参数归一化）只留一条，标题相同且发布
  时间相近（48h 窗口内）的转载只留最新一条；无发布时间的条目不做标题去重（判错的代价
  不对等——多显示一条只是冗余，误删就是永久丢一篇）。
- 文案清理：移除文档与注释中的网络环境相关敏感措辞（机制不变：数据层直连优先、
  失败自动降级系统网络设置）。

### 发布前修复（20 项数据准确性与健壮性）

- 阶梯概率先单调化再派生分布/隐含中位（报价噪声会让分布总和超过 100%）
- 过滤 Prometheus 非有限样本（NaN/Inf 进缓存会破坏接口的 JSON 序列化）；
  报价字段三态判定：缺席＝该侧无报价，非法数值＝故障冒泡，不再静默转 0
- Kalshi 列表跟随 cursor 翻页（该系列逐月挂新合约，超单页上限后不翻页会静默丢整月），
  cursor 按不透明 token 转义；日 K 只认最近 2 天（停止报价的低流动合约旧价
  不再被当成当前预期）；部分失败与「市场无报价」严格分开，防止空结果覆盖好缓存
- 刷新全程加锁（并发刷新不再出现旧数据覆盖新结果的丢更新）
- AI 工具输出裁剪进上下文上限（历史压成关键点摘要、已结算月只留最近 12 个），
  并携带数据新鲜度元数据；30/180 天前的取样按时间戳定位而非索引（序列有空洞时不偏移）
- 预期曲线只画有完整区间的已结算月（单边结果是开放区间，不画成精确点）；
  远期覆盖月数按实际有报价的数量展示；图表跟随容器尺寸变化（侧栏收起/展开即时自适应）
- 现货卡标注观测时点；口径文案统一为「分组统计聚合的中位」并在加载时以当前代码为准
- 文档运行依赖清单对齐 a-stock-data v3.7；另有两项上游问题已在各自仓库发布补丁版
  （a-stock-data v3.7.1 / investment-news v1.0.3）并同步回本仓库

> 数据源实测备注（三个坑都已写进 `backend/signals.py` 注释）：
> ① Kalshi 的 `/markets` 列表与 `/markets/trades` 已不再对未认证请求返回价格字段
> （连最活跃的系列也全 null），零鉴权取价走 **candlesticks**（日 K 自带 yes_bid /
> yes_ask 收盘价与持仓量）；② Vast.ai 反直觉地**不能带浏览器 User-Agent**（会 403）；
> ③ 型号名必须用**精确细分名**（`H100 SXM` 等）——写 `H100` 永远查不到数据，
> 看起来像「没人在租」，其实是查错了名字。

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
