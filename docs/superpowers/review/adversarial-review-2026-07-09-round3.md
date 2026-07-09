# Adversarial Review: AI-Native Agent Phase 1 (Round 3)

**日期**：2026-07-09
**对象**：`docs/superpowers/plans/2026-07-08-ai-native-agent-phase-1.md`
**基准**：`docs/superpowers/specs/2026-07-08-ai-native-agent-module-design.md`

在应用了前两轮的修补（Task 13 & 14 / R1-R12）之后，第三轮对抗性评审确认了历史记录重建、侧边栏持久化、并发控制等问题已被完美修复，但依然发现了一个严重违反规范底线的问题。

## 优点 (Strengths)
- **对话历史重建**：在 `runner.py` 中完美通过 `_convos.list_messages()` 获取历史并 prepend 解决上下文丢失 (R9)。
- **侧边栏持久化**：生命周期精准映射了 `agentApi.listThreads()` 和 `createThread()`，闭环了侧边栏的持久化体验 (R10)。
- **并发控制**：在 `decision_node` 完美重构了 `asyncio.gather` 并发调用，基于全局 `EastmoneyRateLimiter` 实现了不堵塞的安全高频并发 (R11)。
- **Rate Limiter 逻辑**：锁的生命周期正确横跨了 sleep 阶段，规避了提前释放的问题。
- **SQLite 结构**：通过 `PRAGMA user_version` 实现的 migration 非常稳健且幂等。

---

## 发现的问题与修补建议 (R13-R15)

### R13: Take Profit 被错误交由 LLM 生成 (Critical)
**问题：** 计划中明确将 `take_profit = None` 并且注释说明 `留空交 LLM 推理`。随后缺失字段在生成时会被标为 `llm_reasoning`。
**影响：** 这严重违反了规范 (Spec §6 L3) 中的硬性要求：`stop_loss / entry_* / take_profit 等硬性价位字段一律由 L2 fallback 值兜底，LLM 不得生成`。并且在系统提示中也明确指出 `target_price 是唯一允许你推理调整的字段`。
**修复要求：** **绝不能将 `take_profit` 留给 LLM 生成。** 在 `decision_node` 中必须为其实现一个 Python 层的 fallback（如 `target_price` 存在则用其作基准，或使用 `current_price * 1.20` 作为后备）。必须在 `tool_results` 字典中将其 `basis_type` 明确标记为 `model_fallback` 并且给予对应的 `model_version` (如 `fixed_20_pct`)，确保决策卡能准确显示其降级来源。

### R14: 入场区间错误继承模型类型 (Important)
**问题：** `entry_low` 和 `entry_high` 在 `decision_node` 里被硬编码为 `current_price * 0.98` 和 `current_price * 1.02`。但在 `build_decision_card` 组装时，它们错误地继承了 `target_tool` 的 `basis_type`（经常是 `model`）。
**影响：** 决策卡会错误地将这些简单的固定比例降级宣称为是通过完整的 Quant 模型 (`model`) 计算出来的，掩盖了它们是 fallback 的本质。
**修复要求：** 在传入 `build_decision_card` 前的 `tool_results` 字典中，显式地为 `entry_low` 和 `entry_high` 赋予 `basis_type: "model_fallback"` 及其具体版本号 (如 `fixed_spread.v1`)，从而呈现正确的出处。

### R15: 吞掉异常不利于调试 (Minor)
**问题：** `decision_node` 中的 `_invoke` 异常处理 (`except Exception as e:`) 会静默吞掉所有异常，直接将其映射为一个 `model_fallback` 的降级 contract。
**影响：** 虽然在 `DataUnavailable` 抛出时执行降级是合理的，但静默吞掉其他 bug（如 `TypeError`, `KeyError`）会让 Agent 开发和调试极其痛苦。
**修复要求：** 在 `except` 块中加入 `import logging` 和 `logging.exception(f"Tool {tool.name} failed")` 打印真实堆栈，然后再返回 fallback contract。

---

## 结论
**评估结果：** **No (需再做修复)**
**原因：** 存在将硬指标（止盈价）越权开放给 LLM 自由生成的严重违规设计。必须通过纯 Python 的 `model_fallback` 降级修复后，才能进入真实的开发实施阶段。
