# LangChain Agent 工作台第一期设计

**日期：** 2026-08-15

**状态：** 已通过设计评审

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
- `backend/portfolio.py` 与 `backend/myreports.py` 已验证的用户目录原子写入模式。
- 浏览器本地保存的旧 LLM 配置和旧聊天记录。

新 Agent 子系统只做增量接入。第一期不修改现有 AI 入口的行为。

## 3. 已确认决策

| 范围 | 决策 |
|---|---|
| 前端组件层 | 在现有 React/Vite 中嵌入 `assistant-ui` |
| 前端 Runtime | `@assistant-ui/react-ag-ui` + `@ag-ui/client` |
| 前后端协议 | AG-UI HTTP 流 |
| 后端进程 | 复用现有 FastAPI |
| Agent Runtime | `ag-ui-langgraph` 包装 LangChain `create_agent` |
| 模型 | OpenAI 与 OpenAI-compatible API |
| 旧 CLI 订阅 | 新 Agent 第一期不支持 |
| 会话 | 服务端用户目录 JSON 文件 |
| Skill | 用户目录保存完整 Skill 目录 |
| Skill 脚本 | 一并加载和展示，但绝不执行 |
| MCP 传输 | stdio 与 Streamable HTTP |
| MCP 能力 | 只接 Tools；不接 OAuth、Resources、Prompts |
| 工具审批 | 内置只读工具自动执行；MCP 工具默认逐次审批，可放行到当前会话结束 |
| Artifact | Markdown、表格、JSON 快照、来源清单 |
| 预算 | 对步骤、工具、时长和上下文做硬限制；token 只记录，货币费用只估算 |
| 页面布局 | 左侧会话、中间对话、右侧 Inspector 的三栏工作台 |
| 崩溃恢复 | 从最后完整历史重试；第一期不做运行栈原位续跑 |

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
     -> ag-ui-langgraph Endpoint
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
  -> ~/.vibe-research/agent/ JSON 与目录存储
```

稳定边界：

- 活跃运行与前端之间使用 AG-UI。
- 会话、Skill、MCP、Policy、Artifact 管理使用 REST。
- `AgentFactory` 隔离产品服务与具体编排实现。
- `ToolRegistry` 隔离 LangChain 与内置/MCP/Artifact 工具。

后续进入 LangGraph 时，编排层只替换 `AgentFactory` 产出的 Graph。

## 6. 依赖基线

第一版使用以下精确版本：

### 前端

- `@assistant-ui/react@0.15.14`
- `@assistant-ui/react-ag-ui@0.0.54`
- `@ag-ui/client@0.0.58`

### 后端

- `langchain==1.3.15`
- `langchain-openai==1.5.1`
- `ag-ui-langgraph==0.0.42`
- `langchain-mcp-adapters==0.3.2`

AG-UI 相关 0.x 依赖不得使用宽松版本范围。升级必须整组进行，并先通过协议合同测试。

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

`runtime.tsx` 不包含投研业务规则。`ApprovalBridge` 是前端唯一允许调用 assistant-ui experimental interrupt API 的模块。

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

自定义事件通过注册的数据组件渲染。未知事件不得把原始 JSON 直接展示在聊天正文中。

### 7.4 模型配置

新 Agent 使用独立的浏览器本地模型配置，不复用旧 `vr-llm` key。保存 provider 标签、Base URL、model 和 API Key。

API Key 随当前运行请求提供，只在后端当前运行内存中使用，不能写入 thread、run、policy、MCP 或日志文件。

## 8. 后端设计

新增 `backend/agent/` 包：

- `router.py`：AG-UI 与管理 API。
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

每次运行：

1. 校验模型配置，并对 Base URL 应用现有 SSRF 安全姿态。
2. 加载 thread 与 policy 快照。
3. 解析本会话启用的 Skills。
4. 合并工具集合。
5. 组装 LangChain middleware。
6. 使用内存 checkpointer 创建 `create_agent` Graph。
7. 交给 AG-UI LangGraph adapter 执行。

同一个 thread 同时只允许一个活跃 run。第二个请求在模型调用前被拒绝。

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
3. flush 并关闭。
4. 使用 `os.replace` 原子落位。

损坏 JSON 改名为 `<name>.corrupt-<timestamp>` 并向 UI 报错，不能静默当作空会话。

### 9.2 Thread 文档

包含：

- `schema_version`
- `id`
- `title`
- `created_at`
- `updated_at`
- `selected_skills`
- 规范化 AG-UI messages
- 消息完整状态，包括 `partial`
- Artifact 引用
- 最近 run 摘要

partial assistant 消息可以恢复显示，但不能进入下一轮模型历史。

### 9.3 Run 文档

包含：

- `schema_version`
- `id`
- `thread_id`
- `status`：`running`、`awaiting_approval`、`completed`、`failed`、`cancelled`、`interrupted`
- 开始、结束和耗时
- 不可变的预算快照
- 模型调用和工具调用次数
- Provider 返回时的 token usage
- 不含密钥和无限原文的工具摘要
- 终态错误 code 与脱敏 message

后端启动时，遗留的 `running` 和 `awaiting_approval` 自动改为 `interrupted`。

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

导入先解压到同级临时目录，再原子移动。覆盖同名 Skill 必须显式确认。

### 10.2 渐进加载

初始上下文只放入已选 Skill 的 name/description。Agent 调用内部 `load_skill` 后才读取完整指令。

Skill 工具：

- `load_skill(name)`：返回校验后的 `SKILL.md` 指令和资源索引。
- `read_skill_resource(name, relative_path)`：读取 `references/` 中允许的文本资源。

`assets/` 只向 Agent 返回元数据和安全下载链接；UI 只预览安全图片、PDF 和文本类型，不执行 raw HTML。

`scripts/` 完整保存在用户目录并显示文件清单，但不注册为工具、不传给 shell、不执行。Agent 资源工具拒绝任何 `scripts/` 路径。

所有真实路径必须位于对应 Skill root 内。符号链接逃逸和绝对路径一律失败关闭。

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

stdio 使用参数数组并以 `shell=False` 执行。密钥只在连接时从环境变量解析，不能回写 JSON。

Streamable HTTP URL 应用与模型 Base URL 相同的元数据地址和 public mode SSRF 规则。

### 11.3 工具注册

使用 `langchain-mcp-adapters`，不自行实现 MCP framing。

工具名格式：

```text
mcp__<server-id>__<tool-name>
```

UI 保留原 server/tool 显示名。不同 MCP 和内置工具不会重名。

连接按需建立。Registry 缓存健康连接，并在后端关闭或配置变更时关闭 stdio 子进程和 HTTP session。

### 11.4 审批

每次 MCP 调用在执行前被拦截。选择：

- 允许本次。
- 允许当前 thread session 中该 server/tool。
- 拒绝。

session allowance 只在内存中存在，后端停止后失效。第一期不提供永久信任。

审批使用 LangChain human-in-the-loop middleware 和 AG-UI structured interrupt outcome。`ag-ui-langgraph` 必须启用标准 interrupt outcome。`ApprovalBridge` 为每个 open interrupt 提交 `ResumeEntry`。

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

- 每个 run 最多 8 次模型调用。
- 每个 run 最多 16 次工具调用。
- 单工具最多 30 秒。
- 整次 run 最多 5 分钟。
- 单个工具结果进入后续链路前最多 6,000 字符。
- Provider 格式化前的 prompt context 最多 120,000 字符。

限制保存在 `policy.json`，UI 只能在校验范围内修改。

上下文超限时依次保留：

1. System 和 Policy 指令。
2. 已选 Skill 元数据和已加载 Skill 指令。
3. 当前用户消息。
4. 能放入预算的最新完整轮次。

旧轮次只从当前模型请求中省略，仍保留在 thread 文件。UI 显示本次发生过 context truncation。

Provider 返回 token usage 时才记录；未返回就标记 unavailable。费用只允许显示为估算值，第一期不据此终止运行。

## 14. 运行数据流

### 14.1 正常运行

1. 前端发送 thread ID、消息、已选 Skills 和模型配置。
2. 后端校验配置，并确保同 thread 没有活跃 run。
3. 先保存用户消息和 `running` run 记录。
4. `AgentFactory` 解析 Agent、工具、Skill 和 Policy。
5. 标准 AG-UI 事件流式返回前端。
6. 在用户消息、工具完成、assistant 消息完成和 run 终态时持久化；不逐 token 写盘。
7. 终态更新 thread 摘要与 run 文件。

### 14.2 MCP 审批

1. 模型请求 MCP 工具。
2. human-in-the-loop middleware 在执行前 interrupt。
3. `ag-ui-langgraph` 发出带 interrupt outcome 的 `RUN_FINISHED`。
4. `ApprovalBridge` 展示请求并获取选择。
5. 前端提交覆盖全部 pending interrupt 的 `ResumeEntry[]`。
6. Graph 在同一后端进程中恢复。

前端将 interrupt metadata 与 assistant message 一并持久化。页面刷新后，如果后端进程和内存 checkpointer 仍在，可以恢复审批 UI。

### 14.3 取消与恢复

- 用户停止会中止 HTTP 流，取消下游模型/工具工作，并把 run 标记为 `cancelled`。
- 网络断开走相同取消路径。
- 已完成工具和 partial assistant 内容继续可见。
- 后端重启把遗留活跃 run 改为 `interrupted`。
- 重试从最后完整模型历史创建新 run。

第一期不承诺重启后的原 interrupt 恢复。

## 15. 错误处理

| 错误 | 行为 |
|---|---|
| 模型/MCP/Skill 配置错误 | run 前 REST 4xx，或显示为不可启用项并给出修复信息 |
| 模型端点失败 | AG-UI `RUN_ERROR`，run 记录脱敏错误，不泄露 API Key |
| 内置工具失败 | 结构化 ToolMessage 回喂 Agent，run 可以继续 |
| MCP 连接/调用失败 | 结构化工具失败并更新健康状态，不拖垮 FastAPI |
| 预算超限 | 产生可解释终态，不再发起模型/工具调用 |
| JSON 写入失败 | 明确显示“未持久化”，不能假装成功 |
| JSON 损坏 | 保留 `.corrupt-<timestamp>`，从正常列表隔离并显示恢复信息 |
| AG-UI 事件顺序非法 | 协议错误并结束 run，由合同测试覆盖 |

日志必须脱敏 API Key、MCP secret header 和无限工具原文。LangSmith tracing 默认关闭，且不属于第一期依赖。

## 16. API 表面

```text
POST   /api/agent/run

GET    /api/agent/threads
POST   /api/agent/threads
GET    /api/agent/threads/{thread_id}
PATCH  /api/agent/threads/{thread_id}
DELETE /api/agent/threads/{thread_id}

GET    /api/agent/runs/{run_id}
POST   /api/agent/runs/{run_id}/cancel
POST   /api/agent/runs/{run_id}/retry

GET    /api/agent/skills
POST   /api/agent/skills/import
POST   /api/agent/skills/refresh
DELETE /api/agent/skills/{skill_name}

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

## 17. 测试策略

### 17.1 后端单元测试

- 每个现有工具 Schema 都被正确转换一次。
- 工具裁剪和失败转换。
- thread/run 原子写入与损坏文件保留。
- Skill frontmatter、zip、路径逃逸、symlink、大小和脚本拒绝。
- MCP 命名冲突、env 引用、生命周期和结果脱敏。
- Artifact 类型、不可变、大小和安全渲染元数据。
- 预算计数、超时、上下文裁剪和终态。

### 17.2 协议合同测试

针对锁定版本覆盖：

- 文本事件顺序。
- 串行工具与 Provider 流式并行 tool call fragments。
- 通过 call ID 关联结果。
- `RUN_ERROR` 和 cancel 终态。
- interrupt approve、deny、session allowance、reload、resume。
- 未知 CUSTOM 事件。
- history 导入导出不丢消息完整状态。

### 17.3 集成测试

使用完全离线的确定性 fixture：

- 会跨多步请求工具的 scripted fake chat model。
- fake stdio MCP Server。
- fake Streamable HTTP MCP Server。
- 临时 `VR_DATA_DIR`。
- 完整 Skill load 与 Artifact 创建。

默认测试不得依赖付费模型或公网。

### 17.4 前端测试

增加 Vitest 和 React Testing Library，测试组件与 Runtime 状态。增加 Playwright 覆盖：

- 桌面三栏。
- 移动端两个抽屉。
- 流式文本与工具调用。
- 审批、拒绝、session allowance。
- 停止、重试、刷新和历史恢复。
- Artifact 预览与下载。
- 错误提示和响应式无重叠。

### 17.5 回归测试

现有 backend 非 live 测试、frontend 测试和 production build 必须保持通过。给旧 AI endpoints 和入口补充明确 smoke coverage。

## 18. 第一期间里程碑

### 18.1 1A：Agent 垂直闭环

**估算：** 6-8 人日。

交付：

- `/agent` 工作台骨架。
- assistant-ui AG-UI Runtime。
- FastAPI AG-UI Endpoint。
- LangChain `create_agent`。
- 全部内置工具的 `ToolRegistry` 适配。
- OpenAI-compatible 模型配置。
- 流式文本/工具、停止和终态错误。

退出条件：

- fake model 完成多步工具 run。
- OpenAI 和 DeepSeek 各人工验证一次。
- 旧 AI 路由与测试不变且通过。

### 18.2 1B：服务端会话

**估算：** 5-7 人日。

交付：

- Thread 与 Run Store。
- 会话 CRUD 与历史恢复。
- cancel、partial、retry、损坏隔离和启动修复。

退出条件：

- 刷新与后端重启保留完整历史。
- partial 不进入下一轮模型历史。
- 并发写不产生半截 JSON。
- 损坏文件保留并可识别。

### 18.3 1C：Skill 与 MCP

**估算：** 8-12 人日。

交付：

- 完整 Skill 目录发现和导入。
- 渐进加载与受控资源读取。
- stdio/HTTP MCP 管理与健康状态。
- MCP 工具 namespace。
- interrupt 审批与 session allowance。

退出条件：

- fake stdio 和 HTTP MCP 均可工作。
- approve、deny、reload、resume 均通过。
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
- 桌面与移动端 Playwright 截图和交互测试通过。
- 全部离线测试和 frontend build 通过。

## 19. 成本与风险

- 基础实施：27-39 人日。
- 加 15% 集成缓冲：31-45 人日。
- 一名熟悉仓库的高级工程师：约 6-9 周。

主要不确定项：

1. assistant-ui experimental interrupt API。
2. 不同 OpenAI-compatible Provider 的流式 tool call 差异。
3. Windows、macOS、Linux 下 MCP 子进程和 HTTP 行为。
4. 真实 Skill 目录内容的多样性。

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
2. 将 `AgentFactory.create_agent` 替换为编译后的显式 `StateGraph`。
3. 将内存 checkpointer 替换为持久化 checkpointer。
4. 只有在规模或事务需求成立后，才把 thread/run JSON 迁移到数据库。
5. 保持 `ToolRegistry`、`SkillRegistry`、`MCPRegistry`、Policy、Artifact Schema 和前端工作台稳定。

如果 UI 组件或管理 Store 直接依赖 Graph 内部结构，则违反本设计的演进边界。
