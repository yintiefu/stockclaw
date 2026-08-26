# Agent 工作台复用 assistant-ui Demo 组件实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 Agent 工作台的手写聊天面替换为官方 demo(`/vol2/1000/code/assistant-ui-demo`)的整套 assistant-ui 组件,保留 AG-UI 运行时与 FastAPI 后端。

**Architecture:** demo 的 registry 组件(runtime 无关)落入 `frontend/src/components/assistant-ui/` 与 `frontend/src/components/ui/`;`AgentThread.tsx` 重写为薄适配层,通过 thread.tsx 新增的 `Composer` / `FooterExtra` slot 挂接现有工作台语义(禁用态、SteerAway、statusNote、重试、产物按钮)。`Agent.tsx` 与后端零改动。

**Tech Stack:** React 19 + Vite 6 + Tailwind v4(CSS-first)+ `@assistant-ui/react@0.15.16` + `@base-ui/react@1.7.0` + vitest + Playwright。

**设计文档:** `docs/superpowers/specs/2026-08-22-agent-workspace-assistant-ui-reuse-design.md`(已经两轮外部评审闭环)

**关键背景(执行者必读):**

- 所有命令默认在 `/vol2/1000/code/stockclaw/frontend` 下执行(除 git 命令在仓库根)。本机 npx 必须在 frontend 目录下运行,否则会拉到假的 `tsc` 包。
- demo 源目录:`/vol2/1000/code/assistant-ui-demo`(下文简写 `$DEMO`)。
- 本机 pypi 不可达但 npm 正常;Playwright chromium 已缓存在 `~/.cache/ms-playwright`。
- **可访问名契约(e2e/vitest 依赖,不可漂移)**:输入框 `aria-label="Agent 消息"`;发送 `title="发送"`;停止 `title="停止"`;SteerAway「转向新问题」「发送新问题(取消当前审批)」;「在 Inspector 打开」「重试本轮」。
- 现有测试基线:`npx vitest run` 全绿;`npm run test:e2e` 20 条全绿。任何任务完成后不得留下红测试。

**文件结构总览:**

```
frontend/src/components/ui/           ← 新增 6 个 base-ui 基件(与现有业务组件无同名冲突)
  avatar.tsx button.tsx collapsible.tsx dialog.tsx skeleton.tsx tooltip.tsx
frontend/src/components/assistant-ui/ ← 新增 10 个 demo registry 组件
  thread.tsx markdown-text.tsx tool-fallback.tsx tool-group.tsx reasoning.tsx
  tooltip-icon-button.tsx follow-up-suggestions.tsx attachment.tsx file.tsx image.tsx
frontend/src/index.css                ← 修改:tw-shimmer、custom variants、keyframes、新色板变量
frontend/src/components/agent/AgentThread.tsx        ← 重写为适配层
frontend/src/components/agent/AgentThread.test.tsx   ← 重写(测试先行)
```

---

### Task 1: 依赖升级

**Files:**
- Modify: `frontend/package.json`(经 npm 自动修改)

- [ ] **Step 1: 安装依赖**

```bash
cd /vol2/1000/code/stockclaw/frontend
npm install --save-exact @assistant-ui/react@0.15.16
npm install tw-shimmer@^0.4.12
```

- [ ] **Step 2: 验证版本**

```bash
npm ls @assistant-ui/react tw-shimmer @assistant-ui/react-ag-ui
```

预期:`@assistant-ui/react@0.15.16`、`tw-shimmer@0.4.x`、`@assistant-ui/react-ag-ui@0.0.54`,无 `UNMET PEER`(react-ag-ui 的 peer 只要求 react)。

- [ ] **Step 3: 立即回归运行时层单测(升级可能影响 react-ag-ui 集成)**

```bash
npx vitest run src/lib/agent
```

预期:全部 PASS(含 `approval.contract`)。若有 FAIL,停下分析——0.15.16 与 react-ag-ui 0.0.54 的兼容问题是本计划最大风险,不得带病前进。

- [ ] **Step 4: Commit**

```bash
cd /vol2/1000/code/stockclaw
git add frontend/package.json frontend/package-lock.json
git commit -m "build(frontend): pin @assistant-ui/react 0.15.16 and add tw-shimmer"
```

---

### Task 2: index.css 主题变量与动画

**Files:**
- Modify: `frontend/src/index.css:1-48`(头部)、`:root`(约 174-209 行)、`.light`(约 211-241 行)

- [ ] **Step 1: 头部导入与自定义变体**

在第 2 行 `@import 'tw-animate-css';` 之后加一行:

```css
@import 'tw-shimmer';
```

在第 6 行 `@custom-variant dark ...` 之后加(base-ui 组件的 data 属性变体,demo 组件大量使用):

```css
@custom-variant data-open (&:where([data-state="open"], [data-open]:not([data-open="false"])));
@custom-variant data-closed (&:where([data-state="closed"], [data-closed]:not([data-closed="false"])));
```

- [ ] **Step 2: collapsible keyframes(放在 `@custom-variant dark` 之后、`@theme` 之前)**

说明:`tw-animate-css` 已提供 `--animate-collapsible-down/up`,但其 keyframes 的 fallback 链不含 base-ui 的 `--collapsible-panel-height`;demo 用同名 keyframes 覆盖以加入该变量。**必须照搬**,否则折叠动画落到 `auto` 高度会跳变:

```css
/* 覆盖 tw-animate-css 的同名 keyframes:加入 base-ui 的 --collapsible-panel-height */
@theme inline {
  @keyframes collapsible-down {
    from {
      height: 0;
    }
    to {
      height: var(--radix-collapsible-content-height, var(--collapsible-panel-height, auto));
    }
  }
  @keyframes collapsible-up {
    from {
      height: var(--radix-collapsible-content-height, var(--collapsible-panel-height, auto));
    }
    to {
      height: 0;
    }
  }
}
```

- [ ] **Step 3: `@theme inline` 补 6 个颜色映射(Tailwind v4 只为 `--color-*` 生成工具类)**

在 `--color-card-foreground` 之后插入:

```css
  --color-popover: hsl(var(--popover));
  --color-popover-foreground: hsl(var(--popover-foreground));

  --color-secondary: hsl(var(--secondary));
  --color-secondary-foreground: hsl(var(--secondary-foreground));

  --color-ring: hsl(var(--ring));
  --color-input: hsl(var(--input));
```

同时在 `--radius-*` 一组(约 40-42 行)补两档——demo 复制件大量使用 `rounded-xl`(更多菜单、代码块、气泡),未声明时 Tailwind v4 回落默认 `0.75rem`(12px),会小于本项目 `--radius-lg` 的 16px,造成圆角阶梯倒挂:

```css
  --radius-xl: calc(var(--radius) + 4px);
  --radius-2xl: calc(var(--radius) + 8px);
```

- [ ] **Step 4: `:root`(暗色)补原始变量**

在 `--border: 210 30% 22%;` 行之后插入:

```css
    --popover: 222 40% 9%;             /* 对齐 card 档 */
    --popover-foreground: 210 30% 92%;
    --secondary: 220 28% 15%;          /* 对齐 muted 档 */
    --secondary-foreground: 210 30% 92%;
    --ring: 210 30% 22%;               /* 对齐 border 档 */
    --input: 210 30% 18%;
```

- [ ] **Step 5: `.light` 补原始变量**

在 `.light` 的 `--border: 214 20% 84%;` 行之后插入:

```css
    --popover: 0 0% 100%;
    --popover-foreground: 222 40% 12%;
    --secondary: 210 20% 92%;
    --secondary-foreground: 222 40% 12%;
    --ring: 214 20% 84%;
    --input: 214 20% 80%;
```

- [ ] **Step 6: 构建冒烟**

```bash
cd /vol2/1000/code/stockclaw/frontend && npm run build
```

预期:构建成功(vite 会校验 `@import 'tw-shimmer'` 可解析)。

- [ ] **Step 7: Commit**

```bash
cd /vol2/1000/code/stockclaw
git add frontend/src/index.css
git commit -m "feat(agent): port demo theme vars, shimmer and collapsible animations"
```

---

### Task 3: 复制 ui/ 基件并修复 button 的非法 color-mix

**Files:**
- Create: `frontend/src/components/ui/{avatar,button,collapsible,dialog,skeleton,tooltip}.tsx`

- [ ] **Step 1: 复制 6 个基件**

```bash
cd /vol2/1000/code/stockclaw
for f in avatar button collapsible dialog skeleton tooltip; do
  cp "/vol2/1000/code/assistant-ui-demo/components/ui/$f.tsx" "frontend/src/components/ui/$f.tsx"
done
```

- [ ] **Step 2: 修复 button.tsx secondary 变体**

demo 原文把无单位 HSL 三元组传入 `color-mix()`,在本项目变量体系下是非法 CSS。把 `frontend/src/components/ui/button.tsx` 中 secondary 变体的:

```tsx
        secondary:
          "bg-secondary text-secondary-foreground hover:bg-[color-mix(in_oklch,var(--secondary),var(--foreground)_5%)] aria-expanded:bg-secondary aria-expanded:text-secondary-foreground",
```

改为:

```tsx
        secondary:
          "bg-secondary text-secondary-foreground hover:bg-secondary/80 aria-expanded:bg-secondary aria-expanded:text-secondary-foreground",
```

- [ ] **Step 3: 类型检查**

```bash
cd /vol2/1000/code/stockclaw/frontend && node_modules/.bin/tsc --noEmit
```

预期:零错误。(注:文件里的 `React.ComponentProps<...>` 等类型位置 UMD 引用在项目 tsconfig 下合法,已实测,勿画蛇添足加 import。)

- [ ] **Step 4: Commit**

```bash
cd /vol2/1000/code/stockclaw
git add frontend/src/components/ui
git commit -m "feat(agent): copy demo base-ui primitives, fix secondary color-mix"
```

---

### Task 4: 复制 assistant-ui/ 组件 + 逐文件适配(除 thread.tsx)

**Files:**
- Create: `frontend/src/components/assistant-ui/{thread,markdown-text,tool-fallback,tool-group,reasoning,tooltip-icon-button,follow-up-suggestions,attachment,file,image}.tsx`

- [ ] **Step 1: 复制 10 个组件**

```bash
cd /vol2/1000/code/stockclaw
mkdir -p frontend/src/components/assistant-ui
for f in thread markdown-text tool-fallback tool-group reasoning tooltip-icon-button follow-up-suggestions attachment file image; do
  cp "/vol2/1000/code/assistant-ui-demo/components/assistant-ui/$f.tsx" "frontend/src/components/assistant-ui/$f.tsx"
done
```

(thread.tsx 本任务只复制不改,Task 5 适配。)

- [ ] **Step 2: tooltip-icon-button.tsx —— title 透传(可访问名契约)**

把 `<Button variant="ghost" size="icon" {...rest}` 一处改为(注意 `title` 放 `{...rest}` 之前,调用方显式传 title 时可覆盖):

```tsx
            <Button
              variant="ghost"
              size="icon"
              title={tooltip}
              {...rest}
```

- [ ] **Step 3: markdown-text.tsx —— img 安全覆写 + 中文化**

在 `defaultComponents` 的 `hr` 条目之前插入 img 覆写(远程图片不自动加载,复用现有 `agent-blocked-image` 工具类,对齐 1D 零外联隐私语义):

```tsx
  img: ({ className, alt, ...props }) => (
    <span
      className={cn("agent-blocked-image", className)}
      title={typeof alt === "string" && alt ? alt : "远程图片未加载"}
      {...props}
    >
      {typeof alt === "string" && alt ? alt : "远程图片未加载"}
    </span>
  ),
```

同时把 `CodeHeader` 里的 `<TooltipIconButton tooltip="Copy" onClick={onCopy}>` 改为 `tooltip="复制"`。

- [ ] **Step 4: tool-fallback.tsx 中文化(4 处)**

1. `const label = isCancelled ? "Cancelled tool" : "Used tool";` → `const label = isCancelled ? "已取消的工具" : "调用工具";`(shimmer 处用的也是同一变量,无需另改)
2. `Result:` 段落文本 → `执行结果:`
3. `const headerText = isCancelled ? "Cancelled reason:" : "Error:";` → `const headerText = isCancelled ? "取消原因:" : "执行错误:";`
4. `APPROVAL_OPTION_DEFAULT_LABELS` 四项:`"allow-once": "允许"`、`"allow-always": "始终允许"`、`"reject-once": "拒绝"`、`"reject-always": "始终拒绝"`;confirm 视图的默认标题 fallback `${approvalOptionLabel(confirming)}?` 保持不变,按钮文本 `Confirm` → `确认`、`Back` → `返回`(两处 Button)。
5. **JSX 硬编码按钮文本 3 处**(不走 labels map,demo 原文 512 / 534 / 543 行):options 分支拒绝兜底 `Deny` → `拒绝`;非 options 兜底分支 `Allow` → `允许`、`Deny` → `拒绝`。

- [ ] **Step 5: tool-group.tsx / reasoning.tsx 中文化**

- tool-group.tsx:`const label = \`${count} tool ${count === 1 ? "call" : "calls"}\`;` → `const label = \`${count} 次工具调用\`;`
- reasoning.tsx:两处 `Reasoning{durationText}`(正常 + shimmer 重复文本)→ `思考过程{durationText}`

- [ ] **Step 6: image.tsx / attachment.tsx / file.tsx 中文化**

- image.tsx:`aria-label="Download image"` → `aria-label="下载图片"`;`aria-label="Copy image"` → `aria-label="复制图片"`;sr-only `Generating image…` → `正在生成图片…`;`aria-label="Click to zoom image"` → `aria-label="点击放大图片"`;`aria-label="Close zoomed image"` → `aria-label="关闭放大图片"`
- attachment.tsx:`DialogTitle` 的 `Image Attachment Preview` → `图片附件预览`;`tooltip="Add Attachment"` 与 `aria-label="Add Attachment"` → `添加附件`;`tooltip="Remove file"` → `移除文件`
- file.tsx:`{children || "Unnamed file"}` → `{children || "未命名文件"}`

- [ ] **Step 7: 类型检查**

```bash
cd /vol2/1000/code/stockclaw/frontend && node_modules/.bin/tsc --noEmit
```

预期:零错误。若报 `noUnusedLocals`(复制件含未用 import),删除对应 import 即可;deprecated 导出(`ReasoningGroup`/`ToolGroup`)是 export,不会触发,保留。

- [ ] **Step 8: Commit**

```bash
cd /vol2/1000/code/stockclaw
git add frontend/src/components/assistant-ui
git commit -m "feat(agent): copy demo assistant-ui components with localization and img guard"
```

---

### Task 5: thread.tsx 适配(slot + 能力守卫 + 中文化)

**Files:**
- Modify: `frontend/src/components/assistant-ui/thread.tsx`

以下编辑全部基于 Task 4 复制进来的文件(demo 原文行号仅供参考,以内容匹配为准)。

- [ ] **Step 1: ThreadProps 增加 composer / footerExtra(ReactNode,不走 ThreadComponents 组件槽)**

> 为什么不用 `ThreadComponents.Composer?: ComponentType`:工作台侧的 Composer/FooterExtra 必然携带闭包状态(pendingApproval、statusNote、activeThread 等),只能以内联函数提供——函数身份随流式重渲染变化,React 会按"不同组件类型"卸载重挂子树,导致输入焦点丢失、SteerAwayComposer 草稿清空、工具折叠面板状态抖动。元素(ReactNode)按类型协调,天然免疫该问题;`ThreadComponents` 保持 demo 原样零改动。

react 导入增加 `type ReactNode,`(thread.tsx 头部 `import { createContext, useContext, type ComponentType, type FC, type PropsWithChildren } from "react";`)。

把 demo 原文的:

```tsx
export type ThreadProps = {
  components?: ThreadComponents | undefined;
};
```

改为:

```tsx
export type ThreadProps = {
  components?: ThreadComponents | undefined;
  /** 覆写整个 Composer(工作台用于禁用态与 SteerAway 换装);传元素而非组件类型 */
  composer?: ReactNode | undefined;
  /** 渲染在 ViewportFooter 内 Composer 之前的附加区(statusNote / 重试) */
  footerExtra?: ReactNode | undefined;
};
```

`Thread` 组件本体改为(透传给 ThreadRoot):

```tsx
export const Thread: FC<ThreadProps> = ({
  components = EMPTY_COMPONENTS,
  composer,
  footerExtra,
}) => {
  const isEmpty = useAuiState(isNewChatView);

  return (
    <ThreadComponentsContext.Provider value={components}>
      <ThreadRoot isEmpty={isEmpty} composer={composer} footerExtra={footerExtra} />
    </ThreadComponentsContext.Provider>
  );
};
```

- [ ] **Step 2: 内部 Composer 改名 ThreadComposer 并在 footer 消费元素 props**

1. 把 `const Composer: FC = () => {` 改为 `const ThreadComposer: FC = () => {`
2. `ThreadRoot` 签名,把 demo 原文的:

```tsx
const ThreadRoot: FC<{ isEmpty: boolean }> = ({ isEmpty }) => {
```

改为:

```tsx
const ThreadRoot: FC<{
  isEmpty: boolean;
  composer?: ReactNode;
  footerExtra?: ReactNode;
}> = ({ isEmpty, composer, footerExtra }) => {
```

3. 同一个 `ThreadRoot` 的 ViewportFooter 内,把:

```tsx
            <ThreadScrollToBottom />
            <ThreadFollowupSuggestions />
            <Composer />
```

改为:

```tsx
            <ThreadScrollToBottom />
            <ThreadFollowupSuggestions />
            {footerExtra ?? null}
            {composer ?? <ThreadComposer />}
```

- [ ] **Step 3: 能力守卫(Reload / Edit;Dictation demo 已自带守卫)**

1. `AssistantActionBar` 中,把 Reload 一行:

```tsx
      <ActionBarPrimitive.Reload render={<TooltipIconButton tooltip="Refresh" />}><RefreshCwIcon /></ActionBarPrimitive.Reload>
```

改为(tooltip 顺带中文化):

```tsx
      <AuiIf condition={(s) => s.thread.capabilities.reload}>
        <ActionBarPrimitive.Reload render={<TooltipIconButton tooltip="重新生成" />}><RefreshCwIcon /></ActionBarPrimitive.Reload>
      </AuiIf>
```

2. `UserActionBar` 中,把 Edit 一行:

```tsx
      <ActionBarPrimitive.Edit render={<TooltipIconButton tooltip="Edit" className="aui-user-action-edit" />}><PencilIcon /></ActionBarPrimitive.Edit>
```

改为:

```tsx
      <AuiIf condition={(s) => s.thread.capabilities.edit}>
        <ActionBarPrimitive.Edit render={<TooltipIconButton tooltip="编辑" className="aui-user-action-edit" />}><PencilIcon /></ActionBarPrimitive.Edit>
      </AuiIf>
```

- [ ] **Step 4: 文案与可访问名(逐处替换)**

| 位置(demo 原文) | 替换为 |
|---|---|
| `<span className="sr-only">Loading conversation</span>` | `<span className="sr-only">正在加载会话</span>` |
| Welcome h1:`How can I help you today?` | `开始一项投研任务` |
| `TooltipIconButton tooltip="Scroll to bottom"`(含类名的长行,只改 tooltip 属性) | `tooltip="滚动到底"` |
| Input `placeholder="Send a message..."` | `placeholder="输入投研问题"` |
| Input `aria-label="Message input"` | `aria-label="Agent 消息"` |
| `tooltip="Voice input"` | `tooltip="语音输入"` |
| `aria-label="Start voice input"` | `aria-label="开始语音输入"` |
| `tooltip="Stop dictation"` | `tooltip="停止听写"` |
| `aria-label="Stop voice input"` | `aria-label="停止语音输入"` |
| Send 的 `tooltip="Send message"` | `tooltip="发送"` |
| Send 的 `aria-label="Send message"` | `aria-label="发送"` |
| Cancel 的 `aria-label="Stop generating"` | `aria-label="停止" title="停止"`(双写,e2e 用 `getByTitle("停止")`) |
| EditComposer:`Cancel` 文本 | `取消` |
| EditComposer:`Update` 文本 | `更新` |
| `tooltip="Copy"`(ActionBarPrimitive.Copy) | `tooltip="复制"` |
| `tooltip="More"` | `tooltip="更多"` |
| `Export as Markdown`(Item 内文本) | `导出 Markdown` |
| BranchPicker `tooltip="Previous"` / `tooltip="Next"` | `tooltip="上一个"` / `tooltip="下一个"` |
| 消息流式指示符 `aria-label="Assistant is working"` | `aria-label="Agent 正在处理"` |

- [ ] **Step 5: 类型检查**

```bash
cd /vol2/1000/code/stockclaw/frontend && node_modules/.bin/tsc --noEmit
```

预期:零错误(`AuiIf`、`useAuiState` 等在 0.15.16 均已导出)。

- [ ] **Step 6: Commit**

```bash
cd /vol2/1000/code/stockclaw
git add frontend/src/components/assistant-ui/thread.tsx
git commit -m "feat(agent): adapt demo thread with composer/footer slots and zh locale"
```

---

### Task 6: AgentThread 适配层(测试先行)

**Files:**
- Rewrite: `frontend/src/components/agent/AgentThread.test.tsx`
- Rewrite: `frontend/src/components/agent/AgentThread.tsx`

- [ ] **Step 1: 重写测试文件(先写,后实现)**

完整替换 `frontend/src/components/agent/AgentThread.test.tsx`:

```tsx
import { cleanup, render, screen } from "@testing-library/react";
import {
  AssistantRuntimeProvider,
  useExternalStoreRuntime,
} from "@assistant-ui/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("./SteerAwayComposer", () => ({
  SteerAwayComposer: () => <textarea aria-label="转向新问题" />,
}));

import { AgentThread } from "./AgentThread";

const ARTIFACT_RESULT = JSON.stringify({
  ok: true,
  artifact: {
    id: "artifact-1",
    title: "证据表",
    type: "table",
    run_id: "run-1",
    parent_artifact_id: null,
  },
  thread_revision: 2,
});

function Harness({
  isRunning,
  pendingApproval = false,
  statusNote = null,
  toolResult,
  onOpenArtifact,
}: {
  isRunning: boolean;
  pendingApproval?: boolean;
  statusNote?: string | null;
  toolResult?: unknown;
  onOpenArtifact?: (id: string) => void;
}) {
  const runtime = useExternalStoreRuntime({
    isRunning,
    messages: [
      { role: "user", content: [{ type: "text", text: "生成证据表" }] },
      {
        role: "assistant",
        content: [
          {
            type: "tool-call",
            toolCallId: "tc-1",
            toolName: "create_artifact",
            args: {},
            result: toolResult,
          },
          { type: "text", text: "完成" },
        ],
      },
    ],
    onNew: () => {},
    onCancel: () => {},
    onAddMessage: () => Promise.resolve(),
  });
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <AgentThread
        pendingApproval={pendingApproval}
        statusNote={statusNote}
        onOpenArtifact={onOpenArtifact}
      />
    </AssistantRuntimeProvider>
  );
}

afterEach(() => cleanup());

describe("AgentThread 运行态", () => {
  it("空闲时：输入可用、发送可见、无停止、无附件入口（无 adapter 自隐藏）", () => {
    render(<Harness isRunning={false} />);
    expect(screen.getByLabelText("Agent 消息")).not.toBeDisabled();
    expect(screen.getByTitle("发送")).toBeInTheDocument();
    expect(screen.queryByTitle("停止")).toBeNull();
    // spec §7.1 验证点：无 attachment adapter 时 AddAttachment 应自隐藏；
    // 若本断言失败，按 spec 从 thread.tsx 的 Composer 移除附件三件套引用后复跑
    expect(screen.queryByTitle("添加附件")).toBeNull();
  });

  it("运行中：输入禁用、只显示停止命令", () => {
    render(<Harness isRunning />);
    expect(screen.getByLabelText("Agent 消息")).toBeDisabled();
    expect(screen.queryByTitle("发送")).toBeNull();
    expect(screen.getByTitle("停止")).toBeInTheDocument();
  });

  it("终态错误区域高度稳定", () => {
    const { rerender } = render(<Harness isRunning={false} statusNote={null} />);
    const area = screen.getByTestId("agent-status-area");
    expect(area).toHaveClass("min-h-8");
    rerender(<Harness isRunning={false} statusNote="后端重启导致上次运行中断" />);
    expect(screen.getByTestId("agent-status-area")).toHaveClass("min-h-8");
    expect(screen.getByText("后端重启导致上次运行中断")).toBeInTheDocument();
  });

  it("等待审批时普通 Composer 让位给 steer composer", () => {
    render(<Harness isRunning={false} pendingApproval />);
    expect(screen.getByLabelText("转向新问题")).toBeInTheDocument();
    expect(screen.queryByLabelText("Agent 消息")).toBeNull();
  });

  it("create_artifact 结果的 Inspector 按钮在折叠面板外直接可见", () => {
    const opened: string[] = [];
    render(
      <Harness
        isRunning={false}
        toolResult={ARTIFACT_RESULT}
        onOpenArtifact={(id) => opened.push(id)}
      />,
    );
    // 关键契约：无需展开工具折叠条即可点击
    const button = screen.getByRole("button", { name: "在 Inspector 打开" });
    button.click();
    expect(opened).toEqual(["artifact-1"]);
  });

  it("无效 artifact 结果不渲染 Inspector 按钮", () => {
    render(
      <Harness
        isRunning={false}
        toolResult={{ ok: true, artifact: { id: "artifact-2" } }}
        onOpenArtifact={() => {}}
      />,
    );
    expect(screen.queryByRole("button", { name: "在 Inspector 打开" })).toBeNull();
  });
});
```

重试按钮的断言由 e2e 覆盖(「重试本轮」),单测不构造 last_run,Harness 无需 activeThread prop。

- [ ] **Step 2: 运行测试确认失败**

```bash
cd /vol2/1000/code/stockclaw/frontend && npx vitest run src/components/agent/AgentThread.test.tsx
```

预期:FAIL——现实现的输入框在 running 时消失(`toBeDisabled` 断言失败)、无 markdown 渲染层,且产物按钮测试走新契约。

- [ ] **Step 3: 重写 AgentThread.tsx**

完整替换 `frontend/src/components/agent/AgentThread.tsx`:

```tsx
import { createContext, useContext } from "react";
import {
  AuiIf,
  ComposerPrimitive,
  useAuiState,
  type ToolCallMessagePartComponent,
  type ToolCallMessagePartStatus,
} from "@assistant-ui/react";
import { ArrowUpIcon, ExternalLink, RotateCcw, SquareIcon } from "lucide-react";

import { Thread, type ThreadComponents } from "@/components/assistant-ui/thread";
import {
  ToolFallback as DemoToolFallback,
} from "@/components/assistant-ui/tool-fallback";
import { TooltipIconButton } from "@/components/assistant-ui/tooltip-icon-button";
import { Button } from "@/components/ui/button";

import { SteerAwayComposer } from "./SteerAwayComposer";

import type { AgentThread } from "@/lib/agent/types";

/** 从 create_artifact 结果中严格解析 artifactId（沿用 1D 契约校验）。 */
function artifactIdFromResult(toolName: string, result: unknown): string | null {
  if (toolName !== "create_artifact") return null;
  let value = result;
  if (typeof value === "string") {
    try { value = JSON.parse(value); } catch { return null; }
  }
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const payload = value as { ok?: unknown; artifact?: unknown; thread_revision?: unknown };
  if (payload.ok !== true || !payload.artifact || typeof payload.artifact !== "object" || Array.isArray(payload.artifact)) return null;
  const artifact = payload.artifact as Record<string, unknown>;
  const validType = artifact.type === "markdown" || artifact.type === "table"
    || artifact.type === "json" || artifact.type === "sources";
  const validParent = artifact.parent_artifact_id === null || typeof artifact.parent_artifact_id === "string";
  const validRevision = typeof payload.thread_revision === "number"
    && Number.isInteger(payload.thread_revision) && payload.thread_revision >= 0;
  if (
    typeof artifact.id !== "string" || artifact.id.length === 0
    || typeof artifact.title !== "string" || artifact.title.length === 0
    || typeof artifact.run_id !== "string" || artifact.run_id.length === 0
    || !validType || !validParent || !validRevision
  ) return null;
  return artifact.id;
}

/** demo ToolFallback + 「在 Inspector 打开」；按钮在折叠面板外，不依赖展开状态。 */
export function ToolFallback({
  toolName,
  argsText,
  result,
  status,
  onOpenArtifact,
}: {
  toolName: string;
  argsText?: string;
  args?: unknown;
  result?: unknown;
  status?: ToolCallMessagePartStatus;
  onOpenArtifact?: (artifactId: string) => void;
}) {
  const artifactId = artifactIdFromResult(toolName, result);
  return (
    <div className="w-full">
      <DemoToolFallback toolName={toolName} argsText={argsText} result={result} status={status} />
      {artifactId && onOpenArtifact ? (
        <button
          type="button"
          onClick={() => onOpenArtifact(artifactId)}
          className="mt-1 inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium text-primary hover:bg-primary/10"
        >
          <ExternalLink className="h-3.5 w-3.5" aria-hidden />
          在 Inspector 打开
        </button>
      ) : null}
    </div>
  );
}

const NOOP_OPEN_ARTIFACT = () => {};

/** 工作台上下文：向静态 ToolFallback 提供 onOpenArtifact（组件身份稳定，不随重渲染卸载子树）。 */
const OnOpenArtifactContext = createContext<(artifactId: string) => void>(NOOP_OPEN_ARTIFACT);

/** ThreadComponents.ToolFallback 的静态实现：模块级稳定身份 + Context 取回调。 */
const WorkspaceToolFallback: ToolCallMessagePartComponent = (props) => {
  const onOpenArtifact = useContext(OnOpenArtifactContext);
  return <ToolFallback {...props} onOpenArtifact={onOpenArtifact} />;
};

/** 静态组件表：所有引用为模块级身份，流式重渲染不会触发消息子树卸载。 */
const STATIC_COMPONENTS: ThreadComponents = {
  ToolFallback: WorkspaceToolFallback,
};

/** 工作台 Composer：demo 结构 + 收敛禁用态 + 运行中锁输入。 */
function WorkspaceComposer({ disabled }: { disabled?: boolean }) {
  const isRunning = useAuiState((s) => s.thread.isRunning);
  return (
    <ComposerPrimitive.Root className="aui-composer-root relative flex w-full flex-col">
      <div
        data-slot="aui_composer-shell"
        className="border-border/60 focus-within:border-border dark:border-muted-foreground/15 dark:focus-within:border-muted-foreground/30 flex w-full cursor-text flex-col gap-2 rounded-(--composer-radius) border bg-(--composer-bg) p-(--composer-padding) transition-[border-color]"
      >
        <ComposerPrimitive.Input
          placeholder={disabled ? "正在同步会话状态…" : "输入投研问题"}
          className="aui-composer-input caret-primary placeholder:text-muted-foreground/60 max-h-48 min-h-10 w-full resize-none bg-transparent px-2.5 py-1 text-base leading-6 outline-none"
          rows={1}
          enterKeyHint="send"
          aria-label="Agent 消息"
          disabled={disabled || isRunning}
        />
        <div className="aui-composer-action-wrapper relative flex items-center justify-end">
          <div className="flex items-center gap-1.5">
            <AuiIf condition={(s) => !s.thread.isRunning}>
              <ComposerPrimitive.Send
                render={
                  <TooltipIconButton
                    tooltip="发送"
                    side="bottom"
                    type="button"
                    variant="default"
                    size="icon"
                    className="aui-composer-send size-7 rounded-full"
                    aria-label="发送"
                    disabled={disabled}
                  />
                }
              >
                <ArrowUpIcon className="aui-composer-send-icon size-4" />
              </ComposerPrimitive.Send>
            </AuiIf>
            <AuiIf condition={(s) => s.thread.isRunning}>
              <ComposerPrimitive.Cancel
                render={
                  <Button
                    type="button"
                    variant="default"
                    size="icon"
                    className="aui-composer-cancel size-7 rounded-full"
                    aria-label="停止"
                    title="停止"
                  />
                }
              >
                <SquareIcon className="aui-composer-cancel-icon size-3.5 fill-current" />
              </ComposerPrimitive.Cancel>
            </AuiIf>
          </div>
        </div>
      </div>
    </ComposerPrimitive.Root>
  );
}

/** 重试动作：仅 failed/cancelled/interrupted 的最新 run 可重试（原逻辑迁移）。 */
function RetryAction({
  activeThread,
  onRetry,
}: {
  activeThread: AgentThread | null;
  onRetry: (runId: string) => void;
}) {
  const status = activeThread?.last_run?.status;
  if (status !== "failed" && status !== "cancelled" && status !== "interrupted") return null;
  const runId = activeThread?.last_run?.id;
  if (!runId) return null;
  return (
    <button
      onClick={() => onRetry(runId)}
      className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs text-muted-foreground hover:text-primary"
      title="用新的运行重试本轮"
    >
      <RotateCcw className="h-3.5 w-3.5" />
      重试本轮
    </button>
  );
}

export function AgentThread({
  activeThread = null,
  onRetry = () => {},
  statusNote = null,
  composerDisabled = false,
  pendingApproval = false,
  onOpenArtifact,
}: {
  activeThread?: AgentThread | null;
  onRetry?: (runId: string) => void;
  statusNote?: string | null;
  /** 权威收敛期间禁用输入（Stop/终态后等待取消持久化与 reload 完成） */
  composerDisabled?: boolean;
  /** 待审批且可恢复：普通 Composer 让位给 SteerAwayComposer */
  pendingApproval?: boolean;
  onOpenArtifact?: (artifactId: string) => void;
}) {
  const retryable = activeThread?.last_run?.status === "failed"
    || activeThread?.last_run?.status === "cancelled"
    || activeThread?.last_run?.status === "interrupted";

  // 组件身份稳定性：composer/footerExtra 传「元素」而非组件类型——元素按类型协调，
  // activeThread/statusNote 等流式高频变化只会触发普通重渲染，不会卸载 Composer 子树
  // （否则输入焦点丢失、SteerAway 草稿清空、工具折叠状态抖动）。
  // components 表是模块级常量 STATIC_COMPONENTS，同样稳定。
  return (
    <OnOpenArtifactContext.Provider value={onOpenArtifact ?? NOOP_OPEN_ARTIFACT}>
      <Thread
        components={STATIC_COMPONENTS}
        composer={pendingApproval
          ? <SteerAwayComposer disabled={composerDisabled} />
          : <WorkspaceComposer disabled={composerDisabled} />}
        footerExtra={statusNote || retryable ? (
          <div className="space-y-2">
            <div data-testid="agent-status-area" className="min-h-8">
              {statusNote ? (
                <div className="rounded-md border border-border bg-black/20 px-3 py-1.5 text-xs text-muted-foreground">
                  {statusNote}
                </div>
              ) : null}
            </div>
            <RetryAction activeThread={activeThread} onRetry={onRetry} />
          </div>
        ) : null}
      />
    </OnOpenArtifactContext.Provider>
  );
}
```

要点:
- `ToolFallback` 同时兼容 demo 的 part props(`argsText`/`status`)与现有测试的直接调用(`result` 字符串)。
- **组件身份稳定性(评审 Critical 修正)**:`components` 是模块级常量,`composer`/`footerExtra` 是元素而非组件类型;`onOpenArtifact` 经 Context 注入静态 ToolFallback。任何 props 高频变化都只触发重渲染,不触发子树卸载。pendingApproval 切换时 SteerAwayComposer ↔ WorkspaceComposer 的类型切换是**有意的换装**(与现状一致)。
- 运行中锁输入(`disabled={disabled || isRunning}`)对应测试「运行中:输入禁用」。

- [ ] **Step 4: 运行测试确认通过**

```bash
cd /vol2/1000/code/stockclaw/frontend && npx vitest run src/components/agent/AgentThread.test.tsx
```

预期:全部 PASS。若 base-ui 组件在 jsdom 报 ResizeObserver/Floating 之类错误,在 `frontend/src/test/setup.ts` 追加对应 stub(现有 ResizeObserver stub 已在);若 `getByTitle("发送")` 命中多个元素,检查 TooltipIconButton 的 title 透传是否被 sr-only 方案替代了原生属性。

- [ ] **Step 5: 全量 vitest**

```bash
npx vitest run
```

预期:全绿(其余组件测试不受影响——AgentThread 对外契约未变)。

- [ ] **Step 6: Commit**

```bash
cd /vol2/1000/code/stockclaw
git add frontend/src/components/agent/AgentThread.tsx frontend/src/components/agent/AgentThread.test.tsx
git commit -m "feat(agent): rebuild chat surface on demo thread with workspace slots"
```

---

### Task 7: 构建与 e2e 全量回归

- [ ] **Step 1: 生产构建**

```bash
cd /vol2/1000/code/stockclaw/frontend && npm run build
```

预期:`tsc -b && vite build` 零错误(strict + noUnusedLocals)。

- [ ] **Step 2: e2e(自起后端 8873 fixture + 前端 5873)**

```bash
npm run test:e2e
```

预期:20 条全部 PASS。重点关注:
- 流式与工具文本(ToolFallback 折叠条仍显示工具名,工具文本在展开后可见)
- 「在 Inspector 打开」直接点击(不展开)
- 审批用例(approve once/session、reject、steer-away)——**若失败原因为出现重复的行内 Allow/Deny 按钮**,按 spec §8 在 `AgentThread.tsx` 的 `ToolFallback` 中改用 demo 子组件自行组合并去掉 Approval 渲染(复制件不动),再跑
- Stop/Retry、409 收敛、双主题截图无横向溢出

- [ ] **Step 3: Commit(如有 e2e 触发的修正)**

```bash
cd /vol2/1000/code/stockclaw
git add -A frontend
git commit -m "fix(agent): e2e hardening for demo chat surface"
```

(无修正则跳过本步。)

---

### Task 8: 视觉抽查与收尾查漏

- [ ] **Step 1: 双主题截图**

后端与前端需在跑(e2e 的服务已停,自己起):后端用 fixture 模式不必需——空态欢迎页不依赖后端数据,但页面需模型配置才渲染 runtime;为纯视觉目的,直接复用 e2e 页面代价高,改用**已运行的 `npm run dev`(用户 dev server 在 :5899)不可靠**(AGENTS.md 警告其可能 stale)。推荐:自起 vite 于 5898:

```bash
cd /vol2/1000/code/stockclaw/frontend && npx vite --host 127.0.0.1 --port 5898
```

(Bash 工具后台模式启动。)再写 `/tmp/agent-shots.mjs`:

```js
import { createRequire } from "node:module";
const { chromium } = createRequire("/vol2/1000/code/stockclaw/frontend/package.json")("@playwright/test");

const browser = await chromium.launch();
for (const theme of ["dark", "light"]) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await ctx.addInitScript((t) => {
    localStorage.setItem("vr-theme", t);
    // 跳过模型配置 gating,直接放行 runtime 区域(与 e2e fixture 无关,仅视觉)
    localStorage.setItem("vr-agent-model", JSON.stringify({
      provider: "openai_compatible", baseURL: "http://127.0.0.1:8900/v1",
      model: "visual-check", apiKey: "dummy",
    }));
  }, theme);
  const page = await ctx.newPage();
  await page.goto("http://127.0.0.1:5898/agent", { waitUntil: "domcontentloaded" });
  await page.waitForLoadState("networkidle", { timeout: 15000 }).catch(() => {});
  await page.screenshot({ path: `/tmp/agent-welcome-${theme}.png`, fullPage: true });
  await ctx.close();
}
await browser.close();
```

```bash
node /tmp/agent-shots.mjs
```

人工查看两张截图:欢迎页居中、暖橙主题无 zinc 灰、composer 圆角卡片、无横向滚动条。

- [ ] **Step 2: 英文文案查漏**

```bash
grep -n '"[A-Z][a-z][^"]*"' /vol2/1000/code/stockclaw/frontend/src/components/assistant-ui/*.tsx \
  | grep -v className | grep -v 'data-slot' | grep -v '^.*import'
```

逐行人工复核剩余命中(类名/属性名除外),发现用户可见英文按 spec §7.1 清单口径中文化并补测。

- [ ] **Step 3: 最终提交**

```bash
cd /vol2/1000/code/stockclaw
git status   # 确认无私密文档被暂存
git add -A frontend
git commit -m "chore(agent): visual pass and localization sweep for demo chat surface"
```

---

## 自审记录

- **Spec 覆盖**:§3 依赖→Task 1;§4/§6 CSS→Task 2;§6 ui 基件→Task 3;§6/§7.2/§7.1(3)(除 thread)→Task 4;§7.1→Task 5;§7.3→Task 6;§9→Task 6/7;§8 验证点→Task 7 Step 2;「AddAttachment 自隐藏验证」→Task 6 Step 1 的显式断言;视觉抽查→Task 8。
- **占位符扫描**:无 TBD/TODO;所有代码步骤含完整代码或精确 old/new。
- **类型一致性**:`ThreadProps.composer/footerExtra`(ReactNode,Task 5 定义)与 `AgentThread.tsx`(Task 6 传入元素)一致;`STATIC_COMPONENTS: ThreadComponents`(demo 原类型,无 slot 扩展)与 `WorkspaceToolFallback: ToolCallMessagePartComponent` 匹配;`ToolFallback` 导出签名(`status?: ToolCallMessagePartStatus`)与测试调用及 `WorkspaceToolFallback` 的 spread 兼容。
- **评审修正(第三轮)**:①composer/footerExtra 从 ThreadComponents 组件槽改为 ThreadProps 的 ReactNode 元素 props + Context 注入回调,消除组件身份抖动(Critical);②Task 4 补 `mkdir -p`;③Task 2 补 `--radius-xl/2xl` 防圆角倒挂;④Task 4 补 3 处硬编码 Allow/Deny 中文化;⑤指示符 aria-label、file/attachment/image 微文案、status 具名类型导入。
