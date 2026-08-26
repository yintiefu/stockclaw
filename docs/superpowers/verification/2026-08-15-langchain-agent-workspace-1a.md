# LangChain Agent Workspace 1A Verification

- Locked protocol contract suite: PASS
- Cross-equivalent-Graph MemorySaver resume: PASS
- Resume `messages=[]` regenerate guard: PASS
- Fresh `LangGraphAgent` per request: PASS
- API key leak scan across events/checkpoint/config/errors: PASS（含真实 ChatOpenAI 路径的 handle/snapshot 泄漏测试）
- Disconnect/cancel terminal behavior: PASS
- OpenAI manual check: **PARTIAL** —— 以 BigModel GLM-5.2（OpenAI 兼容 /v4 协议）完成完整链路实测；OpenAI 官方通道未测
- DeepSeek-compatible manual check: **NOT RUN** —— 未获得 DeepSeek 官方通道密钥；BigModel 实测覆盖了同协议族
- Legacy backend/frontend regression: PASS（后端 147 passed；node 16 passed；vitest 12 passed；`npm run build` 通过）
- Transition limit decision: 未启用 —— 实测（langgraph 1.2.11）recursion_limit 按单次 invoke 计、不跨 resume 累计，故 1A 不宣称转移数策略（`PRODUCT_TRANSITION_LIMIT = None`）

Decision: 1A 自动化出口标准与评审修复全部完成；OpenAI/DeepSeek 官方通道的独立实测仍未执行，标注为 PARTIAL/NOT RUN，不阻塞 1B 规划，但 1B 前建议补测一次官方通道。

## 实测记录（2026-08-15，人工通道）

- Provider: bigmodel / GLM-5.2 / `https://open.bigmodel.cn/api/coding/paas/v4`（密钥不记录）
- 注意：`https://open.bigmodel.cn/api/paas/v4` 报 429 code 1113（账户余额不足）；改用 coding 套餐端点后成功。后端正确把上游错误转成脱敏 `RUN_ERROR`，未泄露密钥。
- 文本流式：PASS（51 个 TEXT_MESSAGE_CONTENT 增量）
- 工具调用渲染：PASS —— `query_quote("600519")` 返回真实行情（1341.99 元 / -0.98% / PE-TTM 20.6）并流式总结
- 运行中状态：PASS —— 输入禁用、发送隐藏、停止按钮可见
- Stop 行为：PASS —— 点击后输入恢复可用；同线程再次发起返回 200（coordinator 已释放，无 THREAD_BUSY 残留）
- 浏览器控制台/网络错误：无（HTTP 全 200）
- 流内 RUN_ERROR 页面可见性：修复后 PASS（传输层 tee 流扫描 RUN_ERROR → 页面错误条），并有单测锁定

## 评审修复记录（2026-08-15 code review）

1. **密钥隔离**：`RuntimeHandle` 不再保存 `model` 引用（运行期间仅 Graph 内部持有模型，符合 spec 第 231 行）；新增真实 `ChatOpenAI` 路径的 handle/snapshot 泄漏扫描测试。
2. **原子性（Critical）**：resume（校验→重建→转 running→清 pending）与 steer-away（校验→关闭旧句柄→建新句柄）全部收敛到 `RunCoordinator` 的单锁方法 `acquire_resume` / `acquire_steer_away`；新增并发 resume 测试断言恰好一个 200、一个 409、`build_chat_model` 只调用一次。
3. **请求形状失败关闭**：`_classify` 按 spec 校验 —— start 恰好一条 user message；纯 resume `messages=[]`；steer-away 全量 cancelled + 恰好一条 user message；混合/畸形 → 400 `INVALID_REQUEST_SHAPE`。`RUN_CONFIG_MISMATCH` 改为 **409**（spec 第 643/736 行）。均有测试锁定。
4. **标准工具事件与前端渲染**：桥接层从 `TOOL_CALL_START.rawEvent` 合成标准 `TOOL_CALL_ARGS` 事件；垂直切片测试恢复要求该事件；`AgentThread` 注册 `tools.Fallback` 兜底渲染工具名/参数/结果。
5. **终态错误可见 + 运行态验收**：传输层扫描 `RUN_ERROR` 并路由到页面错误条（含单测）；新增 `AgentThread.test.tsx` 用 `useExternalStoreRuntime` 锁定"空闲→输入可用/发送可见"与"运行→输入禁用/停止可用"。
6. **基线同步**：plan/spec 的依赖锁定已更新为实际基线（`langgraph==1.2.11`；移除 `langchain-mcp-adapters` 并注明原因）；本验证文档对 provider 实测改为 PARTIAL/NOT RUN 的真实标注。

## 与计划的偏差（实现期发现并记录）

1. **依赖锁定冲突**：`langchain==1.3.15` 要求 `langgraph>=1.2.11`，与计划锁定的 `1.2.9` 冲突 → 升至 **1.2.11**（经用户确认）。
2. **mootdx / langchain-mcp-adapters 互斥**：mootdx 全版本要求 `httpx<0.26`，而 mcp≥1.9.2 要求 `httpx>=0.27` → 1A 移除 `langchain-mcp-adapters`（1A 未用 MCP），1C 引入 MCP 时再评估拆分（经用户确认）。
3. **锁定版本事件形状**：`CustomEvent.value` 为 JSON 字符串（非 dict）；工具参数内嵌在 `TOOL_CALL_START.rawEvent`，无独立 `TOOL_CALL_ARGS` 事件（桥接层已合成标准事件）。
4. **@ag-ui/client 去重**：`@assistant-ui/react-ag-ui@0.0.54` 内嵌 0.0.57 与顶层 0.0.58 类型冲突 → npm `overrides` 强制统一 0.0.58。
5. **测试布局**：`tests/agent/` 不放 `__init__.py`（会以 `agent` 包名遮蔽 `backend/agent`），改为添加 `tests/__init__.py`。
6. **Starlette 断连测试**：ASGI scope 声明 `spec_version 2.4` 时 Starlette 不监听断连，断连取消测试使用默认 spec_version。
