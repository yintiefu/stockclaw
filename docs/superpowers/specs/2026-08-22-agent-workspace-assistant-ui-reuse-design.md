# Agent 工作台聊天面复用 assistant-ui 官方 demo 组件设计

**日期：** 2026-08-22

**状态：** 设计已获用户批准，待实施规划

**上游设计：** `docs/superpowers/specs/2026-08-17-langchain-agent-workspace-1d-design.md`（1D 已交付，本设计只动前端聊天面）

**参考实现：** `/vol2/1000/code/assistant-ui-demo`（assistant-ui + LangChain 官方 demo，Next.js 16 + LangGraph dev server）

## 1. 背景与目标

官方 demo 的前端由两层构成：

1. **组件层**——`components/assistant-ui/*`（shadcn-registry 风格复制件：thread、markdown-text、tool-fallback、tool-group、reasoning 等）与 `components/ui/*`（`@base-ui/react` 基件）。这层只消费 `AssistantRuntimeProvider` 的标准 runtime context，**与运行时实现解耦**。
2. **运行时层**——`useStreamRuntime`（`@assistant-ui/react-langchain`）+ LangGraph Agent Server 专用 REST/SSE 协议 + Next.js catch-all 代理。

当前工作台的聊天面是手写的极简 `AgentThread.tsx`（187 行，仅 MessagePrimitive.Content 裸文本 + 简单工具兜底），与 demo 的完成度差距大。

**本设计把聊天面替换为 demo 组件层的整套复制件**，获得：GFM markdown 渲染（含代码块语言头 + 复制按钮）、工具调用折叠面板（状态图标 / 用时 / 参数 / 结果 / 审批流 UI）、多工具调用分组、思考过程披露（流式期间自动展开 + 底部跟随）、滚动到底按钮、消息操作栏（复制 / 导出 Markdown）、编辑态 composer、分支选择器、欢迎页——观感与官方 demo 一致。

**已确认的三个用户决策：**

- 采用方案 1：只复用组件层，保留 `useAgUiRuntime` + FastAPI AG-UI 后端。
- 聊天内容区沿用 demo 默认的 44rem 居中（`--thread-max-width`）。
- 用户可见文案中文化（repo 惯例：UI 文案中文）。

### 为什么不迁运行时层（方案 2）

- LangGraph Agent Server 协议与自研 AG-UI 传输不兼容，切换等于重写 `runs.py` / `router.py` 的流式与线程权威存储，1B–1D 的治理、审批、产物事件需全部按 LangGraph 流模式重新对接。
- 方案 1 不锁未来路径：后端已用 LangChain 1.x `create_agent`（内部即 StateGraph），扩展 LangGraph 能力（持久化 checkpointer、子图、更多中断）在图内部进行、经 AG-UI 照常流出；若将来确需 LangGraph Platform 的 time-travel/fork，再迁传输层，届时组件层零改动（`useStreamRuntime` 接线本身只有约 50 行，见 demo `app/assistant.tsx`）。

## 2. 产品边界

遵守 `VISION.md`：本设计纯前端 UI 复用，不改变任何数据端点与 AI 工具的输出语义；不新增买卖建议类 UI。组件层带来的消息操作（复制 / 导出 Markdown）作用于会话内容本身，不产生新的投研结论。

## 3. 明确不做

- 不迁 `useStreamRuntime` / LangGraph Agent Server 协议，不引入 Next.js / `@assistant-ui/next`。
- 不动后端任何代码；不动 `runtime.tsx` / `history.ts` / `approval.ts` / `workspace.ts` 等运行时与状态层。
- 不改线程列表（`AgentThreadList`）、Inspector、设置抽屉、能力管理（1C–1D 资产原样）。
- 不做附件上传后端接线（demo 的 attachment UI 同样没有后端 adapter）。
- 不引入 demo 的 zinc/oklch 主题、Inter/IBM Plex 字体、oxlint/oxfmt；沿用项目暖橙 HSL 主题与现有字体。
- 不把 `Agent.tsx` 的 props 契约改掉——工作台页面零改动。

## 4. 已验证的技术事实（探索结论）

| 事实 | 结论 |
|---|---|
| demo 组件依赖 `@assistant-ui/react` 0.15.16 的新 API（`MessagePrimitive.GroupedParts`、`respondToApproval`、`ActionBarMorePrimitive`、`AuiIf`、`groupPartByType`、`SuggestionPrimitive`、`useScrollLock`、`useToolCallElapsed`） | 本地 0.15.14 缺 `GroupedParts` 与 `respondToApproval`，**必须升到 0.15.16** |
| `react-ag-ui@0.0.54` peerDependencies 仅要求 react | 升级 `@assistant-ui/react` 不与之冲突 |
| `@assistant-ui/react-markdown@0.14.12` 已安装，`styles/dot.css` 存在于包内 | markdown-text.tsx 可原样复制 |
| `@base-ui/react@1.7.0`、cva、clsx、tailwind-merge、lucide-react、remark-gfm、tw-animate-css 均已安装；`lib/utils.ts` 已有等价 `cn()` | 只需新增 `tw-shimmer@^0.4.12` 一个依赖 |
| 项目 `index.css` 已是 Tailwind v4 CSS-first + shadcn 风格 HSL 变量 | 缺 `--popover` / `--popover-foreground` / `--ring` / `--input`，需补 |
| demo `globals.css` 的关键移植项 | `@import "tw-shimmer"`、`@custom-variant data-open/data-closed`、collapsible-down/up keyframes（引用 `--collapsible-panel-height`） |
| `frontend/src/components/ui/` 现有文件为业务组件（AskAiButton 等），无同名基件 | button/tooltip/collapsible/dialog/skeleton/avatar 直接落入无冲突 |
| demo thread.tsx 的 44rem 居中由组件内 `--thread-max-width` CSS 变量实现 | 用户已确认沿用，无需改 |

## 5. 架构

```
AgentRuntimeProvider（useAgUiRuntime + AgentHttpAgent → POST /api/agent/run SSE，不变）
        │  标准 runtime context（AssistantRuntimeProvider）
        ▼
<Thread /> ← demo thread.tsx 复制件（src/components/assistant-ui/thread.tsx）
   ├─ MarkdownText（react-markdown + remark-gfm + dot.css + img 安全覆写）
   ├─ ToolFallback + ToolGroup（折叠面板、状态图标、用时、审批流 UI）
   ├─ Reasoning（思考披露，流式自动展开）
   ├─ File / Image / Attachment（无 adapter 时自隐藏，前瞻性保留）
   ├─ Composer ← 经新增 slot 覆写：converging 禁用态 + 待审批时换 SteerAwayComposer
   └─ FooterExtra ← 新增 slot：statusNote（agent-status-area）+ RetryAction
        ▲
AgentThread.tsx 重写为薄适配层 —— 对外 props 契约不变，Agent.tsx 零改动
```

组件层的其余消费者（ThreadList 等）不在 demo 中（demo 单线程），工作台继续用自研 `AgentThreadList`。

## 6. 文件清单

### 新增（从 `/vol2/1000/code/assistant-ui-demo` 复制到 `frontend/src/components/`）

- `assistant-ui/`：`thread.tsx`、`markdown-text.tsx`、`tool-fallback.tsx`、`tool-group.tsx`、`reasoning.tsx`、`tooltip-icon-button.tsx`、`follow-up-suggestions.tsx`、`attachment.tsx`、`file.tsx`、`image.tsx`
- `ui/`：`button.tsx`、`tooltip.tsx`、`collapsible.tsx`、`dialog.tsx`、`skeleton.tsx`、`avatar.tsx`

复制优先逐字保留（含 `aui-*` data-slot 类名与注释），适配仅限第 7 节列出的点。demo 中的 deprecated 兼容导出（`reasoning.tsx` 的 `ReasoningGroup`、`tool-group.tsx` 的 `ToolGroup`）原样保留——它们是 `export`，不会触发 `noUnusedLocals`，保留可降低后续 registry 升级的合并成本；仅清理复制件中真正未被引用的 import（`noUnusedLocals` 会在 build 时强制）。

### 修改

- `frontend/package.json`：`@assistant-ui/react` 精确 pin `0.15.16`；新增 `tw-shimmer@^0.4.12`
- `frontend/src/index.css`：
  - `@import 'tw-shimmer'`
  - 移植 `@custom-variant data-open` / `data-closed`（base-ui 组件的 data 属性变体）
  - 移植 collapsible-down/up `@keyframes`（fallback 链 `--radix-collapsible-content-height, --collapsible-panel-height, auto`）
  - `@theme inline` 新增 6 个颜色映射（Tailwind v4 只为 `--color-*` 命名空间生成工具类，`:root` 裸变量不会让 `bg-popover` 等类生效）：`--color-popover: hsl(var(--popover))`、`--color-popover-foreground: hsl(var(--popover-foreground))`、`--color-ring: hsl(var(--ring))`、`--color-input: hsl(var(--input))`、`--color-secondary: hsl(var(--secondary))`、`--color-secondary-foreground: hsl(var(--secondary-foreground))`
  - `:root` / `.light` 补原始变量（具体取值：暗色 `--popover: 222 40% 9%`、`--popover-foreground: 210 30% 92%`、`--ring: 210 30% 22%`、`--input: 210 30% 18%`、`--secondary: 220 28% 15%`、`--secondary-foreground: 210 30% 92%`；亮色 `--popover: 0 0% 100%`、`--popover-foreground: 222 40% 12%`、`--ring: 214 20% 84%`、`--input: 214 20% 80%`、`--secondary: 210 20% 92%`、`--secondary-foreground: 222 40% 12%`——popover 对齐 card 档、secondary 对齐 muted 档、ring/input 对齐 border 档，保持暖橙体系）
- `frontend/src/components/agent/AgentThread.tsx`：重写为适配层（见 7.3）

## 7. 关键适配点

### 7.1 thread.tsx（复制后）

1. `ThreadComponents` 类型增加两个可选 slot（缺省回落 demo 原实现，保持复制件的独立可用性）：
   - `Composer?: ComponentType`——工作台传入带禁用态 / SteerAway 换装的 composer
   - `FooterExtra?: ComponentType`——渲染在 ViewportFooter 内 Composer 之前的区块
2. 能力守卫：`ActionBarPrimitive.Reload` / `.Edit` 与 Dictation 相关节点包 `AuiIf condition={s => s.thread.capabilities.reload / edit / dictation}`；AG-UI 运行时不具备这些能力时按钮自动隐藏，具备时自然出现。
3. 文案中文化：composer placeholder（「输入投研问题」）、Welcome 欢迎语（投研语境）、tooltips（复制 / 刷新 / 编辑 / 导出 Markdown / 滚动到底 / 发送 / 停止）、ToolFallback 的「调用工具 / 已取消」、ToolGroup 的「N 次工具调用」、Reasoning 的「思考过程」。`aui-*` 类名与 data-slot 保持不变。
4. **可访问名契约（测试依赖，不可漂移）**：现有 vitest 与 e2e 依赖以下可访问名——输入框 `aria-label="Agent 消息"`（e2e 33/41/305 行、vitest 49/56/74 行）；发送按钮 `title="发送"`（e2e 36 行）；停止按钮 `title="停止"`（e2e 155 行）；SteerAwayComposer 的「转向新问题」与「发送新问题（取消当前审批）」；「在 Inspector 打开」与「重试本轮」按钮名。因此：`TooltipIconButton` 必须把 `title={tooltip}` 透传给底层 `<Button>`（demo 版只有 sr-only span，无原生 title）；`ComposerPrimitive.Input` 的 aria-label 用「Agent 消息」而非 demo 的「Message input」；Cancel 按钮同时写 `aria-label` 与 `title`（demo 版 Cancel 走 `Button aria-label`，无 title）。
5. **button.tsx secondary 变体的非法 color-mix**：demo 原文 `hover:bg-[color-mix(in_oklch,var(--secondary),var(--foreground)_5%)]` 把无单位 HSL 三元组直接传入 `color-mix()`，在本项目变量体系下是非法 CSS。改为标准工具类 `hover:bg-secondary/80`；其余变体逐字保留。
6. 附件 UI 验证：确认无 attachment adapter 时 `ComposerPrimitive.AddAttachment` 自隐藏；若不自隐藏，则从 Composer 中移除 `ComposerAddAttachment` / `ComposerAttachments` / `AttachmentDropzone` 三处引用（attachment.tsx 仍复制保留，接 adapter 时再启用）。
7. 44rem 居中为 demo 默认，不改。

### 7.2 markdown-text.tsx（复制后）

增加 `img` 组件覆写：远程图片不自动加载，渲染为占位块并复用现有 `agent-blocked-image` 工具类——与 1D「markdown Artifact 零外联请求」的隐私语义对齐（会话 markdown 与 Artifact 同等对待）。其余逐字保留。

### 7.3 AgentThread.tsx（适配层重写）

对外 props 契约不变（`activeThread` / `onRetry` / `statusNote` / `composerDisabled` / `pendingApproval` / `onOpenArtifact`），`Agent.tsx` 零改动：

- 内部渲染 demo `<Thread components={...}>`
- `ToolFallback` slot：demo ToolFallback + 现有 `artifactIdFromResult` 解析，`create_artifact` 结果附「在 Inspector 打开」按钮。**按钮渲染在折叠面板外部**（ToolFallback 折叠条下方独立一行，不随折叠隐藏）——demo ToolFallback 对 complete 状态默认折叠，e2e（83-85 / 362-364 行）直接按角色名点击该按钮，不先展开
- `Composer` slot：demo Composer 结构 + `composerDisabled` 透传到 Input/Send；`pendingApproval` 时渲染 `SteerAwayComposer`（原逻辑）
- `FooterExtra` slot：statusNote 区（保留 `data-testid="agent-status-area"`）+ RetryAction（failed/cancelled/interrupted 最新 run 可重试，原逻辑迁移）
- 外层保留 `data-testid="agent-thread-region"` 由父级容器承担（现状即如此，不改）

## 8. 数据流与错误处理（不变）

- AG-UI SSE 解析（409 冲突 / 503 MCP 不可用 / RUN_ERROR / 自定义事件白名单）与 `AgentHistoryController` 收敛逻辑原样。
- 流内消息错误经 demo `MessageError`（`ErrorPrimitive`）渲染为错误条。
- 审批流继续走现有 `ApprovalPanel`（Inspector / 移动端位置不变）。**验证点**：demo ToolFallback 自带 Allow/Deny 审批按钮，其渲染取决于 tool-call part 上是否出现 `interrupt` / `approval` 数据（`offersInterruptAction` 守卫）。当前 history 转换器只写消息级 `metadata.custom.agui.interrupts`，理论上 part 级为空、按钮不出现；实施时以真实中断流截图/测试确认——若出现与 ApprovalPanel 重复的 Allow/Deny，在适配层的 ToolFallback 中去掉 `ToolFallback.Approval` 渲染（复制件本身不动）。

## 9. 测试策略

1. **vitest**：更新 `AgentThread.test.tsx` 及受影响组件套件——新 DOM 结构（markdown 元素、折叠工具面板、shimmer 类）；`src/test/setup.ts` 已 stub ResizeObserver，base-ui Tooltip/Dialog 若需再补 stub。按钮数量类断言（如 `getAllByRole("button")).toHaveLength(1)`）改为语义断言（`getByTitle("停止")` / `queryByTitle("发送")` 为空）——demo Thread 会引入滚动到底等新按钮，数量断言必然误报。
2. **构建**：`npm run build`（tsc strict + noUnusedLocals / noUnusedParameters）必须零错误——复制件中的未用导入需清理。
3. **e2e**：`agent-workspace.spec.ts` 全量 20 条回归——流式、工具文本、artifact 打开（依赖「在 Inspector 打开」按钮）、审批、Stop/Retry、409 收敛、390/1280/1440 × 双主题截图无横向溢出；必要时仅调整选择器。
4. **视觉抽查**：dev-host 配方（createRequire frontend + headless chromium）对空态 / 欢迎页 / 工具折叠态 / markdown 消息截图，双主题。

## 10. 风险与对策

| 风险 | 对策 |
|---|---|
| `@assistant-ui/react` 0.15.16 × `react-ag-ui` 0.0.54 兼容性（`approval.ts` 的 interrupt hooks 曾标记 version-sensitive） | `approval.contract` vitest + e2e 审批用例（approve once/session、reject、steer-away）全量覆盖；若 break 则评估升 react-ag-ui 或在适配层绕开受影响 API |
| base-ui 组件在 jsdom 的行为 | 现有 setup 已 stub ResizeObserver；按需补 |
| demo 组件的英文文案遗漏 | 中文化清单以 `grep` 扫描复制件内的用户可见字符串收尾核对 |
| 0.15.16 与 0.15.14 之间的行为差异影响现有页面 | 全量 vitest（现有 54+ 用例）+ e2e 回归 |

## 11. 实施顺序（供 writing-plans 细化）

依赖升级 → 复制 `ui/` 基件 → `index.css` 移植 → 复制 `assistant-ui/` 组件并中文化 → thread.tsx slot 适配 → AgentThread.tsx 改造 → vitest 更新 → build + e2e + 截图回归。
