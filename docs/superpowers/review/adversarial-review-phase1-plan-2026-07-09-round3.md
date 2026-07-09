# 对抗性评审：Phase 1 实施计划 vs 架构设计说明（Round 3）

**评审日期**：2026-07-09  
**评审员**：Antigravity (AI 架构评审组)  
**评审对象**：[Phase 1 实施计划](file:///vol2/1000/code/stockclaw/docs/superpowers/plans/2026-07-08-ai-native-agent-phase-1.md)  
**参照标准**：[AI-Native Agent Module Design](file:///vol2/1000/code/stockclaw/docs/superpowers/specs/2026-07-08-ai-native-agent-module-design.md)  
**评审状态**：**Rejected - Critical Regression (代码回滚/丢失)**

---

## 🚨 致命级别回退预警 (Critical Regression)

经过详细对比，当前的实施计划文档（共 4660 行）与**第一轮评审时的原始状态完全一致**，这意味着在**第二轮评审中已经验证修复的 8 个核心漏洞全部重新出现**。

这可能是不小心的 `git checkout`、编辑器覆盖或者是版本控制失误导致的文件回滚。

### 重新出现的核心漏洞（必须再次修复）

#### 1. 致命缺陷：网络 I/O 阻塞异步事件循环
- **位置**：`Task 8 Step 8.4` 的 `_stream_llm_text` (约 2501 行)
- **回退症状**：重现了同步的 `requests.post`。
- **影响**：由于这是在 FastAPI 的 `async def` 路由下执行的，同步网络请求会完全冻结整个服务的异步事件循环，导致并发请求卡死。
- **要求**：必须恢复使用 `httpx.AsyncClient` 以及异步流式解析 `aiter_lines()`。

#### 2. 前端崩溃风险：无效的导入函数名
- **位置**：`Task 11 Step 11.1` 的 `useAgentStream.ts`
- **回退症状**：恢复了错误的导入 `import { loadLlmConfig } from "@/lib/llm";`。
- **影响**：前端代码中实际导出的名称是 `loadLlm`，调用 `loadLlmConfig` 会导致 Vite 编译直接报错崩溃。

#### 3. 持久化数据丢失：没有写入 SQLite 以及会话丢失
- **位置**：`Task 8 Step 8.4` 的 `run_agent` 及 `Task 9`
- **回退症状**：`run_agent` 再次丢失了 7 步持久化入库的逻辑（包括 `threads`, `conversations`, `decisions` 的写入）。
- **影响**：用户一旦刷新页面，所有会话记录和决策卡都将丢失，完全不符合 Spec 中要求的本地 SQLite 持久化能力。同时，CRUD 相关的 API 端点（`agentApi`）在后端均缺失。

#### 4. 前端架构错误：缺少 `tool_trace` 事件推送
- **位置**：`Task 7 Step 7.8` 的 `decision_node` 及 `runner.py`
- **回退症状**：`decision_node` 再次变成了仅仅执行工具而不捕获 `running/ok/error` 状态。
- **影响**：UI 上的「折叠小药丸」组件将没有任何数据来源，无法向用户展示 Agent 调用工具的中间过程。

#### 5. 脆弱的 Prompt 格式化
- **位置**：`Task 8 Step 8.4` 的 `_stream_llm_text`
- **回退症状**：使用了 `.format(context=context_str)`。
- **影响**：如果 `SYSTEM_PROMPT_AGENT` 内部存在其他未转义的大括号 `{}`，直接使用 `.format()` 将触发 `KeyError` 导致服务异常崩溃。需换回 `.replace("{context}", context_str)`。

#### 6. SQLite 锁机制及路径污染
- **位置**：`Task 9 Step 9.3` 及 `Task 3 Step 3.0`
- **回退症状**：`db.py` 恢复了模块级执行的 `_DB_PATH` 获取方式，导致测试中 monkeypatch 失效。并且 `DataUnavailable` 等基础对象又在 `valuation.py` 等文件中重复定义，污染了架构层次。

---

## 评审结论与下一步行动

当前实施文档状态为**不可执行**。

**行动建议**：
1. 请检查您的 Git 状态、编辑器历史或分支信息，确认是否意外恢复了旧版文件。
2. 建议找回经过 Round 2 修复的那个 5001 行版本的 `2026-07-08-ai-native-agent-phase-1.md`。
3. 恢复修复版后，请再次提交评审以确认处于 Ready 状态。
