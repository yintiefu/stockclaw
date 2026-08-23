# Agent 工作台原生 LangGraph 迁移设计

**日期：** 2026-08-23

**状态：** 已完成第三轮对抗性评审修正，待进入 writing-plans

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
        `- LangChain HumanInTheLoopMiddleware
        |
        v
astock / gstock / market / newsradar / other data modules

FastAPI :8900
  existing data APIs and legacy AI entry points
```

LangGraph Server 和 FastAPI 是两个本地进程。Agent Server 直接导入 `backend/tools.py`
及其数据模块，不经 FastAPI HTTP 转发。这样保留同一套客观数据工具，同时不改变旧入口。

LangGraph Server 只监听 `127.0.0.1:2024`，不复用 `VR_API_KEY`，也不经 FastAPI 反代。
这是针对本地个人项目的明确取舍：同一用户的本机进程属于信任边界，Agent Server 不支持
监听局域网或公网地址。LangGraph API 默认 `CORS_ALLOW_ORIGINS=*`，会允许其他网页跨域
读写本机 Agent API，甚至提交 HITL 审批；仅绑定 loopback 不能阻止这种浏览器访问。
`langgraph.json` 必须通过 `env` 把 `CORS_ALLOW_ORIGINS` 固定为
`http://127.0.0.1:5899`。该设置是浏览器读取隔离，不是请求侧鉴权或 CSRF 防护：恶意 Origin
的预检会得到 400，实际 GET/POST 响应不会带 `Access-Control-Allow-Origin`，所以网页脚本
读不到响应；但浏览器仍可发送不触发预检的简单请求，形成创建 thread、提交 run 等盲写，
可能消耗付费模型和数据源配额。在仅 loopback、单用户的信任模型下接受这一残余风险。以后
若需要消除盲写、增加其他 origin 或支持远程访问，必须单独设计鉴权、CSRF 防护与反代，不能
只修改监听地址或 CORS allowlist。

Agent graph 不创建或注入 `MemorySaver`。在 Agent Server 中，线程、checkpoint 和 store
由 LangGraph Server 管理。开发环境使用 `langgraph dev` 自带的 `.langgraph_api/` 本地
持久化目录，该目录必须加入 `backend/.gitignore`。重启恢复行为对运行时版本敏感，必须使用第
5.1 节钉住并验证过的版本组。

## 5. 后端组件

迁移后的 `backend/agent/` 只保留以下窄职责：

- `__init__.py`：保留 Agent 模块边界。
- `settings.py`：定位、读取并校验本地静态配置。
- `tool_registry.py`：把 `tools.py` 中的现有工具转换为 LangChain tools。
- `graph.py`：加载模型、MCP tools 和 middleware，导出编译后的 Agent Server graph。
- `ssrf.py`：继续服务旧 `chat.py` 的用户输入 URL 校验；新静态 Agent 配置不复用该策略。

`backend/langgraph.json` 必须包含 `"dependencies": ["./"]`、`agent` graph，以及通过
`env` 声明的本地前端 CORS allowlist：

```json
{
  "dependencies": ["./"],
  "graphs": { "agent": "./agent/graph.py:graph" },
  "env": { "CORS_ALLOW_ORIGINS": "http://127.0.0.1:5899" }
}
```

在 `langgraph dev` 路径中，`"./"` 只满足非空 dependencies 校验并把后端目录加入
`sys.path`，不会安装 `requirements.txt`。因此 `backend/requirements.txt` 仍是唯一依赖
清单，但预装完整 `.venv` 是启动 Agent Server 的前置条件。标准开发命令为：

```bash
cd backend
.venv/bin/pip install -r requirements.txt
.venv/bin/langgraph dev --host 127.0.0.1 --port 2024 --no-browser
```

Agent 工作台迁移后，后端虚拟环境统一要求 Python 3.11+。现有数据 API 与旧 AI 入口仍在
同一环境运行，但不改变其业务合同。当前 `.venv` 是 Python 3.13.14，已经满足要求；实施在
原环境内安装新增依赖，不为本次迁移无谓重建它。验证依赖可重现性时另建临时干净 venv。

### 5.1 版本与依赖合同

本地重启恢复 spike 进程级验证的是以下 LangGraph 四件套：它们可以在 interrupt 挂起后
停止进程、从 `.langgraph_api/` 恢复 thread/checkpoint，再通过 `command.resume` 完成运行：

```text
langgraph-cli[inmem]==0.4.31
langgraph-api==0.12.6
langgraph-runtime-inmem==0.32.6
langgraph==1.2.11
```

spike 环境的 `langchain-core` 实际为 `1.6.0`，且最小 graph 不经过 LangChain，因此不把
LangChain 侧版本描述为已完成进程级恢复验证。实施使用的完整 Agent 运行时版本合同如下，
每一项都必须显式写入 `backend/requirements.txt` 的 Agent 分组，而不是
`requirements-dev.txt`：

```text
langgraph-cli[inmem]==0.4.31
langgraph-api==0.12.6
langgraph-runtime-inmem==0.32.6
langgraph==1.2.11
httpx==0.28.1
langchain==1.3.15
langchain-core==1.5.5
langchain-openai==1.5.1
langchain-mcp-adapters==0.3.2
mcp==1.26.0
deepagents==0.7.7
langchain-anthropic==1.5.4
langchain-google-genai==4.3.1
```

清华镜像可安装的最新 Deep Agents 版本是 `0.7.7`；其元数据要求
`langchain>=1.3.14,<2.0.0` 和 `langchain-core>=1.5.0,<2.0.0`，当前环境已满足，不存在强制
升级 `langchain-core` 的直接冲突。该包会同时拉入当前 Agent 不直接使用的硬依赖
`langchain-anthropic` 与 `langchain-google-genai`，这是采用 Deep Agents middleware 的已知
依赖成本。`langchain-anthropic==1.5.4` 的作用是避免由 `1.6.0` 直接要求 core `>=1.6.0`，
但它不会让干净 venv 的 resolver 主动选择 core `1.5.5`；真正固定解析结果的是
`langchain-core==1.5.5` 的直接 requirements 条目。`1.5.4`/`4.3.1` 与 core `1.5.5` 的组合
已在当前 venv 做增量 dry-run 兼容验证，干净 venv 仍必须按上面的完整显式合同复验。

依赖任务仍先在当前 `requirements.txt` 上执行 `pip install --dry-run`，记录解析差异；再在
临时干净 Python 3.11+ venv 中安装完整 requirements、运行 `pip check` 和 LangChain、
LangGraph、MCP、mootdx 合同测试。若解析结果改变上述钉住版本，必须先重新验证并更新版本
合同，不能只运行新 Agent 测试。

迁移完成前不得放宽上述全部钉住版本。以后升级必须重新执行
“挂起 interrupt -> 停服 -> 重启 -> 恢复”的进程级测试；首次迁移也必须用完整合同，尤其
是 core `1.5.5`，重跑该恢复 case，补上 spike 未覆盖的 LangChain 组合。

### 5.2 Graph 组装

`graph.py` 的一次性异步 builder 按固定顺序执行：

1. 加载并校验 `settings.json`。
2. 构建固定的 OpenAI-compatible `ChatOpenAI(parallel_tool_calls=False)`。
3. 构建现有内置投研工具。
4. 通过 `MultiServerMCPClient.get_tools()` 发现静态配置的 MCP tools。
5. 建立 Deep Agents `FilesystemBackend`，`root_dir` 固定为配置声明的单一 Skills
   根目录。
6. 组合 `SkillsMiddleware`、只注册 `ls`/`read_file` 的 `FilesystemMiddleware` 和
   `HumanInTheLoopMiddleware`。
7. 调用 `create_agent`，由 Agent Server 接管 graph 持久化与执行。

异步 builder 暴露 `model: BaseChatModel | None = None` 测试注入参数。生产路径在参数为
`None` 时才从 settings 构建 `ChatOpenAI`；离线测试复用
`tests/agent/fakes.py::ScriptedChatModel` 注入带 tool calls 的回复序列，不能访问真实
provider。测试配置由 `backend/conftest.py` 在任何 Agent 模块导入前把
`VR_AGENT_SETTINGS` 指向临时目录，禁止读取真实 `~/.vibe-research/agent/settings.json`。

`MultiServerMCPClient.get_tools()` 是异步 API。LangGraph API `0.12.6` 支持 `async def`
graph factory，但 factory 会在每个 run 上调用；把 MCP 发现放进去会为每个 run 重复连接。
因此模块在 Agent Server 启动导入阶段只执行一次异步 builder，并导出已编译的 `graph`，
不用 per-run factory。`graph.py` 的模块级初始化会读取配置、发现 MCP 并构建 graph，这是有意
的 import 副作用；MCP 发现失败会使模块导入失败，从而阻止服务进入 ready 状态。测试必须先
准备隔离配置与 fake MCP，再导入模块；测试主体直接调用异步 builder 并注入 scripted model。

`chat.SYSTEM_PROMPT.format(context="Agent 工作台")` 继续作为固定系统提示。配置、Skill
内容和 MCP 元数据仍可能形成 prompt injection 通道，因此不宣称 system prompt 能完全抵御
覆盖尝试。固定 system prompt、只读 Skills 工具面、MCP 逐次审批和行为测试共同保护产品
中立边界。

### 5.3 内置工具

内置工具继续以 `tools.py` 为唯一来源。适配层只负责：

- 将已有 JSON schema 转换为 LangChain tool schema。
- 在线程池中调用现有同步实现，避免阻塞 Graph 的异步运行。
- 保留现有结构化错误结果和输出裁剪行为。

LangGraph `ToolNode` 会并发执行同一模型消息中的多个 tool call，而 `astock.em_get` 的
时间戳间隔不是原子锁。适配层必须提供双重防线：

1. 构建 `ChatOpenAI` 时设置 `parallel_tool_calls=False`，减少模型产生并行工具批次。
2. 在 `tool_registry.py` 创建一个进程级共享 `asyncio.Lock`，所有内置工具在进入线程池前
   获取同一把锁，完成后释放。锁不能按工具实例、工具名、thread 或 run 分拆。

第二层是权威防线，因为第三方 OpenAI-compatible provider 可能忽略第一层参数，且 Graph
仍可能从其他路径收到多个工具调用。MCP 调用不使用这把锁。迁移不改变 `em_get` 两次请求
至少间隔 1 秒的规则。

### 5.4 MCP

MCP 完全使用 `langchain-mcp-adapters`：

- 支持静态配置中的 `stdio` 和 Streamable HTTP transport。
- 使用 `MultiServerMCPClient` 默认无状态 session；每次工具调用创建并清理连接。
- 构造 client 时设置 `tool_name_prefix=True`，MCP 工具统一命名为
  `<server_name>_<tool_name>`。组装 graph 前再校验 MCP、内置工具和 middleware 工具名全局
  唯一；若配置的 server name 仍造成重名，启动失败并列出冲突名称。
- 不维护自定义 session registry、generation、catalog、lease、alias store 或 health store。
- MCP 初始化或工具发现失败会阻止 Agent Server 启动。
- MCP 工具执行错误使用 adapter 原生 error `ToolMessage`，不转换成伪成功结果。

所有发现到的 MCP tools 自动加入 `HumanInTheLoopMiddleware.interrupt_on`，只允许
`approve` 和 `reject`。内置只读投研工具与 Skills 只读文件工具不触发审批。

### 5.5 Skills

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
该文件包含明文模型与 MCP secret，文档要求在 POSIX 系统设置为 `0600`，并同步修订
`AGENTS.md` 的隐私口径。loader 发现 group/other 权限时向 stderr 输出明确警告，但不因
Windows 等不支持 POSIX mode 的平台拒绝启动。该路径必须保持 Git 忽略。

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

模型 `baseURL` 和 MCP 地址来自本机静态管理员配置，不是远程请求参数。本次明确把它们视为
可信配置，不套用 `agent.ssrf` 的 public-mode 私网限制；这是删除前端动态 URL 后的信任
边界变化。旧 `chat.py` 仍通过保留的 `agent.ssrf` 校验用户请求携带的 base URL。

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
`http://127.0.0.1:2024`，并 rewrite 掉 `/agent-api` 前缀，因为 LangGraph API 路由位于
server 根路径；现有 `/api` 到 FastAPI 的代理保持不变。

### 7.1 Thread runtime

assistant-ui 的 LangChain runtime 负责消息水合、流式、工具调用、停止生成、编辑后分支和
重试。项目只实现一个薄的 `RemoteThreadListAdapter` 调用 LangGraph SDK threads API，并
通过 `useStreamRuntime({ unstable_threadListAdapter: adapter })` 接入。该选项在
`@assistant-ui/react-langchain==0.0.27` 仍标为 unstable；依赖升级必须以 adapter 合同测试
为门槛，不能假设 API 稳定：

- adapter 实现必需的 `list`、`rename`、`delete`、`initialize`、`fetch`、`generateTitle`、
  `archive` 和 `unarchive`；`initialize(threadId)` 负责把 assistant-ui 本地 thread 初始化为
  LangGraph remote thread。
- `generateTitle` 从首条用户消息派生标题并写入 LangGraph thread metadata。
- LangGraph threads API 没有 archive 概念。`archive(remoteId)` 先读取并保留现有 metadata，
  再通过 threads update 写入 `archived: true`；`unarchive` 写入 `archived: false`。`list` 和
  `fetch` 把该字段翻译为 assistant-ui 的 `status: "archived" | "regular"`。首期 UI 不显示
  归档按钮，但 adapter 的必需方法保持可用。
- thread 切换不属于 adapter；使用 `useStreamRuntime` 顶层的受控 `threadId` 与
  `onThreadIdChange` 接入页面路由/选中状态。
- 提供自定义 adapter 后，不再配置顶层 create/delete callbacks；这些选项会被静默忽略。

不再定义自己的 run 文档、revision、watermark、重复 run 或终态恢复模型。
自动标题只覆盖 assistant-ui 调用 `generateTitle` 的 thread；从 LangGraph SDK、Studio 或
其他客户端创建的 thread 不再由后端自动命名，显示其 metadata 标题或默认标题。这是接受的
行为变化。

### 7.2 审批

审批 UI 直接消费 `useLangChainInterrupts()`：

- 展示原生 HITL action request 的工具名和参数。
- 每个 action 提供批准或拒绝。
- `HumanInTheLoopMiddleware` 把同一模型消息中的多个待审批 tool call 聚合为一个
  interrupt；UI 按 `action_requests` 原顺序收集全部决定，再通过一次
  `const respond = useLangChainRespond(); respond({ decisions })` 提交。决定数量必须与
  action 数量严格一致。
- 不使用 `useLangChainRespondAll()`；它用于同一 checkpoint 上多个独立 interrupt，不是
  LangChain HITL middleware 的聚合载荷。
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

- `runs.py`、`stores.py`、`protocol.py`、`router.py`、`runtime.py`。
- `models.py` 中自研 thread/run wire models。
- `governance.py`、`artifacts.py`、`provenance.py`、`policy.py`。
- `capabilities.py`、自研 `mcp.py`、自研 `skills.py`、`tool_executor.py`。
- FastAPI 中 `/api/agent/*` 路由、启动和关闭生命周期挂载。
- 与这些合同绑定的 backend tests。

删除或重写对应前端 Agent API、AG-UI runtime、workspace store、管理面板、Inspector 和测试。
删除后端 `ag-ui-*` 依赖以及前端 `@ag-ui/client`、`@assistant-ui/react-ag-ui`；加入
`@assistant-ui/react-langchain==0.0.27` 及其经锁文件解析的 LangGraph React/SDK 依赖。
同时删除 `frontend/package.json` 中只为 AG-UI 存在的 `overrides["@ag-ui/client"]`。
后端 requirements 的 Agent 运行时分组删除 `ag-ui-langgraph`、`ag-ui-protocol` 和
`ag-ui-a2ui-toolkit`，并按第 5.1 节逐条写入完整版本合同。

以下文件属于明确保护范围，不得被目录级删除误伤：

- 后端 `agent/ssrf.py`、`tests/agent/test_ssrf.py`、仍可复用的 `tests/agent/fakes.py` 和
  `tests/agent/fake_mcp_server.py`。
- 旧入口使用的 `chat.py`、`tools.py`、`mcp_server.py` 和数据模块。
- 前端 `src/lib/agents.ts`、`src/lib/ndjson.ts`，以及旧入口和 Agent 页面共用的
  assistant-ui Markdown/Thread 展示组件。

实施时只删除已确认没有旧入口消费者的文件和导出；每个删除动作由 `rg` 引用检查与测试
覆盖验证。

## 10. 验证

### 10.1 后端

- 配置测试覆盖合法配置、缺失字段、非法 JSON、路径错误和 secret 脱敏。
- 测试环境在导入 graph 前设置临时 `VR_AGENT_SETTINGS`，并断言真实默认配置未被读取。
- Graph 测试覆盖中立系统提示、内置工具、MCP tools、Skills middleware 和 HITL policy。
- 复用 `tests/agent/fakes.py::ScriptedChatModel` 验证普通回复、带 tool calls 的内置工具循环
  和 HITL 恢复，不另建第二套 scripted model。
- 并发调用两个会进入 `astock.em_get` 的内置工具，mock 底层 HTTP `get` 记录实际开始
  时间，断言两次请求间隔不小于 1 秒；同时断言所有内置 tool 实例共享同一进程级锁。
- 使用 fake MCP server 验证工具发现、server-name 前缀、暂停、批准后执行和拒绝后不执行；
  构造重名 fixture 验证 Agent Server 拒绝启动并报告冲突名称。
- 使用 fixture Skill 验证元数据发现、`SKILL.md` 渐进读取、根目录逃逸失败，以及写入
  工具未注册。
- Agent Server 子进程集成测试使用临时工作目录启动真实
  `langgraph dev --no-browser --no-reload`。测试 harness 以 session scope 复用正常启动过程，
  避免每个 case 重启；专门的恢复 case 再完整编排“interrupt 挂起 -> 停服 -> 确认
  `.langgraph_api/` 落盘 -> 重启 -> 恢复同一 interrupt -> 终态”。禁用 watchfiles，避免测试
  写临时文件时触发 reload 干扰停服与重启。
- Agent Server HTTP 集成测试按 CORS 层次断言：恶意 Origin 的 OPTIONS 预检得到 HTTP 400；
  直接发送带恶意 Origin 的 GET/POST 时允许下游返回 200，但响应不得包含
  `Access-Control-Allow-Origin`；允许的本地前端 Origin 正常获得该响应头。另用
  `text/plain` 简单 POST 实证盲写可创建 thread、提交 run 并推进到 interrupt，把这一残余面
  固定为已知风险，不能误写成 CORS 会阻止请求执行。
- 依赖安装与 clean-venv 测试需要访问清华镜像，标记为 `@pytest.mark.live`，不进入默认
  `pytest -m "not live"`；它在临时 Python 3.11+ venv 运行安装、`pip check`、钉版本断言，
  以及 LangChain、LangGraph、MCP、mootdx 合同测试。
- 现有 `pytest -m "not live"` 保持通过；删除的自研合同测试由原生集成测试替代。

### 10.2 前端

- 测试 LangGraph thread adapter 的全部必需方法，尤其是 `initialize`、`fetch`、
  `generateTitle`、`archive` 和 `unarchive`；归档测试断言 metadata 合并不丢 title 等字段，
  且 `list`/`fetch` 正确翻译 status。
- 对 `unstable_threadListAdapter` 做编译与运行合同测试，升级 assistant-ui 时优先发现破坏。
- 测试 `threadId`/`onThreadIdChange` 的受控切换，并断言自定义 adapter 下不依赖会被忽略的
  顶层 create/delete callbacks。
- 测试 `useStreamRuntime` 固定 assistant ID/API URL，不发送模型密钥。
- 测试一个聚合 HITL interrupt 的多个 action 按原顺序通过一次
  `respond({ decisions })` 提交，且数量不匹配时不恢复运行。
- 测试删除 Agent localStorage 模型配置后旧入口配置仍可使用。
- 运行前端单元测试和 `npm run build`。

### 10.3 E2E 基建与浏览器验收

当前 `frontend/playwright.config.ts` 直接启动 `tests.agent_e2e_app`，该 fixture 依赖将删除的
`agent.router`，必须随架构一起重建：

- Playwright 总计启动三个隔离服务：生产 `app:app` FastAPI、测试专用 LangGraph Server、
  Vite。FastAPI 只通过临时 `VR_DATA_DIR`/`VR_REPORTS_DIR` 隔离用户数据，不再保留
  `tests.agent_e2e_app` 薄壳或 Agent 接缝，探活 URL 改为 `/api/health`。
- E2E graph 与配置固定放在 `backend/tests/agent_e2e/graph.py` 和
  `backend/tests/agent_e2e/langgraph.json`。启动 helper 把这两个 fixture 复制到独立临时 cwd，
  设置生产 backend 为 `PYTHONPATH`，再用
  `langgraph dev --config <temp>/langgraph.json --no-browser --no-reload` 启动，使
  `.langgraph_api/` 不触碰开发会话。
- E2E graph 导入生产 `agent/graph.py` 时会先执行一次模块级生产 builder，再调用同一异步
  builder 注入 `tests/agent/fakes.py::ScriptedChatModel` 构建测试 graph；两次构建是已知且
  接受的测试启动成本，不在生产 graph 中增加 `VR_E2E` 分支。E2E settings 使用无效模型
  凭据，首次生产 builder 只实例化模型而不调用 provider。
- fake MCP 固定使用 `tests/agent/fake_mcp_server.py` 的 stdio transport，由 E2E settings
  启动和回收，不分配端口，也不增加第四个 Playwright `webServer`。
- LangGraph 通过 `VR_AGENT_SETTINGS` 指向 E2E 专用 settings，Skills 只引用临时 fixture。
- Vite 同时注入隔离的 FastAPI URL 与 LangGraph URL。三个服务均使用固定测试端口、
  `127.0.0.1` 和 `reuseExistingServer: false`；测试专用 LangGraph 配置把
  `CORS_ALLOW_ORIGINS` 设为该 Vite 测试 origin。
- 进程重启恢复由第 10.1 节的后端子进程测试编排；Playwright 不依赖无法在测试中重启的
  `webServer` 管理器，只验证浏览器刷新后的 checkpoint 水合。
- 验证 Vite `/agent-api` 代理剥离前缀后访问 LangGraph 根 API。

使用仓库 `AGENTS.md` 指定的 headless Playwright Chromium，在桌面和移动 viewport 验证：

- 新建会话并收到流式回复。
- 刷新页面后恢复消息。
- 切换、重命名和删除 thread。
- MCP 调用暂停，批准后继续，拒绝后不执行。
- 使用本地前端 Origin 可正常读取 Agent API；恶意 Origin 的预检失败，实际响应不含 CORS
  allow-origin 头。盲写风险由第 10.1 节 HTTP 集成测试覆盖，不在浏览器测试中错误断言服务端
  拒绝实际请求。
- 停止生成可用，布局无重叠或溢出。

### 10.4 文档与依赖检查

- 更新 `README.md`、`README_en.md`、`backend/README.md` 和根 `AGENTS.md`：Python 3.11+、
  FastAPI/LangGraph 双进程命令、预装 requirements 前置条件、`settings.json` 明文 secret 与
  `0600` 权限、仅本机监听，以及 `CORS_ALLOW_ORIGINS` 只能阻止跨域读取、不能消除简单请求
  盲写的浏览器信任边界。
- 在 `backend/.env.example` 记录 `VR_AGENT_SETTINGS`；在 `backend/.gitignore` 忽略
  `.langgraph_api/`。
- 更新 `CHANGELOG.md`，说明 Agent 工作台会话不迁移、功能删减和启动方式变化。
- 在后端和前端依赖清理后运行引用搜索，确认没有残留 `ag_ui`、`@ag-ui/client`、
  `react-ag-ui` 或 `/api/agent/*` 运行路径。

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
- LangGraph Server 只监听本机，且 `langgraph.json.env.CORS_ALLOW_ORIGINS` 明确限制为本地
  前端 origin；恶意网页脚本不能读取 Agent API 响应，但简单请求盲写仍可能创建 thread、
  提交 run 并消耗模型或数据源配额。该残余面在本地单用户模式下被明确接受和测试。
- 旧 AI 入口行为和 API 合同不变。
