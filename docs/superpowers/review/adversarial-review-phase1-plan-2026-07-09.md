# 对抗性评审：Phase 1 实施计划 vs 设计文档

**评审日期**：2026-07-09  
**评审员**：Antigravity (AI 架构评审组)  
**评审对象**：[Phase 1 实施计划](file:///vol2/1000/code/stockclaw/docs/superpowers/plans/2026-07-08-ai-native-agent-phase-1.md)（4660 行）  
**评审基准**：[AI Native 设计文档](file:///vol2/1000/code/stockclaw/docs/superpowers/specs/2026-07-08-ai-native-agent-module-design.md)（706 行，含附录 B 全部 12 项采纳）  
**评审状态**：**Approved with 8 Issues**（5 中风险 + 3 低风险，无阻塞项）  

---

## 1. Spec 覆盖度系统交叉验证

逐项核对 Spec §9 Phase 1 的 12 项交付物是否在实施计划中完整覆盖：

| Spec # | 交付物 | Plan 落点 | 覆盖 | 偏差 |
|---|---|---|---|---|
| 1 | 合规解禁 + 物理隔离 | Task 1（Step 1.1-1.9） | ✅ | 无 |
| 2 | Rate Limiter | Task 2（Step 2.1-2.5） | ✅ | 无 |
| 3 | quant.valuation | Task 3（Step 3.1-3.5） | ✅ | 无 |
| 4 | quant.stops（含 fallback） | Task 4（Step 4.1-4.5） | ✅ | 无 |
| 5 | quant.cadence | Task 5（Step 5.1-5.5） | ✅ | 无 |
| 6 | portfolio 字段扩展 | Task 6（Step 6.1-6.5） | ✅ | 无 |
| 7 | agents 核心 | Task 7（Step 7.1-7.12） | ✅ | ⚠️ 见漏洞 1、2 |
| 8 | runner + NDJSON endpoint | Task 8（Step 8.1-8.9） | ✅ | ⚠️ 见漏洞 3、4、5 |
| 9 | persistence（aiosqlite） | Task 9（Step 9.1-9.8） | ✅ | ⚠️ 见漏洞 6 |
| 10 | 前端 /agent 路由 + 模型校验 | Task 10（Step 10.1-10.10） | ✅ | ⚠️ 见漏洞 7 |
| 11 | CustomAgentChat + 事件分发 | Task 11（Step 11.1-11.6） | ✅ | 无 |
| 12 | DecisionCard + ContextDrawer | Task 12（Step 12.1-12.4） | ✅ | 无 |

**覆盖结论**：12/12 项交付物均已覆盖。但在具体实现层面存在 8 个偏差或盲区。

---

## 2. 对抗性发现

### 漏洞 1：`runner.py` 中 `_stream_llm_text` 使用同步 `requests.post` 阻塞 event loop（严重 ⚠️）

**Spec 约束**：§2 原则 5（频控与并发分离）+ 第三轮评审发现二（`asyncio.to_thread` 卸载同步调用）  
**Plan 位置**：Task 8 Step 8.4，[runner.py L2501](file:///vol2/1000/code/stockclaw/docs/superpowers/plans/2026-07-08-ai-native-agent-phase-1.md#L2501)

#### 问题描述

`runner.py` 的 `_stream_llm_text()` 函数声明为 `async def`，但内部使用 `requests.post(..., stream=True)` + `resp.iter_content()` 进行流式读取。**`requests` 是同步阻塞库**。在 `async def` 中调用它会完全冻结 FastAPI 的事件循环，导致：

1. 正在推送的 NDJSON 流式响应被卡住——用户在浏览器看到长时间无反应
2. 其他并发请求（包括 `/api/quote` 等数据接口）全部排队
3. Rate Limiter 的 `asyncio.sleep()` 无法被调度

Plan 自身在 Task 7 的全局硬约束第 2 条（L28）明确写了"所有调 astock/gstock 的 @tool 必须用 `await asyncio.to_thread`"，并且在 `tools.py` 中正确使用了 `_run_sync`。**但在 `runner.py` 中自己又违反了同一约束**，使用同步 `requests` 调用上游 LLM。

#### 修复方案

`_stream_llm_text` 应使用 `httpx.AsyncClient` 替代 `requests`：

```python
import httpx

async def _stream_llm_text(cfg, system_prompt, user_messages, context_codes):
    base = cfg["baseURL"].rstrip("/")
    if not base.endswith(("/v1", "/v3", "/api/v3")):
        base = base + "/v1"
    context_str = "；".join(context_codes) if context_codes else "（无）"
    messages = [{"role": "system", "content": system_prompt.format(context=context_str)}]
    messages.extend(user_messages)

    async with httpx.AsyncClient(timeout=120.0) as client:
        async with client.stream(
            "POST",
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {cfg['apiKey']}"},
            json={"model": cfg["model"], "messages": messages, "temperature": 0.3, "stream": True},
        ) as resp:
            if resp.status_code != 200:
                text = await resp.aread()
                raise RuntimeError(f"模型接口 HTTP {resp.status_code}: {text[:300]}")
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    return
                try:
                    j = json.loads(data)
                except json.JSONDecodeError:
                    continue
                choices = j.get("choices") or []
                if choices:
                    delta = choices[0].get("delta") or {}
                    text = delta.get("content")
                    if text:
                        yield text
```

需要在 `backend/requirements.txt` 或 `pyproject.toml` 中新增 `httpx` 依赖。

---

### 漏洞 2：`decision_node` 串行调工具但注释说"并发"，且未发 `tool_trace` 事件（中风险）

**Spec 约束**：§7 NDJSON 协议表要求 `tool_trace` 事件（`status: running/ok/error`）  
**Plan 位置**：Task 7 Step 7.8，[decision_node L2090-2136](file:///vol2/1000/code/stockclaw/docs/superpowers/plans/2026-07-08-ai-native-agent-phase-1.md#L2090)

#### 问题描述

1. **注释写"# 并发调工具"**，但代码是 `await _invoke(...)` 串行顺序调用 4 个工具。由于每个工具都经过 `_run_sync → async with eastmoney_limiter`（1s cool-down），4 个串行调用将耗时 ≥ 4s。这本身是正确的行为（Rate Limiter 设计如此），但注释应改为"# 串行调工具（Rate Limiter 限流，见 Task 2）"。

2. **更关键的问题**：`decision_node` 调工具的过程中**没有生成 `tool_trace` 事件**。Spec §7 NDJSON 协议表和 Phase 1 #11 验收条件都要求前端能展示"atr_stop 运行中"等小药丸。但 `decision_node` 只是在 graph 内部调工具，返回 `decision_card`；`runner.py` 拿到 card 后只推 `text_delta` + `decision_artifact` + `citations` + `done`，**`tool_trace` 事件在整个数据流中从未被生成**。

#### 修复方案

在 `runner.py::run_agent` 中，应在调用 `graph.ainvoke` **之前或通过回调**推送 `tool_trace` 事件。Phase 1 简化版可以在 `decision_node` 中收集工具调用结果后，把 trace 信息写入 `state["artifacts"]`，然后 `runner.py` 在推决策卡之前从 graph 结果中提取并推送 tool_trace 事件：

```python
# runner.py::run_agent 中，在推 decision_artifact 之前
graph_result = await agent_graph.ainvoke(graph_state)
decision_card = graph_result.get("decision_card")
tool_traces = graph_result.get("tool_traces") or []  # decision_node 写入

for trace in tool_traces:
    yield {"type": "tool_trace", "tool": trace["tool"], "status": trace["status"],
           "args": trace.get("args", {}), "summary": trace.get("summary")}
```

---

### 漏洞 3：`runner.py::_stream_llm_text` 的 `{context}` 占位符在 f-string 内会被吞掉（严重 ⚠️）

**Plan 位置**：Task 1 Step 1.5（[prompts.py L209](file:///vol2/1000/code/stockclaw/docs/superpowers/plans/2026-07-08-ai-native-agent-phase-1.md#L209)）和 Task 8 Step 8.4（[runner.py L2498](file:///vol2/1000/code/stockclaw/docs/superpowers/plans/2026-07-08-ai-native-agent-phase-1.md#L2498)）

#### 问题描述

`agents/prompts.py` 中 `SYSTEM_PROMPT_AGENT` 使用 f-string 定义：

```python
SYSTEM_PROMPT_AGENT = f"""你是用户的私人投资分析师...
{ANALYSIS_FRAMEWORK_AGENT}

当前页面上下文：
{{context}}"""
```

由于整个字符串是 **f-string**（`f"""`），Python 会尝试在定义时立即插值 `{context}`。为了保留它作为运行时占位符，Plan 正确地使用了双花括号 `{{context}}`。

但在 `runner.py` 第 2498 行：
```python
messages = [{"role": "system", "content": system_prompt.format(context=context_str)}]
```

此时 `system_prompt` 中的 `{context}` 已经是字面的 `{context}`（因为 f-string 的 `{{}}` 变成了单 `{}`），所以 `.format(context=...)` 会正确工作。

**然而**：`ANALYSIS_FRAMEWORK_AGENT` 字符串本身含有 `{...}` 花括号（例如写了花括号包围的 JSON 示例或数学表达式），在被 f-string 插入时会导致 `KeyError` 或 `ValueError`。虽然当前 `ANALYSIS_FRAMEWORK_AGENT` 的文本恰好没有裸花括号，但这是**脆弱设计**——未来任何人在框架文本里加了 `{` 就会立即崩溃。

同样，`runner.py` 的 `.format(context=...)` 调用如果遇到 prompt 文本中的任何其他 `{...}` 占位符（非 `{context}`），都会抛出 `KeyError`。

#### 修复方案

使用安全的模板替换而非 `.format()`：

```python
# prompts.py 改为普通字符串 + 占位符
SYSTEM_PROMPT_AGENT = SYSTEM_PROMPT_AGENT_TEMPLATE.replace("{{context}}", "{context}")

# runner.py 改为 replace
content = system_prompt.replace("{context}", context_str)
messages = [{"role": "system", "content": content}]
```

或者使用 `string.Template`（`$context`）完全避开花括号冲突。

---

### 漏洞 4：`agentApi` 客户端方法调用了 Phase 1 不存在的后端端点（中风险）

**Spec 约束**：§9 Phase 1 #8 只列了 `/api/agent/chat` 一个新端点  
**Plan 位置**：Task 10 Step 10.3，[api.ts L3527-3539](file:///vol2/1000/code/stockclaw/docs/superpowers/plans/2026-07-08-ai-native-agent-phase-1.md#L3527)

#### 问题描述

`agentApi` 对象定义了 7 个方法：
- `listThreads` → `GET /api/agent/threads`
- `createThread` → `POST /api/agent/threads`
- `renameThread` → `PATCH /api/agent/threads/:id`
- `deleteThread` → `DELETE /api/agent/threads/:id`
- `listMessages` → `GET /api/agent/threads/:id/messages`
- `saveDecision` → `POST /api/agent/decisions`
- `listDecisions` → `GET /api/agent/decisions`

但 **Phase 1 后端只实现了 `/api/agent/chat`**。如果前端代码在任何地方调用了这些方法（例如 `AgentSidebar` 在 Phase 2 中会），请求会返回 404，导致前端报错。

Plan L3542 的注释说明了这一点：
> "Phase 1 的 `api.agent.threads.*` 等只是接口预留...Phase 2 再补后端 CRUD"

但代码中使用 `get<>` / `request<>` 封装，如果被误调会**抛出 `ApiError` 并可能让页面崩溃**（现有 `request()` 函数在 404 时会 `throw new ApiError`）。

#### 修复方案

两种选择：
1. **Phase 1 直接在后端加上这些 CRUD 端点**——工作量小（persistence 层已在 Task 9 实现了 CRUD，只差路由注册），且能让 `AgentSidebar` 真正持久化。
2. **在 agentApi 的未实现方法中 early-return mock 数据**，并加 `// TODO Phase 2` 注释。

建议选方案 1——Spec §9 Phase 1 #9 已经实现了 `persistence/threads.py` 和 `persistence/conversations.py` 的完整 CRUD，只需在 `app.py` 注册几个路由即可。

---

### 漏洞 5：`runner.py` 未做会话持久化——前端发送的消息和 Agent 回复不会写入 SQLite（中风险）

**Spec 约束**：§8 持久化层要求 `conversations` 表保存历史对话  
**Plan 位置**：Task 8 Step 8.4，[runner.py 全文](file:///vol2/1000/code/stockclaw/docs/superpowers/plans/2026-07-08-ai-native-agent-phase-1.md#L2447)

#### 问题描述

`runner.py::run_agent()` 的完整流程是：

1. 构建 graph state → 调 `agent_graph.ainvoke` → 拿到 decision_card
2. 流式调 LLM → 推 `text_delta`
3. 推 `decision_artifact` + `citations` + `done`

**全程没有任何一步写入 `persistence.conversations` 或 `persistence.threads`**。Task 9 辛苦实现的 CRUD 层在 Phase 1 运行时是死代码——用户发的消息和 Agent 的回复只存在于前端的 zustand store（内存态），**刷新页面即丢失**。

这与 Spec §7 `AgentSidebar` 描述的"会话列表（从 SQLite 同步，支持删除和重命名）"直接矛盾。

#### 修复方案

在 `runner.py::run_agent()` 中加入持久化步骤：

```python
async def run_agent(req: AgentChatReq) -> AsyncGenerator[dict, None]:
    from persistence import threads, conversations, decisions as dec_store

    # 1. 创建或复用 thread
    thread_id = req.thread_id
    if not thread_id:
        thread_id = await threads.create_thread(title="新会话", model=req.llm.model)

    # 2. 保存用户消息
    for msg in req.messages:
        await conversations.append_message(thread_id, msg)

    # ... 原有 graph + LLM 逻辑 ...

    # 3. 流式 LLM 结束后，保存助手回复
    full_text = "".join(all_text_deltas)
    await conversations.append_message(thread_id, {"role": "assistant", "content": full_text},
                                       artifacts_json=[decision_card] if decision_card else None)

    # 4. 如果有决策卡，写入 decisions 表
    if decision_card:
        await dec_store.create_decision(thread_id=thread_id, **decision_card_fields)

    yield {"type": "done", "summary": {"thread_id": thread_id}}
```

---

### 漏洞 6：`db.py` 使用模块级 `_DB_PATH` 读取环境变量——测试中 `monkeypatch.setenv` 无法生效（低风险）

**Plan 位置**：Task 9 Step 9.3，[db.py L2849](file:///vol2/1000/code/stockclaw/docs/superpowers/plans/2026-07-08-ai-native-agent-phase-1.md#L2849)

#### 问题描述

```python
# 模块加载时立即读 env
_DB_PATH = os.environ.get("VR_AGENT_DB", _DEFAULT_DB)
```

测试 fixture 中：
```python
monkeypatch.setenv("VR_AGENT_DB", str(db_path))
from persistence import db as db_mod  # 延迟 import
```

`monkeypatch.setenv` 会修改 `os.environ`，但 `from persistence import db` 只在**首次** import 时执行模块级代码。如果另一个测试已经 import 过 `persistence.db`，Python 不会重新执行模块顶层代码，`_DB_PATH` 仍然是上一次的值。

#### 修复方案

将 `_DB_PATH` 改为函数调用时读取：

```python
def _get_db_path() -> str:
    return os.environ.get("VR_AGENT_DB", _DEFAULT_DB)
```

或在测试中使用 `importlib.reload(db_mod)` 强制重载（Plan 的 test fixture 似乎假设了延迟 import 能解决问题，但在同一 pytest session 内跑多个测试时不可靠）。

---

### 漏洞 7：`loadLlmConfig` 函数在 `useAgentStream.ts` 中被引用但不存在（低风险）

**Plan 位置**：Task 11 Step 11.1，[useAgentStream.ts L3889](file:///vol2/1000/code/stockclaw/docs/superpowers/plans/2026-07-08-ai-native-agent-phase-1.md#L3889) 和 Task 10 Step 10.4，[AgentWorkspace.tsx L3559](file:///vol2/1000/code/stockclaw/docs/superpowers/plans/2026-07-08-ai-native-agent-phase-1.md#L3559)

#### 问题描述

代码 import 了 `loadLlmConfig`：
```typescript
import { loadLlmConfig } from "@/lib/llm";
```

但查看实际的 [llm.ts](file:///vol2/1000/code/stockclaw/frontend/src/lib/llm.ts) 文件，导出的函数名是 `loadLlm`（不是 `loadLlmConfig`）：
```typescript
export function loadLlm(): LlmConfig | null { ... }
```

并且 `loadLlm` 返回 `LlmConfig | null`，而 `useAgentStream` 和 `AgentWorkspace` 中直接当作非 null 使用（`llm.provider.startsWith("cli-")`），如果用户未配置 LLM 就访问 `/agent` 页面，会抛出 `TypeError: Cannot read properties of null`。

#### 修复方案

1. 将 import 改为 `import { loadLlm } from "@/lib/llm"`
2. 添加 null 检查：`const llm = loadLlm(); if (!llm) return <CliBlocker />;`（未配置 LLM 时也展示拦截层）

---

### 漏洞 8：`DataUnavailable` 异常在 `quant/stops.py` 和 `quant/valuation.py` 中重复定义（低风险）

**Plan 位置**：Task 3 Step 3.3（[valuation.py L560](file:///vol2/1000/code/stockclaw/docs/superpowers/plans/2026-07-08-ai-native-agent-phase-1.md#L560)）和 Task 4 Step 4.3（[stops.py L1108](file:///vol2/1000/code/stockclaw/docs/superpowers/plans/2026-07-08-ai-native-agent-phase-1.md#L1108)）

#### 问题描述

`DataUnavailable` 异常在两个模块中各自独立定义：
```python
# quant/valuation.py
class DataUnavailable(Exception): ...

# quant/stops.py
class DataUnavailable(Exception): ...
```

这是两个**不同的类**。`agents/tools.py` 在 `_invoke` 中 `except Exception` 统一捕获不会出问题，但如果未来任何代码尝试 `except quant.valuation.DataUnavailable`，它不会捕获 `quant.stops.DataUnavailable`，反之亦然。

同时，`_contract` 辅助函数也在两个模块中重复定义。

#### 修复方案

将 `DataUnavailable` 和 `_contract` 统一放到 `quant/__init__.py`：

```python
# quant/__init__.py
class DataUnavailable(Exception):
    """quant 工具因数据不足无法走完整公式。"""

def contract(tool, inputs, outputs, model_version, assumptions, citations, explanation, basis_type="model"):
    return {
        "tool": tool, "inputs": inputs, "outputs": outputs,
        "basis_type": basis_type, "model_version": model_version,
        "model_assumptions": assumptions, "citations": citations, "explanation": explanation,
    }
```

然后各子模块 `from quant import DataUnavailable, contract`。

---

## 3. Spec 约束交叉验证（非功能性）

| Spec 硬约束 | Plan 实现 | 验证 |
|---|---|---|
| 数据层不污染（§2 原则 1） | 全局硬约束 #1 + Task 1-12 不改 astock/gstock | ✅ |
| asyncio.to_thread 卸载同步层（§ 三轮评审发现二） | 全局硬约束 #2 + Task 7 tools.py `_run_sync` | ✅（但 runner.py 违反，见漏洞 1） |
| NDJSON 不含内嵌 JSON（§2 原则 4） | 全局硬约束 #3 + Task 8 runner 用 artifact 事件 | ✅ |
| Rate Limiter 锁横跨业务（§6 代码） | 全局硬约束 #4 + Task 2 测试 | ✅ |
| 不用 CopilotKit（§2 原则 3） | 全局硬约束 #5 + Task 11 自定义 useAgentStream | ✅ |
| basis_type 归并规则（§6 约束 3） | Task 7 decision.py `merge_basis_type` | ✅ |
| model_versions_json 字段级字典（§6 约束 3） | Task 7 decision.py `_version_label` + `build_decision_card` | ✅ |
| PRAGMA user_version migration（§8） | Task 9 db.py `_run_migrations` | ✅ |
| WAL + busy_timeout=5000（§8） | Task 9 db.py `_connect` | ✅ |
| foreign_keys=ON（conversations ON DELETE CASCADE） | Task 9 db.py `_connect` + schema | ✅ |
| line buffer 跨 chunk 拼接（§7 代码） | Task 11 useAgentStream.ts | ✅ |
| TextDecoder `{stream:true}`（§7） | Task 11 useAgentStream.ts L4002 | ✅ |
| 单条坏帧不中断整流（§7） | Task 11 useAgentStream.ts L4016-4018 | ✅ |
| flush 残留行（§7） | Task 11 useAgentStream.ts L4022-4031 | ✅ |
| CLI 模型拦截覆盖层（§7 组件树） | Task 10 CliBlocker.tsx | ✅ |
| 决策卡 4 档色标（§7 决策卡组件） | Task 12 DecisionCard.tsx `BASIS_COLORS` | ✅ |
| 收藏写入 SQLite（§9 #12） | ⚠️ 仅写 zustand store（内存） | 见漏洞 5 |
| `.env.example` 加 6 项（§8） | Task 8 Step 8.6 | ✅ |
| max_iterations=8（§8 .env） | Task 8 Step 8.6 .env.example | ✅（预留，Phase 2 生效） |
| 双通道收益追踪（§9 Phase 3） | N/A（Phase 3，不在 Phase 1） | ✅ 正确排除 |

---

## 4. 最终评审结论

| 维度 | 评分 | 说明 |
|---|---|---|
| **Spec 覆盖度** | **9.5 / 10** | 12/12 交付物全覆盖；扣分项为 tool_trace 事件未被生成 |
| **代码正确性** | **7.5 / 10** | `runner.py` 的同步 `requests` 阻塞 event loop 是严重问题；`loadLlmConfig` 函数名错误会导致编译失败 |
| **架构一致性** | **9 / 10** | 三层架构忠实于 Spec；持久化层与运行时断联是主要缺口 |
| **可维护性** | **8.5 / 10** | TDD 流程严谨（先写失败测试 → 实现 → 验证通过）；`DataUnavailable` 重复定义是代码卫生问题 |
| **端到端完整性** | **7.5 / 10** | 消息未持久化 + thread CRUD 无后端端点 → 刷新丢失全部会话 |

> [!IMPORTANT]
> **结论：Approved with 8 Issues — 可启动实施，但需在执行过程中修正以下关键项：**
> 
> **必须修正（启动前或 Task 执行时）**：
> 1. 漏洞 1：`runner.py` 改用 `httpx.AsyncClient` 替代同步 `requests`（或至少用 `asyncio.to_thread` 包装）
> 2. 漏洞 7：`loadLlmConfig` → `loadLlm` + null 检查（否则前端编译失败）
> 
> **建议修正（实施过程中）**：
> 3. 漏洞 2：`decision_node` 生成 `tool_trace` 事件写入 graph state，runner 提取并推送
> 4. 漏洞 3：`SYSTEM_PROMPT_AGENT` 使用 `.replace()` 替代 `.format()` 防花括号冲突
> 5. 漏洞 4+5：在 Task 8/9 中顺带注册 thread/conversation CRUD 路由 + runner 写持久化
> 
> **可延后（代码卫生）**：
> 6. 漏洞 6：`_DB_PATH` 改为函数调用时读取
> 7. 漏洞 8：`DataUnavailable` + `_contract` 提取到 `quant/__init__.py`
