# Adversarial Review: AI-Native Agent Phase 1 (Round 2)

**日期**：2026-07-09
**对象**：`docs/superpowers/plans/2026-07-08-ai-native-agent-phase-1.md`
**基准**：`docs/superpowers/specs/2026-07-08-ai-native-agent-module-design.md`

在包含 Task 13 的回修 (R1-R8) 之后，对第一阶段实施计划的第二轮对抗性评审中，发现了以下 4 个关键的架构和逻辑遗漏。这些遗漏会破坏系统的多轮对话能力、持久化体验以及性能，必须在实施阶段加以修复。

## 发现的问题与修补建议 (R9-R12)

### R9: 修复多轮对话记忆缺失 (runner.py) - 严重偏差
**问题：** `runner.py` 中并没有从 SQLite 数据库查询来重建历史对话，导致 LLM 完全处于“失忆”状态。这与 Spec 中多轮对话语义背道而驰。
**修复要求：** 在 `run_agent` 内部，在构造发给 LLM 的 `messages` 之前，必须调用 `await _convos.list_messages(thread_id)` 获取当前会话的历史记录，并将其 prepend 到请求的 `req.messages` 前面。

### R10: 前端侧边栏持久化对接 (AgentSidebar.tsx) - 高优先级
**问题：** 尽管后端在 R4 中添加了用于 SQLite 持久化的路由，前端侧边栏组件 `AgentSidebar.tsx` 仍然在使用临时的 `ephemeral IDs` (`local-${Date.now()}`)，并且未能在页面加载时从后端拉取已保存的 threads，导致页面刷新后历史记录依然丢失。
**修复要求：** `AgentSidebar.tsx` 必须添加 `useEffect`，在 mount 时调用 `agentApi.listThreads()`；同时新建对话动作必须调用 `agentApi.createThread()` 并获取后端分配的真实 ID，保证前端状态的持久化一致性。

### R11: 恢复 Quant Tools 的并发调用 (decision.py) - 性能问题
**问题：** `decision_node` 中基于对东财 403 错误的过度防备，错误地将四个核心 quant 工具串行化调用。这导致响应显著变慢，违反了 Spec 中并发执行的要求。
**修复要求：** 全局 `EastmoneyRateLimiter` 已经通过横跨业务逻辑的 `asyncio.Lock` 提供了安全的频率控制。所以对工具的调用已经是安全的，必须在 `decision_node` 中恢复使用 `await asyncio.gather(...)` 并发执行量化工具。

### R12: 移除 LLM Reasoning 的硬编码 Bypass (decision.py / valuation.py) - 逻辑问题
**问题：** 在 `decision_node` 处理逻辑中，当遇到 `forward_pe_target` 数据缺失时，代码被错误地 hardcode 为给出一个 `+15%` (`current_price * 1.15`) 的默认 fallback 目标价。这绕过了规范中要求的、在 `model_fallback` 无效时转由 LLM 进行推导 (`llm_reasoning`) 的流程。
**修复要求：** 移除这个硬编码的 `* 1.15` 粗暴降级方案。当数据缺失 (抛出 `DataUnavailable`) 时，对于无法在 Python 层面简单估算的 `target_price`，应当让该字段为空，强制交由 LLM 根据提示词规则进行推理和预测，确保 `basis_type: llm_reasoning` 的正常运行。

---

## 结论
该实现计划在整体架构层面已较为完善，但在对接细节及边界处理上仍有遗漏。以上四项修补 (R9-R12) 需在后续实际开发 (Subagent-Driven Development 等方式) 中同步应用到实施代码中，作为最终落地的附加要求。
