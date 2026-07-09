# 对抗性评审：Phase 1 实施计划 vs 评审意见（Round 2）

**评审日期**：2026-07-09  
**评审员**：Antigravity (AI 架构评审组)  
**评审对象**：[Phase 1 实施计划](file:///vol2/1000/code/stockclaw/docs/superpowers/plans/2026-07-08-ai-native-agent-phase-1.md) (已更新)  
**评审目标**：验证第一轮评审发现的 8 项漏洞是否被有效闭环，且未引入新的架构冲突。  
**评审状态**：**Approved - Ready for Execution**

---

## 第一轮漏洞闭环验证

### 🔴 必须修正（阻塞运行）

| 编号 | 漏洞 | 修复验证 | 结果 |
|---|---|---|---|
| **1** | `runner.py` 用同步 `requests.post` 调 LLM，冻结 event loop | Task 8 Step 8.4 已将 `_stream_llm_text` 改用 `httpx.AsyncClient`，并使用了 `client.stream` 和 `resp.aiter_lines()` 处理。Task 0 已添加 `httpx` 安装步骤。 | ✅ **Fixed** |
| **7** | `loadLlmConfig` 函数名错误导致前端编译失败 | Task 11 Step 11.1 中 `useAgentStream.ts` 已修正 import 为 `loadLlm`，并在 `send` 方法中添加了 `!llm` 及 `cli-` 前缀的 null 检查防线。 | ✅ **Fixed** |

### 🟡 建议修正（功能完整性）

| 编号 | 漏洞 | 修复验证 | 结果 |
|---|---|---|---|
| **2** | `decision_node` 串行工具调用的注释以及缺少 `tool_trace` 事件推送 | Task 7 Step 7.8 中注释已修正为「串行调工具（Rate Limiter 限流）」。`decision_node` 中新增了 `tool_traces` 列表收集 `running/ok/error` 状态。Task 8 Step 8.4 `run_agent` 增加了 `for trace in tool_traces` 循环推送事件。 | ✅ **Fixed** |
| **3** | `SYSTEM_PROMPT_AGENT` 的 `{context}` 占位符在 f-string/`.format` 下可能导致的 KeyError | Task 8 Step 8.4 中，将 `.format(context=...)` 改为了 `.replace("{context}", context_str)`，提升了系统鲁棒性，允许后续随意修改系统 prompt 而不崩溃。 | ✅ **Fixed** |
| **4&5** | `agentApi` 调用不存在端点 & 消息不写 SQLite，刷新丢失会话 | Task 8 Step 8.4 的 `run_agent` 完整引入了 7 步持久化流程，包括创建 thread，持久化用户及助手消息、写入 decisions 表和 touch thread。Task 9 Step 9.9 补全了 7 个 CRUD 路由；Step 9.10 增加了相关单测。 | ✅ **Fixed** |

### 🟢 架构卫生（低风险）

| 编号 | 漏洞 | 修复验证 | 结果 |
|---|---|---|---|
| **6** | `db.py` 中 `_DB_PATH` 导致 `monkeypatch.setenv` 失效 | Task 9 Step 9.3 中已将 `_DB_PATH` 提取为 `_get_db_path()` 函数在连接时即时求值。 | ✅ **Fixed** |
| **8** | `DataUnavailable` 等在 `quant` 子模块重复定义 | Task 3 Step 3.0 增加了 `quant/__init__.py` 创建步骤，统一提取 `DataUnavailable` 类和 `contract` 组装函数，子模块统一 `from quant import`。 | ✅ **Fixed** |

---

## 额外检查及观察

1. **依赖顺序说明补充**：
   - 在附录 C 明确指出了 `Task 8`（需要使用 SQLite）依赖于 `Task 9`（构建 Persistence 层），建议的执行顺序为 `Task 0-7` → `Task 9` → `Task 8` → `Task 10-12`。这是非常合理的执行期规划。
2. **文档结构一致性**：
   - Markdown 锚点与实际代码同步修改。
   - 所有单测期望依然保证红绿重构逻辑闭环。

## 最终结论

所有在第一轮对抗性评审中发现的 8 项漏洞**均已得到正确且优雅的修正**。这些修改完全遵循了 Spec 的要求，没有破坏核心三层架构、没污染客观数据层、正确遵循了异步化和持久化要求。

**当前状态**：计划已完全就绪，可以安全进入 Subagent-Driven Execution 或 Inline Execution。建议研发团队直接按照该计划开启实施。
