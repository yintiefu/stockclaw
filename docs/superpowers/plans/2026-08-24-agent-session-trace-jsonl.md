# Agent Session Trace (JSONL) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 Agent 工作台提供**调用链路级**可观测：每个 run 的执行流程（模型调用、工具调用、耗时、token 用量、审批中断）实时追加写入按线程组织的 JSONL 追踪文件，并配套终端查看命令，用于分析执行流程。全部本地，零云端依赖。

**Background:** LangGraph runtime 的会话存储是 pickle 检查点（`~/.vibe-research/agent/server/.langgraph_api/`），不透明、无法直接阅读，也无法回答「这一轮是怎么执行的：模型调了几次、每次调了什么工具、各花多久、token 用了多少」。本计划通过 LangChain middleware 在写入侧生成人类可读、可 `tail -f` / `jq` 的 JSONL 调用链路。

**Tech Stack:** Python 3.11+，LangChain 1.3.15 `AgentMiddleware`（已对 venv 源码核实的钩子签名），Pydantic 2，pytest，Playwright（既有三服务 E2E）。

---

## Scope And Invariants

- 只做写入侧追踪（middleware）+ 终端查看命令。**不做**：LangSmith/OTel 等云端追踪（违反本机数据边界）、历史会话补录（trace 只记录启用后的新 run）、前端 UI 展示（另立任务）。
- **追踪中间件的任何失败都不得影响 agent 运行**。已核实：`before_*`/`after_*` 钩子作为图节点执行、无内建异常捕获，异常会直接中断整个 run（`langchain/agents/middleware/factory.py:1545-1641`）；`wrap_tool_call` 中非 `ToolInvocationError` 的异常也会 re-raise（`langchain/prebuilt/tool_node.py:1054-1067`）。因此所有文件写入必须 try/except 包住，失败仅打 stderr 警告。**且失败只告警一次**：写入器首错后自禁用（熔断），只打一行 stderr，防止不可写目录导致的事件级刷屏；首次写入时 `mkdir(parents=True, exist_ok=True)` 自建目录。
- **中间件实例是进程级单例**（graph.py:76 一次性装配），任何可变状态（seq 计数、熔断标志、run 级去重集合）必须按 `(thread_id, run_id)` 键控隔离，`after_agent` 清理；被中断的 run 永不触达 `after_agent`，键控表需有界（FIFO 淘汰，上限 1024）防泄漏。
- **thread_id 文件名加固**：白名单 `^[a-zA-Z0-9_-]+$`，否则 sha1 摘要替代；写入前断言 `resolved_path.parent == traces_dir.resolve()`（服务端已实证 422 拒绝非 UUID，此为脱离该服务运行时的纵深防御）。
- 追踪文件含对话与工具入参/结果的**明文**，落用户目录，与 settings.json 同一隐私边界（本机、绝不提交仓库）；不得写入仓库任何位置。
- 测试不得产生真实文件 IO：`backend/conftest.py` 的测试 settings 显式关闭 trace；E2E harness 指向临时目录。
- 既有不变量继续成立：`tools.py` 是内置工具唯一定义源；内置工具经进程级 `BUILTIN_SERIAL_LOCK` 串行；密钥绝不进入线程元数据/图状态/检查点/日志/前端请求/错误响应——**追踪文件属于「日志」**，同样不得出现密钥（消息内容天然不含密钥，但注意不要把 settings 对象整个 dump 进事件）。
- 中间件置于 middleware 列表**第一位**（wrap 链第一个为最外层，计时含其余中间件开销，最接近真实耗时）。

## 已核实的钩子事实（执行者不必重新调研）

来源：`backend/.venv/lib/python3.13/site-packages/`（langchain 1.3.15）。以下含对抗性评审后的修正。

- 基类 `AgentMiddleware`（`langchain/agents/middleware/types.py:385`）。钩子均同步+`a`前缀异步成对，本计划用到：
  - `before_agent(state, runtime) -> dict | None`（types.py:431）——整个 run 开始触发一次；
  - `after_agent(state, runtime) -> dict | None`（types.py:650）——run 正常结束触发一次；**interrupt 暂停时不会触发**（图在 HITL 的 after_model 节点暂停），如实接受此边界：被中断的 run 没有末尾 `run_end`；恢复（resume）产生新 run_id；
  - `after_model(state, runtime) -> dict | None`（types.py:479）——每轮模型输出后触发；**after_* 钩子按 middleware 列表逆序执行**（factory.py:1789-1799），故 trace 中间件（列表第一位）的 after_model 在 HITL 的 after_model **之后**运行，能看到 HITL 改写后的消息；
  - `wrap_model_call(request: ModelRequest, handler) -> ModelResponse | AIMessage | ExtendedModelResponse`（types.py:503/598）——计时包住 `handler(request)`；`ModelResponse.result: list[BaseMessage]`（types.py:272-287）；**ChatOpenAI 开了 streaming=True，返回可能是 `ExtendedModelResponse`（分块）**，usage/model_name 提取必须对两种形状防御式处理（`usage_metadata`、`response_metadata.model_name`、`AIMessage.tool_calls`）；
  - `wrap_tool_call(request: ToolCallRequest, handler) -> ToolMessage | Command`（types.py:674/756）——`request.tool_call["name"/"args"/"id"]`（`langchain/prebuilt/tool_node.py:132-149`）；handler 结果**原样透传**；handler 抛错时记 `status=error` 后 re-raise（外层 ToolNode 会转成错误 ToolMessage，维持既有行为）。
- **HITL 拒绝不经过 ToolNode（评审 Critical 1，已核实）**：`HumanInTheLoopMiddleware.after_model` 在 resume 后调 `_process_decision`——拒绝的 tool_call 从 `last_ai_msg.tool_calls` 中**剔除**（`human_in_the_loop.py:483` `last_ai_msg.tool_calls = revised_tool_calls`），同时返回人造 `ToolMessage(status="error")`；ToolNode 只执行幸存（已批准）的调用。因此：
  - `wrap_tool_call` **只记录被 ToolNode 真正分发的执行**（含 HITL 批准后的执行与被拒后重新发起的调用）；
  - 被拒事件用 **orphan ToolMessage 特征**在 trace 的 `after_model` 里识别：某条 `ToolMessage` 的 `tool_call_id` 不在紧邻其前的 AIMessage 剩余 `tool_calls` 里 ⇒ HITL 人造拒绝消息（批准的正常路径中 tool_call_id 必有对应），据此发 `hitl_reject` 事件（字段：name、tool_call_id、status="error"、content=拒绝文案；**args 不可得**——已被 HITL 从状态剥离，如实记录）。
- **流式 usage 默认缺失（评审 Important 2，已核实）**：`langchain_openai/chat_models/base.py:1231-1250` 仅在使用默认 OpenAI baseURL 时自动开 `stream_usage`；本产品配置自定义 baseURL（智谱等），流式响应常无 usage chunk ⇒ `usage_metadata` 为 None。**契约：`input_tokens/output_tokens` 记 `null`，不报错、不硬填 0**。不在 `_build_model` 默认开 `stream_options`（第三方 OpenAI 兼容服务对该字段支持不一，有断流风险）；如某上游确认支持，可在 settings 层另议。
- **thread_id 服务端已限 UUID（评审 Critical 3，已实证）**：对运行中服务 `POST /threads {"thread_id":"../../etc/pwn"}` → 422 `Invalid thread ID: must be a UUID`，HTTP 面穿越不可达。但中间件可能脱离该服务运行（单测伪造 runtime、未来嵌入场景），写入器仍做**纵深防御**：白名单 `^[a-zA-Z0-9_-]+$`，不匹配则用 `hashlib.sha1` 摘要做文件名；写入前断言 `resolved_path.parent == traces_dir.resolve()`。
- thread_id / run_id：`runtime.execution_info.thread_id / run_id`（`langgraph/runtime.py:26-57`，由 `langgraph/pregel/_algo.py:694-700` 填充）；备用路径 `langgraph.config.get_config()["configurable"]["thread_id"]`。
- 自定义 middleware 标准写法：继承 `AgentMiddleware`，实例加入 `create_agent(middleware=[...])` 即完成注册（无需装饰器/register，factory 按方法覆盖检测建节点，`factory.py:1595-1597`）；同步+异步双实现，异步委托同步（参照 `langchain/agents/middleware/human_in_the_loop.py:488-500`）。
- 并发：langgraph dev（in-mem）单进程、worker max=1，单写者；每事件 open-append-close 即可，无需锁。但**中间件实例是进程级单例**（`graph.py:76` 模块加载时 `asyncio.run(build_graph())` 一次装配，实例存活整个进程生命周期，跨线程跨 run 共享）——任何实例级可变状态（如 seq 计数）必须按 `(thread_id, run_id)` 隔离。

## 追踪文件格式

位置：`~/.vibe-research/agent/traces/<thread_id>.jsonl`（每线程一文件，多 run 追加）。UTF-8，每行一个 JSON 事件：

```jsonl
{"ts":"2026-08-24T20:15:03.123+08:00","event":"run_start","thread_id":"01a0...","run_id":"01a0..."}
{"ts":"...","event":"model_call","run_id":"...","seq":1,"duration_ms":8213,"model":"glm-5.2","input_tokens":3210,"output_tokens":1105,"tool_calls":[{"name":"query_quote","args":{"query":"德明利"}}],"content_preview":"我来帮你..."}
{"ts":"...","event":"model_call","run_id":"...","seq":2,"duration_ms":4310,"model":"glm-5.2","input_tokens":null,"output_tokens":null,"tool_calls":[...],"content_preview":"..."}
{"ts":"...","event":"tool_call","run_id":"...","seq":3,"name":"query_quote","args":{...},"duration_ms":412,"status":"ok","result_preview":"...(截断至2000字符)","result_chars":2100}
{"ts":"...","event":"hitl_reject","run_id":"...","seq":4,"name":"fixture_echo","tool_call_id":"...","status":"error","content":"用户拒绝该工具调用。"}
{"ts":"...","event":"tool_call","run_id":"...","seq":5,"name":"fixture_echo","args":{...},"duration_ms":389,"status":"ok","result_preview":"approved fixture value"}
{"ts":"...","event":"run_end","run_id":"...","status":"success","total_ms":76018}
```

- `ts` 用钩子执行时的墙钟，**带本地时区、毫秒精度**：`datetime.now().astimezone().isoformat(timespec="milliseconds")`（毫秒是快速工具调用排序与耗时分析的最低可用精度，秒级会丢序）；
- **token 用量可空**：自定义 baseURL 的流式上游常无 usage chunk（见「已核实的钩子事实」），`input_tokens/output_tokens` 记 `null` 是正常形态，消费方不得假定非空；
- `tool_call` 只覆盖被 ToolNode 真正分发的执行；HITL 拒绝走独立 `hitl_reject` 事件（orphan ToolMessage 识别，args 不可得），**不会**出现 `status=error` 的 `tool_call` 事件混入拒绝语义；
- content/result 预览截断常量 `PREVIEW_LIMIT = 2000` 字符，同时记录 `result_chars` 原始尺寸；`args` 全量；
- `seq` 为 run 内单调递增序号，由 `(thread_id, run_id)` 键控的计数器维护（见 Task 2），跨 run 不串号、不跨线程污染；
- 事件行构造为**纯函数**（便于单测）；序列化失败（不可 JSON 化的 args）降级为 `str()` 并截断。

## File Map

### Backend Production

- `backend/agent/session_trace.py`（新）：`SessionTraceMiddleware` + 事件纯函数构造器 + 追加写入器（异常隔离 + 熔断 + thread_id 清洗 + parent 断言 + `(thread_id, run_id)` 键控 seq）+ CLI 查看器 `main()`（argparse：`list` / `show <thread_id>`，支持 `--traces-dir` 覆盖，无子命令默认 `list`）。
- `backend/agent/settings.py`：新增 `TraceSettings`（`enabled: bool = True`、`dir: Path` 默认 `~/.vibe-research/agent/traces`），挂到 `AgentSettings.trace`（`default_factory`，字段缺省=默认开启，现有 settings.json 无需改动，重启即生效）；加载时 `expanduser().resolve()`，目录不可创建时降级（stderr 警告 + 丢弃事件），不阻断启动。
- `backend/agent/graph.py`：middleware 列表第一位插入 `SessionTraceMiddleware(settings.trace)`；`enabled=False` 时不加。
- `scripts/dev`：新增 `trace` 子命令（`cd backend && .venv/bin/python -m agent.session_trace <args>` 透传；usage 补充）。

### Backend Tests

- `backend/conftest.py`：`_TEST_SETTINGS` 增加 `"trace": {"enabled": false}`。
- `backend/tests/agent/test_graph.py`：**局部工厂 `make_settings(tmp_path)` 同样补 `"trace": {"enabled": false}`**（评审 Important 1：该工厂独立于 conftest 全局设置，缺省会以 enabled=True 指向真实 `~/.vibe-research/agent/traces/`，违反测试零真实 IO）。
- `backend/tests/agent/test_session_trace.py`（新）：事件行格式（含 `input_tokens: null` 契约）、usage/工具调用提取（含 `ExtendedModelResponse` 分块形状）、**异常隔离**（monkeypatch 写入抛 `OSError` → 钩子返回 None / 工具结果原样透传、不冒泡）、**熔断只告警一次**、preview 截断、**`hitl_reject` orphan 识别**（拒绝消息的 tool_call_id 无对应 tool_call → 事件；批准的正常 ToolMessage 不误报）、**seq 按 run 隔离**（两个伪造 run 各自从 1 起）、**thread_id 清洗**（`../`、斜杠、空格 → 摘要文件名 + parent 断言）、settings 新字段校验。
- `backend/tests/agent/test_graph.py`：中间件组合断言更新（默认含 trace、settings 关闭时不含）。
- `backend/tests/agent_e2e/`：server harness 生成的 settings 打开 trace、`dir` 指向临时 root；`frontend/e2e/agent-workspace.spec.ts` 主流程加断言：run 结束后 trace 文件存在、首行 `event=="run_start"`、且含 `tool_call` 事件。

### Docs

- `README.md`：Agent 工作台「要点」新增调用链路追踪条目（命令、文件位置、事件字段、明文隐私边界、interrupt 无 `run_end`、resume 为新 run_id 的说明）；settings JSON 示例补 `trace` 字段。
- `CHANGELOG.md`：未发布条目追加一行。

## Commit Gates

每个任务完成后：

```bash
cd backend && .venv/bin/pytest -m "not live"        # 离线全量
bash -n ../scripts/dev                               # 动到 scripts/dev 时
```

涉及 E2E 的任务另跑 `cd frontend && npm run test:e2e`。前端源码零改动（`npm run build` 不受影响，不需重跑）。

## Tasks

### Task 1: settings 与测试隔离

- [ ] `settings.py`：`TraceSettings` + `AgentSettings.trace`（`extra="forbid"` 下新增可选字段，注意默认值不破坏既有 settings 文件）
- [ ] `conftest.py`：测试 settings 显式关闭 trace
- [ ] `test_graph.py::make_settings`：局部工厂同样关闭 trace（或指向 `tmp_path/"traces"`）——堵住评审 Important 1 的真实目录写入
- [ ] `test_settings.py`：新增字段缺省=开启、显式关闭、非法字段报错（字段位置、不回显值）三类用例
- [ ] 跑 Task 范围 pytest 通过后提交

### Task 2: 追踪中间件核心

- [ ] `session_trace.py`：事件构造纯函数（`run_start/model_call/tool_call/hitl_reject/run_end`）+ 追加写入器（open-append-close、`PREVIEW_LIMIT`、序列化降级、全量 try/except 仅 stderr、**首错熔断只告警一次**、**首写 `mkdir(parents=True)`**、**thread_id 白名单清洗 + sha1 摘要回退 + `resolved_path.parent == traces_dir.resolve()` 断言**）+ `SessionTraceMiddleware`（五钩子同步+异步双实现；`wrap_model_call` 计时并防御式提取 `ModelResponse`/`ExtendedModelResponse` 两种形状的 usage/model_name/tool_calls/文本预览，**usage 缺失记 null**；`wrap_tool_call` 计时、透传结果、except 记 error 后 re-raise；`after_model` 做 orphan ToolMessage 扫描发 `hitl_reject`——利用 after_* 逆序执行保证此时 HITL 已完成改写）
- [ ] **seq 键控状态**：`(thread_id, run_id) -> counter` 的 dict，`before_agent` 初始化、`after_agent` 清理、FIFO 上限 1024 防中断 run 泄漏；熔断标志为实例级（进程单例、无并发写者）
- [ ] `graph.py`：首位注入（`enabled=False` 不加）
- [ ] `test_session_trace.py`：按 File Map 全部用例落地；`test_graph.py` 组合断言更新
- [ ] 手动冒烟：临时 settings 指向 `/tmp` 目录，`build_graph()` + ScriptedChatModel 跑一轮（含一次 HITL 拒绝恢复），检查 JSONL 逐行可 `json.loads`、`hitl_reject` 事件出现
- [ ] 提交

### Task 3: CLI 查看器与 scripts/dev 接入

- [ ] `session_trace.py::main()`：argparse；无子命令默认 `list`（列 traces 目录：线程、行数、最后时间）；`show <thread_id>`（按 run 分组的时间线渲染：步骤、耗时、token（含 null 显示「—」）、工具状态、`hitl_reject` 标注、预览再截短；`--raw` 输出 `jq`-friendly 原文）；全局 `--traces-dir` 覆盖默认目录
- [ ] `scripts/dev`：`trace` 子命令透传 + usage（无参数时同 `list`）；`bash -n` 通过
- [ ] 提交

### Task 4: E2E 断言

- [ ] server harness settings 打开 trace（dir 在临时 root 内）
- [ ] `agent-workspace.spec.ts` 主流程断言：trace 文件存在、`run_start`/`tool_call` 事件齐全；**既有「拒绝」流程断言 `hitl_reject` 事件存在**
- [ ] `npm run test:e2e` 全绿；提交

### Task 5: 文档与终验

- [ ] README 要点 + settings 示例；CHANGELOG 追加
- [ ] 全量验收（见下）
- [ ] 提交

## Final Acceptance Checklist

- [ ] `.venv/bin/pytest -m "not live"` 全绿（含新增 `test_session_trace.py`；跑前跑后 `ls ~/.vibe-research/agent/traces/` 无测试新增文件——测试零真实 IO 的直接验证）
- [ ] `npm run test:e2e` 全绿（含 trace 与 `hitl_reject` 断言）
- [ ] 手动链路验证：`scripts/dev restart agent` → 真实问题（如德明利追问）→ `scripts/dev trace` 看到含耗时/token 的完整链路；另开终端 `tail -f ~/.vibe-research/agent/traces/<thread_id>.jsonl` 可实时跟看
- [ ] 拒绝路径验证：配置一个 MCP 工具并拒绝一次 → `hitl_reject` 事件出现且**没有**对应 `tool_call` 事件；批准一次 → 正常 `tool_call`
- [ ] 破坏性验证：把 traces 目录改成不可写路径 → agent 仍正常运行，stderr **仅一行**告警（熔断生效，无刷屏）
- [ ] `git status` 无越界改动；追踪文件不出现在仓库任何位置
