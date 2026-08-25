# AI 调用统一到 LangGraph 与配置化工作流设计

**日期：** 2026-08-25

**状态：** 第二轮架构评审修订，待复审

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
- 底稿工具清单、标题、参数和空结果语义。
- 输入长度、单节内容、阶段输出和阶段上下文等软限制。
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
      empty_policy: gap_if_empty
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
    context: [dossier.summary, dossier.missing, stages]
    on_error: fail

limits:
  section_chars: 1800
  dossier_summary_chars: 6000
  stage_output_chars: 1200
  stage_context_chars: 24000
```

`${input.code}` 不是通用模板语言。loader 只允许 `${input.<已声明字段>}` 这种完整值引用，
不支持表达式、函数、条件、循环、属性遍历或字符串内代码执行。

完整 `debate.yaml` 在实施时迁移现有 13 项底稿清单及其参数和空结果策略，不能以示例中的两项
代替生产清单。`gap_if_empty` 表示空结果计入数据缺口但不熔断工作流；`allow_no_record` 表示
“无记录”本身是合法业务结果。任何单项失败都不因这两个枚举而直接中止。

`dossier.summary` 由代码对已经裁剪的结构化底稿做确定性压缩，只提取各节关键字段、来源、时间
和缺口，不增加一次 LLM 调用，也不生成新判断。`referee` 不接收完整底稿。运行时按字符计数
执行 `stage_output_chars`：达到上限即结束上游模型流，结果写明 `truncated=true`；前端不得先展示
超限文本、checkpoint 又保存裁剪文本。配置软限制还必须低于代码硬上限。

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
```

所有 `single_pass` 工作流固定把模型输出写入 `WorkflowState.result`，YAML 不声明动态 state 字段。
页面根据 workflow ID 使用固定业务标题；不增加 `audit`、`digest` 等同义状态字段。

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
- `empty_policy`（仅 `gap_if_empty` / `allow_no_record`）、`on_error` 等字段使用受限枚举。
- 配置软限制不超过代码硬上限。
- 输入引用只指向 schema 声明字段。
- Skill instruction 路径不能逃出对应内置 Skill 目录。

任一配置无效时 Agent Server 不进入 ready 状态，错误只报告文件、字段和原因，不包含密钥。

thread 记录 `config_version`。升级后历史结果仍可查看；未完成任务的版本与当前配置不兼容时，
禁止使用新配置静默恢复旧 checkpoint，只允许查看已有状态或重新发起。

### 6.5 状态模型、错误哨兵与变体编译

`workflow_state.py` 使用 `TypedDict` 定义 Graph state，使用 Pydantic 定义写入 state 的边界对象。
首期公共字段固定为：

```python
class WorkflowState(TypedDict):
    workflow_id: str
    variant: str | None
    input: dict[str, object]
    config_version: int
    workflow_status: Literal[
        "pending", "running", "completed", "partial", "failed", "cancelled",
        "interrupted"
    ]
    current_stage: str | None
    started_at: str | None
    completed_at: str | None
    dossier: DossierResult | None
    stages: Annotated[dict[str, StageResult], merge_stage_results]
    result: str | None
    result_summary: str | None
    errors: Annotated[list[WorkflowError], append_workflow_errors]
```

`StageResult` 固定包含 `id`、`status`（`pending | running | completed | failed | skipped |
cancelled | interrupted`）、`content`、`truncated`、`context_truncated`、`started_at`、`completed_at` 和
`error`。错误哨兵
`WorkflowError` 只包含稳定 `code`、脱敏中文 `message`、`retryable` 和可选 `stage_id`，不保存
异常对象、请求正文或上游响应。失败阶段必须写 `content=null`；后续上下文序列化器遇到失败或
跳过的引用时，只插入 `【阶段 <id> 未产出】`，不得把错误文本作为研究事实，也不得抛 `KeyError`。
`merge_stage_results` 只按稳定 stage ID 合并本节点更新，同一 ID 的新终态覆盖旧 `running`，不得
覆盖其他 stage；`append_workflow_errors` 只追加本节点的新错误。节点不能原地修改上一 checkpoint
的容器。

`DossierResult` 固定包含按配置顺序排列的 `sections`、确定性 `summary`、`missing` 和
`has_substantive_data`。每个 section 明确区分 `completed`、`no_record`、`gap` 和 `failed`。
`result_summary` 最长 80 字，由 `finalize` 根据状态、阶段数、缺口数等字段确定性生成；辩论摘要
只能描述“已完成/部分完成、分歧与待核验项数量”，不能生成胜负、倾向或投资结论。

每个 workflow 在 Server 启动时从 YAML **编译一次**为静态 `StateGraph`。`debate` 图为所有
stage 分别生成 `start_<id>` 和 `run_<id>` 两个节点；`validate_input` 校验 `variant`，
`collect_dossier` 后进入该 variant 的首个 `start`。`start_<id>` 只写 `current_stage` 和
`StageResult(status="running")` 并立即返回，使长模型调用前先形成 checkpoint；`run_<id>` 才执行
模型、写完成或错误终态。条件边根据已校验的 `variants[variant]` 查找下一个 `start`，末节点进入
`finalize`。`on_error: continue` 写失败哨兵后沿同一 variant 继续；`on_error: fail` 直接进入失败
终态。loader 在编译前校验所有可能的边，运行期不解释表达式、不新增节点，也不为每次请求重新
编译 Graph。

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

同一 `(route, scope_key)` 只维护一个当前 thread：弹层打开时通过 metadata search 找到最新
thread 并复用，切换页面或标的只切换归属，不创建空 thread。用户点击“清空本页对话”时才删除
该 thread，下一次提问再创建。不同 scope 的历史是用户真实产生的对话，不设置自动 TTL，也不
后台删除；这样既避免同一 scope 重复堆积，又不违背隔离持久化的既有决定。embedded metadata
固定只存 `channel="embedded"`、`route`、`scope_key` 和展示标题，不存页面快照或消息内容。

页面每轮调用把最新页面数据写入独立 `page_context` 状态字段。快照固定包含 Server 记录的
`captured_at`、前端数据源可选 `source_as_of`、单调 `version`、`route`、`scope_key` 和受长度硬
上限约束的 `content`。动态 system middleware 以 `【当前页面快照 v<version> · <time>】` 边界
注入，固定中立规则仍在外层；页面内容不能生成新的 system message 或修改工具权限。

`EmbeddedAgentState` 将该字段声明为
`Annotated[PageContextSnapshot | None, keep_latest_nonempty_context]`。首轮必须提供合法非空快照；
后续输入省略字段、传 `null` 或传空 `content` 时保留已有快照，不增加版本。只有显式提供且通过
route、scope 和长度校验的新非空快照才由 Server 更新时间、递增版本并整体替换。清空上下文不
通过 reducer 表达，而是使用已有“清空本页对话”删除整个 embedded thread。

每个完成的 assistant turn 在 state 中记录所用 `page_context` 的版本和时间。准备下一轮模型输入
时，middleware 为历史 turn 插入紧凑的版本标记，只注入当前快照全文，不重复注入旧快照全文，
并明确“历史回答可能基于旧快照，不得当作当前数据”。这样更新当前快照不会抹掉历史回答的时间
归属，也不会让多轮上下文按快照大小线性膨胀。

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

`bull` / `bear` 等立论阶段可以读取按节裁剪后的完整底稿；交叉反驳只读取 YAML 明示的前序
阶段；`referee` 只读取 `dossier.summary`、`dossier.missing` 和各阶段受限输出。runtime 在每次
模型调用前计算最终序列化上下文长度。超过 `stage_context_chars` 时，固定 policy、当前 Skill 和
当前用户输入不可裁剪；其余预算按“前序阶段受限输出 → `dossier.summary` / missing → 完整底稿
按配置顺序”的优先级装入，放不下的部分替换为带 ID 的省略标记，并记录
`context_truncated=true`。该过程只做确定性截断，不调用模型生成摘要，不得静默突破 TPM 或
上下文限制。

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

锁和并发容量都不得依赖某个 asyncio event loop。实现使用模块级 `threading.Lock` 保护串行组，
使用模块级 `threading.BoundedSemaphore(4)` 限制 `parallel_safe` 组的**全进程**在途调用；两者
都在 `asyncio.to_thread()` 提交的同步 dispatch 函数内部获取和释放，异步任务不阻塞事件循环，
也不创建模块级 `asyncio.Lock` / `asyncio.Semaphore`。因此不同 Graph、run 或 event loop 合计最多
4 个 `parallel_safe` handler 同时进入真实工具调用，等待 semaphore 的 worker 不访问上游；所有
串行工具仍只允许 1 个。该数值是代码安全硬上限，YAML
不能放大。

取消等待中的 async task 不等于终止已经进入 worker 的同步调用；worker 必须在真实调用返回后
释放 lock/semaphore，迟到结果丢弃但容量不能提前归还。

首期 13 项辩论底稿分类按现有 `debate.py` 固化如下；这是代码白名单，不从 YAML 推断：

| 策略 | 工具 |
|---|---|
| `parallel_safe` | `query_quote`、`query_valuation_percentile`、`query_financials`、`query_kline`、`query_announcements`、`query_reports`、`query_news` |
| `eastmoney_serial` | `query_valuation`、`query_fund_flow`、`query_margin`、`query_holders`、`query_lockup`、`query_concepts` |

loader 必须逐项验证 `debate.yaml` 的 13 个工具都存在于代码策略表；工具新增或底层数据源改变时，
必须先更新代码分类和静态测试。未知工具仍默认串行，不能因不在本表而拒绝通用 Agent 启动。

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

历史列表用**一次** LangGraph `threads.search()` 请求：metadata 负责
`channel + workflow_type` 过滤，`extract` 同时投影 `values.workflow_status` 和
`values.result_summary`，Server 顶层 thread `status` 提供 `busy / idle / interrupted / error`。
本项目当前依赖闭包中的 Python SDK `0.4.2` 和锁定的 JS SDK `1.9.31` 均原生支持 `extract`，
一次请求最多 10 个路径，本设计只使用 2 个。列表不得逐条调用 `getState()`。

不在 Graph 终态回写 thread metadata：Graph 内为此调用自己的 Server HTTP API 会形成双写、
鉴权和失败一致性问题，而且不是消除 N+1 的必要条件。若以后升级 LangGraph 版本，升级测试必须
先验证 `search + extract` 合同；不支持该能力的版本不得静默退化成 N+1。

列表和详情不得单独信任 checkpoint 中的 `workflow_status`，必须与 Server 顶层 `thread.status`
计算 `effective_workflow_status`：

| 条件 | 派生状态 |
|---|---|
| `thread.status == "busy"` | `running` |
| state 已是 `completed / partial / failed / cancelled / interrupted` | 保留 state 终态 |
| state 为 `running` 且 thread 为 `error` | `failed` |
| state 为 `running` 且 thread 为 `idle / interrupted` | `interrupted` |

最后一行覆盖用户取消后浏览器未完成回写、Server 重启和进程异常退出等孤儿 checkpoint。由于
Server 状态不能证明用户意图，不能把它猜成 `cancelled`。列表直接显示派生状态，不对每条历史
执行修复写入；因此仍保持单次 `search`，不会产生新的 N+1。详情页也使用同一纯函数，避免两个
页面解释不一致；从列表进入时复用已取得的 Thread，直接 URL 打开详情时只为所选 thread 补一次
`threads.get()`，不批量查询其他历史。

### 10.3 类型化状态

通用工作流状态至少包含：

```text
input
config_version
workflow_status
effective_workflow_status（前端派生，不写入 checkpoint）
started_at / completed_at
stages{}
result
result_summary
errors[]
```

辩论额外包含 `dossier.sections` 和 `dossier.missing`。每个阶段包含稳定 ID、状态、内容和脱敏错误。
页面不通过解析文本判断运行状态。`interrupted` 表示运行已不活跃但节点没有提交正常终态；它与
用户意图明确且成功回写的 `cancelled` 分开。

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

Python 端用 Pydantic discriminated union 构造事件，前端用对应 TypeScript discriminated union
消费；业务代码不得直接拼裸 `dict`。所有事件共有 `type`、`workflow_id`、`run_id`、`seq`
和 `emitted_at`；emitter 为每个 run 从 `seq=1` 开始连续递增，事件专有字段如下：

| `type` | 专有字段 |
|---|---|
| `workflow.status` | `status`、`message` |
| `dossier.progress` | `section_id`、`section_status`、`completed`、`total` |
| `dossier.ready` | `completed`、`missing`、`has_substantive_data` |
| `stage.started` | `stage_id`、`label` |
| `stage.delta` | `stage_id`、`delta` |
| `stage.completed` | `stage_id`、`truncated` |
| `stage.failed` | `stage_id`、`error_code`、`message`、`retryable` |
| `workflow.completed` | `status`（`completed | partial`） |
| `workflow.failed` | `error_code`、`message`、`retryable` |

事件 `type` 和字段名固定在代码中，YAML 只能提供阶段 ID、标签和用户可见文案。模型 token
delta 与 stage ID 明确绑定，不能依赖前端“最后一个未完成阶段”猜测归属。Python/TypeScript
合同 fixture 必须逐事件双向校验，未知事件由前端记录并忽略，缺少必填字段则进入可恢复错误态。

### 11.3 流式重连与 checkpoint 对账

节点内 `stage.delta` 是易失 UI 数据，节点完成后的 checkpoint state 才是权威结果。workflow
run 固定使用 `streamResumable=true`、`onDisconnect=continue` 和节点级同步 durability；同一
Server 进程内，客户端可用 SSE `Last-Event-ID` 重放 resumable stream，但不得假设流缓冲能跨
Server 重启。

刷新或断线重连按以下顺序执行：

1. 先读取 thread state 和 run 状态，以 checkpoint 渲染所有已完成阶段。
2. run 仍在执行时，清空当前未提交阶段的本地 delta 缓冲并显示该阶段 Loading；随后用已保存的
   event ID 调用 `joinStream()`，没有游标时从 `-1` 请求当前进程可用的完整重放。
3. 收到 `stage.completed` 只表示节点准备返回，不把 delta 当成已提交结果；等待对应 state update，
   或轮询 `getState()` 直到该阶段为终态，再用 checkpoint 的完整 `content` 原子替换临时缓冲。
4. join 结束、超时或流缓冲不存在时统一 `getState()`；若节点仍未提交，继续显示 Loading，不拼接
   “重连后半段”的 delta。Server 重启后从最后 checkpoint 重试该阶段。

客户端按 `run_id` 独立跟踪 custom event `last_seq`。消费规则固定为：

- `seq == last_seq + 1`：正常消费。
- `seq <= last_seq`：视为重放或重复事件，幂等忽略。
- `seq > last_seq + 1`：当前 stage 的 delta 缓冲立即标记为 dirty，清空文本并切回 Loading；本轮
  不再拼接后续 `stage.delta`，等待 checkpoint 对账后整体替换。
- 重连时没有本地 `last_seq` 且首个重放事件不是 `seq=1`，同样按 gap 处理。

SSE 基于 TCP，本身保证同一连接内有序，不把普通网络丢包描述成单事件跳号；以上规则防御的是
重连游标错误、服务端重放窗口缺失和客户端处理异常。客户端保存 SSE event ID、`last_seq` 和
run ID 时必须经过 `lib/storage.ts` 包装器，不直接访问 `localStorage`。该协议允许实时流提升体验，
但页面完整性永远不依赖 token 事件是否全部到达。

### 11.4 设置页

现有“接入 AI”页改为 Agent 只读状态页：

- 检查 `/agent-api/ok`。
- 显示脱敏模型名称、Base URL 主机、内置 Skill 数量和已配置 MCP server 数量。
- 显示 Agent 配置文件路径及修改后重启提示。
- 不提供 API Key 回读、浏览器保存或 CLI 选择。
- 当配置文件缺失或模型未配置时，显示精确路径、最小启动步骤和“一键复制配置模板”；模板中的
  `api_key` 只能是显式占位符，状态 API 和页面都不得读取真实 key。

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

`VR_API_KEY` 是 FastAPI 的鉴权配置，不会自动保护独立的 LangGraph Server。受支持的启动方式要求
LangGraph `:2024` 和 Vite `:5899` 都绑定 loopback；`/agent-api` 只是本机开发代理，不能作为公网
鉴权层。`resolveAgentApiUrl()` 给请求附加 `authHeaders()` 也不能替代服务端校验，因此本设计不把
“发送 Bearer”写成安全措施。

当 FastAPI 以 `VR_API_KEY` 公网模式运行时，远端只支持已有 `/api/*`，不得把 `/agent-api` 反代
到未鉴权的 `:2024`。未来若要支持远端 Agent，必须另立设计：在 LangGraph custom auth 或受信
网关中校验凭据、隔离用户 thread，并覆盖 SSE/普通请求；该能力不属于本次迁移。文档、启动脚本
和 E2E 都必须验证 loopback bind，不提供 `0.0.0.0:2024` 示例。

## 13. 错误、取消与恢复

| 场景 | 行为 |
|---|---|
| YAML、内置 Skill 或工具引用无效 | Agent Server 启动失败，报告文件与字段 |
| 用户 MCP 配置或发现失败 | 保留现有 fail-fast 语义，Agent Server 不进入 ready 状态 |
| 单项底稿工具失败 | 记录数据缺口并继续其他项 |
| 所有客观数据失败 | 辩论在首次角色模型调用前终止 |
| 阶段模型调用失败 | 按 `on_error` 继续或终止，并写入阶段终态 |
| 模型连接失败 | 当前阶段标记失败，可从该阶段重试 |
| 前端断线或刷新 | Graph 继续运行，按 11.3 先对账 checkpoint，再把 stream 作为临时展示 |
| 用户主动停止 | 使用 `cancel(wait=true, action="interrupt")`；客户端存活时再写取消 checkpoint |
| Agent Server 重启 | 丢弃易失 stream，从最后 checkpoint 重试未完成阶段 |
| 配置版本不兼容 | 只允许查看或重新发起，不用新配置继续旧任务 |

重试从最后一个未完成阶段开始。已完成底稿和阶段不得重复执行。取消运行本身不能保证正在执行
节点返回并提交状态，因此 workflow client 在仍存活时等待 cancel 确认，再通过固定 payload 的
`updateState` 只把 `workflow_status` 和当前阶段改为 `cancelled`，保留原 checkpoint 的待执行节点
供用户以后显式重试；不得修改结果、底稿或已完成阶段。

如果客户端在确认或回写前消失，state 可以停留在 `running`，但 thread 已不再 `busy`；10.2 的
派生规则必须立即显示为 `interrupted`，不能永久显示“生成中”，也不能猜成“已取消”。用户发起
重试时，client 先把该孤儿阶段写为 `interrupted` 形成审计 checkpoint，再从原待执行节点恢复；
新 run 的 `busy` 状态在执行期间派生为 `running`，完成后由 `run_<id>` 覆盖当前阶段终态。

所有开始后的阶段最终必须表现为 `completed`、`failed`、`cancelled` 或 `interrupted`。用户错误
使用中文；上游错误只保留脱敏摘要，不回显请求头、密钥或完整响应正文。

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
- `single_pass` 出现 `result.field` 或其他动态 state 字段时校验失败，输出只写公共 `result`。

### 15.2 Graph 单元测试

使用 scripted model，离线验证：

- 辩论两个 variant 的阶段顺序。
- variant 条件边只走配置顺序，非法 variant 在任何底稿/模型调用前失败。
- `start_<id>` 在长模型调用前提交 `running` checkpoint，`run_<id>` 只合并自身 stage 终态。
- 每个角色只看到配置允许的上下文。
- 底稿判空、合法无记录、缺口、固定顺序和裁剪。
- `gap_if_empty` 只记录缺口、不熔断；`allow_no_record` 保留合法空记录。
- 阶段失败写 `WorkflowError` 哨兵；后续上下文只出现“未产出”标记，不出现错误正文或 `KeyError`。
- 阶段输出、底稿摘要和最终上下文执行配置软限制与代码硬上限，referee 不接收完整底稿。
- 无真实数据时不调用角色模型。
- 反思空输入、截断和不调用投研数据工具。
- 复盘和资讯提炼只消费页面输入。
- 所有 `single_pass` Graph 只写 `WorkflowState.result`，state 中不存在动态业务字段。
- `page_context` 按 scope 隔离且不能覆盖 system policy；多轮更新保留快照版本/时间归属。
- `page_context` 的非空覆盖 reducer：首轮空值失败，后续 omitted / null / empty 保留旧快照，合法新值才递增版本。

### 15.3 工具执行测试

- 所有内置工具有显式策略或走默认串行。
- 不同 Graph/run/event loop 的 Eastmoney 工具共享同一个 `threading.Lock`，实际请求间隔满足约束。
- async task 取消后，已进入 worker 的同步调用仍持锁到真实返回，不提前放行下一次调用。
- 13 项底稿工具与代码白名单逐项一致，7 项 `parallel_safe`、6 项 `eastmoney_serial`。
- 所有 Graph/run/event loop 合计最多 4 个 `parallel_safe` handler 进入真实调用；第 5 个等待容量。
- async task 取消后，已进入 worker 的同步调用仍占用 semaphore 到真实返回。
- 工具异常继续返回结构化错误，结果仍按上限裁剪。

### 15.4 LangGraph Server 集成测试

- 六个 graph id 均可发现并运行。
- 固定 custom events 合同完整。
- Python Pydantic 与 TypeScript discriminated union 的逐事件合同 fixture 一致。
- `threads.search(extract=...)` 用一次请求返回 workflow 状态和摘要，列表不触发 N+1 `getState()`。
- thread metadata、节点 checkpoint、取消 checkpoint、resumable stream 和 Server 重启恢复行为正确。
- state=`running` 与 thread=`idle / interrupted / error` 的派生状态矩阵正确；取消后关闭页面不会显示永久运行。
- 孤儿状态在显式重试前写 `interrupted` 审计 checkpoint，且不重复已完成阶段。
- 中途刷新不会把“仅重连后收到的半段 delta”当作完整阶段结果；checkpoint 最终覆盖临时流。
- Agent Server 重启后已完成历史和兼容的未完成任务可恢复。
- 配置版本不兼容时拒绝恢复。
- 密钥不进入 thread、state、checkpoint、trace 或错误响应。

### 15.5 前端与 E2E

- `workspace`、`embedded`、`workflow` 三类 thread 正确过滤。
- 升级前无 `channel` 的 Agent thread 仍出现在工作台。
- 不同 route/股票的嵌入式问答不串历史；同一 scope 复用一个 thread，清空操作才删除它。
- 嵌入式问答不暴露用户 MCP、用户 Skill 或 HITL。
- 各业务页面只查询和展示自己的 workflow 历史。
- 页面刷新、断线、失败、取消和重试后 UI 与 Graph state 一致。
- custom event 重复 seq 幂等忽略；seq gap 清空临时文本、停止拼接并等待 checkpoint。
- 失败阶段不会永久显示“生成中”。
- 设置页不读取、显示或发送 API Key。
- 缺少配置时可复制无密钥的最小模板，并显示实际配置路径和重启步骤。
- LangGraph、Vite 启动命令和测试 harness 只监听 loopback；公网模式不暴露 `/agent-api`。
- E2E 使用隔离 FastAPI、测试 LangGraph Server、scripted model、fake tools 和 Vite，不读取真实
  用户设置、会话、持仓或 API Key。

## 16. 验收标准

1. 原有页面 AI 功能保留入口、流式反馈、停止和保存研究记录能力。
2. 前端不再调用 `/api/chat`、`/api/debate`、`/api/reflect`。
3. FastAPI 不再解析模型 SSE、运行 function-calling 循环或持有请求级模型配置。
4. 模型密钥只存在 Agent `settings.json`，不进入前端和持久化运行数据。
5. 多空辩论仍使用同一份确定性客观底稿，终点仍是分歧点和验证清单。
6. 反思仍只审计现有推理，不产生新的投资观点或操作判断。
7. Eastmoney 调用在所有 Agent Graph/run 之间保持进程级串行，`parallel_safe` 全进程最多 4 个在途调用。
8. 工作台、嵌入式问答和专用工作流历史互不串流，能从 checkpoint 恢复，取消或崩溃后不出现永久“生成中”。
9. FastAPI 现有行情、市场、持仓、研报和文件 API 合同不变。
10. 后端离线测试、前端单测、LangGraph 集成测试和浏览器 E2E 全部通过。

## 17. 主要取舍

- 多 Graph 比单 Router Graph 多几个注册入口，但换来独立状态、事件和测试边界。
- YAML 只描述受限业务流程，牺牲任意 DAG 灵活性，换取可验证性和安全性。
- 专用工作流直接加载内置 Skill，牺牲用户覆盖能力，换取产品中立边界和结果可重复性。
- 统一为 API 模型会移除 CLI 订阅便利，但消除无工具调用能力的第二类运行语义。
- 工作流 checkpoint 会占用本地存储，但支持历史、刷新和服务重启恢复；用户可以在对应页面明确
  删除单条历史。

## 18. 第一轮对抗性评审处置

| # | 结论 | 设计处置 |
|---|---|---|
| 1 | 接受风险，修正成因 | Python 3.13 的 `asyncio.Lock()` 构造和无竞争获取不会绑定 loop，`asyncio.run(build_graph())` 也未调用工具；但跨 loop 的进程串行仍值得机制化保证，改为 worker 内 `threading.Lock`。 |
| 2 | 不接受建议 | 锁定 SDK 原生支持 `threads.search(extract)`，不存在必然 N+1；保留单次投影，禁止 Graph 内 metadata 双写。 |
| 3 | 接受 | 明确 delta 易失、checkpoint 权威、resumable stream 重放和重连后 `getState()` 原子对账。 |
| 4 | 接受 | 增加 `WorkflowState`、错误哨兵、静态编译和 variant 条件边规范。 |
| 5 | 接受 | 页面快照增加版本/时间，历史 turn 保留快照引用。 |
| 6 | 接受 | 增加阶段输出/上下文上限和确定性 dossier summary；referee 不读完整底稿。 |
| 7 | 接受 | `required` 改为 `gap_if_empty`，明确它不熔断。 |
| 8 | 接受风险，不接受原修复 | 仅附加 Bearer 不能提供服务端鉴权；维持 loopback-only，公开模式禁止反代 `/agent-api`。 |
| 9 | 部分接受 | 同一 scope 查找并复用一个 thread，用户清空时删除；不对真实历史设置自动 TTL。 |
| 10 | 接受 | Python/TypeScript 使用 discriminated union 和共享合同 fixture。 |
| 11 | 接受 | 缺配置时提供无密钥模板、实际路径和重启指引。 |

## 19. 第二轮对抗性评审处置

| # | 结论 | 设计处置 |
|---|---|---|
| 1 | 接受问题，调整判定 | 非 busy thread 与 state `running` 确会形成孤儿展示；原因无法证明为用户取消，统一派生为 `interrupted`（thread error 派生 `failed`），重试前再写审计 checkpoint。 |
| 2 | 接受 | 删除 `result.field: audit`，所有单步工作流固定写 `WorkflowState.result`。 |
| 3 | 接受风险，调整实现 | 全进程并发硬上限为 4；不用 loop-bound `asyncio.Semaphore`，改为 worker 内 `threading.BoundedSemaphore(4)`。 |
| 4 | 接受防御规则，修正成因 | TCP/SSE 同连接不会静默跳过单个事件；仍对重放窗口、游标或客户端异常造成的 seq gap 清空脏缓冲并等待 checkpoint。 |
| 5 | 接受 | `page_context` 仅用合法非空快照覆盖；后续空值保留旧快照。 |
| 6 | 接受 | 固化现有 7 项并行、6 项东财串行的 13 项代码白名单并逐项测试。 |
