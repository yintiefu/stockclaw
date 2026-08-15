# LangChain Agent Workspace 1B Verification

日期：2026-08-15 · 分支：`feature/langchain-agent-workspace-1a`（按用户要求直接在当前分支执行 1B）

## 自动化结果（Task 8 Step 2）

- 后端离线套件：`pytest -m "not live"` → **194 passed, 12 deselected**（含 1A 全部回归）
  - `tests/agent/test_stores.py`（原子写/损坏隔离/revision CAS/对账）12 项
  - `tests/agent/test_thread_api.py`（CRUD/冲突/busy 删除/lifespan 对账）12 项
  - `tests/agent/test_run_persistence.py`（准入/边界持久化/取消/重试/端到端生命周期）18 项
- 前端 Node 遗留套件：`npm test` → **16 passed, 0 failed**
- 前端 Vitest：`npx vitest run` → **5 files / 26 tests passed**
- 生产构建：`npm run build`（tsc -b + vite）→ **通过**

## 检查项

- Atomic store/corruption/reconciliation suite: **PASS**
- Revision/duplicate/message-conflict suite: **PASS**
- Partial/cancel/retry lifecycle: **PASS**（离线全绿）
- Refresh and backend-restart recovery: **PASS**
- Secret persistence scan: **PASS**
- Legacy backend/frontend regression: **PASS**
- Browser desktop/mobile acceptance: **PASS（1 项未捕获，见下）**

## 浏览器验收（CDP 127.0.0.1:16002，真实 BigModel GLM-5.2 通道）

实测通过：

- 新建/重命名/切换/删除（删除同时清掉对应 run 文件）
- 刷新恢复消息与选中线程；`localStorage` 仅含既有键，无线程/消息历史、无模型密钥
- 多轮 live 会话（含 9 次工具调用的长会话）正常流式、正常入库（rev 41→64）
- 后端运行中途被 kill -9 → 重启对账将该 run 标记 `interrupted`，历史完整，页面显示中文中断提示与「重试本轮」
- 点击重试 → 新产品 run（`retry_of` 指向中断 run），原问题不重复追加，完整回答落盘
- 损坏 JSON（`{broken`）→ 列表返回仅文件名的恢复提示，健康线程可用
- 移动端 390px 无横向溢出，输入区/线程控件可用
- `~/.vibe-research/agent/` 全目录 grep 无模型 API key

未捕获（如实标注）：

- **Stop 按钮中途停止的部分输出持久化**：两次尝试点击停止时运行恰好已完成，未能在 live 流中捕获；
  该路径由离线测试覆盖（`test_disconnect_persists_partial_then_excludes_from_next_input`、
  `test_cancelled_stream_shields_partial_persistence_before_reraising`），未在 live 复验。

## 验收期间发现并修复的问题

1. `@ag-ui/client` 要求 SSE 首事件必须是 `RUN_STARTED` → 准入 revision 事件改为在首事件后补发。
2. assistant-ui 发送其内部线程 ID → `requestInit` 以服务端线程 ID 覆盖（新增回归测试）。
3. 409 权威重载后 runtime 本地消息不重置 → 页面引入 session epoch 强制重建并重新水合。
4. assistant-ui 水合/流式后消息顺序与 ID 形态（assistant 前置、tool 消息用 call ID、重复条目）与服务端不一致 →
   服务端前缀校验改为「去重 + 按 ID/tool 引用匹配 + 内容一致」，forked head 仍失败关闭。

## 决策

Proceed to 1C：所有自动化项通过；验收测试数据已从 `~/.vibe-research/agent/` 清理，
无密钥或用户数据出现在隔离的 Agent 数据目录之外。注意：live Stop 部分输出持久化建议在 1C 开发时顺手复验一次。
