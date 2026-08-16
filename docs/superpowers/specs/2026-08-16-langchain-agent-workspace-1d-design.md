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
- `agent/provenance.py`：工具执行来源、URL 提取/规范化/去重和 200 条上限。

现有模块只做必要接线：

- `models.py` 增加明确的 Policy、usage、source 和 Artifact 线模型。
- `tool_registry.py` 把内置同步调用交给 `BoundedToolExecutor`，保留同 run 串行锁和结果裁剪。
- `capabilities.py` 组合治理 middleware，同时维持 MCP guard/HITL 顺序。
- `runtime.py` 在 create/resume 时把相同 `RunControl` 注入新请求级 middleware。
- `runs.py` 管理 Policy snapshot、active segment、终态映射、Artifact/source 提交与 handle 生命周期。
- `stores.py` 复用原子 JSON 写入原语，不复制新的写盘实现。
- `router.py` 只增加 REST、事件编码和异常到 HTTP/AG-UI 的映射。

前端新增：

- `AgentWorkspace`
- `AgentInspector`
- `RunInspector`
- `ArtifactViewer`
- `SourceInspector`
- `AgentSettingsDrawer`

复用并迁移现有 `AgentThreadList`、`AgentThread`、`ApprovalPanel`、`SkillManager`、`McpManager`、history controller 和 runtime API，不重写已通过合同测试的审批桥。

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
terminal_error | null
```

所有 reservation、segment 和 revision 更新由同一锁保护。每次 Inspector 可见状态发生改变后 `control_revision` 单调递增，用于 `budget.updated` 的乱序丢弃。

### 8.1 模型调用 reservation

在调用 Provider 之前，middleware 原子执行：

1. 检查 run 未取消、未终止。
2. 检查 active 时间仍有余额。
3. 若已 reservation 数等于限制，抛出 `MODEL_CALL_LIMIT_EXCEEDED`。
4. 先递增 reservation，再调用 Provider。

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

一次 assistant message 中的多个 tool call 分别原子 reservation。第 N 个占满额度后，其余未开始调用不得执行；run 以 `TOOL_CALL_LIMIT_EXCEEDED` 失败。

不能使用 LangChain 内置 `ToolCallLimitMiddleware` 作为产品 run 权威计数。

### 8.3 Middleware 装配顺序

模型与上下文治理可以作为外层 middleware；工具执行治理必须位于 MCP guard 和 HITL 的实际执行之后。

请求级装配的逻辑顺序为：

```text
ContextAndModelGovernance
McpArgumentGuard              # 仅存在于有 MCP binding 的 run
HumanInTheLoopMiddleware      # 仅存在于有 MCP binding 的 run
ToolExecutionGovernance       # tool wrapper 中最内层
```

锁定 LangChain 版本中，tool wrapper 的第一个 middleware 是最外层。`HumanInTheLoopMiddleware` 在 `after_model` 阶段阻止进入 tool node；审批恢复后，请求才经过 `McpArgumentGuard` 并最终到达最内层 `ToolExecutionGovernance` reservation。实施测试必须直接验证 reject、pending 和 approve 三条路径的计数，不能只断言 tuple 顺序。

如果 1C 的附加 middleware 也实现 tool wrapper，它必须明确归类为“执行前守卫”或“执行治理”，不得不经审查插入 `ToolExecutionGovernance` 之后。

### 8.4 产品 run 生命周期

start/retry/steer-away 的治理准入顺序：

1. 在任何 user/run 写入前加载并校验最新 Policy；损坏时 fail-closed。
2. 完成既有 duplicate、revision、head、busy 和两阶段 Capability lease 校验。
3. 创建 `PolicySnapshot`、`RunControl` 和 `ActiveRunHandle`；thread 被原子占用时立即开启第一个 active segment，并把完整 `budget_snapshot` 放入新 RunDocument。
4. 持久化 user message/run，持久化等待计入 active elapsed。
5. 每次模型/工具 reservation 在真实调用前同步到 run JSON；持久化失败则不发起对应外部调用并终止 run。
6. usage、context telemetry、segment 关闭和终态在既有 run/thread 锁下持久化，再发送相应事件。

resume 不执行步骤 1 或创建新 control，只复用 handle 中的 snapshot/control 并重开 active segment。Policy UI 修改、文件变化或默认值变化都不能改变正在 resume 的 run。retry 和 steer-away 是新产品 run，完整执行以上六步。

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

在每个模型/工具调用边界检查剩余 active 时间，并以剩余 active 时间包裹等待。到期后停止等待、禁止任何后续步骤并以 `RUN_ACTIVE_TIMEOUT` 失败。对于不能被 Python 强杀的同步调用，遵循第 10 节的迟到结果规则。

## 10. 有界同步工具执行器

### 10.1 容量模型

进程级 `BoundedToolExecutor` 使用：

```text
ThreadPoolExecutor(max_workers=4)
BoundedSemaphore(4)
capacity_wait_seconds=1
```

调用流程固定为：

1. 在 submit 前等待 capacity token，等待时间计入 active elapsed。
2. 一秒内未获得 token，抛出 `TOOL_CAPACITY_EXHAUSTED`；不得进入 executor 队列。
3. 获得 token 后 submit。
4. future 完成回调释放 token，而不是等待者超时或取消时释放。
5. 等待者以 `min(tool deadline, active deadline)` 等待结果。
6. 超时后尝试 `future.cancel()`；若已经运行，停止等待并丢弃迟到结果，但 token 继续占用到 future 实际退出。

这保证 executor 中最多四个已提交但未退出的调用，没有无界排队。服务关闭时停止接受新任务并以有界方式 shutdown；不能因第三方永久阻塞而无限卡住 FastAPI shutdown。

### 10.2 工具类型

- 内置同步工具经此 executor 执行。
- 同一产品 run 继续使用现有 `asyncio.Lock` 串行执行内置工具，尤其不能并行 Eastmoney throttled fetch。
- Skill 工具是受控本地异步读取，不占同步 worker，但仍受工具次数、tool deadline 和 active deadline 限制。
- MCP 工具不占同步 worker；外层 Governance 以 Policy deadline 等待它。MCP Registry 既有 60 秒连接/调用生命周期限制继续存在，实际截止取更早者。
- Artifact 的序列化与临时文件准备使用同一有界 executor，但 worker 只能写固定命名的 staging file，不能修改 thread/run 或把文件落到最终 ID；权威提交遵循第 15 节。持久化错误不能转为普通可纠正工具结果。

`TOOL_TIMEOUT` 表示单工具 Policy deadline；`RUN_ACTIVE_TIMEOUT` 优先表示产品 run active 总期限先耗尽。

## 11. 确定性上下文裁剪

### 11.1 字符计量

上下文限制发生在 Provider 格式化之前。字符数使用统一 canonical renderer：

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

错误写入 `RunDocument.error_code/error_message`，message 必须脱敏且面向用户。最终 `budget.updated` 和 `thread.revision.updated` 先于 AG-UI terminal event。

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
- 单个完整 Artifact JSON 的 UTF-8 序列化上限为 1,048,576 bytes。
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

- 1-50 列，column key 唯一且非空，label 长度 1-100。
- 最多 5,000 行。
- 每个 row 只能包含已声明 key。
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

- `tool_call` 只能引用当前产品 run 已完成并已持久化的工具调用，不能引用 pending、rejected、其他 run 或不存在的 ID。
- `url` 只接受绝对 HTTP/HTTPS URL；只规范化和记录，不发网络请求。
- 对 `sources` 类型 Artifact，`source_index` 必须在本次 `sources` 数组范围内且唯一；解析后持久化为对应 `source_id`，输入中的索引不落盘。

可纠正错误作为结构化 tool result 返回，Agent 可以修改输入后重试：类型/schema、大小、父链、来源引用和业务冲突错误。

下列错误表示存储一致性无法保证，必须以 `ARTIFACT_PERSISTENCE_FAILED` 终止 run：Artifact 文件写入失败、thread reference 提交失败且补偿失败、来源/run 提交失败或已提交后的事件状态无法确定。此类错误不能伪装成普通工具结果。

提交顺序：

1. 在 thread/run 锁下复验 run、thread、parent leaf 和 source descriptors。
2. 有界 executor 只把完整 Artifact JSON 写入固定命名的 staging file；如果 timeout/cancel，迟到 future 只能产生该临时文件，完成回调负责清理，绝不能晚到提交权威状态。
3. future 在 deadline 前返回且 run 仍活跃后，进入不含 await 的短提交区：先把新增 SourceRecord 原子提交到 run；这些记录独立描述本次已执行工具/模型提供 URL，即使随后 Artifact 失败也仍是合法 provenance。
4. 使用 `os.replace` 把 staging file 原子落为最终 Artifact JSON。
5. 原子提交 thread `artifact_ids` 和递增 revision。
6. 只有以上提交成功才返回工具成功并发出 `artifact.created`。

步骤 2 失败或迟到时不进入提交区；步骤 3 失败时删除 staging file；步骤 4 或 5 失败时删除尚未被 thread 引用的新文件，删除也失败则记录 orphan warning并交给启动对账。JSON 文件之间不虚构事务；已提交的独立 SourceRecord 不回滚。任何失败都不能发送虚假的 `artifact.created`。

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
- 完整 assistant message 持久化后，从文本 content 中提取 HTTP/HTTPS URL，记录为 `model_url`。
- partial 和 pending-interrupt assistant message 不提取 URL。
- 工具来源按 `tool_call_id` 去重。
- URL 去重 key：scheme/host 小写、移除 fragment、移除默认端口、空 path 规范为 `/`；不改 query 顺序，不跟随 redirect。
- 达到 200 条后停止新增，设置 `sources_truncated=true`；已有来源顺序不变，不做评分或优先级替换。

Artifact 的 `source_ids` 只引用同一 run 的 SourceRecord。下载 Artifact 时保留 ID，不把 run 中的 summary 复制进 Artifact 文件。

## 17. Artifact REST 与删除一致性

新增接口：

```text
GET    /api/agent/threads/{thread_id}/artifacts
GET    /api/agent/artifacts/{artifact_id}
GET    /api/agent/artifacts/{artifact_id}/download
DELETE /api/agent/artifacts/{artifact_id}?thread_revision=<revision>
```

列表按 `created_at` 升序返回轻量 metadata、parent/child 状态；detail 返回完整类型化 content。

`artifact_id` 不包含 thread 路径信息，也不新增第二份全局索引。Artifact service 先从 thread 文档的 `artifact_ids` 解析唯一所属 thread，再构造受控路径；零个引用返回 404，多个 thread 引用同一 ID 视为存储损坏并隔离。未被 thread 引用的 orphan 不能通过 detail/download API 暴露。

download：

- Markdown 使用 `.md`，table 使用 `.json`，JSON 使用 `.json`，sources 使用 `.json`。
- attachment filename 只由服务端 Artifact ID 和固定扩展名构造，不使用用户 title。
- 设置 `Content-Disposition: attachment`、`X-Content-Type-Options: nosniff` 和限制性 CSP。
- JSON/Markdown 均以 UTF-8 返回；不返回 `text/html` 或 `image/svg+xml`。

删除步骤：

1. 校验 `thread_revision`、Artifact 属于该 thread、thread 当前没有 `running/awaiting_approval` run，且 Artifact 是叶子。
2. 从 thread `artifact_ids` 移除并原子提交新 revision。
3. 删除 Artifact 文件并同步父目录。
4. 删除文件失败时保留已经提交的 thread 状态，并记录显式 orphan warning；不得把引用重新写回造成 revision 回滚。

成功返回新 thread revision。revision 冲突返回 `409 THREAD_REVISION_CONFLICT`；busy 返回 `409 THREAD_BUSY`。

删除 thread 时，必须先在锁内确认不 busy，然后删除该 thread 的全部 Artifact 和 run 文件，最后删除 thread 文件。任一 cleanup 失败都保留 thread 文档并返回错误，不能留下一个已经消失但数据未清理的 thread。

## 18. 启动对账与迁移

不批量重写旧 JSON，继续保留 `schema_version=1`，字段只做 additive 扩展：

- 旧 `budget_snapshot={}`：无治理数据。
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

## 19. AG-UI 事件合同

REST 使用 snake_case；自定义 AG-UI event name 和 payload 字段使用 camelCase。

### 19.1 budget.updated

```json
{
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
  "runId": "run-id",
  "sourceCount": 7,
  "sourcesTruncated": false
}
```

只有 run JSON 已持久化后发送；payload 不重复发送 source 原文，Inspector 通过已有 event state 或 REST 读取权威数据。

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
usage.token_status: available | partial | unavailable
usage.total_tokens: int | null
context_truncation: ContextTruncation
sources: SourceRecord[]
sources_truncated: bool
```

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
| 500 | `ARTIFACT_PERSISTENCE_FAILED` | 写盘/一致性失败，不能宣称成功 |
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

## 22. Inspector

Inspector 只展示当前 thread/当前或选中 run 的运行产物，不承担全局设置。

四个 tab：

- `Run`：状态、active/approval/wall 时间、模型/工具次数与上限、Provider token status、context truncation、终态错误。
- `Approval`：复用 `ApprovalPanel`，展示全部 pending interrupt 和 once/thread-session/reject 动作。
- `Artifact`：以当前/选中 run 为主列出 Artifact，并补充同 thread 的父子版本链；支持安全预览、下载、删除叶子和跳转版本。
- `Sources`：工具执行记录和模型 URL 分组展示，明确标注“执行记录”与“模型提供，未验证”，不显示排名。

tab badge 只显示数量或 pending 状态。来源/Artifact 为空时使用简短空状态，不出现功能宣传文案。

## 23. Settings drawer

Settings 是独立抽屉，包含四个 tab：

- `Model`：浏览器本地 `vr-agent-model`，沿用专用 secret header 和 HTTPS/loopback 规则。
- `Skills`：复用 `SkillManager`；选择仍是 thread-scoped，并携带 thread revision。
- `MCP`：复用 `McpManager`；配置仍是 backend-global。
- `Policy`：编辑 backend-global Policy，显示 revision、范围、默认值、保存冲突和损坏 reset。

当任何前端已知 thread 为 `running` 或 `awaiting_approval` 时，Model reference 不可改变，避免 resume 产生 `RUN_CONFIG_MISMATCH`。Policy 可以修改，因为 active run 使用快照。Skill selection 继续受当前 thread busy/revision 规则约束。

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
- 390x844 不得出现横向 overflow、工具栏重叠、按钮文字溢出或 Composer 被遮挡。
- 桌面和移动端都保持 thread、chat、Inspector 自身滚动位置稳定。

## 25. Artifact 前端安全

- Markdown renderer 禁用 raw HTML，不加载任意 iframe、script 或远端 embed。
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

- Policy missing/default、范围、unknown field、CAS conflict、正常 reset、corrupt fail-closed 和显式 reset。
- `RunControl` 在并发 reservation 下不超过模型/工具上限。
- start/resume 复用同一计数；retry/steer-away 使用新快照。
- active segment 暂停审批等待，模型/工具/capacity 等待计入。
- token `available/partial/unavailable` 聚合。
- context canonical 计数、完整 user turn、tool pair 原子、Skill 指令保留、强制内容超限。
- reject/pending MCP 不计工具调用，approve/allowance/业务 error 计数。

### 27.2 Executor 测试

- 四个同步 worker 被占用时，第五个在一秒内得到 `TOOL_CAPACITY_EXHAUSTED`。
- submit 前取得 token，executor queue 永不超过容量。
- timeout/cancel 后，运行中 future 仍占容量，迟到结果被丢弃。
- 全部 future 实际退出后容量恢复。
- 同一 run 的内置工具保持串行；Eastmoney 分类的调用绝不并发。
- MCP 60 秒生命周期限制仍存在，Policy 以更短 deadline 外包裹。

### 27.3 Artifact 与来源测试

- 四种 schema 的合法/非法边界、UTF-8 1 MB、table 50/5000、JSON depth/node、sources 200。
- ID/path traversal、symlink、跨 thread/run、跨类型 parent、分叉、cycle 和非叶删除。
- 只允许当前 run completed tool call 来源；URL scheme/规范化/去重。
- partial assistant 不提取 URL；模型 URL 标记 unverified。
- arguments/result 脱敏先于 1,000 字符裁剪。
- Artifact 文件、thread reference、run source 的提交顺序和失败补偿。
- persistence failure 不发送 `artifact.created`。
- Artifact 在之后的 run failure/cancel 后仍存在。
- 删除 revision/busy/leaf、thread cascade、orphan/corrupt 启动对账。
- attachment filename、MIME、CSP 和 `nosniff`。

### 27.4 集成与协议测试

- 第 9 次模型调用被阻断，Provider 没有收到第 9 个请求。
- 第 17 次实际工具调用被阻断，handler 没有执行第 17 次。
- active 5 分钟用 fake clock 到期后失败，审批等待不推进 active time。
- 四 worker 饱和，第五个 run 明确失败，释放后新 run 恢复。
- resume 保持之前的次数和原 Policy；运行中 PATCH Policy 不漂移。
- context 裁剪不改 thread JSON，Inspector telemetry 与实际输入一致。
- Artifact 创建事件只在提交后出现，Artifact 可在 REST 重载后恢复。
- source/run 持久化先于 `sources.updated`。
- 最终 revision/budget/source event 先于 terminal event。
- 后端 restart 对账不破坏合法历史 Artifact。
- start/resume/retry/steer-away、MCP approval、Capability lease 和 secret scan 全部回归。

### 27.5 前端与浏览器测试

- Inspector 四 tab、Settings 四 tab 和事件 stale revision 丢弃。
- Policy CAS/corrupt reset 交互。
- 四类 Artifact viewer、200 行预览、下载/删除和版本链。
- 来源两种 verification label，不出现评分或推荐措辞。
- running/awaiting approval Composer 行为。
- 抽屉互斥、Escape、backdrop、focus trap、focus return 和 ARIA。
- Playwright 覆盖 1440x900 与 390x844、light/dark screenshot。
- 无 console error、横向 overflow、元素重叠和文字溢出。

## 28. 实施切片与退出条件

### 28.1 切片一：治理运行时

交付 Policy、RunControl、middleware、有界 executor、上下文和 usage。退出条件：硬限制集成测试通过，1A-1C 全部回归绿色，Inspector 尚未完成时也能从 run REST 验证权威状态。

### 28.2 切片二：Artifact 与来源

Artifact schema/store/tool/REST/reconciliation 和 provenance 必须同一切片完成。退出条件：四类 Artifact 可通过工具创建并安全下载，所有一致性失败均无虚假事件，启动对账和 secret scan 通过。

### 28.3 切片三：最终工作台

完成桌面/移动布局、Inspector、Settings、事件和 Playwright。退出条件：可视化确认稿对应的桌面和移动交互落地，所有自动化、截图、无 overlap 检查通过。

### 28.4 最终门禁

```bash
cd backend && .venv/bin/pytest -m "not live"
cd frontend && npm test
cd frontend && npx vitest run
cd frontend && npm run build
cd frontend && npx playwright test
git diff --check
```

1D 只有在以下条件同时成立时完成：

- 预算在产品 run 范围内跨 resume 硬执行，不是显示值。
- 慢同步工具不会产生无界队列，迟到结果不污染 run。
- 四类 Artifact 安全、不可变、可追踪且可恢复。
- Sources 明确区分执行记录与未验证模型 URL，不作真实性或投资价值评分。
- 三栏桌面和移动抽屉在两个验收 viewport、light/dark 下无重叠和 overflow。
- 旧 AI 表面和 1A-1C 的 revision、resume、approval、retry、cancel、Skill、MCP 与密钥隔离合同无回归。

## 29. 后续演进边界

1D 完成后，AG-UI、REST、PolicySnapshot、Artifact 和 SourceRecord 是产品合同。未来替换显式 `StateGraph` 时，可以更换 `AgentFactory`/handle 内部编排，但不得要求前端理解 Graph node、checkpoint 或 middleware 内部状态。

如果未来需要强杀同步工具、多进程运行或跨重启恢复审批，应分别引入进程隔离、数据库/分布式锁和持久化 checkpointer；这些能力不能通过扩大本期线程池或把 secret 写入 checkpoint 来伪造。
