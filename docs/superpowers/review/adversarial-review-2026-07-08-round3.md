# 对抗性评审报告：AI Native 投资分析平台改造设计（第三轮评审）

**文件名称**：`adversarial-review-2026-07-08-round3.md`  
**评审日期**：2026-07-08  
**评审员**：Antigravity (AI 架构评审组)  
**评审状态**：**Approved — 可进入 writing-plans 阶段**  
**针对的 spec 文件**：[2026-07-08-ai-native-agent-module-design.md](file:///vol2/1000/code/stockclaw/docs/superpowers/specs/2026-07-08-ai-native-agent-module-design.md)  
**前序评审**：[第一轮](file:///vol2/1000/code/stockclaw/docs/superpowers/review/adversarial-review-2026-07-08.md) · [第二轮](file:///vol2/1000/code/stockclaw/docs/superpowers/review/adversarial-review-2026-07-08-round2.md)  

---

## 1. 评审概述

经过前两轮对抗性评审共 12 项漏洞/盲区挖掘（第一轮 8 项 + 第二轮 4 项），Spec 进行了全面修订。本轮基于最终版 Spec（706 行）进行**收尾级深度审查**，重点验证：

1. 前两轮所有修补方案是否已正确固化（回归验证）
2. 新增内容是否引入了新的矛盾或遗漏
3. 从现有代码库出发，Spec 的落地是否存在实操层面的隐性冲突

### 第三轮结论

前两轮 12 项修补意见已**全部正确落地**，无回归问题。本轮额外发现 **4 个实操层面的盲区**，均为中低风险，不阻塞 Phase 1 启动，但建议在实施计划（writing-plans）中纳入明确的处理措施。

---

## 2. 回归验证：前两轮修补落地检查

| 原漏洞 | 落地状态 | 验证位置 |
|---|---|---|
| R1-1. Semaphore → Rate Limiter | ✅ 完整落地 | §2 原则 5、§6 约束 4、§6 `rate_limiter.py` 完整代码 |
| R1-2. 美港股四级降级链 | ✅ 完整落地 | §2 原则 6、§6 约束 1（L1-L4 四级定义清晰） |
| R1-3. aiosqlite + WAL + busy_timeout | ✅ 完整落地 | §8 隐私与安全边界表 |
| R1-4. NDJSON 结构化事件流 | ✅ 完整落地 | §7 协议表（8 种事件类型）、§2 原则 4 |
| R1-5. portfolio.json 增加 cash/risk | ✅ 完整落地 | §8 portfolio.json 示例 |
| R1-P1. chat.py → chat_legacy.py | ✅ 完整落地 | §9 Phase 1 #1 |
| R1-P2. max_iterations=8 | ✅ 完整落地 | §8 .env.example、§9 Phase 2 |
| R1-P3. 双通道收益追踪 | ✅ 完整落地 | §9 Phase 3 |
| R2-1. Rate Limiter 锁范围修正 | ✅ 完整落地 | §6 `rate_limiter.py` 使用 `acquire()`/`release()`、§9 Phase 1 #2 验收条件含"锁必须横跨业务逻辑" |
| R2-2. 前端 line buffer 跨 chunk 拼接 | ✅ 完整落地 | §7 完整 TypeScript 代码含 `stream:true`、flush 残留行、坏帧容错 |
| R2-3. basis_type 归并规则 | ✅ 完整落地 | §6 约束 3 归并规则 + `model_versions_json` 字段级字典示例 |
| R2-4. PRAGMA user_version 迁移 | ✅ 完整落地 | §8 隐私与安全边界表「Schema 迁移」行 |

**回归结论**：全部 12/12 项已正确固化，无一遗漏或回退。

---

## 3. 第三轮新发现

### 发现一：`chat_legacy.py` 重命名对 `app.py` 的导入链断裂（中风险）

#### 问题描述
Spec §9 Phase 1 #1 要求将 `chat.py` 重命名为 `chat_legacy.py`。但当前 [app.py](file:///vol2/1000/code/stockclaw/backend/app.py#L21) 第 21 行：
```python
import chat as chat_layer
```
以及 [mcp_server.py](file:///vol2/1000/code/stockclaw/backend/mcp_server.py) 均直接导入 `chat` 模块。重命名后，**所有老端点 `/api/chat` 将立即因 `ModuleNotFoundError` 崩溃**，除非同步修改所有导入语句。

#### 建议
在 Phase 1 #1 的实施计划中，必须明确列出需要修改的全部导入点：
- `app.py` 第 21 行：`import chat as chat_layer` → `import chat_legacy as chat_layer`
- `mcp_server.py`：检查并更新 `chat` 导入
- 可考虑保留一个 `chat.py` 兼容垫片（`from chat_legacy import *`），但这会模糊物理隔离的边界，不推荐

---

### 发现二：`agents/tools.py` 同步/异步混合调用隐患（中风险）

#### 问题描述
Spec §6 中定义的 `@tool` 装饰器使用 `async def`：
```python
@tool
async def atr_stop(code: str, ...) -> dict:
    async with eastmoney_limiter:
        return await quant.stops.atr_stop(code, period, multiplier)
```

但底层 `quant.stops.atr_stop` 调用的是 `astock.kline()`（参见 [astock.py](file:///vol2/1000/code/stockclaw/backend/astock.py)），而 `astock.kline()` 使用的是 `mootdx`（同步阻塞式 TCP 连接）。`astock.tencent_quote()` 使用的是标准库 `urllib.request.urlopen()`（同步阻塞）。`astock.em_get()` 使用的是 `requests.get()`（同步阻塞）。

当在 `async def` 函数中直接 `await` 调用这些同步函数时，**同步调用会阻塞整个 asyncio 事件循环**，导致：
1. Rate Limiter 的 `asyncio.sleep()` 无法被正常调度
2. 其他并发的 SSE 流式响应被完全卡死
3. FastAPI 的整体吞吐量退化为单线程串行

#### 建议
所有调用同步数据层（`astock` / `gstock`）的 `@tool` 函数，必须使用 `asyncio.to_thread()` 将同步调用卸载到线程池：

```python
@tool
async def atr_stop(code: str, period: int = 14, multiplier: float = 2.0) -> dict:
    async with eastmoney_limiter:
        # 同步的 quant 函数内部调用 astock（urllib/requests/mootdx 均同步阻塞），
        # 必须用 to_thread 卸载到线程池，否则会冻结整个 event loop
        return await asyncio.to_thread(quant.stops.atr_stop, code, period, multiplier)
```

这是一个**必须在 Phase 1 落地时严格遵守的约束**——否则 Rate Limiter 虽然代码正确，但因为 event loop 被阻塞而无法正常运作 cool-down 计时。

---

### 发现三：`conversations` 表 schema 缺失（低风险）

#### 问题描述
Spec §8 定义了 `threads` 表和 `decisions` 表的完整 SQL schema，以及 `signals_log` 表的 Phase 3 schema。但 `conversations` 表**没有给出 schema 定义**——仅在目录结构中提到 `persistence/conversations.py`，在数据库结构图中列为 `conversations (历史对话与上下文详细记录)`。

在 Phase 1 #9 中，验收条件是"threads/decisions 表 migration 可重入"，但 `conversations` 表同样需要在 Phase 1 中创建（否则会话历史无法持久化），其 schema 设计却缺失。

#### 建议
在实施计划中补充 `conversations` 表的 schema 定义。建议结构：

```sql
CREATE TABLE IF NOT EXISTS conversations (
    id          TEXT PRIMARY KEY,        -- 消息 ID (ULID)
    thread_id   TEXT NOT NULL,           -- 所属会话
    role        TEXT NOT NULL,           -- system | user | assistant | tool
    content     TEXT,                    -- 消息正文
    tool_calls_json TEXT,               -- assistant 消息的工具调用序列化
    tool_call_id TEXT,                  -- tool 消息的关联 ID
    created_at  INTEGER NOT NULL,
    FOREIGN KEY (thread_id) REFERENCES threads(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_conversations_thread ON conversations(thread_id, created_at);
```

---

### 发现四：`/api/agent/chat` 端点的请求体设计与鉴权协调未定义（低风险）

#### 问题描述
Spec 定义了 `/api/agent/chat` 返回 NDJSON 流，但**请求体（Request Body）的结构没有明确定义**。当前老端点 `/api/chat` 的请求体是 [ChatReq](file:///vol2/1000/code/stockclaw/backend/app.py#L83-L86)：

```python
class ChatReq(BaseModel):
    messages: list[dict]
    context: str = ""
    llm: LLMConfig
```

新端点需要额外支持：
- `thread_id`：关联到哪个会话（新建 vs 续聊）
- 自选股 / 持仓的上下文注入方式（`AgentTopBar` 中的 "上下文: 自选●3 持仓●2"）
- 用户风格偏好（保守/平衡/激进，Phase 4 但接口需预留）

此外，新端点的鉴权需要与现有的 `_require_api_key` 中间件兼容（路径以 `/api/` 开头即会触发鉴权检查），这没有问题，但需要确认 SSE 流式响应在鉴权失败时能正确返回 401 而非挂起连接。

#### 建议
在 Phase 1 实施计划中，定义 `AgentChatReq` 的 Pydantic 模型：

```python
class AgentChatReq(BaseModel):
    thread_id: str | None = None        # None = 新建会话
    messages: list[dict]                 # 至少含最新一条 user 消息
    context_codes: list[str] = []        # 自选/持仓代码列表（Agent 自动查数据）
    llm: LLMConfig
    style: str = "balanced"              # conservative | balanced | aggressive（Phase 4 预留）
```

---

## 4. Spec 中的一处格式瑕疵

Spec 第 158–159 行存在一个未闭合的 Markdown 代码块：

```
注：单工具返回的 `model_version` 是字符串；Decision Node 合并成决策卡时改为**字段级字典**写入 `model_versions_json`（见约束 3 归并规则）。
```
```

第 159 行有一个孤立的 ` ``` ` 闭合标记，但前面并没有对应的开启标记。这不影响语义理解，但可能导致某些 Markdown 渲染器将后续内容错误地当作代码块。建议移除该行。

---

## 5. 最终评审结论

| 评估维度 | 第一轮 | 第三轮（最终） | 变化 |
|---|---|---|---|
| **架构合理性** | 8 / 10 | **9.5 / 10** | 四级降级链 + 归并规则 + 物理隔离使架构严密闭环 |
| **高并发稳定性** | 4 / 10 | **8.5 / 10** | Rate Limiter 锁修复 + 强缓存；扣分项为同步数据层阻塞 event loop 需 `to_thread` |
| **可实施性 (Phase 1)** | 9 / 10 | **9.5 / 10** | 12 项交付物验收条件清晰，含具体的性能/正确性断言 |
| **数据安全性** | 9.5 / 10 | **9.5 / 10** | 未变，已足够严密 |
| **协议完备性** | 未评分 | **9 / 10** | NDJSON 8 种事件 + line buffer + 坏帧容错；扣分项为请求体未定义 |

> [!IMPORTANT]
> **结论：批准进入实施阶段 (Approved for Implementation)**。  
> 建议在 `writing-plans` 阶段将本轮 4 项发现纳入具体任务：
> 1. **发现一**（`chat_legacy.py` 导入链）→ Phase 1 #1 任务中追加 `app.py` / `mcp_server.py` 导入修改
> 2. **发现二**（`asyncio.to_thread`）→ Phase 1 #7 agents 核心任务中，作为 `tools.py` 的硬性编码约束
> 3. **发现三**（`conversations` 表 schema）→ Phase 1 #9 persistence 任务中补充建表 SQL
> 4. **发现四**（`AgentChatReq` 请求体）→ Phase 1 #8 runner + endpoint 任务中定义
