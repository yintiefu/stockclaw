# 对抗性评审报告：AI Native 投资分析平台改造设计 (第二轮评审)

**文件名称**：`adversarial-review-2026-07-08-round2.md`  
**评审日期**：2026-07-08  
**评审员**：Antigravity (AI 架构评审组)  
**评审状态**：Approved with Recommendations (设计方案已极大完善，修正逻辑代码漏洞后即可动工)  
**针对的 spec 文件**：[2026-07-08-ai-native-agent-module-design.md](file:///vol2/1000/code/stockclaw/docs/superpowers/specs/2026-07-08-ai-native-agent-module-design.md)  

---

## 1. 评审概述

在第一轮对抗性评审后，设计文档（Spec）进行了深度迭代，合规隔离、限流器设计、四级降级链、SQLite 异步化以及 NDJSON SSE 协议均已固化入文档（参见附录 B 的 8 项采纳项）。

### 第二轮评审结论
经过对更新后 Spec 的二次审查，我们认为**当前设计方案在架构和可行性上已达到生产级水准**。原有的核心系统性漏洞（如高频封禁风险、JSON 拦截崩溃风险、美港股降级逻辑撕裂）已得到彻底解决。

但在细化落地代码与具体协议实现层面，我们依然挖掘出 **3 个逻辑漏洞/设计盲区**。其中**限流器的 Python 上下文锁范围错误**属于会导致限流失效的严重代码级 Bug，必须在实现时予以修正。

---

## 2. 深度漏洞与技术实现微调

### 漏洞一：`EastmoneyRateLimiter` 上下文管理器锁范围失效（严重）
> **Spec 6.3 节代码**：
> ```python
>     async def __aenter__(self):
>         async with self._lock:
>             now = asyncio.get_event_loop().time()
>             wait = max(0, self._last_release + self._cool_down - now)
>             if wait: await asyncio.sleep(wait)
>             return self
> ```

#### 对抗性分析：
在上述实现中，`async with self._lock` 的作用域仅限于 `__aenter__` 方法内部。一旦 `return self` 被执行，Python 解释器会自动退出该 `async with` 块，从而**立即释放** `self._lock`。
这意味着，当调用者（例如 `atr_stop` 任务）拿到 `self` 并开始执行实际的东财 HTTP 请求时，**锁早已被释放**。
如果此时有另一个并发任务进入，它会发现锁是空闲的，从而直接进入 `__aenter__`。如果前一个任务的请求尚未完成且未触发 `__aexit__`（未更新 `_last_release`），后一个任务会计算出 `wait = 0` 并立即执行，导致**多个并发 HTTP 请求同时在网络上运行**，限流机制彻底瘫痪。

#### 修正方案：
锁的获取必须横跨调用者的整个生命周期，并在 `__aexit__` 中统一释放。

```diff
 class EastmoneyRateLimiter:
     """cool-down 节流器：acquire 后强制 sleep(1.0)，防瞬时高频。"""
     def __init__(self, cool_down: float = 1.0):
         self._lock = asyncio.Lock()
         self._cool_down = cool_down
         self._last_release = 0.0
+
     async def __aenter__(self):
-        async with self._lock:
-            now = asyncio.get_event_loop().time()
-            wait = max(0, self._last_release + self._cool_down - now)
-            if wait: await asyncio.sleep(wait)
-            return self
+        # 1. 锁必须在 __aenter__ 开始时 acquire，且在退出此方法时不释放
+        await self._lock.acquire()
+        now = asyncio.get_event_loop().time()
+        wait = max(0, self._last_release + self._cool_down - now)
+        if wait:
+            await asyncio.sleep(wait)
+        return self
+
     async def __aexit__(self, *exc):
-        self._last_release = asyncio.get_event_loop().time()
+        try:
+            # 2. 调用者业务逻辑结束后，更新释放时间
+            self._last_release = asyncio.get_event_loop().time()
+        finally:
+            # 3. 确保在 __aexit__ 中释放锁，让下一个排队请求进入
+            self._lock.release()
```

---

### 漏洞二：前端 NDJSON 流式分块解析中的“截断 JSON”崩溃风险
> **Spec 7.3 节描述**：前端用 `fetch + ReadableStream + TextDecoder` 按行解析 NDJSON 流。

#### 对抗性分析：
在网络传输中，`ReadableStream` 每次返回的 `value`（Chunk）大小是不固定的。一个完整的 NDJSON 行（以 `\n` 结尾）可能被切分在两个不同的 Chunk 中。
例如：
*   Chunk 1 的末尾是：`{"type": "text_delta", "te`
*   Chunk 2 的开头是：`xt": "分析数据"}\n`
如果前端直接对 Chunk 转换为文本后按 `\n` 切分并 `JSON.parse`，在遇到 Chunk 1 末尾的半截字符串时会直接抛出 `SyntaxError: Unexpected end of JSON input`，导致流式渲染直接中断。

#### 修正方案：
前端必须维护一个残余字符缓冲区（Line Buffer），在每次分块到达时进行拼接，仅当遇到 `\n` 时才做切分和解析。

```typescript
// useAgentStream.ts 推荐的标准解析实现
const response = await fetch('/api/agent/chat', { ... });
const reader = response.body?.getReader();
const decoder = new TextDecoder();
let lineBuffer = '';

if (reader) {
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    
    // 拼接缓冲区
    lineBuffer += decoder.decode(value, { stream: true });
    const lines = lineBuffer.split('\n');
    
    // 弹出最后一个可能不完整的行，留作下次拼接
    lineBuffer = lines.pop() || '';
    
    for (const line of lines) {
      if (line.trim()) {
        try {
          const event = JSON.parse(line);
          dispatch(event); // 派发结构化事件
        } catch (e) {
          console.error("NDJSON 帧解析失败:", line, e);
        }
      }
    }
  }
}
```

---

### 漏洞三：多工具并发调用下的 `basis_type` 决策链归并冲突
> **Spec 6.3 节契约**：决策卡数据库表及 API 定义了 `basis_type`，可选值为 `model | model_fallback | llm_reasoning | hybrid`。

#### 对抗性分析：
在 Agent 执行过程中，Orchestrator 可能会在一个会话中针对同一只股票调用多个不同的 quant 工具。
例如：
1.  调用 `atr_stop` 发生降级，返回 `model_fallback`。
2.  调用 `pe_percentile_revert` 运行正常，返回 `model`。
3.  调用 `simple_dcf` 时，LLM 对折现率进行了微调，返回 `hybrid`。
当这些结果最终被 Decision Node 合并为一张单一的“决策卡”并存入数据库 `decisions` 表时，**整张卡片的最终 `basis_type` 应该取什么值？** Spec 中对此没有定义归并逻辑。

#### 建议修补方案（归并权重定义）：
合并节点应当遵循**“最大不确定性优先”**原则，按照以下优先级决定整张决策卡的 `basis_type`：
1.  若任意子组件包含 `llm_reasoning`，且没有模型辅助 $\rightarrow$ 整张卡标记为 `llm_reasoning`。
2.  若任意子组件经过了 LLM 的微调修改（如修正了模型输出的目标价） $\rightarrow$ 整张卡标记为 `hybrid`。
3.  若无 LLM 介入，但至少有一个工具运行在 `model_fallback` 状态 $\rightarrow$ 整张卡标记为 `model_fallback`。
4.  只有当所有参与计算的工具都完美运行在主路径上 $\rightarrow$ 整张卡标记为 `model`。

同时，`decisions` 表中的 `model_versions_json` 应该以字典形式保存每个具体组件的来源，例如：
`{"stop_loss": "model_fallback(atr_stop.v1)", "take_profit": "model(pe_percentile.v1)"}`。

---

## 3. SQLite 轻量级重入迁移设计 (db.py 最佳实践)

为了满足 Spec 9.9 节中“threads/decisions 表 migration 可重入”的要求，且不引入外部迁移工具，建议在 `persistence/db.py` 中利用 SQLite 内置的 `user_version` 实现轻量级迁移机制，避免多次启动时建表语句冲突或结构损坏：

```python
# backend/persistence/db.py 推荐结构

import aiosqlite
import os

DB_PATH = os.path.expanduser(os.environ.get("VR_AGENT_DB", "backend/.cache/stockclaw.db"))

# 按版本递增的 SQL 脚本字典
MIGRATIONS = {
    1: [
        """CREATE TABLE IF NOT EXISTS threads (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            model TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        );""",
        """CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY,
            thread_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at INTEGER NOT NULL
        );"""
    ],
    2: [
        """CREATE TABLE IF NOT EXISTS decisions (
            id TEXT PRIMARY KEY,
            thread_id TEXT NOT NULL,
            code TEXT NOT NULL,
            name TEXT,
            created_at INTEGER NOT NULL,
            target_price REAL,
            entry_low REAL,
            entry_high REAL,
            stop_loss REAL,
            take_profit REAL,
            cadence_json TEXT,
            basis_type TEXT NOT NULL,
            model_versions_json TEXT,
            assumptions_json TEXT,
            citations_json TEXT,
            status TEXT,
            linked_position_code TEXT,
            price_at_creation REAL,
            current_price REAL,
            pnl_pct REAL,
            updated_at INTEGER,
            raw_artifact_json TEXT
        );"""
    ],
    3: [
        """CREATE TABLE IF NOT EXISTS signals_log (
            id TEXT PRIMARY KEY,
            created_at INTEGER NOT NULL,
            source_node TEXT NOT NULL,
            code TEXT,
            signal_type TEXT NOT NULL,
            severity REAL,
            payload_json TEXT,
            handled INTEGER DEFAULT 0
        );"""
    ]
}

async def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute("PRAGMA journal_mode=WAL;")
        await conn.execute("PRAGMA busy_timeout=5000;")
        
        # 获取当前 schema 版本
        async with conn.execute("PRAGMA user_version;") as cursor:
            row = await cursor.fetchone()
            current_version = row[0] if row else 0
            
        # 顺次执行未应用的迁移
        for version, sql_statements in sorted(MIGRATIONS.items()):
            if version > current_version:
                for sql in sql_statements:
                    await conn.execute(sql)
                await conn.execute(f"PRAGMA user_version = {version};")
                await conn.commit()
                current_version = version
```

---

## 4. 评审结论

基于文档的更新和本轮的补充设计，我们对改造方案给予**最终批准 (Approved)**。本 Spec 已彻底消除了阻碍项目推进的重大架构风险。在随后的 `writing-plans`（编写详细实施计划）阶段中，应当直接采纳本评审报告第 2、3 节中关于**限流器锁修复**、**前端 NDJSON 帧拼接**、**`basis_type` 归并权重**和**轻量级数据库迁移**的详细设计。
