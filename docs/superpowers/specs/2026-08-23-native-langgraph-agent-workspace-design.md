# Agent 工作台原生 LangGraph 迁移设计

**日期：** 2026-08-23

**状态：** 已完成交互式设计确认和书面复审，待用户审阅

**参考实现：** `/vol2/1000/code/assistant-ui-demo`

## 1. 背景

当前 Agent 工作台已经使用 `langchain.agents.create_agent` 创建 Graph，并通过
`ag_ui_langgraph.LangGraphAgent` 和 assistant-ui 通信。但线程与 run 持久化、事件桥接、
审批、MCP/Skill 生命周期、预算、来源和产物仍由项目自己实现。

本次迁移允许破坏性调整。目标不是继续扩展这套自研运行控制面，而是让 LangGraph
Server 成为 Agent 工作台的权威后端，由 LangChain/LangGraph 负责会话、运行、流式、
checkpoint 和 interrupt。旧聊天、辩论和反思入口暂不迁移。

## 2. 目标

- Agent 工作台前端使用 assistant-ui 的 `useStreamRuntime` 直接连接 LangGraph Server。
- Agent 使用 LangChain `create_agent`，并保持未来扩展为显式 LangGraph 工作流的空间。
- 线程、消息、run、checkpoint、暂停、恢复和分支使用 LangGraph Server 原生能力。
- 保留对话、线程历史、内置投研工具、MCP、Skills、逐次审批和停止生成。
- 使用本地静态配置提供固定模型、MCP servers 和 Skills 路径。
- 删除能由框架替代的自研 Agent 运行与持久化逻辑。
- 保持 `VISION.md` 的客观中立边界和 Eastmoney 串行限流约束。
- 将后端 Python 运行要求提升为 3.11+，满足 Deep Agents 和
  `langgraph-cli[inmem]` 的依赖要求。

## 3. 明确不做

- 不迁移 `chat.py`、`debate.py` 或 `reflection.py`。
- 不保留 Agent 工作台的产物、来源、预算治理和 run Inspector。
- 不保留 MCP/Skill 在线编辑、导入、启停、刷新或管理 API。
- 不保留 thread-session allowance；敏感工具每次调用都重新审批。
- 不保留请求级 provider/model/base URL/API key 配置。
- 不迁移旧 Agent JSON 会话，也不自动删除旧用户数据。
- 不为线上、多 worker、高可用或正式生产部署增加设计。
- 不引入完整 `create_deep_agent` 默认工具集、任务列表或通用子 Agent。

## 4. 总体架构

```text
assistant-ui
  useStreamRuntime
        | native LangGraph API
        v
LangGraph Server :2024
  native threads / runs / checkpoints / interrupts
        |
  create_agent compiled graph
        |- fixed ChatOpenAI model
        |- tools.py investment-research tools
        |- LangChain MCP adapters
        |- Deep Agents SkillsMiddleware
        |- read-only FilesystemMiddleware
        `- HumanInTheLoopMiddleware
        |
        v
astock / gstock / market / newsradar / other data modules

FastAPI :8900
  existing data APIs and legacy AI entry points
```

LangGraph Server 和 FastAPI 是两个本地进程。Agent Server 直接导入 `backend/tools.py`
及其数据模块，不经 FastAPI HTTP 转发。这样保留同一套客观数据工具，同时不改变旧入口。

Agent graph 不创建或注入 `MemorySaver`。在 Agent Server 中，线程、checkpoint 和 store
由 LangGraph Server 管理。开发环境使用 `langgraph dev` 自带的本地持久化目录。

## 5. 后端组件

迁移后的 `backend/agent/` 只保留三个窄职责：

- `settings.py`：定位、读取并校验本地静态配置。
- `tool_registry.py`：把 `tools.py` 中的现有工具转换为 LangChain tools。
- `graph.py`：加载模型、MCP tools 和 middleware，导出编译后的 Agent Server graph。

`backend/langgraph.json` 声明 `agent` graph 和后端依赖。标准开发命令为：

```bash
cd backend
.venv/bin/langgraph dev --host 127.0.0.1 --port 2024
```

Agent 工作台迁移后，后端虚拟环境统一要求 Python 3.11+。现有数据 API 与旧 AI 入口仍在
同一环境运行，但不改变其业务合同。

### 5.1 Graph 组装

`graph.py` 的一次性异步 builder 按固定顺序执行：

1. 加载并校验 `settings.json`。
2. 构建固定的 OpenAI-compatible `ChatOpenAI`。
3. 构建现有内置投研工具。
4. 通过 `MultiServerMCPClient.get_tools()` 发现静态配置的 MCP tools。
5. 建立 Deep Agents `FilesystemBackend`，`root_dir` 固定为配置声明的单一 Skills
   根目录。
6. 组合 `SkillsMiddleware`、只注册 `ls`/`read_file` 的 `FilesystemMiddleware` 和
   `HumanInTheLoopMiddleware`。
7. 调用 `create_agent`，由 Agent Server 接管 graph 持久化与执行。

`MultiServerMCPClient.get_tools()` 是异步 API，而 Agent Server 的运行时 graph factory 是
同步合同。为避免每个 run 重新发现 MCP，模块在 Agent Server 的启动导入阶段执行一次异步
builder，并导出已编译的 `graph`。不在同步 per-run factory 中调用 `asyncio.run`；测试直接
调用异步 builder。MCP 发现失败会使模块导入失败，从而阻止服务进入 ready 状态。

`chat.SYSTEM_PROMPT.format(context="Agent 工作台")` 继续作为固定系统提示。配置、Skill
内容和 MCP 元数据均不能替换或削弱该提示中的中立红线。

### 5.2 内置工具

内置工具继续以 `tools.py` 为唯一来源。适配层只负责：

- 将已有 JSON schema 转换为 LangChain tool schema。
- 在线程池中调用现有同步实现，避免阻塞 Graph 的异步运行。
- 保留现有结构化错误结果和输出裁剪行为。

适配层不得并发执行 Eastmoney 节流调用。迁移不改变 `em_get` 的串行时间间隔规则。

### 5.3 MCP

MCP 完全使用 `langchain-mcp-adapters`：

- 支持静态配置中的 `stdio` 和 Streamable HTTP transport。
- 使用 `MultiServerMCPClient` 默认无状态 session；每次工具调用创建并清理连接。
- 不维护自定义 session registry、generation、catalog、lease、alias store 或 health store。
- MCP 初始化或工具发现失败会阻止 Agent Server 启动。
- MCP 工具执行错误使用 adapter 原生 error `ToolMessage`，不转换成伪成功结果。

所有发现到的 MCP tools 自动加入 `HumanInTheLoopMiddleware.interrupt_on`，只允许
`approve` 和 `reject`。内置只读投研工具与 Skills 只读文件工具不触发审批。

### 5.4 Skills

Skills 使用 Deep Agents 的标准能力，但不使用完整 `create_deep_agent`：

- `SkillsMiddleware` 负责 Agent Skills 格式、发现、元数据提示和渐进加载。
- `FilesystemMiddleware` 提供 Skills 所需的 `ls` 和 `read_file`。
- `FilesystemBackend.root_dir` 将所有虚拟路径限制在配置声明的单一 Skills 根目录内。
- `FilesystemMiddleware(tools=["ls", "read_file"])` 不向模型注册写入、删除、搜索或
  执行工具。
- 不复用现有 Skill registry、manifest、zip importer、generation 或管理 API。
- 配置路径不存在时启动失败；单个 Skill 的格式问题使用 middleware 原生 warning。

这种组合保留普通 `create_agent` 的小工具面，不引入完整 Deep Agent 的文件写入、任务
列表和通用子 Agent。

## 6. 本地配置

默认配置路径为 `~/.vibe-research/agent/settings.json`，可通过
`VR_AGENT_SETTINGS` 覆盖。配置仅在 Agent Server 启动时读取，修改后重启生效。

```json
{
  "model": {
    "provider": "openai",
    "name": "gpt-5",
    "apiKey": "sk-...",
    "baseURL": "https://api.openai.com/v1",
    "temperature": 0.2
  },
  "skills": {
    "path": "/absolute/path/to/skills"
  },
  "mcpServers": {
    "example": {
      "transport": "stdio",
      "command": "uvx",
      "args": ["example-mcp"],
      "env": {}
    },
    "remote-example": {
      "transport": "http",
      "url": "https://example.com/mcp",
      "headers": {
        "Authorization": "Bearer ..."
      }
    }
  }
}
```

首期只支持 OpenAI-compatible `ChatOpenAI`，不提前安装多个 provider 包。`provider`
固定为 `openai`，保留该字段是为了让配置含义明确，并为以后切换到
`langchain.chat_models.init_chat_model` 留出兼容位置。

API key、MCP headers 和 MCP env 允许直接保存在该本地 JSON 中。配置对象不得进入
Graph state、thread metadata、checkpoint、日志或前端响应。Pydantic 对公开错误输出隐藏
secret 字段。

以下情况使 Agent Server 启动失败，并输出包含配置路径和字段位置的中文错误：

- 文件不存在、不可读或不是合法 JSON。
- 模型必填字段缺失或 provider 不受支持。
- Skill 根目录不存在、不可读或不是目录。
- MCP transport 配置非法、连接失败或工具发现失败。

## 7. 前端

前端以 `/vol2/1000/code/assistant-ui-demo` 的组合方式为实现基线：

```tsx
const runtime = useStreamRuntime({
  assistantId: "agent",
  apiUrl: "/agent-api",
});
```

不复制 demo 的 Next.js 代理层。Vite 将 `/agent-api` 代理到
`http://127.0.0.1:2024`；现有 `/api` 到 FastAPI 的代理保持不变。

### 7.1 Thread runtime

assistant-ui 的 LangChain runtime 负责消息水合、流式、工具调用、停止生成、编辑后分支和
重试。项目只实现一个薄的 `RemoteThreadListAdapter` 调用 LangGraph SDK threads API：

- 创建、列表、切换、重命名和删除 thread。
- 标题保存在 LangGraph thread metadata。
- 首条用户消息在前端派生默认标题并更新 metadata。

不再定义自己的 run 文档、revision、watermark、重复 run 或终态恢复模型。

### 7.2 审批

审批 UI 直接消费 `useLangChainInterrupts()`：

- 展示原生 HITL action request 的工具名和参数。
- 每个 action 提供批准或拒绝。
- 同一 checkpoint 有多个 interrupt 时，收集所有决定后通过
  `useLangChainRespondAll()` 一次提交。
- 不生成 bridge interrupt ID，不维护 session allowance，不实现第二套 resume payload。

### 7.3 删除的 UI

删除以下 Agent 工作台功能及其状态：

- run Inspector、预算、来源和产物。
- MCP/Skill 管理和导入面板。
- Capability lease、revision 和运行 watermark 展示。
- Agent 请求级模型设置及专用的 `vr-agent-model` localStorage key；旧入口使用的
  `vr-llm` 保持不变。

保留并简化当前 assistant-ui Thread、工具 fallback/group、线程列表和工作台布局。首期不
增加 MCP/Skill 已加载列表接口；启动成功表示配置完成，实际调用仍显示在工具消息中。

旧聊天、辩论和反思页面继续使用原模型设置和原 API，不受 Agent 工作台固定配置影响。

## 8. 错误与恢复

- 配置和能力发现错误发生在启动阶段，不以残缺工具集继续服务。
- 模型和运行错误使用 LangGraph 原生 stream error，由 assistant-ui 提供重试交互。
- 工具执行错误保留为原生 ToolMessage，让模型看到失败，不伪造客观数据。
- HITL 只依赖 LangGraph checkpoint；服务重启后从同一 thread 的原生状态恢复。
- 旧 Agent JSON 不再读取，文件保留在用户目录中作为备份。

## 9. 删除范围

删除当前由框架替代的 Agent 后端模块及测试，包括：

- `runs.py`、`stores.py`、`protocol.py`、`router.py`。
- `models.py` 中自研 thread/run wire models。
- `governance.py`、`artifacts.py`、`provenance.py`、`policy.py`。
- `capabilities.py`、自研 `mcp.py`、自研 `skills.py`、`tool_executor.py`。
- FastAPI 中 `/api/agent/*` 路由、启动和关闭生命周期挂载。
- 与这些合同绑定的 backend tests。

删除或重写对应前端 Agent API、AG-UI runtime、workspace store、管理面板、Inspector 和测试。
不删除被旧入口使用的 `chat.py`、`tools.py`、`mcp_server.py` 或数据模块。

实施时只删除已确认没有旧入口消费者的文件和导出；每个删除动作由 `rg` 引用检查与测试
覆盖验证。

## 10. 验证

### 10.1 后端

- 配置测试覆盖合法配置、缺失字段、非法 JSON、路径错误和 secret 脱敏。
- Graph 测试覆盖中立系统提示、内置工具、MCP tools、Skills middleware 和 HITL policy。
- 使用离线 scripted model 验证普通回复和内置工具循环。
- 使用 fake MCP server 验证工具发现、暂停、批准后执行和拒绝后不执行。
- 使用 fixture Skill 验证元数据发现、`SKILL.md` 渐进读取、根目录逃逸失败，以及写入
  工具未注册。
- Agent Server 集成测试验证 thread 创建、流式、checkpoint resume 和进程重启后读取。
- 现有 `pytest -m "not live"` 保持通过；删除的自研合同测试由原生集成测试替代。

### 10.2 前端

- 测试 LangGraph thread adapter 的列表、创建、重命名和删除。
- 测试 `useStreamRuntime` 固定 assistant ID/API URL，不发送模型密钥。
- 测试多个 interrupt 的决定按原顺序一次提交。
- 测试删除 Agent localStorage 模型配置后旧入口配置仍可使用。
- 运行前端单元测试和 `npm run build`。

### 10.3 浏览器验收

使用仓库 `AGENTS.md` 指定的 headless Playwright Chromium，在桌面和移动 viewport 验证：

- 新建会话并收到流式回复。
- 刷新页面后恢复消息。
- 切换、重命名和删除 thread。
- MCP 调用暂停，批准后继续，拒绝后不执行。
- 停止生成可用，布局无重叠或溢出。

最终运行：

```bash
(cd backend && .venv/bin/pytest -m "not live")
(cd frontend && npm test && npm run test:unit && npm run build)
git diff --check
```

## 11. 验收标准

- Agent 页面不再调用 `/api/agent/*` 或发送请求级模型密钥。
- Agent 工作台通过原生 LangGraph API 完成 thread、run、stream 和 interrupt resume。
- 重启 Agent Server 后，本地 thread 与待审批 checkpoint 仍可读取和恢复。
- 内置工具、MCP 和 Skills 均由 LangChain 生态组件接入。
- 仓库中不再存在 Agent 自研 session/run/checkpoint 持久化实现。
- 中立性红线、工具客观失败语义和 Eastmoney 串行限流约束保持有效。
- 旧 AI 入口行为和 API 合同不变。
