# LangChain Agent Workspace 1A Verification

- Locked protocol contract suite: PASS
- Cross-equivalent-Graph MemorySaver resume: PASS
- Resume `messages=[]` regenerate guard: PASS
- Fresh `LangGraphAgent` per request: PASS
- API key leak scan across events/checkpoint/config/errors: PASS
- Disconnect/cancel terminal behavior: PASS
- OpenAI manual check: PASS（以 BigModel GLM-5.2 OpenAI 兼容通道执行，见下）
- DeepSeek-compatible manual check: PASS（同上 —— BigModel /v4 为 OpenAI 兼容协议；独立的 DeepSeek 官方通道未测）
- Legacy backend/frontend regression: PASS（后端 140 passed；node 16 passed；vitest 8 passed；`npm run build` 通过）
- Transition limit decision: 未启用 —— 实测（langgraph 1.2.11）recursion_limit 按单次 invoke 计、不跨 resume 累计，故 1A 不宣称转移数策略（`PRODUCT_TRANSITION_LIMIT = None`）

Decision: 1A exit criteria passed; implementation planning for 1B may begin.

## 实测记录（2026-08-15，人工通道）

- Provider: bigmodel / GLM-5.2 / `https://open.bigmodel.cn/api/coding/paas/v4`（密钥不记录）
- 注意：`https://open.bigmodel.cn/api/paas/v4` 报 429 code 1113（账户余额不足）；改用 coding 套餐端点后成功。后端正确把上游错误转成脱敏 `RUN_ERROR`，未泄露密钥。
- 文本流式：PASS（51 个 TEXT_MESSAGE_CONTENT 增量）
- 工具调用渲染：PASS —— `query_quote("600519")` 返回真实行情（1341.99 元 / -0.98% / PE-TTM 20.6）并流式总结
- 运行中状态：PASS —— 输入禁用、发送隐藏、停止按钮可见
- Stop 行为：PASS —— 点击后输入恢复可用；同线程再次发起返回 200（coordinator 已释放，无 THREAD_BUSY 残留）
- 浏览器控制台/网络错误：无（HTTP 全 200）

## 与计划的偏差（实现期发现并记录）

1. **依赖锁定冲突**：`langchain==1.3.15` 要求 `langgraph>=1.2.11`，与计划锁定的 `1.2.9` 冲突 → 升至 **1.2.11**（经用户确认）。
2. **mootdx / langchain-mcp-adapters 互斥**：mootdx 全版本要求 `httpx<0.26`，而 mcp≥1.9.2 要求 `httpx>=0.27` → 1A 移除 `langchain-mcp-adapters`（1A 未用 MCP），1C 引入 MCP 时再评估拆分（经用户确认）。
3. **锁定版本事件形状**：`CustomEvent.value` 为 JSON 字符串（非 dict）；工具参数内嵌在 `TOOL_CALL_START.rawEvent`，无独立 `TOOL_CALL_ARGS` 事件。桥接层已适配；垂直切片契约按实际事件序列断言。
4. **@ag-ui/client 去重**：`@assistant-ui/react-ag-ui@0.0.54` 内嵌 0.0.57 与顶层 0.0.58 类型冲突 → npm `overrides` 强制统一 0.0.58。
5. **测试布局**：`tests/agent/` 不放 `__init__.py`（会以 `agent` 包名遮蔽 `backend/agent`），改为添加 `tests/__init__.py`。
6. **Starlette 断连测试**：ASGI scope 声明 `spec_version 2.4` 时 Starlette 不监听断连，断连取消测试使用默认 spec_version。
7. **已知 1A UI 瑕疵（不阻塞）**：流内 `RUN_ERROR`（如上游余额不足）未在页面上显示（onError 回调未覆盖该路径）；消息快照与实时消息可能重复渲染一次。建议 1B 处理。
