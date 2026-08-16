# LangChain Agent 工作台 1D 完整设计

**日期：** 2026-08-16

**状态：** 已完成交互确认，待书面复审

**上游设计：** `docs/superpowers/specs/2026-08-15-langchain-agent-workspace-design.md`

**前置里程碑：** 1A、1B、1C 已实现；本设计以当前分支实际代码和 1C 验证结果为基线。

## 1. 目标

1D 完成第一期 Agent 工作台的最后一段：在不改变 1A-1C 协议和安全边界的前提下，加入可硬执行的运行治理、类型化 Artifact、来源追踪、完整三栏工作台与响应式交互。

本里程碑按三个可独立验证的切片交付：

1. 治理运行时：Policy、跨 resume 的预算、active deadline、同步工具容量、上下文裁剪和 token usage。
2. Artifact 与来源：类型、不可变版本链、`create_artifact` 工具、REST、来源追踪和崩溃对账。
3. 最终工作台：三栏布局、Inspector、设置抽屉、事件接入、Artifact 安全查看器和移动端体验。

三个切片都必须保持测试绿色；不得以“先展示、后执行”的方式交付虚假的预算功能，也不得先开放没有完整校验和持久化的 Artifact 工具。

## 2. 产品边界

1D 继续遵守 `VISION.md`：只提供客观数据、分析框架和研究工具。

- 不输出买卖建议、价格预测、目标价、评级、排名或择时信号。
- Artifact 只是研究材料，不是投资结论或推荐报告。
- Sources 只陈述信息来自哪个工具执行记录或哪个模型给出的 URL，不对来源做评分、排名或真实性认证。
- Agent 的中立 System Prompt、Capability lease、MCP 审批和 SSRF 防护不得弱化。
- 不修改旧 `/api/chat`、复盘、辩论、反思或 CLI 订阅行为。

## 3. 明确不做

- 不切换到显式 LangGraph `StateGraph`，继续使用 `create_agent`。
- 不恢复 32 次 Graph transition 限制。1A 已证明 LangGraph recursion count 会在跨 resume 调用时重置，不能作为产品 run 的硬限制。
- 不做模型自动摘要；上下文只做确定性的字符预算裁剪。
- 不估算货币费用。任意 OpenAI-compatible Provider 没有统一且可靠的价格表。
- 不从任意 assistant 文本隐式创建 Artifact；Artifact 只能由显式 `create_artifact` 工具创建。
- 不抓取、访问或验证模型给出的 URL。
- 不支持 HTML、SVG、JavaScript、React、公式执行或其他可执行 Artifact。
- 不做多 worker、多进程共享治理状态或持久化 LangGraph checkpoint。
- 不为已经存在的 thread/run 批量重写 JSON。

## 4. 当前基线与已验证事实

当前实现已经具备：

- `create_agent`、AG-UI bridge、服务端权威 thread 历史和 run JSON。
- start、resume、steer-away、retry、cancel、重复请求与 revision 合同。
- 无密钥 `CapabilityLease`、Skill 快照、MCP binding、参数守卫、HITL 审批和 thread-session allowance。
- 请求级 Graph/模型重建，同一产品 run 的 MemorySaver 在审批 resume 间复用。
- `RunUsage` 和 `budget_snapshot` 的持久化骨架。

基线验证结果：

```text
backend:       347 passed, 15 deselected
frontend node: 16 passed
vitest:        54 passed
build:         passed（只有既有 large chunk warning）
```

已确认的实现事实影响 1D 设计：

- 当前 `RunUsage` 是事后推断，token 默认为空，不能执行硬预算。
- 当前内置同步工具通过 `asyncio.to_thread` 进入默认 executor，缺少全局有界容量。
- MCP Registry 自身有 60 秒端到端调用限制；1D 的 30 秒默认值是更外层的产品 run policy，不能替换连接生命周期限制。
- `router.py` 和 `runs.py` 已经较大，新职责必须进入聚焦模块，只在原文件保留装配和生命周期接缝。
- LangChain 锁定版本的 tool wrapper 按 middleware 注册顺序“第一个最外层”组合。
- 当前 request middleware 顺序是附加 middleware、`McpArgumentGuard`、`HumanInTheLoopMiddleware`。1D 必须保留参数守卫与 HITL，不得让被拒绝或仍待审批的调用消耗工具预算。

## 5. 总体架构

```text
AgentWorkspace
  |-- Thread column
  |-- AgentThread / Composer
  |-- AgentInspector
  |     |-- Run
  |     |-- Approval
  |     |-- Artifact
  |     `-- Sources
  `-- AgentSettingsDrawer
        |-- Model
        |-- Skills
        |-- MCP
        `-- Policy

REST + AG-UI
  -> agent.router
     -> RunCoordinator
        -> ActiveRunHandle
           |-- immutable CapabilityLease
           |-- immutable PolicySnapshot
           |-- shared secret-free RunControl
           `-- MemorySaver
     -> GovernanceMiddleware
     -> BoundedToolExecutor
     -> ArtifactService / ArtifactStore
     `-> ProvenanceCollector
```

核心所有权：

- `ActiveRunHandle` 拥有一个产品 run 的 `RunControl`，从 start 到所有 resume 始终复用同一实例。
- `RunControl` 只持有 Policy 快照、计数、计时和遥测，不持有模型 key、MCP secret、session、真实 Skill 路径或无限工具原文。
- 每个 HTTP start/resume 仍重建请求级 Graph、模型和 middleware；新 middleware 引用同一个 `RunControl`。
- retry 和 steer-away 创建新产品 run，因此读取最新 Policy 并创建新 `RunControl`。
- Artifact 和来源属于产品 run/thread 存储，不进入 LangGraph checkpoint。

## 6. 文件与职责边界

后端新增或聚焦以下模块：

- `agent/policy.py`：Policy 模型、默认值、范围校验、CAS store、损坏状态和显式 reset。
- `agent/governance.py`：`PolicySnapshot`、`RunControl`、模型/工具 reservation、active segment、上下文裁剪、usage 归并和治理 middleware。
- `agent/tool_executor.py`：进程级有界同步 executor、容量令牌、deadline/cancel 和 shutdown。
- `agent/artifacts.py`：Artifact 类型校验、store、版本链、下载、删除和启动对账。
- `agent/provenance.py`：工具执行来源、URL 提取/规范化/去重和 200 条上限；后端把固定版本 CommonMark parser 声明为直接依赖，不依靠传递依赖。

现有模块只做必要接线：

- `models.py` 增加明确的 Policy、usage、source 和 Artifact 线模型。
- `tool_registry.py` 把内置同步调用交给 `BoundedToolExecutor` 并保留结果裁剪；现有同 run execution lock 的所有权上移到治理 wrapper，tool handler 不得再次获取同一把锁。
- `capabilities.py` 组合治理 middleware，同时维持 MCP guard/HITL 顺序。
- `runtime.py` 在 create/resume 时把相同 `RunControl` 注入新请求级 middleware。
- `runs.py` 管理 Policy snapshot、active segment、终态映射、Artifact/source 提交与 handle 生命周期。
- `stores.py` 复用原子 JSON 写入原语，不复制新的写盘实现。
- `protocol.py` 扩展显式 CustomEvent 白名单和 payload 校验；未知事件继续 fail-closed。
- `router.py` 增加 REST、事件编码和异常到 HTTP/AG-UI 的映射。

前端新增：

- `AgentWorkspace`
- `AgentInspector`
- `RunInspector`
- `ArtifactViewer`
- `SourceInspector`
- `AgentSettingsDrawer`
- thread-scoped zustand workspace store：保存 Inspector run 选择和三类事件的最高 revision，不持久化密钥或服务端事实。

复用并迁移现有 `AgentThreadList`、`AgentThread`、`ApprovalPanel`、`CapabilityBar`、`CapabilityManagerDialog`、`SkillManager`、`McpManager`、history controller 和 runtime API，不重写已通过合同测试的审批桥。

## 7. Policy 文档

### 7.1 模型与默认值

`policy.json` 使用 schema version 1：

```json
{
  "schema_version": 1,
  "revision": 3,
  "updated_at": "2026-08-16T12:00:00Z",
  "max_model_calls": 8,
  "max_tool_calls": 16,
  "tool_timeout_seconds": 30,
  "max_active_seconds": 300,
  "max_context_chars": 120000
}
```

| 字段 | 默认值 | 合法范围 |
|---|---:|---:|
| `max_model_calls` | 8 | 1-32 |
| `max_tool_calls` | 16 | 1-64 |
| `tool_timeout_seconds` | 30 | 5-120 |
| `max_active_seconds` | 300 | 30-1800 |
| `max_context_chars` | 120000 | 16000-500000 |

以下安全限制固定在服务端，不进入 Policy UI：

- 同步 worker：4。
- worker capacity 等待：1 秒。
- 普通工具结果：6,000 字符。
- Skill 工具结果：沿用 1C 的 60,000 字符。
- 单个 Artifact 序列化内容：1 MB。

### 7.2 CAS、缺失和损坏

- `policy.json` 不存在时，GET 返回默认值、`revision=0`、`persisted=false`，但不自动写盘。
- PATCH 请求包含当前 `revision` 和至少一个待更新字段；未知字段失败。成功后写入全部规范化字段并把 revision 加一。
- revision 不匹配返回 `409 POLICY_REVISION_CONFLICT` 和当前 revision。
- 正常 reset 请求包含当前 revision，写回默认值并把 revision 加一。
- JSON 损坏、schema 不合法或数值越界时，Policy 进入 fail-closed 状态：不能开始新 run，GET/PATCH 返回 `503 POLICY_CORRUPT`，现有 active run 继续使用自己的合法快照。
- Policy 的普通读取使用非破坏性解析；不得复用“读取即隔离”的通用文档读取路径，否则第一次 GET 隔离文件、第二次 GET 可能把损坏误判成缺失并回落默认值。每次加载都从当前文件重新推导 fail-closed 状态。
- 损坏状态只能通过显式 reset 恢复。请求体必须为 `{"confirm_corrupt": true}`；store 先把原文件保留为 `.corrupt-<timestamp>`，再原子写入 revision 1 的默认文档。
- UI 必须展示损坏原因和独立确认，不得静默采用默认值。

### 7.3 快照规则

- start、retry、steer-away 在新产品 run 准入时读取最新 Policy。
- resume 复用原产品 run 的 `PolicySnapshot`，不重新读文件。
- Policy 可在其他 run 活跃时修改；修改只影响之后创建的产品 run。
- `RunDocument.budget_snapshot` 保存完整限制和 `policy_revision`，创建后不可改变。
- 旧 run 的空 `budget_snapshot={}` 表示“历史 run 治理数据不存在”，不得解释为当前默认限制。

## 8. RunControl

`RunControl` 是产品 run 级、线程安全、无密钥的内存对象。它至少维护：

```text
policy_snapshot
model_calls_reserved
tool_calls_reserved
active_elapsed_ms
active_segment_started_monotonic | null
input_tokens
output_tokens
model_calls_with_usage
model_calls_without_usage
context_truncated
context_original_chars
context_retained_chars
context_removed_turns
control_revision
reservation_lock
terminal_error | null
```

内存计数、segment 和 revision 更新由同一个线程锁保护；另有产品 run 级 `asyncio.Lock` 形式的 `reservation_lock`，串行化“检查/递增 -> run JSON 持久化 -> 失败回滚”事务，但不覆盖 Provider/handler 的实际执行。每次 Inspector 可见状态成功持久化后 `control_revision` 单调递增，用于 `budget.updated` 的乱序丢弃。并行工具不能让较旧的完整 RunDocument 覆盖较新的 reservation。

### 8.1 模型调用 reservation

在调用 Provider 之前，middleware 原子执行：

1. 检查 run 未取消、未终止。
2. 检查 active 时间仍有余额。
3. 若已 reservation 数等于限制，抛出 `MODEL_CALL_LIMIT_EXCEEDED`。
4. 取得 `reservation_lock`，先递增 reservation，并把新的 reservation 与 `control_revision` 原子写入 run JSON。
5. 持久化成功后才调用 Provider；持久化失败则回滚尚未对外生效的内存 reservation 并终止 run。
6. 持久化完成后释放 `reservation_lock`；Provider 等待不占用该锁。

只要真实 Provider 请求已经被发起，无论成功、Provider 报错或请求取消，本次都计数。上下文构建失败发生在 reservation 之前，因此不计模型调用。

不能使用 LangChain 内置 `ModelCallLimitMiddleware` 作为权威计数，因为它的 state/run 边界不能可靠覆盖产品 run 的多次 resume。

### 8.2 工具调用 reservation

工具调用只在所有前置步骤完成、即将进入真实工具 handler 时 reservation：

- MCP 参数守卫拒绝：不计数。
- MCP 进入 HITL 等待：不计数。
- 用户 reject 或 steer-away：不计数。
- approve/allowance 后实际执行：计数。
- 参数 schema 已在 LangChain 层拒绝、未进入 handler：不计数。
- 进入 handler 后返回结构化业务错误：计数。
- `create_artifact` 与 Skill 工具同样计数。

锁定版 LangGraph 把参数 schema 校验延迟到被 tool wrapper 包裹的 execute 内部，因此不能假设进入 `ToolExecutionGovernance` 时参数已经合法。治理 wrapper 先用当前绑定工具的 input schema 预校验 args：失败时不 reservation，直接调用内层 execute，让 LangGraph 产生原生参数校验 ToolMessage；真实 handler 仍不得执行。

预校验后按工具类型完成执行准入：本地同步工具、Skill 和 `create_artifact` 先取得同 run execution lock；需要同步 worker 的内置工具和 Artifact staging 再取得 capacity token。锁或 capacity 等待失败都不 reservation。全部前置准入成功后取得 `reservation_lock`，原子 reservation 并持久化；释放 reservation lock 后立即 submit/调用真实 handler。持久化失败时释放尚未使用的 capacity/lock、回滚尚未对外生效的内存 reservation，handler 零调用且不产生 provenance。MCP 已由 guard/HITL 完成前置准入，不取本地 execution lock 或 worker token，在发起真实 MCP handler 请求前执行同一 reservation/persistence 步骤。

一次 assistant message 中的多个 tool call 分别原子 reservation。第 N 个占满额度后，其余未开始调用不得执行；run 以 `TOOL_CALL_LIMIT_EXCEEDED` 失败。

不能使用 LangChain 内置 `ToolCallLimitMiddleware` 作为产品 run 权威计数。

### 8.3 Middleware 装配顺序

模型与上下文治理可以作为外层 middleware；工具执行治理只在通过所有适用守卫和审批后的真实 handler 入口 reservation。无需审批的内置/Skill 工具先到达治理 wrapper，由它完成与锁定版 LangGraph 一致的 schema 预校验，再进入真实 handler。

请求级装配的逻辑顺序为：

```text
ContextAndModelGovernance
McpArgumentGuard              # 仅存在于有 MCP binding 的 run
HumanInTheLoopMiddleware      # 仅存在于有 MCP binding 的 run
ToolExecutionGovernance       # tool wrapper 中最内层
```

锁定 LangChain 版本中，`McpArgumentGuard` 是 `awrap_model_call` middleware：它在原步骤的模型响应返回后、Graph state 写入和 HITL `after_model` 之前校验 MCP 参数。`HumanInTheLoopMiddleware` 只通过 `after_model/aafter_model` interrupt 阻止进入 tool node，不实现 tool wrapper。审批 resume 不重新调用模型，也不重新运行 guard；批准的调用直接进入 tool node，在真实 handler 入口经过 `ToolExecutionGovernance` reservation。guard 会在工具完成后的下一次模型调用中随请求级 middleware 重新参与。

tool wrapper 的第一个 middleware 是最外层；因此 `ToolExecutionGovernance` 在当前锁定版本中只需位于其他自定义 tool wrapper 的最内层，不能把 HITL 或 guard 错当成 tool wrapper 来推导顺序。实施测试必须直接验证 guard 拒绝、HITL pending/reject、approve 和 allowance 四条路径的计数，不能只断言 tuple 顺序。

如果 1C 的附加 middleware 也实现 tool wrapper，它必须明确归类为“执行前守卫”或“执行治理”，不得不经审查插入 `ToolExecutionGovernance` 之后。

### 8.4 产品 run 生命周期

start/retry/steer-away 的治理准入顺序：

1. 先完成既有 duplicate、revision、head、busy 检查和 Capability preview；duplicate/conflict 语义继续优先于 Policy/Capability 错误。
2. 在任何 user/run 写入前加载并校验最新 Policy，固定 `PolicySnapshot`；损坏时新产品 run fail-closed。
3. 完成既有两阶段 Capability lease 获取和最终 thread 事实重校验；Policy 在此期间发生修改不改变已取得的 snapshot。
4. 创建 `RunControl` 和 `ActiveRunHandle`；thread 被原子占用时立即开启第一个 active segment，并把完整 `budget_snapshot` 放入新 RunDocument。
5. 持久化 user message/run，持久化等待计入 active elapsed。
6. 每次模型/工具 reservation 在真实调用前同步到 run JSON；持久化失败则不发起对应外部调用并终止 run。
7. usage、context telemetry、segment 关闭和终态在既有 run/thread 锁下持久化，再发送相应事件。

start、retry 和 steer-away 共享同一个“Policy snapshot -> RunControl/handle -> budget snapshot -> 持久化 -> 安装 handle”治理提交步骤。retry 虽保留既有独立业务准入检查，也必须调用该共享步骤，不能复制一套容易漏接 Policy、reservation 或 `GRAPH_BUILD_FAILED` 补偿的 run 创建序列。

resume 只执行既有 duplicate/revision/busy/resume-shape 校验，不读取当前 Policy 或创建新 control；它复用 handle 中的 snapshot/control 并重开 active segment。因此当前 `policy.json` 损坏也不能阻断一个持有合法 snapshot 的 resume。Policy UI 修改、文件变化或默认值变化都不能改变正在 resume 的 run。retry 和 steer-away 是新产品 run，完整执行以上七步。

steer-away 只有在新 Policy snapshot 和 Capability lease 都成功取得、最终事实重校验通过后，才在原子 thread transition 中取消旧 run 并接受新 user message。Policy/Capability 失败必须保留旧 run 的 `awaiting_approval`、pending interrupts 和 allowance 状态，不消费前端提交的 cancelled entries。

reservation 同步继续使用完整 RunDocument 的原子替换，不引入增量 journal。最坏每个 run 有 32 次模型和 64 次工具 reservation，SourceRecord 又固定为最多 200 条，因此写放大有明确上界；1D 选择可审计的一致性而不是新增第二套持久化格式。

现有 RunJournal 只保留最近 20 条、每条最多 6,000 字符的 tool summary；新增 SourceRecord 最多 200 条，tool source 的两个 summary 各最多 1,000 字符，model URL 最多 2,048 字符。合法 RunDocument 的典型 canonical JSON 约 0.3-0.5 MB，高比例四字节 Unicode 加结构开销的保守量级约 3 MB；最多 96 次 reservation 完整替换可能产生约 288 MB 写入。该单用户、单 worker 1D 版本明确接受这一审计开销，持久化时间计入 active deadline；实现基准必须覆盖保守 payload，不能把完整工具原文或突破上述条数/字符上限的数据写入 RunDocument。

`RunDocument.usage.model_calls/tool_calls` 直接取 `RunControl` 的已持久化 reservation 数，是 REST、Inspector 和硬限制的唯一权威来源。现有事件日志不再根据 `TEXT_MESSAGE_START`、`TOOL_CALL_RESULT` 等流事件推导这两个字段；它只负责消息边界和 Provider token 聚合。这样 Provider 报错或同步工具超时仍会显示已经发生的 reservation，不会出现限制计数与 Inspector 相差一次。

cancel 首先在 `RunControl` 中设置 terminal/cancel 标志，阻止新的 reservation，再停止当前等待并进入既有终态持久化。已经 reservation 的同步调用遵守迟到结果丢弃规则。

## 9. Active 时间

active execution 按产品 run 累计：

- start 完成准入并占用 thread 后开启 segment。
- 模型等待、工具执行、同步 worker capacity 等待和事件/必要持久化时间计入。
- 进入 `awaiting_approval` 并完成 interrupt 持久化后关闭 segment。
- 合法 resume 重新取得 handle 后开启新 segment。
- 人工审批等待不计入。
- cancel、failed、completed、interrupted 时关闭最后 segment。

`time.monotonic()` 用于强制 deadline，UTC 时间只用于持久化展示。`RunDocument.active_elapsed_ms` 是已关闭 segment 加当前 segment 的快照。

在每个模型/工具调用边界检查剩余 active 时间，并以剩余 active 时间包裹等待。工具的 `tool_timeout_seconds` 从 schema 预校验成功后开始，覆盖同 run 串行锁等待、executor capacity、执行和结果返回；实际截止始终取 tool deadline 与 active deadline 的较早者。到期后停止等待、禁止任何后续步骤并以对应的 `TOOL_TIMEOUT` 或 `RUN_ACTIVE_TIMEOUT` 失败。对于不能被 Python 强杀的同步调用，遵循第 10 节的迟到结果规则。

## 10. 有界同步工具执行器

### 10.1 容量模型

进程级 `BoundedToolExecutor` 使用：

```text
ThreadPoolExecutor(max_workers=4)
BoundedSemaphore(4)
capacity_wait_seconds=1
```

调用流程固定为：

1. 在 submit 前等待 capacity token，等待时间计入 active elapsed 和当前 tool deadline。
2. 一秒内未获得 token，抛出 `TOOL_CAPACITY_EXHAUSTED`；不得进入 executor 队列。
3. 获得 token 后执行治理 admission callback：复查 deadline，取得 `reservation_lock`，reservation 并持久化；失败时释放 token 且不得 submit。
4. admission 成功后立即 submit。
5. future 完成回调释放 token，而不是等待者超时或取消时释放。
6. 等待者以 `min(tool deadline, active deadline)` 等待结果。
7. 超时后尝试 `future.cancel()`；若已经运行，停止等待并丢弃迟到结果，但 token 继续占用到 future 实际退出。

这保证 executor 中最多四个已提交但未退出的调用，没有无界排队。服务关闭时先停止接受新任务、取消尚未开始的 future，并以 `shutdown(wait=False, cancel_futures=True)` 等等价策略保证 FastAPI lifespan shutdown hook 在固定时间内返回。

标准 `ThreadPoolExecutor` 无法杀死运行中的 Python 线程，且解释器退出时可能等待永久阻塞的 worker。因此 1D 只保证“不无限阻塞 FastAPI lifespan shutdown”，不保证在第三方永久阻塞时优雅退出整个 OS 进程；运维仍可在外层进程管理器的退出期限后强制终止。1D 不为此引入自定义 daemon thread pool 或进程隔离。

### 10.2 工具类型

- 内置同步工具经此 executor 执行。
- 同一产品 run 使用同一个 execution lock 串行执行内置工具、Skill 和 `create_artifact`，尤其不能并行 Eastmoney throttled fetch，也不能让同一 parent 的 Artifact 并发分叉。
- Skill 工具是受控本地异步读取，不占同步 worker，但其 execution lock 等待仍受工具次数、tool deadline 和 active deadline 限制。
- MCP 工具不占同步 worker；外层 Governance 以 Policy deadline 等待它。MCP Registry 既有 60 秒连接/调用生命周期限制继续存在，实际截止取更早者。
- Artifact 的序列化与临时文件准备使用同一有界 executor，并复用治理层已取得的 capacity lease；executor 提供 lease-aware submit，不能在 handler 内二次获取 token。worker 只能写符合 `<artifact-id>.<nonce>.artifact.tmp` 的 staging file，不能修改 thread/run 或把文件落到最终 ID；权威提交遵循第 15 节。不同 thread/run 的 staging 名称不得碰撞。
- Artifact staging 一秒内无法取得共享 worker 时，同样以 `TOOL_CAPACITY_EXHAUSTED` 终止 run。该选择是有意的：共享同步容量已经耗尽时不让模型自动重试并扩大拥塞。
- Artifact 持久化错误不能转为普通可纠正工具结果。

`TOOL_TIMEOUT` 表示单工具 Policy deadline；`RUN_ACTIVE_TIMEOUT` 优先表示产品 run active 总期限先耗尽。

## 11. 确定性上下文裁剪

### 11.1 字符计量

上下文限制明确挂在 `awrap_model_call` 的 `ModelRequest` 层，在 Provider 格式化和模型 reservation 之前执行。每个请求级 middleware 虽在 resume 时重建，但继续引用同一 `RunControl`；resume 后的下一次模型调用必须重新执行相同裁剪。字符数使用统一 canonical renderer：

- 字符串按 Python 字符长度计算。
- 结构化 content、tool call 和 tool result 使用 `json.dumps(..., ensure_ascii=False, sort_keys=True, separators=(",", ":"))`。
- role、tool name、tool call ID 和分隔符包含在计量中。
- System/Policy/Skill system context 与 message history 使用同一 renderer。

同一个 helper 同时负责裁剪和 telemetry，避免 UI 数值与真实阻断规则不同。

### 11.2 原子单位

历史按完整 user turn 分组：一个 user message 开始，到下一个 user message 之前结束。以下内容不能拆分：

- assistant tool call 与全部匹配的 tool result。
- 一个 user turn 内的 assistant/tool 循环。
- 当前 user turn 的全部已完成内容。

partial message 和 pending-interrupt message 沿用 1B 规则，不进入普通新 run 历史。纯 resume 仍依赖 MemorySaver，不重建历史。

### 11.3 保留顺序

每次模型调用前：

1. 强制保留中立 System Prompt、Policy 说明和已选 Skill 元数据。
2. 强制保留包含每个当前已加载 Skill 最新 `load_skill` 完整结果的 user turn；这使已加载指令不会因旧历史裁剪而消失。
3. 强制保留当前 user turn 整体。
4. 对其余历史，从新到旧加入完整 user turn，直到下一个 turn 会超限。
5. 输出恢复为原时间顺序；旧 thread JSON 不做修改。

若强制内容本身超过上限，在模型 reservation 前以 `CONTEXT_LIMIT_EXCEEDED` 失败。不得截断 System、Skill 指令、当前 turn、tool args 或 tool result 来勉强通过，也不得调用模型生成摘要。

`context_truncation` 持久化和 REST 返回：

```json
{
  "occurred": true,
  "original_chars": 153204,
  "retained_chars": 118740,
  "removed_turns": 4
}
```

未裁剪时仍返回 `occurred=false` 和本次最新计量。Inspector 只说明发生过裁剪和数量，不展示被省略的私有原文。

## 12. Token usage

Provider 响应包含 usage 时，按模型调用累加 `input_tokens`、`output_tokens` 和可推导的 `total_tokens`。不自行 tokenization，不用字符数伪装 token。

模型/工具调用次数不由 Provider usage 或流事件推导；它们始终来自第 8 节已经持久化的 reservation。Provider usage 只补充 token 字段和下面的 token status。

`usage.token_status`：

- `available`：每个已完成 Provider 调用都返回可用 input/output usage。
- `partial`：至少一个调用有 usage，至少一个调用没有完整 usage。
- `unavailable`：没有任何调用返回 usage。

失败或被取消且没有 usage 的已发起 Provider 调用视为“没有完整 usage”。没有模型调用的 run 为 `unavailable`。REST 和 Inspector 只能显示 Provider 实报 token，不显示费用。

## 13. 治理终态与错误

1D 不增加 `limited` 状态。治理限制使用既有 `failed`，保留此前已经完整持久化的消息、工具结果和 Artifact，不再调用模型制造结束说明。

| code | 触发点 |
|---|---|
| `MODEL_CALL_LIMIT_EXCEEDED` | 下一次模型 reservation 超限 |
| `TOOL_CALL_LIMIT_EXCEEDED` | 下一次实际工具 reservation 超限 |
| `TOOL_TIMEOUT` | 单工具 Policy deadline 到期 |
| `RUN_ACTIVE_TIMEOUT` | 产品 run active 总期限到期 |
| `TOOL_CAPACITY_EXHAUSTED` | 一秒内无法取得同步 worker token |
| `CONTEXT_LIMIT_EXCEEDED` | 强制上下文本身超限 |

错误写入 `RunDocument.error_code/error_message`，message 必须脱敏且面向用户。治理异常到上述 code 的映射与既有 `GRAPH_BUILD_FAILED` 补偿统一进入现有终态持久化路径；不得被硬编码的 `AGENT_RUN_FAILED` 覆盖。最终 `budget.updated` 和 `thread.revision.updated` 先于 AG-UI terminal event。

## 14. Artifact 数据模型

### 14.1 公共文档

Artifact 路径保持：

```text
<VR_DATA_DIR>/agent/artifacts/<thread-id>/<artifact-id>.json
```

公共字段：

```json
{
  "schema_version": 1,
  "id": "artifact-uuid",
  "thread_id": "thread-uuid",
  "run_id": "run-uuid",
  "type": "markdown",
  "title": "研究摘录",
  "created_at": "2026-08-16T12:00:00Z",
  "parent_artifact_id": null,
  "content": {},
  "source_ids": ["source-uuid"]
}
```

- `id`、`thread_id`、`run_id` 由服务端给出。
- `title` 去除首尾空白后长度 1-200。
- 单个完整 Artifact JSON 的 UTF-8 序列化上限为 1,048,576 bytes。计量对象是 source 解析和服务端字段补齐后的最终 canonical JSON，使用 `ensure_ascii=False`、`sort_keys=True`、紧凑 separators，并包含实际落盘的末尾换行；计量结果必须与 staging 写出的字节完全一致。
- `source_ids` 有序去重且最多 200 个。
- Artifact 创建后不可修改。

### 14.2 类型化 content

`markdown`：

```json
{"markdown": "# 标题\n\n正文"}
```

只允许纯 Markdown 字符串。渲染器禁用 raw HTML。

`table`：

```json
{
  "columns": [{"key": "symbol", "label": "代码"}],
  "rows": [{"symbol": "600519"}]
}
```

- 1-50 列，column key 唯一并匹配 `^[A-Za-z_][A-Za-z0-9_]{0,63}$`；面向用户的中文或其他 Unicode 名称放在 label，label 长度 1-100。
- 最多 5,000 行。
- 每个 row 必须包含全部已声明 key，且不能包含其他 key；空单元格使用显式 `null`，不能用字段缺失表达。
- cell 只能是 string、number、boolean 或 null；拒绝 NaN 和 Infinity。
- UI 预览前 200 行，下载包含全部行。

`json`：

```json
{"value": {"key": "value"}}
```

- value 必须是 JSON value。
- 最大嵌套深度 32，根节点深度为 1。
- 最多 50,000 个 object member、array item 和 scalar 节点的总和。
- 拒绝 NaN、Infinity 和非 JSON 类型。

`sources`：

```json
{
  "items": [
    {"source_id": "source-uuid", "note": "用于核对财务口径"}
  ]
}
```

- 最多 200 条，`source_id` 唯一。
- 每个 ID 必须同时存在于公共 `source_ids`。
- `note` 可选，纯文本，最多 2,000 字符。
- 不复制或嵌入外部页面内容。

上面是持久化后的 content。调用 `create_artifact` 时，模型还不知道本次 URL 描述符将生成的 source ID，因此 `sources` 类型的输入用 `source_index` 引用同一请求 `sources` 数组的零基索引；服务端解析描述符后把它规范化为不可变的 `source_id`。其他 Artifact 类型不使用 `source_index`。

### 14.3 不可变版本链

- 新版本通过 `parent_artifact_id` 指向父 Artifact，产生新 ID。
- 父必须属于同一 thread、类型相同。
- 父当前不能已有 child，保证每条链是线性的。
- 只有叶子 Artifact 可以删除；有 child 时返回 `409 ARTIFACT_HAS_CHILD`。
- 不提供原地 PATCH 或移动到其他 thread 的能力。

## 15. create_artifact 工具

这是唯一 Artifact 创建入口；REST 不提供 POST。

输入 schema：

```json
{
  "type": "markdown | table | json | sources",
  "title": "string",
  "content": {},
  "parent_artifact_id": "optional artifact id",
  "sources": [
    {"kind": "tool_call", "tool_call_id": "call id"},
    {"kind": "url", "url": "https://example.com", "label": "optional"}
  ]
}
```

当 `type="sources"` 时，工具输入的 content 形状是：

```json
{
  "items": [
    {"source_index": 0, "note": "用于核对财务口径"}
  ]
}
```

来源描述符最多 200 条：

- `tool_call` 只能引用当前产品 run 已完成并已持久化的工具调用，不能引用 pending、rejected、其他 run 或不存在的 ID。在线运行通过复用的 `ActiveRunHandle`/`RunJournal` 核对已完成调用，并以 `RunDocument.tool_summaries` 的持久化事实复验；后端重启会先把原 active run 标记 interrupted，不存在脱离 handle 继续创建 Artifact 的路径。
- `url` 只接受绝对 HTTP/HTTPS URL；只规范化和记录，不发网络请求。
- 对 `sources` 类型 Artifact，`source_index` 必须在本次 `sources` 数组范围内且唯一；解析后持久化为对应 `source_id`，输入中的索引不落盘。
- 所有 URL 描述符使用第 16.2 节同一个规范化 key 与 run 内既有 `model_url` 去重；命中已有记录时复用其 ID，不修改已持久化 label。
- 描述符按输入顺序规范化。一次请求内两个描述符若解析为同一 tool call ID 或 URL key，整个请求以 `ARTIFACT_SOURCE_INVALID` 失败；即使两个 `source_index` 不同，也不能最终解析成重复 `source_id`。不得用静默去重改变模型提交的索引语义。
- 在任何写入前整体模拟容量：`existing_count + unique_missing_count` 超过 200 时整单失败。例如已有 197 条且四个描述符都需要新记录时，新增为零，Artifact 也不创建。错误 payload 至少包含首个失败的 `descriptor_index`、`reason` 和 `remaining_capacity`，不得回显未脱敏的完整 URL。
- 当 run 已有 200 条 SourceRecord 时，只允许描述符复用既有记录。需要新增 URL、或引用了已完成但因上限未生成 SourceRecord 的 tool call 时，整个 `create_artifact` 以 `ARTIFACT_SOURCE_INVALID` 结构化失败；不得静默省略描述符、缩短 `source_ids` 或创建部分 Artifact。显式请求失败不改变 `sources_truncated`。

可纠正错误作为结构化 tool result 返回，Agent 可以修改输入后重试：类型/schema、大小、父链、来源引用和业务冲突错误。

成功 tool result 形状固定为：

```json
{
  "ok": true,
  "artifact": {
    "id": "artifact-id",
    "title": "研究摘录",
    "type": "markdown",
    "run_id": "run-id",
    "parent_artifact_id": null
  },
  "thread_revision": 9
}
```

前端只依赖该最小 metadata 生成 Inspector 链接，不从 tool result 复制完整 Artifact content。

下列错误表示存储一致性无法保证，必须以 `ARTIFACT_PERSISTENCE_FAILED` 终止 run：Artifact 文件写入失败、thread reference 提交失败且补偿失败、来源/run 提交失败或已提交后的事件状态无法确定。此类错误不能伪装成普通工具结果。

每个产品 run 额外拥有一个 `artifact_mutation_lock`。自动 SourceRecord 写入和 `create_artifact` 都必须取得该锁；`create_artifact` 还在第 8 节的 execution lock 内运行。该组合保证同 run 的来源顺序稳定，并防止两个调用同时通过 parent leaf 检查后产生分叉。`artifact_mutation_lock` 与 coordinator thread lock 的固定顺序是先前者、后者只在两次短复验/提交区取得；cancel 不需要 mutation lock，因此 staging 不会阻塞 cancel。

提交顺序：

1. 取得 `artifact_mutation_lock`，短暂取得 coordinator thread lock，复验 run active、thread、parent leaf、source descriptors 和整体容量，生成不可变提交计划后释放 coordinator lock。
2. 使用 `ToolExecutionGovernance` 已取得的 capacity lease，通过 executor 的 lease-aware submit 把完整 Artifact JSON 写入唯一的 `<artifact-id>.<nonce>.artifact.tmp`；handler 不得再次获取 token，避免嵌套 acquisition 死锁。worker 必须 flush 并 fsync staging file；如果 timeout/cancel，迟到 future 只能产生该临时文件，完成回调负责清理，绝不能晚到提交权威状态。
3. future 在 deadline 前返回后重新取得 coordinator thread lock；在任何权威写入前再次复验 run active、parent leaf、thread revision 和提交计划仍成立。失败时释放锁并删除 staging，不提交部分状态。
4. 在 coordinator lock 内执行不包含网络、capacity 等待或其他外部 await 的短提交：先把新增 SourceRecord 原子提交到 run；这些记录独立描述本次已执行工具/模型提供 URL，即使随后 Artifact 失败也仍是合法 provenance。
5. 使用 `os.replace` 把 staging file 原子落为最终 Artifact JSON，并 fsync Artifact 目录。
6. 原子提交 thread `artifact_ids` 和递增 revision。
7. 只有以上提交成功才返回固定成功 tool result，并在持久化后发出 `artifact.created`。

coordinator lock 可以像现有 journal 路径一样在短提交期间等待 `asyncio.to_thread` 的本地原子 JSON 写，但不得覆盖最长 30 秒的 staging/tool 执行。步骤 2 失败或迟到时不进入提交区；步骤 4 失败时删除 staging；步骤 5 或 6 失败时删除尚未被 thread 引用的新文件，删除也失败则记录 orphan warning 并交给启动对账。JSON 文件之间不虚构事务；已提交的独立 SourceRecord 不回滚，并且即使 Artifact 随后失败也仍在 run 持久化后发送对应 `sources.updated`。任何失败都不能发送虚假的 `artifact.created`。

## 16. 来源与 provenance

### 16.1 SourceRecord

来源记录持久化在所属 `RunDocument.sources` 中，最多 200 条：

`tool_execution`：

```json
{
  "id": "source-uuid",
  "kind": "tool_execution",
  "tool_call_id": "call-id",
  "tool_name": "get_stock_quote",
  "origin": "builtin | skill | mcp | artifact",
  "completed_at": "2026-08-16T12:00:00Z",
  "arguments_summary": "...",
  "result_summary": "...",
  "verification": "executed_record"
}
```

`model_url`：

```json
{
  "id": "source-uuid",
  "kind": "model_url",
  "url": "https://example.com/path",
  "label": "可选标签",
  "created_at": "2026-08-16T12:00:00Z",
  "verification": "model_provided_unverified"
}
```

`arguments_summary` 和 `result_summary` 使用现有递归脱敏，再 canonical JSON 编码并各限制 1,000 字符。它们是执行记录摘要，不是数据真实性认证。

### 16.2 自动收集与去重

- 每个完成并成功持久化的工具调用自动产生一条 `tool_execution` source，包括返回结构化业务 error 的调用。
- pending、参数拒绝、HITL reject、steer-away 和未完成调用不产生 source。
- 完整 assistant message 持久化后，从文本 content 中提取 HTTP/HTTPS URL，记录为 `model_url`。后端直接依赖并固定版本的 CommonMark parser：读取 link/autolink destination，只在不处于 link、inline code、fenced code 或 indented code 的文本节点扫描裸 URL，不能用正则自行解析 Markdown 结构。
- partial 和 pending-interrupt assistant message 不提取 URL。
- 工具来源按 `tool_call_id` 去重。
- 候选 URL 最长 2,048 字符。自动提取中超长或无效候选忽略；`create_artifact` 中的超长或无效描述符返回 `ARTIFACT_SOURCE_INVALID`。
- URL 候选迭代移除尾部句末标点 `.,;:!?，。；：！？`，再按配对表 `() [] {} （）【】｛｝「」『』` 只移除没有匹配左括号的尾部闭合符，直到不再变化；随后用结构化 URL parser 校验必须是绝对 HTTP/HTTPS、具有 hostname 且不含 userinfo。
- URL 去重 key：scheme/host 小写、移除 fragment、移除默认端口、空 path 规范为 `/`；不改 query 顺序或 percent-encoding，不跟随 redirect。Artifact URL 描述符和 assistant 自动提取共享同一去重表及 Golden corpus。
- 自动提取按消息文本顺序尝试新增，填满剩余容量后忽略后续新 key 并设置 `sources_truncated=true`；已有来源顺序不变，不做评分或优先级替换。`create_artifact` 在截断态的行为遵循第 15 节：可复用已有记录，但不能静默丢弃新描述符。

Artifact 的 `source_ids` 只引用同一 run 的 SourceRecord。下载 Artifact 时保留 ID，不把 run 中的 summary 复制进 Artifact 文件。

## 17. Artifact REST 与删除一致性

新增接口：

```text
GET    /api/agent/threads/{thread_id}/artifacts
GET    /api/agent/threads/{thread_id}/artifacts/{artifact_id}
GET    /api/agent/threads/{thread_id}/artifacts/{artifact_id}/download
DELETE /api/agent/threads/{thread_id}/artifacts/{artifact_id}
```

列表按 `created_at` 升序返回轻量 metadata、parent/child 状态；detail 返回完整类型化 content。

detail/download/delete 都先读取路径中的 thread 文档并确认 `artifact_id` 出现在其 `artifact_ids`，再直接构造受控路径；不扫描和解析全部 thread 文档，也不新增第二份全局索引。零个引用返回 404；Artifact 文件内部 thread ID 不匹配时上报损坏并拒绝访问。未被 thread 引用的 orphan 不能通过 API 暴露。

download：

- Markdown 使用 `.md`，table 使用 `.json`，JSON 使用 `.json`，sources 使用 `.json`。
- attachment filename 只由服务端 Artifact ID 和固定扩展名构造，不使用用户 title。
- 设置 `Content-Disposition: attachment`、`X-Content-Type-Options: nosniff` 和限制性 CSP。
- Markdown 固定返回 `Content-Type: text/markdown; charset=utf-8`；table、JSON 和 sources 固定返回 `Content-Type: application/json; charset=utf-8`。不根据用户内容猜测 MIME，不返回 `text/html` 或 `image/svg+xml`。

删除步骤：

1. JSON body 携带 `thread_revision`；校验 revision、Artifact 属于该 thread、thread 当前没有 `running/awaiting_approval` run，且 Artifact 是叶子。
2. 从 thread `artifact_ids` 移除并原子提交新 revision。
3. 删除 Artifact 文件并同步父目录。
4. 删除文件失败时保留已经提交的 thread 状态，并记录显式 orphan warning；不得把引用重新写回造成 revision 回滚。

成功返回新 thread revision。revision 冲突返回 `409 THREAD_REVISION_CONFLICT`；busy 返回 `409 THREAD_BUSY`；文件清理失败返回 `500 ARTIFACT_DELETE_FAILED` 并带无私密信息的 recovery warning。

删除 thread 时，必须先在锁内确认不 busy，再把该 thread 的 Artifact 目录和 run 文件原子 rename 到本次删除操作的受控 tombstone 名称。所有 rename 成功后删除 thread 文件作为提交点，最后清理 tombstone。提交点前失败时回滚已经完成的 rename 并保留 thread；回滚或提交点后的清理失败时返回明确 persistence error 并交给启动对账继续恢复/清理。不得留下可见 thread 永久引用已删除文件，也不能让已删除 thread 的残留文件重新暴露。

## 18. 启动对账与迁移

不批量重写旧 JSON，继续保留 `schema_version=1`，字段只做 additive 扩展：

- 旧 `budget_snapshot={}`：无治理数据。
- 缺少 `control_revision`：0。
- 缺少 `usage.token_status`：`unavailable`。
- 缺少 `context_truncation`：`occurred=false`，其他计量为空。
- 缺少 `sources`：空数组；缺少 `sources_truncated`：false。
- 旧 thread 的 `artifact_ids=[]` 按原样读取。
- 缺少 `policy.json`：运行时默认 Policy，首次 PATCH 才持久化。
- 保留 `vr-agent-model`、Skill/MCP schema、thread revision 和全部 AG-UI 合同。

启动对账在既有 interrupted-run 恢复之后执行：

1. 扫描合法 Artifact 路径，不跟随 symlink。
2. thread 引用了缺失/损坏 Artifact：保留 thread，产生 recovery warning，不静默删除引用。
3. Artifact 文件未被 thread 引用：记录 orphan warning；只自动删除能证明是本写入管线未完成提交的临时文件，普通 orphan JSON 保留供诊断。
4. Artifact 指向不存在或错误 thread/run：隔离为 corrupt/orphan warning，不暴露下载。
5. 版本链分叉、跨 thread/type parent 或 cycle：相关 Artifact 隔离为损坏，不尝试猜测修复。

对账递归扫描 `artifacts/<thread-id>/`，只把严格匹配 `<artifact-id>.<nonce>.artifact.tmp` 的文件视为本管线 staging；现有仅扫描平铺 `*.tmp` 的规则不足以覆盖该目录。删除 tombstone 也使用独立固定命名规则，按“thread 是否仍存在”和提交点状态决定回滚或清理。`RecoveryWarning.document_type` 增加 `artifact` 字面量。

## 19. AG-UI 事件合同

REST 使用 snake_case；自定义 AG-UI event name 和 payload 字段使用 camelCase。

`AgentProtocolBridge.convert` 对 Graph 内产生的 CustomEvent 使用显式白名单：`budget.updated`、`artifact.created`、`sources.updated`。每个事件先解析 JSON 并按本节 schema 校验，再作为 AG-UI `CUSTOM` 原样输出；未知名称继续转换为 `UNSUPPORTED_CUSTOM_EVENT`，不能把任意 Graph event 透传到浏览器。`thread.revision.updated` 继续由 router 在权威提交后直接编码，不进入 bridge。

前端 `runtime.tsx::scanStream` tee 扫描这三类事件并写入 thread-scoped workspace store。store 按事件种类和 run 保存最高 revision，只接受更大的值；流结束、重连或发现 revision 缺口时，以 run/Artifact REST 覆盖为权威状态。

### 19.1 budget.updated

```json
{
  "threadId": "thread-id",
  "runId": "run-id",
  "controlRevision": 12,
  "budgetSnapshot": {},
  "usage": {},
  "activeElapsedMs": 3100,
  "contextTruncation": {}
}
```

- reservation、token usage、segment 关闭或 context telemetry 变化后发送。
- 前端按 run ID 保存最高 `controlRevision`，忽略旧 revision。
- 事件丢失后以 run REST 为权威。

### 19.2 artifact.created

```json
{
  "runId": "run-id",
  "threadId": "thread-id",
  "artifactId": "artifact-id",
  "type": "table",
  "title": "财务快照",
  "threadRevision": 9
}
```

只有 Artifact JSON、run 更新和 thread reference 全部提交成功后发送。前端将对应 tool result 渲染为可打开 Inspector Artifact tab 的链接。

### 19.3 sources.updated

```json
{
  "threadId": "thread-id",
  "runId": "run-id",
  "controlRevision": 13,
  "sourceCount": 7,
  "sourcesTruncated": false
}
```

只有 run JSON 已持久化后发送；Source 变化同时递增 `RunControl.control_revision`。payload 不重复发送 source 原文，Inspector 只用事件标记失效并通过 REST 读取权威数据。

### 19.4 终态顺序

每个 terminal path 必须按以下顺序：

1. 持久化最终 run/thread/Artifact/source 状态。
2. 发送最终 `thread.revision.updated`（若 revision 有变化）。
3. 发送最终 `budget.updated` 和 `sources.updated`（若有变化）。
4. 发送标准 AG-UI `RUN_FINISHED` 或 `RUN_ERROR`。

断连或事件编码失败不能回滚已经提交的数据；前端重载 REST 后收敛。

## 20. REST 线模型扩展

`RunDocument` additive 扩展：

```text
budget_snapshot: PolicySnapshot | {}
control_revision: int
usage.token_status: available | partial | unavailable
usage.total_tokens: int | null
context_truncation: ContextTruncation
sources: SourceRecord[]
sources_truncated: bool
```

`control_revision` 随每次成功持久化的治理/Source 可见状态单调递增。REST 返回该值，前端 REST 收敛时同时更新本地最高 revision，防止随后到达的旧 CustomEvent 覆盖新状态。

历史 run 列表 API：

```text
GET /api/agent/threads/{thread_id}/runs?limit=50&before=<run-id>
```

- `limit` 默认 50，合法范围 1-100。
- 结果按 `(started_at, id)` 倒序；`before` 必须是同 thread 的 run ID，下一页从它之后开始。
- 响应为 `{runs, next_before, warnings}`。`runs` 是轻量摘要，至少包含 `id/status/started_at/updated_at/ended_at/retry_of/error_code`；有下一页时 `next_before` 是本页最后一个 ID，否则为 null。
- `warnings` 只报告扫描到的损坏 run 文件名，不返回路径或文件内容。该接口不返回消息、tool summary、SourceRecord 或 secret。

Policy API：

```text
GET    /api/agent/policy
PATCH  /api/agent/policy
POST   /api/agent/policy/reset
```

PATCH payload：

```json
{
  "revision": 3,
  "max_model_calls": 10,
  "max_tool_calls": 20
}
```

正常 reset：`{"revision": 3}`。损坏 reset：`{"confirm_corrupt": true}`。两种形状互斥。

所有接口继续经过现有 `VR_API_KEY` middleware。Policy 和 Artifact API 不接收或返回模型/MCP secret。

新增管理接口统一返回结构化 `{code, detail}` 错误：

| HTTP | 典型 code | 语义 |
|---:|---|---|
| 400 | `POLICY_INVALID`、`ARTIFACT_INVALID`、`ARTIFACT_SOURCE_INVALID` | 请求可修正，未改变权威状态 |
| 404 | `ARTIFACT_NOT_FOUND`、`THREAD_NOT_FOUND` | 目标不存在或已隔离 |
| 409 | `POLICY_REVISION_CONFLICT`、`THREAD_REVISION_CONFLICT`、`THREAD_BUSY`、`ARTIFACT_HAS_CHILD`、`ARTIFACT_PARENT_CONFLICT` | 并发或版本链冲突，前端重载权威数据 |
| 500 | `ARTIFACT_PERSISTENCE_FAILED`、`ARTIFACT_DELETE_FAILED` | 写盘/一致性失败，不能宣称成功 |
| 503 | `POLICY_CORRUPT` | 新 run fail-closed，需显式 reset |

同一 Artifact 校验错误经 `create_artifact` 调用时使用相同 code，但编码为结构化 tool result；只有 `ARTIFACT_PERSISTENCE_FAILED` 上升为 run 终态。

## 21. 最终桌面工作台

### 21.1 布局

`/agent` 使用全宽、固定工作区高度的三栏布局：

```text
+----------------------+-------------------------------+--------------------------+
| Threads 240px        | Chat minmax(480px, 1fr)      | Inspector 320px          |
| + search             | compact header                | Run Approval Artifact    |
| status / warnings    | messages                      | Sources                  |
| rename / delete      | composer / stop               |                          |
+----------------------+-------------------------------+--------------------------+
```

- 保留全局导航；工作区占据剩余 viewport 高度。
- 三列分别滚动，页面 body 不形成第二条纵向滚动链。
- 列之间用边框分隔，不把页面 section 包成层层 glass card。
- Chat track 使用 `minmax(480px, 1fr)`，Inspector 固定 320px，thread 固定 240px。
- 视觉沿用现有暖橙 token，但以安静、密集、便于扫描的工作台为主。

### 21.2 Thread column

- 顶部使用图标按钮创建 thread，并提供 tooltip/ARIA label。
- 搜索仅过滤已加载 title，不请求服务端全文搜索。
- 每行显示 title、更新时间、最近 run 状态；损坏/恢复 warning 明确显示。
- rename/delete 复用 revision 合同；delete 有确认，busy/409 后重载。
- 选中 thread 有稳定高亮，长 title 截断但可通过 tooltip 查看。

### 21.3 Chat

- 紧凑 header 显示 thread title、当前能力摘要和 model label。
- 1C 顶部的大模型配置/能力管理卡移除，入口迁移到 Settings drawer。
- `running`：Composer 禁用，只显示 Stop command。
- `awaiting_approval`：保留 steer-away Composer，Approval tab 同步高亮。
- 终态错误和状态区域尺寸稳定，不因流式内容导致控制区跳动。
- `create_artifact` 成功结果显示 Artifact icon、title 和“在 Inspector 打开”动作。
- 未配置合法 Model 时显示稳定的配置 gating 状态并提供 Settings 入口，不挂载一个注定失败的 runtime。

## 22. Inspector

Inspector 只展示当前 thread/当前或选中 run 的运行产物，不承担全局设置。

Inspector 通过第 20 节 run 列表选择历史 run。默认选中 `thread.last_run.id`；选择只存在 thread-scoped workspace store，不写服务端或 `localStorage`。切换 thread、选中 run 被删除/隔离或列表重载后找不到目标时，回退到新的 `last_run`。列表按需加载下一页，不一次返回无限历史。

四个 tab：

- `Run`：状态、active/approval/wall 时间、模型/工具次数与上限、Provider token status、context truncation、终态错误。
- `Approval`：复用 `ApprovalPanel`，展示全部 pending interrupt 和 once/thread-session/reject 动作；没有 pending 时仍渲染稳定空状态，不能因组件返回 null 让 Inspector 布局消失。
- `Artifact`：以当前/选中 run 为主列出 Artifact，并补充同 thread 的父子版本链；支持安全预览、下载、删除叶子和跳转版本。
- `Sources`：工具执行记录和模型 URL 分组展示，明确标注“执行记录”与“模型提供，未验证”，不显示排名。

tab badge 只显示数量或 pending 状态。来源/Artifact 为空时使用简短空状态，不出现功能宣传文案。

## 23. Settings drawer

Settings 是独立抽屉，包含四个 tab：

- `Model`：浏览器本地 `vr-agent-model`，沿用专用 secret header 和 HTTPS/loopback 规则。
- `Skills`：复用 `SkillManager` 管理已安装 Skill，并把 `CapabilityBar`/`CapabilityManagerDialog` 的 thread-scoped 选择交互迁入该 tab；本地 draft 只提交一次 PATCH 并携带 thread revision，409 时丢弃 draft、重载权威 thread。
- `MCP`：复用 `McpManager`；配置仍是 backend-global。
- `Policy`：编辑 backend-global Policy，显示 revision、范围、默认值、保存冲突和损坏 reset。

当任何前端已知 thread 为 `running` 或 `awaiting_approval` 时，Model reference 不可改变，避免 resume 产生 `RUN_CONFIG_MISMATCH`。Policy 可以修改，因为 active run 使用快照。Skill selection 继续受当前 thread busy/revision 规则约束。

当 `max_context_chars <= 60000` 时，Policy tab 显示非阻断 warning：单个合法 Skill 指令即可达到 60,000 字符，加载后加上 System 与当前 turn 可能产生 `CONTEXT_LIMIT_EXCEEDED`。warning 只说明当前配置冲突，不自动抬高数值或修改 active run。

父设计提到的 `mcp.health_changed` 不进入 1D 合同：当前 1A-1C 没有后台 MCP 健康监控，1D 不新增轮询或常驻探测器。MCP tab 在 test、refresh 和配置 mutation 成功后通过 REST 重载健康状态；父设计同步记录这一显式降级，避免留下未实现事件。

## 24. 响应式与可访问性

小于 Tailwind `xl` breakpoint：

- 主界面只保留 Chat。
- Thread column 变成左抽屉。
- Inspector 变成右抽屉。
- Settings 是全高抽屉。
- 三种抽屉互斥，打开一个必须关闭另一个。
- 普通移动抽屉宽度 `min(88vw, 360px)`；手机上的 Settings 使用全宽。

交互要求：

- 每个抽屉有可访问名称、close button、`aria-modal` 和正确 dialog role。
- 打开时 focus 进入抽屉，Tab 不逃逸；关闭后 focus 返回触发按钮。
- Escape 和 backdrop 可关闭；有未保存 Policy 修改时先确认。
- Inspector 和 Settings 的 tab 使用 `tablist/tab/tabpanel` 语义并支持方向键移动；选中、focus 和 badge 不能只靠颜色表达。
- 1280px 临界宽度必须验证三栏总宽、全局侧栏和页面容器的组合，不得只测试宽裕的 1440px。
- 390x844 不得出现横向 overflow、工具栏重叠、按钮文字溢出或 Composer 被遮挡。
- 桌面和移动端都保持 thread、chat、Inspector 自身滚动位置稳定。

## 25. Artifact 前端安全

- Markdown renderer 禁用 raw HTML，不加载任意 iframe、script、远端 embed 或远端图片。`![](https://...)` 只能显示为不自动请求网络的占位/链接，避免把模型提供 URL 变成客户端信标。
- URL 只允许 HTTP/HTTPS，使用 `target="_blank" rel="noopener noreferrer"`。
- Table 只渲染服务端已校验 scalar/null cell；前 200 行预览有明确计数。
- JSON viewer 是纯数据树/文本，不把字符串解释为 HTML、组件或样式。
- Sources note 按纯文本渲染。
- download 使用后端 attachment，不用 title 拼 filename。
- CSP、`nosniff` 和 MIME 由后端防守；前端仍不能根据 content 猜测可执行类型。

## 26. 安全与隐私不变量

- `RunControl`、Policy、Artifact、SourceRecord、事件和日志均不得包含模型 API key、MCP secret 或完整环境变量值。
- 来源 arguments/result summary 必须先递归脱敏，再裁剪；顺序不可反转。
- Artifact 路径只由校验后的服务端 UUID 构造，不能接受用户路径。
- URL 记录不触发 DNS、HTTP、preview 或 redirect。
- Artifact/Policy store 使用既有原子写、文件/目录 fsync 和固定临时文件规则。
- 同步工具保持同 run 串行，不能破坏 Eastmoney timestamp spacing。
- Agent 子系统仍只支持一个 FastAPI worker。
- LangSmith tracing 默认关闭；无限 prompt/tool 原文不能写入日志。
- 测试继续在 `conftest.py` 设置的临时 `VR_DATA_DIR` 中运行，不触碰真实用户数据。

## 27. 测试策略

### 27.1 治理单元测试

- Policy missing/default、范围、unknown field、CAS conflict、正常 reset、非破坏性 corrupt fail-closed、重复 GET 仍为 corrupt 和显式 reset。
- `RunControl` 在并发 reservation 下不超过模型/工具上限。
- `reservation_lock` 防止并行工具用旧 RunDocument 覆盖新计数；某次持久化失败后等待者观察 terminal 状态且不进入 handler。
- start/resume 复用同一计数；retry/steer-away 使用新快照。
- active segment 暂停审批等待，模型/工具/capacity 等待计入。
- token `available/partial/unavailable` 聚合。
- context canonical 计数、完整 user turn、tool pair 原子、Skill 指令保留、强制内容超限。
- schema 参数拒绝、guard 拒绝、pending/reject MCP 不计工具调用；approve/allowance/业务 error 计数。
- `usage.model_calls/tool_calls` 与已持久化 reservation 完全一致，Provider error、工具 timeout/cancel 也不能少计。
- 20 条最大 tool summary + 200 条最大 SourceRecord 的保守 RunDocument payload 完整替换基准；不得越过已接受的约 3 MB/次保守量级。
- duplicate/conflict 检查先于 Policy/Capability 错误；新产品 run 才读取 Policy snapshot。
- retry 经过与 start/steer-away 相同的治理提交步骤；Graph build failure 保留既有补偿和细分错误码。

### 27.2 Executor 测试

- 四个同步 worker 被占用时，第五个在一秒内得到 `TOOL_CAPACITY_EXHAUSTED`。
- submit 前取得 token，executor queue 永不超过容量。
- timeout/cancel 后，运行中 future 仍占容量，迟到结果被丢弃。
- 全部 future 实际退出后容量恢复。
- 用测试控制的阻塞 worker 验证 lifespan shutdown hook 在 worker 释放前已于固定期限内返回并拒绝新 submit，断言后释放 worker 以免测试进程在解释器退出时被 join；测试不声称永久阻塞时整个 Python 解释器可优雅退出。
- 同一 run 的内置工具保持串行；Eastmoney 分类的调用绝不并发。
- execution lock 等待计入 tool/active deadline；锁未及时取得时 handler 零调用。
- capacity 拒绝不增加 `usage.tool_calls`，取得 token 后 reservation 持久化失败会释放 token 且 executor 零 submit。
- 同一 assistant message 并行派发多个工具时，第 N 个 reservation 超限后其余调用均不执行。
- MCP 60 秒生命周期限制仍存在，Policy 以更短 deadline 外包裹。

### 27.3 Artifact 与来源测试

- 四种 schema 的合法/非法边界、最终 canonical UTF-8 1 MB（含末尾换行）、table 完整 row key/显式 null/50/5000、JSON depth/node、sources 200。
- ID/path traversal、symlink、跨 thread/run、跨类型 parent、分叉、cycle 和非叶删除。
- 只允许当前 run completed tool call 来源；URL scheme/规范化/去重；同请求 descriptor key/source ID 碰撞整单失败。
- CommonMark link/autolink/裸 URL 提取、link text/code span/fence/indented code 忽略、2,048 字符上限、userinfo 拒绝、配对括号、迭代句末标点和 percent-encoding Golden corpus。
- Sources 已满 200 条时，已有 tool/URL 描述符仍可复用；任何需要新 SourceRecord 的描述符返回 `ARTIFACT_SOURCE_INVALID`，不创建部分 Artifact 或虚假事件。
- 197 条 + 4 个新描述符整单失败且新增为零；错误指出 descriptor index/reason/remaining capacity，显式失败不翻转 `sources_truncated`。
- 自动提取按文本顺序填满容量后设置 `sources_truncated=true`，不重排或替换已有来源。
- partial assistant 不提取 URL；模型 URL 标记 unverified。
- arguments/result 脱敏先于 1,000 字符裁剪。
- Artifact 文件、thread reference、run source 的提交顺序和失败补偿。
- 同一 parent 并发创建只有一个成功；最终 coordinator lock 复验能观察 cancel 和新的 parent child。
- 不同 thread 并发 staging 文件互不碰撞；timeout/cancel 后迟到 future 只产生并清理合法 `.artifact.tmp`，不提交权威状态。
- staging file fsync、最终 replace 后目录 fsync、嵌套临时文件递归清理和删除 tombstone 恢复路径。
- Artifact staging 无 worker 容量时以 `TOOL_CAPACITY_EXHAUSTED` 终止 run，不触发模型自动重试。
- persistence failure 不发送 `artifact.created`。
- SourceRecord 已提交而 Artifact 后续失败时仍发送 `sources.updated`，但不发送 `artifact.created`。
- Artifact 在之后的 run failure/cancel 后仍存在。
- 删除 revision/busy/leaf、`ARTIFACT_DELETE_FAILED`、thread cascade 部分失败/回滚、orphan/corrupt 启动对账。
- table column key pattern、attachment filename、固定 MIME、CSP 和 `nosniff`。
- thread-scoped detail/download 不扫描全部 thread 文档，未引用 orphan 和 thread mismatch 均不可访问。
- Artifact 列表遇到 thread 引用但文件缺失时返回 recovery warning，不伪造 metadata 或静默删除引用。

### 27.4 集成与协议测试

- 第 9 次模型调用被阻断，Provider 没有收到第 9 个请求。
- 第 17 次实际工具调用被阻断，handler 没有执行第 17 次。
- 同一模型响应并行给出多个工具时，原子 reservation 仍保证越过上限的 handler 全部零调用。
- 模型 reservation 写 run JSON 失败时 run 终止，Provider 零调用且不发送声称该 reservation 已持久化的 `budget.updated`。
- 工具 reservation 写 run JSON 失败时 run 终止，handler 零调用且不产生 tool provenance。
- active 5 分钟用 fake clock 到期后失败，审批等待不推进 active time。
- 四 worker 饱和，第五个 run 明确失败，释放后新 run 恢复。
- resume 保持之前的次数和原 Policy；运行中 PATCH Policy 不漂移。
- active run 期间 Policy 文件损坏时，持有原 snapshot 的 resume 和终态持久化继续成功；新 start/retry/steer-away 返回 `503 POLICY_CORRUPT`。
- steer-away 的新 Policy/Capability 准入失败时，旧 run 仍为 `awaiting_approval`，pending interrupts/cancelled entries/new user message 均未消费或写入。
- Policy 损坏窗口内的重复提交仍先返回既有 `DUPLICATE_RUN_ACTIVE/TERMINAL` 409，不被 503 覆盖。
- context 裁剪不改 thread JSON，Inspector telemetry 与实际输入一致。
- resume 后下一次模型调用仍在 `awrap_model_call` 的 `ModelRequest` 层执行相同裁剪。
- Artifact 创建事件只在提交后出现，Artifact 可在 REST 重载后恢复。
- source/run 持久化先于 `sources.updated`。
- 最终 revision/budget/source event 先于 terminal event。
- `AgentProtocolBridge` 只放行并校验三类 1D CustomEvent；三类事件不会变成 `UNSUPPORTED_CUSTOM_EVENT`，其他未知事件仍 fail-closed。
- run 列表排序、分页、跨 thread before 拒绝和损坏 warning；Inspector 所需历史 run 可从 REST 独立恢复。
- 后端 restart 对账不破坏合法历史 Artifact。
- start/resume/retry/steer-away、MCP approval、Capability lease 和 secret scan 全部回归。

### 27.5 前端与浏览器测试

- Inspector 四 tab、历史 run 分页/回退、Settings 四 tab、thread-scoped store 和三类事件 stale revision 丢弃。
- Policy CAS/corrupt reset 交互。
- `max_context_chars <= 60000` 显示非阻断配置 warning，保存语义不变。
- 四类 Artifact viewer、200 行预览、下载/删除和版本链。
- Markdown 图片和 embed 不产生任何自动网络请求。
- 来源两种 verification label，不出现评分或推荐措辞。
- running/awaiting approval Composer 行为。
- Skill selection draft 一次 PATCH、revision conflict 丢弃并重载；迁移现有 `CapabilityBar`/`CapabilityManagerDialog` 合同测试。
- 未配置模型 gating、Approval 空状态和 `tablist/tab/tabpanel` 键盘交互。
- 抽屉互斥、Escape、backdrop、focus trap、focus return 和 ARIA。
- Playwright 覆盖 1440x900、1280x800 与 390x844、light/dark screenshot。
- Playwright 覆盖流式文本/工具调用、approve/reject/allowance、steer-away、stop/retry、结构化 409 重载和 Artifact 创建/下载，而不只覆盖静态布局。
- Playwright 使用测试专用 FastAPI app factory；它装配同一 production router/lifespan，只覆盖 model builder、MCP 和本地工具 fixture。配合临时 `VR_DATA_DIR`、本地 fake OpenAI provider/MCP 和固定种子数据，不得调用真实模型、Eastmoney、MCP 服务或用户目录。
- `playwright.config` 管理 backend/frontend `webServer`、固定 `baseURL`、独立端口和 teardown。Chromium 由仓库 setup 脚本安装并支持 `PLAYWRIGHT_DOWNLOAD_HOST`，不依赖全局安装，也不在每次测试中隐式下载。
- 无 console error、横向 overflow、元素重叠和文字溢出。

## 28. 实施切片与退出条件

### 28.1 切片一：治理运行时

交付 Policy、RunControl、middleware、有界 executor、上下文和 usage；第 20 节的 RunDocument 治理字段、历史 run 列表 REST、Policy REST、`protocol.py` 的 `budget.updated` 白名单与 payload 校验同属切片一。退出条件：硬限制集成测试通过，1A-1C 全部回归绿色，Inspector 尚未完成时也能从 run REST 验证权威状态。

### 28.2 切片二：Artifact 与来源

Artifact schema/store/tool/REST/reconciliation 和 provenance 必须同一切片完成；`protocol.py` 对 `artifact.created`/`sources.updated` 的白名单扩展也在本切片交付。第 20 节的 `sources/sources_truncated` 归本切片，不属于治理切片。退出条件：四类 Artifact 可通过工具创建并安全下载，所有一致性失败均无虚假事件，启动对账和 secret scan 通过。

### 28.3 切片三：最终工作台

完成桌面/移动布局、thread-scoped zustand store、历史 run 选择、Inspector、Settings、事件消费和 Playwright。前端直接加入固定版本的 `@playwright/test` devDependency、提交 `package-lock.json`、Playwright config 和隔离的测试 app/harness；提供显式 Chromium setup 脚本，不得依赖开发机全局包。同步更新 Agent Workspace 使用文档、测试安装说明，并在父设计记录 `mcp.health_changed` 的显式降级。退出条件：可视化确认稿对应的桌面和移动交互落地，完整父设计交互 E2E、截图和无 overlap 检查通过。

### 28.4 最终门禁

```bash
cd backend && .venv/bin/pytest -m "not live"
cd frontend && npm test
cd frontend && npm run test:unit
cd frontend && npm run build
cd frontend && npm run test:e2e
git diff --check
```

首次运行浏览器测试前执行文档化的 `npm run test:e2e:install`；它只安装固定版 Chromium，并支持通过 `PLAYWRIGHT_DOWNLOAD_HOST` 使用可达镜像。该 setup 不在每次门禁中隐式执行。

1D 只有在以下条件同时成立时完成：

- 预算在产品 run 范围内跨 resume 硬执行，不是显示值。
- 慢同步工具不会产生无界队列，迟到结果不污染 run。
- 四类 Artifact 安全、不可变、可追踪且可恢复。
- Sources 明确区分执行记录与未验证模型 URL，不作真实性或投资价值评分。
- 三栏桌面和移动抽屉在 1440、1280 临界宽度和 390 移动 viewport、light/dark 下无重叠和 overflow。
- 旧 AI 表面和 1A-1C 的 revision、resume、approval、retry、cancel、Skill、MCP 与密钥隔离合同无回归。

## 29. 后续演进边界

1D 完成后，AG-UI、REST、PolicySnapshot、Artifact 和 SourceRecord 是产品合同。未来替换显式 `StateGraph` 时，可以更换 `AgentFactory`/handle 内部编排，但不得要求前端理解 Graph node、checkpoint 或 middleware 内部状态。

如果未来需要强杀同步工具、多进程运行或跨重启恢复审批，应分别引入进程隔离、数据库/分布式锁和持久化 checkpointer；这些能力不能通过扩大本期线程池或把 secret 写入 checkpoint 来伪造。
