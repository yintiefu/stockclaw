# AI 调用统一到 LangGraph 与配置化工作流设计

**日期：** 2026-08-25

**状态：** 设计已确认，待实施计划

## 1. 背景

项目目前有两套 AI 运行链路：

- Agent 工作台通过 assistant-ui 直接连接本地 LangGraph Server，由原生 thread、run、
  checkpoint、stream 和 interrupt 承担运行控制。
- 页面内“问 AI”、每日复盘、资讯提炼、多空辩论和反思审计仍通过 FastAPI 的
  `/api/chat`、`/api/debate`、`/api/reflect` 调用自研 OpenAI-compatible SSE/NDJSON 链路。

两套链路已经共享 `backend/tools.py`，但模型配置、流式协议、持久化、错误语义和前端客户端
仍然重复。下一阶段要把所有模型调用统一到 Agent 工作台已经采用的 LangGraph Server，
同时保留现有业务页面和 FastAPI 客观数据 API。

本次设计遵守 `VISION.md`：只提供客观数据、分析框架和核验工具，不推荐买卖、不预测价格、
不给目标价、评级、排名或交易时机。多空辩论止于分歧点和验证清单，反思止于推理审计与
后续核验。

## 2. 已确认决策

1. FastAPI 继续承担行情、市场、持仓、研报、文件等客观数据和本地业务 API；本次只迁移
   模型调用和 AI 编排。
2. 使用一个 LangGraph Server 注册多个专用 Graph，而不是把所有能力塞进一个 Router Graph。
3. 保留现有页面、按钮、弹层和专用结果展示；前端底层改用 LangGraph SDK。
4. 多空辩论和反思审计使用显式 LangGraph 工作流；Skill 承载分析方法和角色指令，不能替代
   确定性编排。
5. Graph 骨架和安全约束保留在代码中；业务流程使用仓库内、版本控制的 YAML 配置。
6. 工作流配置启动时一次性加载，修改后重启 Agent Server 生效；不增加在线编辑或热更新。
7. 模型和 API Key 只来自 Agent `settings.json`；移除浏览器请求级 `vr-llm` 配置和本机 CLI
   订阅接入。
8. 专用工作流隔离持久化，不进入 Agent 工作台会话列表；历史在各自业务页面查看。
9. 旧页面内问答的 `localStorage` 历史不迁移、不再读取，也不主动删除。
10. 内置工作流只能引用仓库内置 Skill；用户 Skill 只影响通用 Agent 工作台。

## 3. 目标与非目标

### 3.1 目标

- LangGraph Server 成为项目唯一模型调用和 AI 编排运行时。
- assistant-ui 继续承担通用 Agent 工作台对话。
- 专用页面通过 LangGraph SDK 使用类型化工作流状态和固定流式事件。
- 复用同一模型工厂、工具注册、Skill 加载、中立策略、checkpoint 和 trace。
- 用严格 YAML 描述阶段、角色、Skill、工具清单、输入可见范围和失败策略。
- 保留多空辩论的同源底稿、数据缺口、阶段隔离、局部失败终态和东财串行限流。
- 页面刷新、前端断线或 Agent Server 重启后可以恢复专用任务状态。

### 3.2 非目标

- 不迁移、重写或删除 FastAPI 客观数据路由。
- 不把工作流 YAML 设计成可执行脚本、通用 DAG 语言或任意 Python 模块加载器。
- 不增加工作流在线编辑、热更新或前端编排器。
- 不保留 CLI 模型接入。
- 不把专用工作流历史混入 Agent 工作台会话列表。
- 不自动导入旧浏览器 AI 对话。
- 不新增买卖信号、评分、排名、回测、选股或交易能力。

## 4. 总体架构

```text
Frontend
  |- Agent 工作台
  |    `- assistant-ui -> agent Graph
  |- 页面内“问 AI”
  |    `- 现有弹层 -> LangGraph SDK -> embedded_agent Graph
  `- 辩论 / 反思 / 复盘 / 资讯提炼
       `- 专用页面 -> LangGraph SDK -> 对应 Workflow Graph

LangGraph Server :2024
  |- agent
  |- embedded_agent
  |- debate
  |- reflection
  |- daily_review
  `- news_digest
       |- shared model factory
       |- fixed neutrality policy
       |- workflow YAML loader / builder
       |- built-in and user Skills
       |- shared tool executor -> tools.py
       `- native threads / runs / checkpoints / streams

FastAPI :8900
  |- existing objective data APIs
  |- portfolio / reports / local business APIs
  `- read-only agent status summary
```

多个 Graph 是业务状态边界，不是多套技术栈。它们运行在同一个 LangGraph Server 中，共用
模型、工具、Skill 基础设施和持久化协议。

## 5. 组件与目录

```text
backend/agent/
  graph.py                    通用 Agent 工作台 Graph
  embedded_graph.py           页面内问答 Graph（无 MCP、无用户 Skill）
  model_factory.py            唯一模型构建入口
  workflow_loader.py          YAML 加载、Pydantic 校验和交叉引用校验
  workflow_builder.py         固定工作流 kind 到 StateGraph 的装配
  workflow_runtime.py         Skill 注入、阶段执行、事件和错误转换
  workflow_state.py           工作流输入、阶段、结果和状态模型
  tool_executor.py            共享工具执行和并发策略
  workflows/
    debate.yaml
    reflection.yaml
    daily_review.yaml
    news_digest.yaml
  builtin_skills/
    stock-analysis/
      SKILL.md
    debate/
      SKILL.md
      references/
        bull.md
        bear.md
        bull-rebut.md
        bear-rebut.md
        referee.md
    reflection-audit/
      SKILL.md
    market-review/
      SKILL.md
    news-digest/
      SKILL.md
```

`backend/langgraph.json` 和 `scripts/dev` 生成的用户工作目录配置均注册相同的 Graph：

```json
{
  "graphs": {
    "agent": "./agent/graph.py:graph",
    "embedded_agent": "./agent/embedded_graph.py:graph",
    "debate": "./agent/workflows_graph.py:debate_graph",
    "reflection": "./agent/workflows_graph.py:reflection_graph",
    "daily_review": "./agent/workflows_graph.py:daily_review_graph",
    "news_digest": "./agent/workflows_graph.py:news_digest_graph"
  }
}
```

具体文件可以在实施计划中按现有模块规模合并，但职责边界不能合并回单一大模块。

## 6. 配置化工作流

### 6.1 配置边界

YAML 可以声明：

- 工作流 ID、配置版本、schema 版本和固定 `kind`。
- 工作流变体及其阶段顺序。
- 阶段使用的内置 Skill 和具体 instruction 文件。
- 每个阶段可见的底稿、输入和前序阶段。
- 底稿工具清单、标题、参数、必需性和空结果语义。
- 输入长度、单节内容长度等软限制。
- 阶段失败后继续、终止或标记部分完成。
- 用户可见的阶段标签和状态文案。

以下内容固定在代码中，不允许 YAML 覆盖：

- `VISION.md` 中立红线和禁止输出类型。
- 工具 schema、handler、参数校验和结果裁剪。
- Eastmoney 工具分类、进程级锁和节流规则。
- 可用工作流 `kind` 和节点类型白名单。
- 文件根目录、Skill 路径逃逸防护和密钥边界。
- thread metadata 保留字段和状态转换。
- 模型调用次数、输入长度等资源硬上限。
- 固定流式事件 schema。

### 6.2 辩论配置

```yaml
schema_version: 1
config_version: 1
id: debate
kind: staged_research

variants:
  standard: [bull, bear, referee]
  cross_exam: [bull, bear, bull_rebut, bear_rebut, referee]

dossier:
  sections:
    - id: quote
      title: 实时行情
      tool: query_quote
      args:
        codes: ["${input.code}"]
      empty_policy: required
    - id: margin
      title: 融资融券
      tool: query_margin
      args:
        code: "${input.code}"
      empty_policy: allow_no_record

stages:
  bull:
    label: 多方研究员
    skill: builtin/debate
    instruction: references/bull.md
    context: [dossier]
    on_error: continue
  bear:
    label: 空方研究员
    skill: builtin/debate
    instruction: references/bear.md
    context: [dossier, stage.bull]
    on_error: continue
  bull_rebut:
    label: 多方反驳
    skill: builtin/debate
    instruction: references/bull-rebut.md
    context: [dossier, stage.bull, stage.bear]
    on_error: continue
  bear_rebut:
    label: 空方反驳
    skill: builtin/debate
    instruction: references/bear-rebut.md
    context: [dossier, stage.bull, stage.bear, stage.bull_rebut]
    on_error: continue
  referee:
    label: 中立主持
    skill: builtin/debate
    instruction: references/referee.md
    context: [dossier, stages]
    on_error: fail

limits:
  section_chars: 1800
```

`${input.code}` 不是通用模板语言。loader 只允许 `${input.<已声明字段>}` 这种完整值引用，
不支持表达式、函数、条件、循环、属性遍历或字符串内代码执行。

完整 `debate.yaml` 在实施时迁移现有 13 项底稿清单及其参数和空结果策略，不能以示例中的两项
代替生产清单。

### 6.3 单步工作流配置

反思、每日复盘和资讯提炼复用固定 `single_pass` 骨架：

```yaml
schema_version: 1
config_version: 1
id: reflection
kind: single_pass
skill: builtin/reflection-audit
instruction: SKILL.md
input:
  text_field: source
  max_chars: 12000
result:
  field: audit
```

首期保存类型化工作流外壳和 Markdown 模型输出，不依赖第三方 OpenAI-compatible 服务普遍
支持 JSON Schema structured output。以后增加结构化模型结果时，YAML 只能引用代码中注册并
测试过的输出 schema ID。

### 6.4 加载与校验

所有配置在 Agent Server 启动时加载一次。`workflow_loader.py` 使用 Pydantic 严格模型并执行：

- `extra="forbid"`，未知字段直接失败。
- `schema_version` 必须是 loader 支持的文件格式版本；`config_version` 必须是正整数，表示工作流
  行为版本。
- 文件名、配置 `id` 和 LangGraph 注册名一致。
- `kind` 属于代码注册白名单。
- 工具和内置 Skill 存在。
- variant 引用的 stage 存在且不重复。
- stage 只能引用已经完成的前序阶段。
- `empty_policy`、`on_error` 等字段使用受限枚举。
- 配置软限制不超过代码硬上限。
- 输入引用只指向 schema 声明字段。
- Skill instruction 路径不能逃出对应内置 Skill 目录。

任一配置无效时 Agent Server 不进入 ready 状态，错误只报告文件、字段和原因，不包含密钥。

thread 记录 `config_version`。升级后历史结果仍可查看；未完成任务的版本与当前配置不兼容时，
禁止使用新配置静默恢复旧 checkpoint，只允许查看已有状态或重新发起。

## 7. Skill 体系

### 7.1 内置 Skill

内置 Skill 随仓库发布并受版本控制，承载：

- 五维个股分析框架。
- 多空角色的分析方法、证据要求和主持人验证清单。
- 反思审计方法。
- 每日复盘和资讯提炼方法。

固定 system policy 始终先于 Skill。中立红线不能只存在于 Skill 中。

专用 Workflow Graph 根据 YAML 确定性加载指定内置 Skill 和 reference 文件，不让模型决定是否
读取，也不允许用户 Skill 替换。

### 7.2 用户 Skill

现有 `settings.json.skills.path` 继续作为用户 Skill 根目录。通用 Agent 工作台可以发现内置和
用户 Skill；页面内问答只发现内置 Skill。虚拟文件系统使用不同路径命名空间：

```text
/builtin/<skill>/...
/user/<skill>/...
```

两类 Skill 都是只读。专用工作流 loader 只解析 `/builtin`。用户 Skill 的格式错误沿用现有
middleware warning 语义，不得影响内置工作流装配；内置 Skill 错误则阻止服务启动。

## 8. Graph 设计

### 8.1 通用 Agent Graph

`agent` 继续使用 `create_agent`，保留内置工具、用户 MCP、内置及用户 Skills 和 MCP HITL。
它只服务 `channel=workspace`。模型创建、
固定 system policy 和工具执行器改为共享组件，不再依赖 `chat.py`。

页面内“问 AI”使用独立的 `embedded_agent` Graph。它复用同一模型工厂、固定 policy、内置
工具、内置 Skills 和工具执行器，但不注册用户 MCP、用户 Skill 或 HITL，避免现有弹层收到无法
处理的审批中断，也防止用户扩展改变页面内置分析行为。其 thread 使用 `channel=embedded`，
并按 `route + scope_key` 隔离，不出现在工作台会话列表。

页面每轮调用把最新页面数据写入独立 `page_context` 状态字段。动态 system middleware 将其以
明确的数据边界注入模型，固定中立规则仍在外层；页面内容不能生成新的 system message 或修改
工具权限。`page_context` 设置长度硬上限并随 checkpoint 本地持久化。

### 8.2 Debate Graph

```text
validate_input
      |
collect_dossier
      |
execute configured stages in selected variant
      |
finalize
```

`collect_dossier` 不经过模型，按照 YAML 固定清单调用共享工具执行器。它保留现有：

- 实质内容判空，避免把“有壳无肉”当成成功数据。
- `allow_no_record` 与真正数据缺口的区分。
- 无任何真实数据时在角色模型调用前终止。
- 配置顺序恢复，完成顺序不影响底稿展示。
- 单节结果裁剪和缺口明示。

每个 stage 只获得 YAML `context` 明确列出的内容。阶段完成或失败都必须写入终态；失败文本不能
作为事实进入后续阶段。`referee` 只归纳共识、分歧根源、数据缺口和验证清单，不裁决多空。

### 8.3 Reflection Graph

`reflection` 校验非空输入并按配置截断，确定性加载 `reflection-audit` Skill。它只审计已有文本
的数据支撑、推测、口径、因果和脆弱环节，不调用投研数据工具产生新观点。输出为审计文本、
是否截断和工作流状态。

### 8.4 Daily Review 与 News Digest

两者复用 `single_pass` Graph builder，但注册为不同 Graph，使用不同输入 schema 和内置 Skill。
它们消费页面已经取得的客观数据，不默认额外调用工具。这样保留当前速度、成本和页面数据口径。

## 9. 工具执行与限流

工具执行策略属于代码安全元数据，不进入 YAML：

```text
eastmoney_serial   进程级共享锁，严格串行
parallel_safe      可以并发
unknown            默认串行
```

通用 Agent 和所有 Workflow Graph 必须经过同一个 `tool_executor.py`。它继续调用
`tools.exec_tool`，保留结构化错误和结果裁剪。

辩论底稿按 YAML 列出工具后，由执行器根据代码元数据自动分组：`parallel_safe` 项有限并发，
`eastmoney_serial` 项严格串行。不同 thread、run 和 Graph 共享同一把 Eastmoney 进程级锁。
模型侧仍设置 `parallel_tool_calls=False`，但进程级执行锁是权威防线。未声明策略的新内置工具
默认串行，不能因漏配变成并发。

本次不改变 FastAPI 数据路由的现有执行方式和合同。

## 10. Thread、状态与历史

### 10.1 Thread 分类

| `channel` | 用途 | 查看入口 |
|---|---|---|
| `workspace` | Agent 工作台普通对话 | 工作台会话列表 |
| `embedded` | 页面内问答 | 对应页面 AI 弹层 |
| `workflow` | 辩论、反思、复盘、资讯提炼 | 对应业务页面历史区 |

新 thread 必须写明 `channel`。工作台 thread adapter 只展示 `workspace`，同时把升级前没有
`channel` 的既有 Agent thread 视为 `workspace`，确保原工作台历史不消失。

旧页面内问答和 `vr-llm` 的 `localStorage` 数据不迁移、不读取、不主动删除。迁移后页面内问答
从新的 `embedded` thread 开始。

### 10.2 Workflow metadata

```json
{
  "channel": "workflow",
  "workflow_type": "debate",
  "title": "多空辩论 · 600519",
  "subject": "600519",
  "config_version": 1
}
```

metadata 只存索引和展示字段，不存模型密钥、完整输入、底稿或结果。完整内容保存在 Graph state
和 checkpoint。

历史列表通过 LangGraph thread search 的 `extract` 从 state 读取 `workflow_status` 和结果摘要，
不把动态运行状态复制到 metadata，也不为列表另建结果数据库。

### 10.3 类型化状态

通用工作流状态至少包含：

```text
input
config_version
workflow_status
started_at / completed_at
stages[]
result
errors[]
```

辩论额外包含 `dossier.sections` 和 `dossier.missing`。每个阶段包含稳定 ID、状态、内容和脱敏错误。
页面不通过解析文本判断运行状态。

### 10.4 历史查看

- 辩论历史只在辩论页查看。
- 反思历史只在研究记录页查看，并可按来源记录过滤。
- 每日复盘和资讯提炼历史在各自页面查看。
- 不增加统一“工作流历史”页面。
- 点击历史项通过 `threads.getState()` 恢复完整页面。
- “重新运行”创建新 thread，不覆盖旧结果。
- 删除操作只删除用户明确选择的 workflow thread。
- “研究记录”继续代表用户主动保存的精选沉淀，不自动复制所有工作流结果。

## 11. 前端接入

### 11.1 客户端边界

- Agent 工作台继续使用 assistant-ui `useStreamRuntime`。
- 页面内“问 AI”保留现有弹层组件，底层改用 LangGraph SDK 和 `embedded_agent` Graph。
- 专用页面使用共享 workflow client，负责创建 thread、启动 run、消费事件、重连、读取状态和
  查询 metadata 历史。
- 不强行用 assistant-ui 渲染辩论、反思或复盘页面。

### 11.2 固定事件

```text
workflow.status
dossier.progress
dossier.ready
stage.started
stage.delta
stage.completed
stage.failed
workflow.completed
workflow.failed
```

事件 schema 由 TypeScript/Python 合同固定。YAML 只能提供阶段 ID、标签和用户可见文案。
模型 token delta 与 stage ID 明确绑定，不能依赖前端“最后一个未完成阶段”猜测归属。

### 11.3 设置页

现有“接入 AI”页改为 Agent 只读状态页：

- 检查 `/agent-api/ok`。
- 显示脱敏模型名称、Base URL 主机、内置 Skill 数量和已配置 MCP server 数量。
- 显示 Agent 配置文件路径及修改后重启提示。
- 不提供 API Key 回读、浏览器保存或 CLI 选择。

FastAPI 新增只读 `/api/agent/status`，读取静态设置并返回脱敏摘要。该端点受现有
`VR_API_KEY` 规则保护，不调用模型，也不改变现有数据 API。LangGraph 实际 readiness 仍以
`/agent-api/ok` 为准。

## 12. 模型、安全与隐私

- 所有 Graph 使用 `settings.json` 中同一个 OpenAI-compatible API 模型。
- 配置仍只在 Agent Server 启动时读取一次，密钥使用 `SecretStr`，不进入前端请求。
- API Key、MCP secret 不进入 thread metadata、Graph state、checkpoint、trace 或错误信息。
- 页面上下文、对话、工具输入和结果会按本地 Agent 语义进入 checkpoint/trace；文档必须如实
  说明这些内容保存在用户本机。
- 内置中立 system policy 固定在代码中，并由所有 Graph 共享。
- 专用工作流只能引用只读内置 Skill，用户 Skill 不能改变其角色、工具或中立边界。
- MCP 只供通用 Agent 使用并保持逐次 HITL；专用工作流 YAML 只能引用内置 `tools.py` 工具。
- LangGraph Server 继续只监听 loopback，不增加公网或局域网部署能力。

## 13. 错误、取消与恢复

| 场景 | 行为 |
|---|---|
| YAML、内置 Skill 或工具引用无效 | Agent Server 启动失败，报告文件与字段 |
| 用户 MCP 配置或发现失败 | 保留现有 fail-fast 语义，Agent Server 不进入 ready 状态 |
| 单项底稿工具失败 | 记录数据缺口并继续其他项 |
| 所有客观数据失败 | 辩论在首次角色模型调用前终止 |
| 阶段模型调用失败 | 按 `on_error` 继续或终止，并写入阶段终态 |
| 模型连接失败 | 当前阶段标记失败，可从该阶段重试 |
| 前端断线或刷新 | Graph 继续运行，前端用 `joinStream()` 或 `getState()` 恢复 |
| 用户主动停止 | 取消当前 run，保留已有 checkpoint 和“已取消”状态 |
| Agent Server 重启 | 使用原生 checkpoint 恢复兼容版本的任务 |
| 配置版本不兼容 | 只允许查看或重新发起，不用新配置继续旧任务 |

重试从最后一个未完成阶段开始。已完成底稿和阶段不得重复执行。所有阶段开始后都必须产生
`completed` 或 `failed` 终态，避免页面永久停在“生成中”。用户错误使用中文；上游错误只保留
脱敏摘要，不回显请求头、密钥或完整响应正文。

## 14. 旧链路移除

迁移完成后：

- 删除 FastAPI `/api/chat`、`/api/debate`、`/api/reflect` 路由和请求模型。
- 删除前端 `lib/llm.ts`、`lib/agents.ts`、NDJSON AI 消费代码及 `vr-llm` 读取逻辑。
- `mcp_server.py` 和其他消费者直接从 `tools.py` 导入，不再通过 `chat.TOOLS` 别名。
- 固定中立 prompt 从 `chat.py` 提取到共享 Agent policy 模块。
- 在确认无引用后删除 `chat.py`、`debate.py`、`reflection.py` 和 `cli_runtime.py`。
- 删除 CLI 检测、模型列表和设置页订阅接入 UI。
- 保留浏览器中的旧 key/value，不执行主动删除；新代码不再读取。

删除必须发生在所有新 Graph、页面接入和回归测试通过之后，不能先删旧链路再补行为。

## 15. 测试设计

### 15.1 配置与 Skill

- 合法 YAML 装配成功。
- 未知字段、非法 kind、重复 stage、前向引用、未知工具、未知 Skill、越界 instruction、非法输入
  引用和超硬上限均启动失败。
- 内置 Skill 元数据和 reference 可读，路径逃逸失败。
- 用户 Skill 不能覆盖内置 Skill，也不能影响专用 Graph。
- 固定中立 policy 始终先于 Skill 注入。

### 15.2 Graph 单元测试

使用 scripted model，离线验证：

- 辩论两个 variant 的阶段顺序。
- 每个角色只看到配置允许的上下文。
- 底稿判空、合法无记录、缺口、固定顺序和裁剪。
- 阶段失败产生终态，失败内容不进入后续事实上下文。
- 无真实数据时不调用角色模型。
- 反思空输入、截断和不调用投研数据工具。
- 复盘和资讯提炼只消费页面输入。
- `page_context` 按 scope 隔离且不能覆盖 system policy。

### 15.3 工具执行测试

- 所有内置工具有显式策略或走默认串行。
- 不同 Graph/run 的 Eastmoney 工具共享进程级锁，实际请求间隔满足约束。
- `parallel_safe` 工具可以有限并发。
- 工具异常继续返回结构化错误，结果仍按上限裁剪。

### 15.4 LangGraph Server 集成测试

- 六个 graph id 均可发现并运行。
- 固定 custom events 合同完整。
- thread metadata、checkpoint、取消、重连和恢复行为正确。
- Agent Server 重启后已完成历史和兼容的未完成任务可恢复。
- 配置版本不兼容时拒绝恢复。
- 密钥不进入 thread、state、checkpoint、trace 或错误响应。

### 15.5 前端与 E2E

- `workspace`、`embedded`、`workflow` 三类 thread 正确过滤。
- 升级前无 `channel` 的 Agent thread 仍出现在工作台。
- 不同 route/股票的嵌入式问答不串历史。
- 嵌入式问答不暴露用户 MCP、用户 Skill 或 HITL。
- 各业务页面只查询和展示自己的 workflow 历史。
- 页面刷新、断线、失败、取消和重试后 UI 与 Graph state 一致。
- 失败阶段不会永久显示“生成中”。
- 设置页不读取、显示或发送 API Key。
- E2E 使用隔离 FastAPI、测试 LangGraph Server、scripted model、fake tools 和 Vite，不读取真实
  用户设置、会话、持仓或 API Key。

## 16. 验收标准

1. 原有页面 AI 功能保留入口、流式反馈、停止和保存研究记录能力。
2. 前端不再调用 `/api/chat`、`/api/debate`、`/api/reflect`。
3. FastAPI 不再解析模型 SSE、运行 function-calling 循环或持有请求级模型配置。
4. 模型密钥只存在 Agent `settings.json`，不进入前端和持久化运行数据。
5. 多空辩论仍使用同一份确定性客观底稿，终点仍是分歧点和验证清单。
6. 反思仍只审计现有推理，不产生新的投资观点或操作判断。
7. Eastmoney 调用在所有 Agent Graph/run 之间保持进程级串行。
8. 工作台、嵌入式问答和专用工作流历史互不串流，且能从 checkpoint 恢复。
9. FastAPI 现有行情、市场、持仓、研报和文件 API 合同不变。
10. 后端离线测试、前端单测、LangGraph 集成测试和浏览器 E2E 全部通过。

## 17. 主要取舍

- 多 Graph 比单 Router Graph 多几个注册入口，但换来独立状态、事件和测试边界。
- YAML 只描述受限业务流程，牺牲任意 DAG 灵活性，换取可验证性和安全性。
- 专用工作流直接加载内置 Skill，牺牲用户覆盖能力，换取产品中立边界和结果可重复性。
- 统一为 API 模型会移除 CLI 订阅便利，但消除无工具调用能力的第二类运行语义。
- 工作流 checkpoint 会占用本地存储，但支持历史、刷新和服务重启恢复；用户可以在对应页面明确
  删除单条历史。
