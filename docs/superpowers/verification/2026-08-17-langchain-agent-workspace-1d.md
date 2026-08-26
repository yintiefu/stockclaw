# 1D 端到端验证记录 · LangChain Agent Workspace

- 计划：`docs/superpowers/plans/2026-08-17-langchain-agent-workspace-1d.md`（Task 20）
- 验证时间：2026-08-21 22:40 – 23:07 (+08:00)
- 验证分支：`feature/langchain-agent-workspace-1a`
- 结论：**PASS**（全部非外部门禁通过；Chromium 已安装，无缺外部前置）

## Step 1 · 干净临时环境依赖验证

```
VR_1D_VERIFY=$(mktemp -d) && python3 -m venv "$VR_1D_VERIFY/venv"
PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
  "$VR_1D_VERIFY/venv/bin/pip" install -r backend/requirements.txt
"$VR_1D_VERIFY/venv/bin/pip" check
"$VR_1D_VERIFY/venv/bin/python" -c 'from importlib.metadata import version; print(version("commonmark"))'
```

结果：安装成功；`pip check` → `No broken requirements found.`；`commonmark 0.9.1`（锁定版本）。
（本开发机 pypi.org 不可达，按计划使用清华镜像。）

## Step 2 · 完整门禁（2026-08-21 22:56 – 23:04）

| 门禁 | 命令 | 结果 |
|---|---|---|
| 后端全量 | `cd backend && .venv/bin/pytest -m "not live"` | **530 passed**, 15 deselected, 238 warnings, 66.4s |
| 前端页面冒烟 | `npm test` | **16/16** |
| 组件/逻辑单测 | `npm run test:unit` | **172/172** |
| 构建 | `npm run build` | ✓ built in 20.4s |
| 浏览器套件 | `npm run test:e2e` | **20/20**，2.6m（提交前连续两次 20/20，2.7m / 2.8m） |
| 空白检查 | `git diff --check` | clean |

如实记录的既有警告（非本切片引入，未消项）：

- 后端 238 条 warning：pydantic 序列化提示 + 两个既有测试的 asyncio 标记误用
  （`test_tool_executor.py` / `test_tool_registry.py`，1A 之前已存在）。
- `vite build` chunk-size 警告（rollup 默认 500KB 提示，产物功能不受影响）。

## Step 3 · 浏览器证据检查

截图（本轮 `npm run test:e2e` 生成，Playwright `outputPath`）：

```
frontend/test-results/agent-workspace-响应式与主题-截图与布局：desktop-1440-dark/desktop-1440-dark.png
frontend/test-results/agent-workspace-响应式与主题-截图与布局：desktop-1440-light/desktop-1440-light.png
frontend/test-results/agent-workspace-响应式与主题-截图与布局：desktop-1280-dark/desktop-1280-dark.png
frontend/test-results/agent-workspace-响应式与主题-截图与布局：desktop-1280-light/desktop-1280-light.png
frontend/test-results/agent-workspace-响应式与主题-截图与布局：mobile-390-dark/mobile-390-dark.png
frontend/test-results/agent-workspace-响应式与主题-截图与布局：mobile-390-light/mobile-390-light.png
```

人工 + 视觉抽查（1280-dark / 390-dark / 1440-light）确认：

- **1280 关键视口**：全局侧栏 + 工作台 `240 / minmax(480,1fr) / 320` 三轨并存，无重叠、
  无裁切，Composer 完整可见。
- **390 移动**：仅单列聊天区；无横向溢出（测试另有 `scrollWidth - clientWidth ≤ 0` 断言）、
  Composer 可见、无控件遮挡；线程/设置抽屉互斥、焦点进入、Esc 关闭断言全部通过。
- **1440 light**：三栏正常，无低对比不可读区域。

每个截图测试同时断言：无水平溢出、Composer 可见、桌面无对话框 / 移动抽屉焦点与 Esc 语义。
「Markdown Artifact 不触发任何远程请求」测试通过（应用自身 Google Fonts 之外零外部请求；
字体域在 allowlist 注明与 Artifact 内容无关）。

## Step 4 · 安全与不变量扫描

| 扫描 | 结果 |
|---|---|
| fixture 密钥（`e2e-key` / `fixture.invalid`）进入生产代码 | 无（仅存在于 `backend/tests/agent_e2e_app.py` 与 `frontend/e2e/`） |
| e2e 数据根与真实用户根重叠 | `agent_e2e_app.py::_resolve_data_root` 双向 `is_relative_to` 守卫，重叠即拒绝启动 |
| 可执行 Artifact MIME | 无：类型白名单 `markdown/table/json/sources`（`artifacts.py:277`） |
| Source 评分/排名/推荐词 | 无：`provenance.py` / `models.py` 仅含“绝不评分/排序/验证”的约束注释与实现 |
| 1D 新代码裸 `localStorage` | 无：`src/lib/agent/**` 全部经包装（`llm.ts`/`api.ts`/`storage.ts` 为 1A 之前既有的访问层模块本身） |
| CustomEvent 白名单 | 恰好四个：`thread.revision.updated` / `budget.updated` / `artifact.created` / `sources.updated`（`protocol.py:118-120,124`） |
| `git status --short` | 无真实用户数据路径；未跟踪目录（`.superpowers/ .vr-dev/ .zcode/ AGENTS.md scripts/ docs/superpowers/plans/2026-08-16-…1c.md`）零跟踪文件修改 |

字节上限与关闭语义证据（后端 530 测试内）：

- `ARTIFACT_MAX_BYTES = 1_048_576`（`models.py:285`），`test_artifacts.py:109-111`
  精确边界（恰好 1MiB 通过编码）。
- `test_tool_executor.py::test_begin_shutdown_rejects_new_admissions_immediately`、
  `test_tool_executor.py::test_shutdown_returns_within_bound_before_blocked_workers_release`。

## Step 5 · 缺陷修复与回归（验证过程中发现并修复）

验证期 e2e 暴露两处真实缺陷，已随 Task 19 修复并附回归测试：

1. **断连取消落盘竞态（后端）**：取消展开中 journal 落盘失败且句柄已摘除时，run 文档
   永远停留 `running`。修复：`runs.py` `cancel_run` 落盘失败回退直接终态写 +
   `ensure_run_terminal` 幂等兜底（`router.py` finally 调用）。回归：
   `test_agent_1d_integration.py::test_cancel_persistence_failure_falls_back_to_terminal_write`、
   `::test_ensure_run_terminal_recovers_stuck_running_document`。
2. **Stop 早于响应头的收敛丢失（前端）**：Stop 打在响应头到达前时 fetch 直接抛
   AbortError，流扫描器从未启动，其 finally 上的收敛钩子永不触发，UI 永远停在 running。
   修复：`runtime.tsx` transportFetch 在 AbortError 分支补发 `onStreamEnd`。回归：
   `runtime.test.tsx::notifies onStreamEnd when Stop aborts before response headers arrive`。

另修两处测试自身的竞态/歧义（重试点击抢在权威 last_run 更新前、`getByText` 命中
`<option>`、模态抽屉下主按钮不可点、应用字体域误报），后端行为正确未改。

1A–1C 回归状态：上述 530 项后端测试覆盖 1A–1C 全部 slice 测试文件
（policy / governance / executor / artifacts / provenance / mcp / resume / router 等），
本轮全绿。

## 提交与切片关闭

Task 19/20 收尾提交：`37771f4`（test(agent): cover workspace in playwright）。
分支含 1A–1D 切片提交共 90 个（`git log main..HEAD`），关键收尾：
`2d761ea`（wire models）→ `c4987de`（policy）→ `38bbec4`（run lifecycle）→
`e08e3ea`（artifacts）→ `49a3ec6`（provenance）→ `b72aa8a`（workspace sync）→
`02d6270`（workspace shell）→ `8cc28b1`（responsive settings）→ `37771f4`（playwright）。

## 1D Exit Checklist 对照

- Slice 1（Policy / 预算 / 截止 / 有界执行 / 上下文 / usage / 分页 / budget.updated）：PASS（530 后端测试）
- Slice 2（四类不可变 Artifact / 受治创建 / Source 溯源 / 线程域 REST / 删除恢复 / 事件顺序）：PASS
- Slice 3（三栏桌面 / 移动抽屉 / Inspector / Settings / 安全查看器 / 事件收敛 / Playwright）：PASS（20/20）
- 第 9 次模型调用与第 17 次实际工具调用在 Provider/handler 前被拦、错误/超时/取消后 reservation 计数仍权威：PASS（governance 测试）
- 执行器队列不超准入容量、超时/取消保留槽位、lifespan 关闭按时返回：PASS（test_tool_executor.py）
- Resume 保留原 Policy 与计数、retry/steer 取新快照、损坏 Policy 不阻塞合法 resume：PASS（resume/policy 测试）
- 上下文裁剪保留完整轮次/工具对/当前输入/中立提示/最新 Skill、强制溢出在 reservation 前失败：PASS（context_governance 测试）
- Artifact 规范字节/路径/父链/描述符/补偿/墓碑/启动恢复全部 fail-closed：PASS（artifacts/artifact_api 测试）
- Sources 区分执行记录与未验证 URL、零网络访问、无评分/排名/推荐：PASS（provenance 测试 + Step 4 扫描）
- 协议桥仅放行 1D Graph CustomEvents、终局顺序 revision→budget→sources→标准终态：PASS（protocol_bridge / governance_order 测试）
- 1440/1280/390 双主题无重叠/溢出/裁切/隐藏 Composer/焦点破损：PASS（截图 + 断言）
- 既有 `/api/chat`、辩论、反思、CLI、东财串行节流、revision/duplicate/resume/retry/cancel/approval/Skill/MCP/密钥隔离契约保持绿：PASS（530 项含既有非 agent 测试）
