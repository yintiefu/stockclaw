# LangChain Agent 工作台第一期设计

**日期：** 2026-08-15

**状态：** 已根据第四轮评审修订，待书面复审

## 1. 目标

在不替换、不迁移现有 AI 功能的前提下，为 Vibe-Research 新增一个独立 Agent 工作台。

第一期是一个完整版本，分四个里程碑交付：

1. 使用 LangChain `create_agent` 建立可调用投研工具的 Agent loop，并通过 AG-UI 流式连接前端。
2. 在服务端用户目录中使用 JSON 文件保存会话和运行记录。
3. 支持用户目录中的完整 Skill，以及后端管理的 MCP Tools。
4. 完成工具审批、投研 Artifact、运行预算、配置和三栏工作台。

第一期只使用 LangChain 的高层 Agent API。系统必须保留一个窄的运行时替换点，使后续可以将 `create_agent` 替换为显式 LangGraph `StateGraph`，而不重写前端协议、工具、Skill、MCP、Artifact 和管理 API。

## 2. 当前项目基础

当前项目包含：

- React 19、Vite、TypeScript、Tailwind 前端。
- Python FastAPI 后端。
- `backend/chat.py` 中的自研 OpenAI-compatible function-calling loop。
- `backend/tools.py` 中 24 个可复用的客观投研数据工具。
- AI 复盘、页面问 AI、多空辩论和反思审计。
- `backend/portfolio.py` 与 `backend/myreports.py` 已有的临时文件加 `os.replace` 原子改名模式；文件与目录 `fsync` 是本期新增加固要求。
- 浏览器本地保存的旧 LLM 配置和旧聊天记录。

新 Agent 子系统只做增量接入。第一期不修改现有 AI 入口的行为。

## 3. 已确认决策

| 范围 | 决策 |
|---|---|
| 前端组件层 | 在现有 React/Vite 中嵌入 `assistant-ui` |
| 前端 Runtime | `@assistant-ui/react-ag-ui` + `@ag-ui/client` |
| 前后端协议 | AG-UI HTTP 流 |
| 协议兼容 | 自定义薄桥接层统一标准 interrupt outcome 与 `resume[]` |
| 后端进程 | 复用现有 FastAPI |
| Agent Runtime | `ag-ui-langgraph` 包装 LangChain `create_agent` |
| 模型 | OpenAI 与 OpenAI-compatible API |
| 旧 CLI 订阅 | 新 Agent 第一期不支持 |
| 会话 | 服务端用户目录 JSON 文件 |
| Skill | 用户目录保存完整 Skill 目录 |
| Skill 脚本 | 完整保存，加载并展示文件清单，但绝不读取内容或执行 |
| MCP 传输 | stdio 与 Streamable HTTP |
| MCP 能力 | 只接 Tools；不接 OAuth、Resources、Prompts |
| 工具审批 | 内置只读工具自动执行；MCP 工具默认逐次审批，可放行到当前会话结束 |
| Artifact | Markdown、表格、JSON 快照、来源清单 |
| 预算 | 对步骤、工具、时长和上下文做硬限制；token 只记录，货币费用只估算 |
| 页面布局 | 左侧会话、中间对话、右侧 Inspector 的三栏工作台 |
| 崩溃恢复 | 从最后完整历史重试；第一期不做运行栈原位续跑 |
| 运行约束 | 第一期间 Agent 子系统只支持单 FastAPI 进程/worker |

## 4. 第一期间明确不做

- 不迁移或删除 `/api/chat`、`AskAiButton`、AI 复盘、辩论和反思。
- 不引入独立 LangGraph Server 或 LangSmith 部署。
- 不实现自定义多节点、多 Agent `StateGraph`。
- 不保证后端重启后从原 token、工具栈或 interrupt 精确续跑。
- 不执行 Skill 中的 `scripts/`，不引入 shell 或代码沙箱。
- 不支持 MCP OAuth、Resources、Prompts 或远程市场。
- 不支持任意 HTML、JavaScript、React 或可执行代码 Artifact。
- 不桥接 Claude Code、Codex、Qwen 等现有 CLI 订阅。
- 不做多用户、租户隔离、计费或云同步。

## 5. 总体架构

```text
现有 React/Vite
  -> /agent
     -> AgentWorkspace
        -> AgentThreadList
        -> assistant-ui AgentThread
        -> AgentInspector
     -> HttpAgent + useAgUiRuntime
        -> AG-UI POST /api/agent/run

现有 FastAPI
  -> agent.router
     -> 自定义 FastAPI AG-UI Endpoint
     -> AgentProtocolBridge
        -> 标准 AG-UI interrupt/resume
        -> ag-ui-langgraph 0.0.42 兼容格式
     -> thread/config/artifact REST API
  -> AgentFactory
     -> 第一期：LangChain create_agent
     -> 后续：显式 LangGraph StateGraph
  -> ToolRegistry
     -> backend/tools.py
     -> MCPRegistry tools
     -> Artifact tools
  -> SkillRegistry
  -> Policy Middleware
  -> RunCoordinator
     -> thread session allowances
     -> ActiveRunHandle
        -> MemorySaver + immutable sanitized run snapshot
        -> request-scoped CompiledGraph
        -> cancel state + pending interrupts
  -> ~/.vibe-research/agent/ JSON 与目录存储
```

稳定边界：

- 活跃运行与前端之间使用 AG-UI。
- 会话、Skill、MCP、Policy、Artifact 管理使用 REST。
- `AgentProtocolBridge` 隔离前端标准 AG-UI 与后端适配器的版本差异。
- `AgentFactory` 通过 `AgentRuntimeHandle` 接口隔离产品服务与具体编排实现。
- `ToolRegistry` 隔离 LangChain 与内置/MCP/Artifact 工具。

`AgentRuntimeHandle` 只暴露 `stream(input)`、`resume(entries)`、`cancel()` 和 `close()`，不得向 router、store 或 UI 暴露 Graph state、node name 或 checkpoint 格式。后续进入显式 LangGraph 时，只替换 `AgentFactory` 产出的 handle 内部实现。

## 6. 依赖基线

第一版使用以下精确版本：

### 前端

- `@assistant-ui/react@0.15.14`
- `@assistant-ui/react-ag-ui@0.0.54`
- `@ag-ui/client@0.0.58`

### 后端

- `langchain==1.3.15`
- `langchain-openai==1.5.1`
- `langgraph==1.2.9`
- `ag-ui-langgraph==0.0.42`
- `ag-ui-protocol==0.1.15`
- `ag-ui-a2ui-toolkit==0.0.4`
- `langchain-mcp-adapters==0.3.2`

前端必须提交 `package-lock.json`，后端必须直接锁定 `ag-ui-protocol`、`ag-ui-a2ui-toolkit` 和 `langgraph`，不能依赖 `ag-ui-langgraph` 的宽松传递范围。`ag-ui-a2ui-toolkit` 会在适配器导入时进入关键路径，因此也属于协议锁定组。AG-UI 相关 0.x 依赖不得使用宽松版本范围；升级必须整组进行，并先通过协议合同测试。

### 6.1 锁定版本的兼容边界

锁定版本存在已确认的协议差异：

- `@assistant-ui/react-ag-ui@0.0.54` 消费 `RUN_FINISHED.outcome.type="interrupt"`，并在下一次 `RunAgentInput.resume[]` 中提交结果。
- `ag-ui-langgraph==0.0.42` 原生发出 `CUSTOM/on_interrupt`，并从 `forwarded_props.command.resume` 读取恢复值。
- `add_langgraph_fastapi_endpoint` 只接受预先构建的固定 Agent，不能满足本项目按请求注入模型密钥、Skills、MCP 与 Policy 的要求。

因此第一期不得直接注册 `add_langgraph_fastapi_endpoint`。`router.py` 使用自定义 FastAPI route，并通过 `AgentProtocolBridge` 完成以下转换：

1. 收集后端 `CUSTOM/on_interrupt`，校验为项目定义的工具审批结构，不把旧 CUSTOM 事件继续发送到前端。锁定适配器序列化后的事件没有可依赖的结构化 LangGraph interrupt ID，因此首次观察每个 interrupt 时由 bridge 生成稳定 UUID，作为前端可见的 `bridge_interrupt_id`。
2. 把终止事件转换为带标准 `interrupt` outcome 的 `RUN_FINISHED`。
3. 对纯 resume，把前端标准 `ResumeEntry[]` 按 `bridge_interrupt_id` 完整校验后，转换为 LangChain human-in-the-loop middleware 的 decision payload，再交给适配器的 `command.resume`；对 steer-away，识别全量 cancelled entries 后交给 `RunCoordinator` 结束旧 run，不把 cancellation 当作 middleware decision。
4. malformed、缺失、重复或未知 `bridge_interrupt_id` 必须产生协议错误，不能按默认允许处理。

`ActiveRunHandle` 保存 `bridge_interrupt_id -> 原始顺序、tool_call_id、审批结构` 的不可变映射，thread interrupt metadata 保存同一个 bridge ID 供页面刷新后恢复展示。相同 pending interrupt 被再次读取时必须复用原 ID。bridge 先针对该映射完成全量 ID 与 tool call 校验，再严格按原始顺序生成 `{"decisions": [...]}` 列表交给 LangGraph；不得把 bridge ID 当作 LangGraph 内部 ID，也不得使用 ID-map resume 路径。

锁定版本使用 `HumanInTheLoopMiddleware(interrupt_on=...)`；项目对外审批 payload 的 `decision` 字段由 bridge 转换为中间件 decision 列表中的 `type` 字段。每个 HTTP 请求创建新的 `LangGraphAgent` 包装当前请求的 Graph，不能复用共享实例，也不能依赖库的 `clone()` 隔离 Graph，因为锁定版本的 clone 仍共享底层 Graph。

该桥接是版本隔离层，不包含投研业务规则。1A 必须先完成离线协议 Spike；如果上述转换、请求级实例隔离或跨同构 Graph 恢复不能在锁定版本下通过合同测试，则停止后续里程碑并重新选择兼容版本，不能绕过审批降级上线。

## 7. 前端设计

### 7.1 新入口

新增导航项与 `/agent` 路由。它是完整工作台，不替换原页面上的 AI 按钮。

桌面布局：

```text
+----------------+--------------------------------+----------------------+
| 会话           | 对话                           | Inspector            |
|                |                                |                      |
| 新建会话       | assistant-ui messages          | 运行                 |
| 搜索/列表      | tool call parts                | 审批                 |
| 重命名/删除    | composer                       | Artifact             |
|                |                                | 来源                 |
+----------------+--------------------------------+----------------------+
```

窄屏下保留对话主区，会话列表和 Inspector 分别变成抽屉，同一时间只允许打开一个抽屉。

### 7.2 组件职责

- `pages/Agent.tsx`：路由入口，只负责装配。
- `components/agent/AgentWorkspace.tsx`：三栏与响应式抽屉。
- `components/agent/AgentThreadList.tsx`：新建、切换、重命名、删除会话。
- `components/agent/AgentThread.tsx`：assistant-ui Thread 与 Composer。
- `components/agent/AgentInspector.tsx`：运行、审批、Artifact、来源标签页。
- `components/agent/ToolApproval.tsx`：MCP 允许与拒绝交互。
- `components/agent/ArtifactViewer.tsx`：四类 Artifact 的安全渲染。
- `components/agent/AgentSettings.tsx`：模型、MCP、Skill、Policy 设置抽屉。
- `lib/agent/runtime.tsx`：`HttpAgent`、`useAgUiRuntime`、history adapter 与 interrupt 隔离。
- `lib/agent/api.ts`：管理 REST 客户端。
- `lib/agent/types.ts`：前端 API 与产品状态类型。

`runtime.tsx` 不包含投研业务规则。`ApprovalBridge` 是前端唯一允许调用版本敏感的 `useAgUiInterrupts`、`useAgUiSubmitInterruptResponses` 和 `useAgUiSteerAway` API 的模块，其他组件只消费项目自己的审批状态与 action。

产品 run 为 `running` 时 Composer 禁用，只保留 Stop；确认旧 run 已持久化为终态并重新加载最新 revision 后才重新启用。`awaiting_approval` 时 Composer 可用，用于触发已经定义的全量 cancelled steer-away。第一期不支持在普通流式生成过程中用一条新消息同时取消并改向。

### 7.3 AG-UI 事件

通用交互只使用标准事件：

- Run start、finish、cancel、error。
- 流式文本。
- Tool call start、args、end、result。
- State snapshot 与 delta。
- 标准 interrupt outcome。

项目自定义事件限定为：

- `artifact.created`
- `sources.updated`
- `budget.updated`
- `mcp.health_changed`
- `thread.revision.updated`

`thread.revision.updated` 只在 thread JSON 原子提交成功后由 `AgentProtocolBridge` 发出，payload 固定为 `thread_id`、`revision` 和 `persisted_at`。Runtime 按 `thread_id` 截获并更新对应 thread 的本地 revision，不渲染到聊天正文，也不把 revision 写入 LangGraph state 或 checkpoint。来自 AG-UI 事件和 REST PATCH response 的 revision 都只在大于该 thread 本地值时应用，避免跨通道乱序使 revision 倒退。终态 revision 事件必须先于对应 `RUN_FINISHED` 发出。若事件因断连丢失，下一次提交会收到 `409 THREAD_REVISION_CONFLICT`，前端再通过 REST 重载权威历史。

第一期生成的标准 interrupt outcome 不设置 `expiresAt`，后端 pending interrupt 也不实现基于时间的过期；它只在提交有效 resume、steer-away、run 取消或进程结束时失效。

其余自定义事件通过注册的数据组件渲染。未知事件不得把原始 JSON 直接展示在聊天正文中。

### 7.4 模型配置

新 Agent 使用独立的浏览器本地模型配置，不复用旧 `vr-llm` key。保存 provider 标签、Base URL、model 和 API Key。

provider、Base URL 和 model 通过 AG-UI `forwardedProps.runtime.model` 提供。模型 API Key 单独放在当前请求的 `X-VR-Agent-Model-Key` header 中，不能进入 AG-UI body、`forwardedProps`、Graph state、RunnableConfig metadata 或 callback payload。项目已有的 `Authorization` header 继续只承载 `VR_API_KEY`，两类密钥不能复用同一个 header。

承载模型 API Key 的请求只有在客户端来源为 loopback（`127.0.0.1` 或 `::1`）时才允许使用 HTTP；非 loopback 请求必须是 HTTPS，否则在构建 Graph 前拒绝。默认不信任 `X-Forwarded-Proto`。只有显式设置 `VR_TRUST_PROXY_HEADERS=1` 且直接连接的源 IP 位于 `VR_TRUSTED_PROXY_IPS` 时，才使用该 header 判断原始 scheme。开发期 Vite proxy 通过 loopback 例外工作。

自定义 FastAPI endpoint 在请求边界读取 API Key，构造只在当前 request task 中存在的 `RunSecrets`，并将不含密钥的 `ModelRef` 交给其余运行时。日志、异常、run JSON 和任何事件编码前都必须经过脱敏。请求执行期间，request-scoped Graph 中的 `ChatOpenAI` 实例可以在内存中持有 provider 的 secret wrapper；进入 `awaiting_approval` 前必须释放 Graph 和模型引用，只保留 MemorySaver 与不含密钥的 immutable run snapshot。需要恢复审批时，前端随 resume 请求重新提供 API Key，`AgentFactory` 使用同一 MemorySaver 和 snapshot 重建等价 Graph。resume 缺少模型密钥时必须在 Graph 构建前失败；允许使用不同的原始密钥，但不含密钥的 `ModelRef` 必须与 run snapshot 完全相同，否则返回 `409 RUN_CONFIG_MISMATCH`。原始 API Key 不得进入 MemorySaver、`ActiveRunHandle`、Graph state、RunnableConfig metadata 或 callback payload。

## 8. 后端设计

新增 `backend/agent/` 包：

- `router.py`：AG-UI 与管理 API。
- `protocol.py`：标准 AG-UI 与 `ag-ui-langgraph` 锁定版本之间的 interrupt/resume 兼容桥。
- `runtime.py`：模型构造、`AgentFactory`、`create_agent` 和 middleware。
- `tool_registry.py`：合并内置、MCP、Artifact 工具。
- `mcp_registry.py`：MCP 配置、连接生命周期、健康和工具包装。
- `skill_registry.py`：Skill 扫描、校验、导入和受控资源读取。
- `policy.py`：审批、预算、超时、上下文裁剪和结果脱敏。
- `stores.py`：thread、run、policy、MCP JSON 存储。
- `artifacts.py`：Artifact 校验、存取和下载。
- `runs.py`：活跃运行、取消、状态和 usage。
- `models.py`：Pydantic API 与持久化模型。

`backend/app.py` 只新增 router 注册，并给 CORS methods 增加 `PATCH`。现有 AI 模块不迁移。

### 8.1 AgentFactory

每次新产品 run：

1. endpoint 从专用 header 提取 `RunSecrets`，校验不含密钥的模型配置，并对 Base URL 应用现有 SSRF 安全姿态。
2. 加载 thread 与 policy 快照。
3. 解析本会话启用的 Skills。
4. 合并工具集合。
5. 组装 LangChain middleware。
6. 创建独立 MemorySaver 和 immutable sanitized run snapshot，使用当前 request 的密钥构建 request-scoped `create_agent` Graph，并包装成 `ActiveRunHandle`。
7. `RunCoordinator` 在产品 run 处于 `running` 或 `awaiting_approval` 时持有该 handle。
8. 为本次 HTTP 请求创建新的 `LangGraphAgent`，交给 `AgentProtocolBridge` 流式执行；请求结束后丢弃该适配器实例。

Graph 进入 interrupt 后，handle 必须释放 Graph 和模型对象，仅保留 MemorySaver、工具/Skill/Policy 的不可变无密钥快照及 pending interrupt。resume 使用新请求提供的密钥和原 MemorySaver 重建同构 Graph；该行为必须由 1A 合同测试验证。若锁定版本无法跨同构 Graph 实例恢复同一个 MemorySaver checkpoint，则协议 Spike 失败，不能通过在 awaiting approval 期间长期保留 API Key 来绕过。

一个产品 run 可以对应多个 AG-UI protocol run ID：首个请求、每次审批 resume 都有新的 protocol run ID，但继续更新同一个 run JSON。run 文档保存 `protocol_run_ids`；retry 创建新的产品 run ID，并通过 `retry_of` 指向目标 run。

同一个 thread 同时只允许一个 `ActiveRunHandle`。`RunCoordinator` 必须以原子方式完成检查与占用：

- 普通新消息遇到 `running` 或 `awaiting_approval` 返回 `409 THREAD_BUSY`。
- 携带完整 `resume[]` 且匹配该 handle 全部 pending interrupt 的请求，可以穿过 busy 守卫并恢复同一产品 run。
- `useAgUiSteerAway` 产生的“全部 pending interrupt 为 cancelled，加恰好一条新 user message”请求可以穿过 busy 守卫。coordinator 在 thread 锁内校验全部 `bridge_interrupt_id`，先把旧产品 run 持久化为 `cancelled` 并关闭旧 handle，再为新消息创建新产品 run；pending MCP 工具不得执行。这个单次 HTTP 请求随后流式执行新 run，传入的 protocol run ID 只归属新产品 run。这里保证进程内无并发穿插，但不虚构跨两个 JSON 文件的事务；若新 run 写入失败，旧 run 保持 cancelled，返回持久化错误并要求前端重载。
- retry 只有在 thread 没有活跃 handle 时才能开始；遇到 `running` 或 `awaiting_approval` 返回 `409 THREAD_BUSY`。
- `completed`、`failed`、`cancelled` 或 `interrupted` 后立即 `close()` 并从 coordinator 释放 handle。
- 后端关闭时依次取消并关闭全部 handle；后端重启后不重建 MemorySaver，而是把遗留状态改为 `interrupted`。

`/api/agent/run` 只接受四种请求形状：

1. start：不含 `resume[]`，相对服务端 head 恰好有一条新 user message。
2. resume：包含当前全部 pending interrupt 的 decision，不含新 user message。
3. steer-away：当前全部 pending interrupt 都是 cancelled，且相对服务端 head 恰好有一条新 user message。
4. retry：`forwardedProps.runtime.retryOf` 包含目标产品 run ID，不含 `resume[]`，也不含新 user message。

其他混合形状一律作为协议错误失败关闭。steer-away 保留旧 pending assistant/tool-call turn 供 UI 显示，但该 turn 和 partial message 都不进入新 run 的模型输入。

retry 目标必须属于同一 thread，是该 thread 最近一次产品 run，状态为 `failed`、`cancelled` 或 `interrupted`，并且目标之后没有新增完整消息；否则返回 `409 RETRY_NOT_ALLOWED`。retry 不重复追加触发目标 run 的 user message，而是从目标 run 已持久化的最后完整、非 partial、非 pending-interrupt 历史继续，创建带 `retry_of` 的新产品 run。它使用请求中的当前 `ModelRef`、thread 当前选择的 Skills 和当前 policy 快照，因此是显式重新执行，不是目标 run 的逐字回放。模型密钥、HTTPS、revision、head、duplicate 和 busy 校验与 start 完全相同。

客户端提交的 messages 只用于 revision、head、message ID 和请求形状校验。`AgentProtocolBridge` 必须以服务端 thread JSON 重建交给适配器的 `RunAgentInput.messages`，不能把客户端历史直接合并进 Graph：start 使用最后完整历史和本次已接受的 user message；纯 resume 将协议必填的 `messages` 明确设为空数组，只通过 `forwarded_props.command.resume` 和 MemorySaver checkpoint 恢复；steer-away 使用最后完整历史和新的 user message；retry 使用上述目标 run 对应的最后完整历史。锁定适配器在检测到非空 resume command 后会先于 regenerate heuristic 进入 `Command(resume=...)`，因此 resume 不需要也不应回填 checkpoint 历史。该行为必须由 1A 合同测试锁定。

下一轮普通对话从服务端保存的完整、非 partial、非 pending-interrupt 历史创建新 Graph，不依赖上一轮 MemorySaver。这是第一期 JSON 历史与后续持久化 LangGraph checkpoint 之间的明确边界。

### 8.2 内置工具适配

`backend/tools.py` 继续是唯一业务实现。适配器把已有 JSON Schema 和 `tools.exec_tool` 包装成 LangChain Tools。

要求：

- 每个现有 Schema 对应且只对应一个 LangChain Tool。
- 结果保持可 JSON 序列化。
- 现有 `{ "error": ... }` 结果作为工具失败回喂模型，不导致 run 崩溃。
- 工具结果进入模型或 UI 前必须裁剪。
- 工具名称和描述保持稳定。
- 第一期同一 run 内的服务端工具串行执行，避免现有行情数据源的时间限流被并发击穿。

## 9. 用户目录存储

默认结构：

```text
~/.vibe-research/agent/
  threads/
    <thread-id>.json
  runs/
    <run-id>.json
  artifacts/
    <thread-id>/
      <artifact-id>.json
  skills/
    <skill-name>/
      SKILL.md
      references/
      assets/
      scripts/
  mcp.json
  policy.json
```

如果设置 `VR_DATA_DIR`，根目录改为 `<VR_DATA_DIR>/agent/`。

### 9.1 文件策略

一个 thread 一个文件，一个 run 一个文件。不维护第二份全局 thread index。个人版本通过扫描 thread 文件并按 `updated_at` 排序生成列表，避免跨文件事务和索引失配。

所有可变 JSON 文档包含 `schema_version`。

写入流程：

1. 进程内锁。
2. 在目标目录写临时文件。
3. flush 并对临时文件执行 `os.fsync`。
4. 关闭临时文件并使用 `os.replace` 原子落位。
5. 在平台支持目录 `fsync` 时同步父目录，确保重命名在崩溃后可见；不支持目录 `fsync` 的平台仍以 `os.replace` 为原子性基线。

损坏 JSON 改名为 `<name>.corrupt-<timestamp>` 并向 UI 报错，不能静默当作空会话。

后端启动对账在把遗留活跃 run 标记为 `interrupted` 后，删除 thread、run、artifact 和配置目录中未完成原子替换的孤儿临时文件。只清理由本写入管线生成且符合固定命名格式的 `.tmp` 文件，不扫描或删除其他用户文件。

第一期的锁和活跃 run 状态都只在进程内有效，因此 Agent 子系统明确只支持一个 FastAPI 进程/worker。不得用 `uvicorn --workers N`、Gunicorn 多 worker 或多个后端实例同时指向同一 `VR_DATA_DIR`。多进程文件锁不属于第一期。

### 9.2 Thread 文档

包含：

- `schema_version`
- `id`
- `title`
- `created_at`
- `updated_at`
- 单调递增的 `revision`
- `selected_skills`
- 规范化 AG-UI messages
- 消息完整状态，包括 `partial`
- Artifact 引用
- 最近 run 摘要

partial assistant 消息可以恢复显示，但不能进入下一轮模型历史。

服务端 thread JSON 是会话历史的唯一权威来源。assistant-ui history adapter 只通过 REST 加载和提交变更，浏览器不维护第二份长期会话副本。每条 message 必须有稳定且在 thread 内唯一的 ID。

每次 AG-UI 请求在 `forwardedProps.runtime.threadRevision` 中携带前端最后加载的 revision。后端必须：

1. 要求 revision 与当前 thread 相等，否则在模型调用前返回 `409 THREAD_REVISION_CONFLICT` 并让前端重新加载。
2. 按 message ID 验证客户端历史与服务端 head 一致。
3. 按 start、resume、steer-away、retry 四种合法形状校验；start 只接受服务端 head 之后恰好一条新 user message，resume 不得带新消息，steer-away 必须同时带全部 cancelled resume entries 和恰好一条新 user message，retry 必须带合法 `retryOf` 且不得带新消息或 resume。
4. 不得把客户端请求中的完整历史再次 append，也不得把它作为 Graph 的权威输入。
5. 在接受用户消息、完成工具、完成 assistant 消息和写入 run 终态时递增 revision；JSON 原子提交成功后，通过 `thread.revision.updated` 通知前端。

第一期不提供 SSE 事件缓存、`Last-Event-ID`、流重连或重新附着。protocol run ID 已存在，或新 user message ID 已存在且内容相同时，视为重复提交：

- 对应产品 run 仍活跃，返回 `409 DUPLICATE_RUN_ACTIVE`，响应包含产品 run ID 和当前状态。
- 对应产品 run 已终止，返回 `409 DUPLICATE_RUN_TERMINAL`，响应包含产品 run ID 和终态。
- 两种情况都不能创建第二个 handle、追加消息或再次写入 run；前端统一通过 REST 重载服务端权威历史。
- protocol run ID 和 message ID 指向不同产品 run，或已有 message ID 的内容不同，返回 `409 MESSAGE_CONFLICT`。

网络断开已经把原 run 标记为 `cancelled` 时，同 ID 重放属于 terminal duplicate；用户必须通过 `/api/agent/run` 的 retry 形状显式创建新产品 run。会话重命名、Skill 选择和其他 PATCH 请求也必须携带 revision。活跃 run 期间允许重命名，但删除 thread 返回 `409 THREAD_BUSY`。前端对所有 thread 409 采用同一简单策略：重载权威历史和 revision，再由用户决定重试。

### 9.3 Run 文档

包含：

- `schema_version`
- `id`
- `thread_id`
- `protocol_run_ids`
- 可选 `retry_of`
- `status`：`running`、`awaiting_approval`、`completed`、`failed`、`cancelled`、`interrupted`
- 开始、结束、墙钟耗时、累计 active execution 耗时和审批等待耗时
- 不可变的预算快照
- 模型调用和工具调用次数
- Provider 返回时的 token usage
- 不含密钥和无限原文的工具摘要
- 终态错误 code 与脱敏 message

后端启动时，遗留的 `running` 和 `awaiting_approval` 自动改为 `interrupted`，随后按 9.1 的规则清理孤儿临时文件。

## 10. Skill 系统

### 10.1 发现与导入

主目录是 `~/.vibe-research/agent/skills/` 或 `<VR_DATA_DIR>/agent/skills/`。

有效 Skill 必须是包含 `SKILL.md` 的目录，并具有合法的 `name` 和 `description`。无效 Skill 仍显示校验错误，但不可启用。

用户可以：

- 手工放入完整 Skill 目录后刷新。
- 在 Agent 设置中导入 zip。
- 为单个会话启用或禁用 Skill。

zip 导入拒绝：

- 绝对路径。
- `..` 路径逃逸。
- 符号链接。
- 超过 500 个条目。
- 压缩包超过 20 MB。
- 解压内容超过 50 MB。
- 单个 `SKILL.md` 超过 256 KB。
- 单个 reference 文本超过 1 MB，或文件名经过 Unicode NFC 规范化后发生冲突。

导入只接受“zip 根目录直接包含 `SKILL.md`”或“zip 只有一个顶层目录且其中包含 `SKILL.md`”两种布局。先解压到 Skill 根目录同一文件系统内的临时目录，再原子移动。覆盖同名 Skill 必须显式确认。

### 10.2 渐进加载

初始上下文只放入已选 Skill 的 name/description。Agent 调用内部 `load_skill` 后才读取完整指令。

Skill 工具：

- `load_skill(name)`：返回校验后的 `SKILL.md` 指令和资源索引。
- `read_skill_resource(name, relative_path)`：读取 `references/` 中允许的文本资源。

`load_skill` 返回的指令最多 60,000 字符；超限 Skill 显示校验错误且不可启用。`read_skill_resource` 只接受 UTF-8 文本，单次最多返回 60,000 字符，超限时返回截断标记和原始字符数。所有读取结果仍计入 run 的工具结果和上下文预算。

`assets/` 只向 Agent 返回元数据和安全下载链接；UI 只预览安全图片、PDF 和文本类型，不执行 raw HTML。

`scripts/` 完整保存在用户目录并显示文件名、大小和修改时间，但不通过 UI 或 Agent 返回脚本文本，不注册为工具、不传给 shell、不执行。Agent 和 REST 资源读取接口都拒绝任何 `scripts/` 路径。

所有真实路径必须位于对应 Skill root 内。符号链接逃逸和绝对路径一律失败关闭。

所有按名称访问 Skill 的 Agent 与 REST 接口，都必须先从本次扫描得到的 `SkillRegistry` 解析 `skill_name`，不能把路径参数直接拼接到文件系统。名称和相对路径在 URL 解码与 Unicode NFC 规范化后再校验；未知名称、编码后的 `..`、规范化碰撞和目录逃逸一律失败关闭。

## 11. MCP Client

### 11.1 范围

支持：

- stdio MCP Server。
- Streamable HTTP MCP Server。
- MCP Tools。
- 添加、编辑、删除、启停、连接测试、健康状态和工具刷新。

不支持 OAuth、Resources、Prompts 和市场。

### 11.2 配置

`mcp.json` 保存：

- 稳定 server ID 和显示名称。
- enabled 状态。
- transport。
- stdio executable 与 args，或 Streamable HTTP URL。
- env 与 HTTP header 的环境变量引用。
- 每个工具的 enabled 状态。
- 不含密钥的最近健康信息。
- 已由用户确认的 stdio executable + args 指纹；命令变化后指纹立即失效。

`server_id` 必须匹配 `^[a-z0-9-]+$`，因此不能包含 `__`；显示名称不受该机器标识格式约束。

stdio 使用参数数组并以 `shell=False` 执行。密钥只在连接时从环境变量解析，不能回写 JSON。

stdio MCP 是受信任的本地程序，不是沙箱。`shell=False` 只避免 shell 展开，不能限制 executable 自身读取文件、联网或启动子进程。添加配置只写 JSON，不得自动启动；首次启用、连接测试和 executable/args 发生变化后的首次连接，都必须展示完整 executable 与参数并取得独立确认。这个确认发生在启动进程之前，不能用后续工具调用审批替代。

“Skill scripts 不执行”只约束 Skill 子系统。用户仍可显式配置一个 stdio MCP executable；UI 必须把这两种能力清楚区分。第一期不提供 MCP 进程沙箱或 executable allowlist。

Streamable HTTP URL 应用与模型 Base URL 相同的元数据地址和 public mode SSRF 规则。现有 public mode 由设置 `VR_API_KEY` 触发，会拒绝解析到 loopback 或私网的 URL；因此该模式下不能连接 localhost Streamable HTTP MCP，只能使用 stdio 或可公开访问的 HTTPS MCP。第一期不为 MCP 增加绕过该规则的例外。

### 11.3 工具注册

使用 `langchain-mcp-adapters`，不自行实现 MCP framing。

工具名格式：

```text
mcp__<server-id>__<tool-name>
```

UI 保留原 server/tool 显示名。不同 MCP 和内置工具不会重名。

namespace 生成前再次校验 registry 中的 `server_id`，避免 `mcp__<server-id>__<tool-name>` 出现分隔符歧义。

连接按需建立。Registry 缓存健康连接，并在后端关闭或配置变更时关闭 stdio 子进程和 HTTP session。

### 11.4 审批

每次 MCP 调用在执行前被拦截。选择：

- 允许本次。
- 允许当前 thread session 中该 server/tool。
- 拒绝。

session allowance 只在内存中存在，后端停止后失效。第一期不提供永久信任。

审批使用 LangChain human-in-the-loop middleware。`AgentProtocolBridge` 将其转换为标准 AG-UI structured interrupt outcome；`ApprovalBridge` 必须为每个 open interrupt 提交且只提交一个 `ResumeEntry`。

项目定义的审批 payload 为：

```json
{
  "decision": "approve | reject",
  "scope": "once | thread_session"
}
```

`thread_session` 只允许与 `approve` 组合。后端按 `bridge_interrupt_id` 和 tool call ID 验证 payload，再转换为 LangChain middleware decision。选择 session allowance 后，`RunCoordinator` 在 handle 之外记录 `(thread_id, server_id, tool_name)`，同一后端进程和 thread 的后续匹配调用由 Policy Middleware 自动允许，不再产生 interrupt。allowance 在 thread 删除、用户显式清除该 thread 的临时授权或后端停止时失效，不能写入 JSON。任何缺项、额外 pending interrupt 或无法识别的 decision 都失败关闭。

`useAgUiSteerAway` 产生的 transport-level cancelled response 不属于上述审批 payload。只有所有 pending interrupt 都各有一个 cancelled entry 且请求同时包含恰好一条新 user message 时，桥接层才接受该形状并交给 coordinator；它不得被转换为 approve/reject，也不得执行任何 pending MCP 工具。部分取消、取消中夹带审批 decision 或取消但没有新消息都失败关闭并保留原 `awaiting_approval`。

拒绝后向 Agent 返回结构化拒绝结果，使其可以不用该工具继续。

## 12. Artifact 系统

Agent 只能通过内置 `create_artifact` 创建 Artifact，不能指定任意磁盘路径。

支持：

- `markdown`
- `table`
- `json`
- `sources`

每个 Artifact 包含：

- `schema_version`
- `id`
- `thread_id`
- `run_id`
- `type`
- `title`
- `created_at`
- 类型化 content
- 可选 `parent_artifact_id`
- 显式 source references

Artifact 不可原地覆盖。修订生成新 ID并指向 parent。

渲染规则：

- Markdown 禁止执行 raw HTML。
- 表格只渲染校验后的 columns 和 rows。
- JSON 只作为数据展示。
- 来源区分工具产生的记录与模型自行给出的未验证 URL。

单个 Artifact 序列化内容上限 1 MB。Inspector 支持预览、下载和删除。

## 13. 运行预算与上下文

默认限制：

- 每个 run 最多 32 次 Graph transition；只有 1A Spike 证明跨 resume 可以按同一产品 run 累计并可靠阻断后，才通过 LangGraph `recursion_limit` 执行。
- 每个 run 最多 8 次模型调用。
- 每个 run 最多 16 次工具调用。
- 单工具最多 30 秒。
- 整次 run 最多累计 5 分钟 active execution；`awaiting_approval` 的人工等待不计入。
- 除 `load_skill` 和 `read_skill_resource` 外，单个工具结果进入后续链路前最多 6,000 字符；Skill 工具使用 10.2 的独立 60,000 字符上限。
- Provider 格式化前的 prompt context 最多 120,000 字符。

限制保存在 `policy.json`，UI 只能在校验范围内修改。

所有计数和 active execution 耗时按产品 run 累计，不能在审批 resume 时重置。active execution 从请求通过校验并占用 thread 后开始，在进入 `awaiting_approval` 时暂停，在合法 resume 重新取得 handle 后继续；模型等待、工具等待和 worker capacity 等待均计入，人工审批等待不计入。`ActiveRunHandle` 保存项目侧累计 transition 数和 active execution 耗时，并在 resume 时向新 Graph 传递剩余额度；1A 必须验证锁定版本的 transition 计数边界、`recursion_limit` 语义和跨同构 Graph resume 行为。如果无法证明可靠执行，就从第一期产品 policy 和 UI 中移除 transition 限制，只保留可由项目 middleware 硬性执行的 8 次模型调用与 16 次工具调用，不能把未验证的 `recursion_limit` 宣称为跨 resume 硬限制。一次并行返回的多个 tool call 按实际 call 数分别计数；第一期执行器仍逐个串行调用。预算快照在产品 run 创建时固定，运行中修改 `policy.json` 只影响下一 run。

已启用的 transition 限制、模型、工具和整次 run 的限制是逻辑硬限制：到达限制后不得再发起新的模型或工具调用，并产生明确终态。当前 `backend/tools.py` 是同步阻塞实现，所有同步工具共用一个全局有界 worker pool，默认 `max_workers=4`；同一 run 的服务端工具仍串行执行。每次获取 worker 最多等待 1 秒，容量用尽时产生 `TOOL_CAPACITY_EXHAUSTED` 终态，不进入无界队列。

30 秒 deadline 或用户取消后，系统停止等待、丢弃迟到结果并禁止后续步骤，但不能保证杀死已经进入第三方同步库的底层调用；该调用会继续占用 worker，直到自行返回。第一期不使用进程隔离，因此 UI 和文档不得声称能强制终止正在执行的 OS thread。集成测试必须用慢速假工具反复触发取消，验证容量耗尽会被明确拒绝且迟到结果不会污染 run；线程全部释放后新 run 可以恢复执行。

上下文超限时依次保留：

1. System 和 Policy 指令。
2. 已选 Skill 元数据和已加载 Skill 指令。
3. 当前用户消息。
4. 能放入预算的最新完整轮次。

旧轮次只从当前模型请求中省略，仍保留在 thread 文件。UI 显示本次发生过 context truncation。

Provider 返回 token usage 时才记录；未返回就标记 unavailable。费用只允许显示为估算值，第一期不据此终止运行。

## 14. 运行数据流

### 14.1 正常运行

1. 前端发送 thread ID、thread revision、一条新 user message、已选 Skills 和不含密钥的模型配置；模型 API Key 放在专用 header。
2. 后端在鉴权和基本 schema 校验后先检查 protocol run ID 与 message ID：重复请求按 9.2 返回定义好的 409，内容冲突返回 `MESSAGE_CONFLICT`，不能让已递增的 revision 掩盖 duplicate 语义。
3. 后端校验 revision、消息 head、模型配置和同 thread 活跃状态。
4. 原子占用 thread，先保存用户消息和 `running` run 记录。
5. 后端从 thread JSON 的最后完整历史重建 `RunAgentInput.messages`，不把客户端历史传给 Graph。
6. `AgentFactory` 解析 Agent、工具、Skill 和 Policy。
7. 标准 AG-UI 事件流式返回前端。
8. 在用户消息、工具完成、assistant 消息完成和 run 终态时持久化；不逐 token 写盘。每次原子提交成功后发出 `thread.revision.updated`，终态 revision 事件先于 `RUN_FINISHED`。
9. 终态更新 thread 摘要与 run 文件。

### 14.2 MCP 审批

1. 模型请求 MCP 工具。
2. human-in-the-loop middleware 在执行前 interrupt。
3. `ag-ui-langgraph` 发出 legacy `CUSTOM/on_interrupt`；`AgentProtocolBridge` 生成或复用 `bridge_interrupt_id`，记录有序映射，持久化 interrupt metadata 和 assistant message，再先发送最新 `thread.revision.updated`，最后发送带标准 interrupt outcome 的 `RUN_FINISHED`。
4. `ApprovalBridge` 展示请求并获取选择。
5. 前端提交覆盖全部 pending interrupt 的标准 `ResumeEntry[]`，并重新提供模型 API Key。
6. `AgentProtocolBridge` 校验和转换 resume，`RunCoordinator` 找到同一 `ActiveRunHandle`；bridge 把适配器输入的 `messages` 设为 `[]`，新建 request-scoped `LangGraphAgent` 并通过原 MemorySaver 恢复同一产品 run。

服务端把 interrupt metadata 与 assistant message 一并写入权威 thread JSON，前端 history adapter 通过 REST 重新加载。页面刷新后，如果后端进程中的 `ActiveRunHandle` 和 MemorySaver 仍在，可以恢复审批 UI；只有 JSON 而没有 handle 时只能显示已中断状态，不能提交原审批。

### 14.3 取消与恢复

- 用户停止会中止 HTTP 流和后续 Agent 步骤，并把 run 标记为 `cancelled`；已进入同步第三方库的工具可能继续到自身超时，但迟到结果必须丢弃。
- 网络断开走相同取消路径；客户端不得用原 ID 重新附着，必须重载历史并通过 `/api/agent/run` 的 retry 形状显式重新执行。
- 已完成工具和 partial assistant 内容继续可见，但 partial 内容不能进入下一次模型输入。
- 用户在审批中输入新消息时，`useAgUiSteerAway` 在同一请求中发送全部 cancelled `resume[]` 和一条新 user message。后端在 thread 锁内结束旧 run、关闭 handle、禁止 pending MCP 调用，再以最后完整历史和新消息创建新 run；旧 pending assistant/tool-call turn 只供 UI 展示。
- 普通 `running` 状态下 Composer 禁用；用户必须先 Stop，等待取消持久化与 revision 重载完成，再发送新消息。
- 后端重启把遗留活跃 run 改为 `interrupted`。
- Retry action 生成新的 protocol run ID，在 `forwardedProps.runtime.retryOf` 中传入最近的目标产品 run ID，并携带当前 revision、模型配置和模型密钥；同一个 `/api/agent/run` response 直接承载新产品 run 的 AG-UI 事件流，不存在第二次附着。
- retry 从目标 run 对应的最后完整模型历史创建新产品 run，不重复用户消息，不恢复原 MemorySaver。

第一期不承诺重启后的原 interrupt 恢复。

## 15. 错误处理

| 错误 | 行为 |
|---|---|
| 模型/MCP/Skill 配置错误 | run 前 REST 4xx，或显示为不可启用项并给出修复信息 |
| thread revision 或消息 head 冲突 | run 前返回 409，前端重新加载服务端权威历史 |
| 活跃或终态 run 的重复 POST | 分别返回 `409 DUPLICATE_RUN_ACTIVE` 或 `409 DUPLICATE_RUN_TERMINAL` 和当前状态；不重放 SSE、不创建 handle，前端重载历史 |
| retry 目标无效、不是最近 run 或 history 已前进 | 返回 `409 RETRY_NOT_ALLOWED`，不创建产品 run，前端重载历史 |
| resume 模型配置变化 | 缺少密钥时在 Graph 构建前拒绝；`ModelRef` 变化返回 `409 RUN_CONFIG_MISMATCH` |
| 非 loopback HTTP 携带模型密钥 | 在 Graph 构建前拒绝；只按受信代理配置接受 forwarded scheme |
| 模型端点失败 | AG-UI `RUN_ERROR`，run 记录脱敏错误，不泄露 API Key |
| 内置工具失败 | 结构化 ToolMessage 回喂 Agent，run 可以继续 |
| MCP 连接/调用失败 | 结构化工具失败并更新健康状态，不拖垮 FastAPI |
| 同步工具 worker 容量耗尽 | 1 秒内无法取得容量时产生 `TOOL_CAPACITY_EXHAUSTED` 终态，不无限排队 |
| 预算超限 | 产生可解释终态，不再发起模型/工具调用 |
| JSON 写入失败 | 明确显示“未持久化”，不能假装成功 |
| JSON 损坏 | 保留 `.corrupt-<timestamp>`，从正常列表隔离并显示恢复信息 |
| AG-UI 事件顺序非法 | 协议错误并结束 run，由合同测试覆盖 |
| interrupt/resume 不完整或不匹配 | 失败关闭并保留 awaiting approval，不执行 MCP 工具 |

日志必须脱敏 API Key、MCP secret header 和无限工具原文。LangSmith tracing 默认关闭，且不属于第一期依赖。

## 16. API 表面

```text
POST   /api/agent/run

GET    /api/agent/threads
POST   /api/agent/threads
GET    /api/agent/threads/{thread_id}
PATCH  /api/agent/threads/{thread_id}
DELETE /api/agent/threads/{thread_id}
DELETE /api/agent/threads/{thread_id}/allowances

GET    /api/agent/runs/{run_id}
POST   /api/agent/runs/{run_id}/cancel

GET    /api/agent/skills
GET    /api/agent/skills/{skill_name}
POST   /api/agent/skills/import
POST   /api/agent/skills/refresh
DELETE /api/agent/skills/{skill_name}
GET    /api/agent/skills/{skill_name}/files/{relative_path:path}

GET    /api/agent/mcp
POST   /api/agent/mcp
PATCH  /api/agent/mcp/{server_id}
DELETE /api/agent/mcp/{server_id}
POST   /api/agent/mcp/{server_id}/test
POST   /api/agent/mcp/{server_id}/refresh

GET    /api/agent/policy
PATCH  /api/agent/policy

GET    /api/agent/threads/{thread_id}/artifacts
GET    /api/agent/artifacts/{artifact_id}
GET    /api/agent/artifacts/{artifact_id}/download
DELETE /api/agent/artifacts/{artifact_id}
```

所有接口继续使用项目现有可选 `VR_API_KEY` middleware。

Skill 详情响应包含 frontmatter、校验状态，以及 `references`、`assets`、`scripts` 的受控 manifest。所有 `{skill_name}` 必须解析到当前 `SkillRegistry` 已扫描条目，不能直接作为目录名使用。文件接口只允许读取 `references/` 和 `assets/`：references 以受限 UTF-8 文本返回；assets 只允许安全图片、PDF 和纯文本 MIME，并设置 `Content-Disposition`、`X-Content-Type-Options: nosniff` 和限制性 CSP。请求未知或 NFC 冲突的 Skill、编码遍历、`scripts/`、HTML、符号链接或逃逸后的路径一律返回 400 或 403。

所有 thread PATCH 请求体包含当前 `revision`；成功响应返回递增后的 revision。删除活跃 thread 返回 409。清除 allowances 只删除进程内该 thread 的临时 MCP 授权，不修改 thread revision。AG-UI `/run` 的 start、resume、steer-away 和 retry 都只从 `X-VR-Agent-Model-Key` 读取模型密钥，不属于任何 REST response schema。前端为 `/run` 提供自定义 fetch/error adapter，识别所有结构化 409 后统一触发 REST history reload；第一期不尝试自动续接原事件流。

## 17. 测试策略

### 17.1 后端单元测试

- 每个现有工具 Schema 都被正确转换一次。
- 普通工具 6,000 字符裁剪、Skill 工具独立 60,000 字符上限和失败转换。
- thread/run 原子写入、损坏文件保留，以及启动时只清理固定命名的孤儿 `.tmp`。
- revision 冲突、active/terminal duplicate 的结构化 409、message ID 内容冲突和活跃 thread 删除保护；重复请求不能产生第二个 handle 或重复落盘。
- Skill frontmatter、zip、路径逃逸、symlink、大小和脚本拒绝；覆盖编码后的 `..`、未知 `skill_name`、NFC 碰撞，以及 Agent 与 REST 两条路径都不能读取 `scripts/`。
- MCP 命名冲突、`server_id` 字符集、env 引用、生命周期和结果脱敏。
- public mode 拒绝 localhost/private Streamable HTTP MCP，未设置 `VR_API_KEY` 的本地模式仍可连接 localhost。
- stdio executable/args 指纹变化立即使确认失效；未确认的启用或连接测试不得拉起进程，创建配置不得拉起进程。
- allowance 在线程删除、显式清除和进程重启后消失；清除 allowance 不递增 thread revision。
- Artifact 类型、不可变、大小和安全渲染元数据。
- 预算计数、active execution 超时、审批等待暂停计时、上下文裁剪、worker capacity 拒绝和终态。

### 17.2 协议合同测试

针对锁定版本覆盖：

- 文本事件顺序。
- 串行工具与 Provider 流式并行 tool call fragments。
- 通过 call ID 关联结果。
- `RUN_ERROR` 和 cancel 终态。
- interrupt approve、deny、session allowance、reload、resume。
- 锁定版本的 legacy `CUSTOM/on_interrupt` 到标准 outcome、标准 `resume[]` 到 middleware decision 的双向转换。
- bridge 为 legacy interrupt 生成稳定 ID，并在刷新/重复读取时复用；bridge ID 与 LangGraph 内部 ID 不一致时，仍能通过 handle 映射按原始顺序恢复。
- 缺失、重复、未知 bridge interrupt ID 失败关闭，且不会执行目标 MCP 工具。
- steer-away 的“全部 pending interrupt cancelled + 一条新 user message”单请求：旧 run 为 `cancelled`、旧 handle 关闭、pending MCP 工具不执行、新 run 建立且 thread head 正确。
- start、resume、steer-away、retry 之外的混合请求形状全部失败关闭。
- 运行中和终态后的重复 POST 分别返回定义好的 409，不创建第二个 handle、不重复持久化；断连取消后的同 ID 重放返回 terminal duplicate。
- retry 形状不追加用户消息，创建带 `retry_of` 的新产品 run，并在同一个 `/api/agent/run` response 中从持久化历史完整返回 AG-UI 事件流；非最近、非可重试终态或 history 已前进返回 `RETRY_NOT_ALLOWED`。
- 服务端重建 `RunAgentInput.messages`；start/steer-away/retry 排除不应进入模型的 partial 或 pending turn，resume 强制使用 `messages=[]` 并且不触发适配器的 time-travel/regenerate 路径。
- interrupt 后丢弃 Graph 与 `LangGraphAgent`，使用同一 MemorySaver、同构新 Graph 和新适配器实例恢复同一产品 run。
- `recursion_limit` 和项目侧 transition 计数能否跨 resume 按产品 run 累计；不能可靠阻断时验证 transition 限制不会出现在第一期 policy 和 UI。
- resume 缺少模型密钥时在 Graph 构建前失败；更换原始密钥但保持同一 `ModelRef` 可以恢复，修改 `ModelRef` 返回 `409 RUN_CONFIG_MISMATCH`。
- 未知 CUSTOM 事件。
- history 导入导出不丢消息完整状态。
- `thread.revision.updated` 只在原子提交后发送且不进入 Graph state，终态事件严格先于 `RUN_FINISHED`；丢失该事件后下一次提交通过 revision 409 触发重载。
- 活跃 run 期间 PATCH 重命名与 AG-UI revision 事件乱序到达时，前端 revision 只能单调增加；后续请求要么使用最新值成功，要么通过 409 重载收敛。
- 标准 interrupt outcome 不包含 `expiresAt`，pending interrupt 只按设计中的显式状态转换失效。
- API Key 不出现在任何 SSE frame、Graph state、MemorySaver 序列化内容、异常文本或 JSON 文件中；覆盖重建 Graph 后 resume 的完整路径。

### 17.3 集成测试

使用完全离线的确定性 fixture：

- 会跨多步请求工具的 scripted fake chat model。
- fake stdio MCP Server。
- fake Streamable HTTP MCP Server。
- 临时 `VR_DATA_DIR`。
- 完整 Skill load 与 Artifact 创建。
- 慢速同步假工具反复取消：占用的 worker 不造成无界排队，容量耗尽按约定拒绝，迟到结果被丢弃，worker 释放后新 run 恢复。
- 非 loopback HTTP 携带模型密钥被拒绝；只在显式受信代理来源下接受 `X-Forwarded-Proto: https`，loopback 开发代理可用。
- 流中断连后 run 为 `cancelled`，partial assistant 被持久化并展示但不进入下一次模型输入，重载得到最新 revision。
- 断连后通过 retry 形状重新执行，使用新 protocol/product run ID，不恢复旧 MemorySaver，并在同一 HTTP response 收到新事件流。

默认测试不得依赖付费模型或公网。

### 17.4 前端测试

增加 Vitest 和 React Testing Library，测试组件与 Runtime 状态。增加 Playwright 覆盖：

- 桌面三栏。
- 移动端两个抽屉。
- 流式文本与工具调用。
- 审批、拒绝、session allowance，以及 steer-away 单请求交互。
- `running` 时 Composer 禁用且 Stop 可用；取消完成并重载 revision 后 Composer 恢复。
- 停止、retry 形状、刷新和历史恢复；所有结构化 409 都重载服务端历史，不尝试重附着 SSE。
- Artifact 预览与下载。
- 错误提示和响应式无重叠。

### 17.5 回归测试

现有 backend 非 live 测试、frontend 测试和 production build 必须保持通过。明确 smoke coverage 至少包含旧 `/api/chat`、`/api/debate`、`/api/reflect` endpoints 和对应入口。为 `backend/app.py` 新增 `PATCH` 的 CORS preflight 测试，证明这是遗留应用 CORS 配置的唯一行为变化。

## 18. 第一期间里程碑

### 18.1 1A：Agent 垂直闭环

**估算：** 8-11 人日，其中前 2-3 人日是协议 Spike。

交付：

- `/agent` 工作台骨架。
- assistant-ui AG-UI Runtime。
- 自定义 FastAPI AG-UI Endpoint 与 `AgentProtocolBridge`。
- LangChain `create_agent`。
- 全部内置工具的 `ToolRegistry` 适配。
- OpenAI-compatible 模型配置。
- 流式文本/工具、停止和终态错误。

退出条件：

- fake model 完成多步工具 run。
- 标准 interrupt outcome、bridge interrupt ID 映射、标准 `resume[]`、`messages=[]` resume、steer-away、每请求新 `LangGraphAgent`、动态模型构造和断线取消在锁定版本下通过合同测试。
- interrupt 后丢弃旧 Graph，再用同一 MemorySaver 和同构新 Graph 恢复；API Key 在该链路中不进入事件、checkpoint 或持久化。
- 明确验证 `recursion_limit` 跨 resume 语义；若不能证明按产品 run 硬限制，就按第 13 节移除 transition 限制。协议转换或跨实例 resume 不通过则停止后续里程碑并重新选定兼容版本。
- OpenAI 和 DeepSeek 各人工验证一次。
- 旧 AI 路由与测试不变且通过。

### 18.2 1B：服务端会话

**估算：** 6-8 人日。

交付：

- Thread 与 Run Store。
- 带 revision、重复 POST 终态、消息冲突处理的会话 CRUD 与历史恢复。
- cancel、partial、retry、损坏隔离和启动修复。

退出条件：

- 刷新与后端重启保留完整历史。
- partial 不进入下一轮模型历史。
- 并发写不产生半截 JSON。
- 重复 run/message 请求返回定义好的 409 且不重复落盘，历史分叉在模型调用前返回 409。
- retry 通过同一 `/api/agent/run` response 建立新产品 run 并完整流式返回，不重复用户消息或附着旧流。
- 断连 partial 可显示但不进入下一轮模型输入；`thread.revision.updated` 丢失后能通过 409 重载收敛。
- 启动对账标记遗留 run 并清理固定命名的孤儿 `.tmp`。
- 损坏文件保留并可识别。

### 18.3 1C：Skill 与 MCP

**估算：** 9-13 人日。

交付：

- 完整 Skill 目录发现和导入。
- 渐进加载与受控资源读取。
- stdio/HTTP MCP 管理与健康状态。
- stdio MCP 启动前信任确认。
- MCP 工具 namespace。
- interrupt 审批与 session allowance。

退出条件：

- fake stdio 和 HTTP MCP 均可工作。
- approve、deny、reload、resume 和 steer-away 均通过。
- traversal、symlink、zip-slip、脚本执行和密钥落盘测试全部失败关闭。

### 18.4 1D：工作台与治理

**估算：** 8-12 人日。

交付：

- 完整 Inspector。
- 类型化 Artifact 和版本链。
- 预算、超时、上下文和 usage。
- 模型/MCP/Skill/Policy 设置。
- 响应式体验、文档和完整回归。

退出条件：

- 四类 Artifact 可安全预览和下载。
- 人为制造的失控 loop 在限制处终止。
- 慢速同步工具造成 worker 饱和时明确返回 `TOOL_CAPACITY_EXHAUSTED`，不无界排队，释放后可以恢复。
- 桌面与移动端 Playwright 截图和交互测试通过。
- 全部离线测试和 frontend build 通过。

## 19. 成本与风险

- 基础实施：31-44 人日。
- 加约 15% 集成缓冲：36-51 人日。
- 一名熟悉仓库的高级工程师：约 7-10 周。

主要不确定项：

1. `assistant-ui` 标准 interrupt 与 `ag-ui-langgraph` legacy interrupt 的兼容桥。
2. assistant-ui 版本敏感的 thread list、history 与 interrupt hooks。
3. 不同 OpenAI-compatible Provider 的流式 tool call 差异。
4. Windows、macOS、Linux 下 MCP 子进程和 HTTP 行为。
5. 真实 Skill 目录内容的多样性。

## 20. 后续 LangGraph 演进

第一期之后可以加入：

- 显式研究节点与条件边。
- SQLite 或数据库 checkpointer。
- 跨进程精确 interrupt 恢复。
- 长时间运行的深度研究。
- 多方、空方、主持、审计、报告工作流。
- Subgraph 和经过选择的多 Agent 模式。

演进顺序：

1. 保持 AG-UI 与管理 REST 合同。
2. 保持 `AgentRuntimeHandle` 接口，将 handle 内的 `create_agent` 替换为编译后的显式 `StateGraph`。
3. 将内存 checkpointer 替换为持久化 checkpointer。
4. 只有在规模或事务需求成立后，才把 thread/run JSON 迁移到数据库。
5. 保持 `ToolRegistry`、`SkillRegistry`、`MCPRegistry`、Policy、Artifact Schema 和前端工作台稳定。

如果 UI 组件或管理 Store 直接依赖 Graph 内部结构，则违反本设计的演进边界。
