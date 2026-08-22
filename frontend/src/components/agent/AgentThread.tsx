import { createContext, useContext, type ComponentType, type PropsWithChildren } from "react";
import {
  AuiIf,
  ComposerPrimitive,
  useAuiState,
  type ToolCallMessagePartComponent,
  type ToolCallMessagePartProps,
} from "@assistant-ui/react";
import { ArrowUpIcon, ExternalLink, RotateCcw, SquareIcon } from "lucide-react";

import { Thread, type ThreadGroupPart, type ThreadComponents } from "@/components/assistant-ui/thread";
import {
  ToolFallback as DemoToolFallback,
} from "@/components/assistant-ui/tool-fallback";
import {
  ToolGroupContent,
  ToolGroupRoot,
  ToolGroupTrigger,
} from "@/components/assistant-ui/tool-group";
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
  result,
  onOpenArtifact,
  ...part
}: ToolCallMessagePartProps & {
  onOpenArtifact?: (artifactId: string) => void;
}) {
  const artifactId = artifactIdFromResult(toolName, result);
  return (
    <div className="w-full">
      <DemoToolFallback {...part} toolName={toolName} result={result} />
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

/** 工作台 ToolGroup：demo 折叠组结构 + 组级「在 Inspector 打开」按钮（渲染在折叠面板外）。 */
const WorkspaceToolGroup: ComponentType<PropsWithChildren<{ group: ThreadGroupPart }>> = ({
  group,
  children,
}) => {
  const onOpenArtifact = useContext(OnOpenArtifactContext);
  const artifactIdsKey = useAuiState((s) => {
    const ids: string[] = [];
    for (const idx of group.indices) {
      const part = s.message.parts[idx];
      if (part?.type !== "tool-call") continue;
      const id = artifactIdFromResult(part.toolName, part.result);
      if (id) ids.push(id);
    }
    return ids.join("\n");
  });
  const artifactIds = artifactIdsKey ? artifactIdsKey.split("\n") : [];
  return (
    <div className="w-full">
      <ToolGroupRoot variant="ghost">
        <ToolGroupTrigger
          count={group.indices.length}
          active={group.status.type === "running"}
        />
        <ToolGroupContent>{children}</ToolGroupContent>
      </ToolGroupRoot>
      {artifactIds.length > 0 ? (
        <div className="flex flex-wrap gap-1">
          {artifactIds.map((artifactId) => (
            <button
              key={artifactId}
              type="button"
              onClick={() => onOpenArtifact(artifactId)}
              className="mt-1 inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium text-primary hover:bg-primary/10"
            >
              <ExternalLink className="h-3.5 w-3.5" aria-hidden />
              在 Inspector 打开
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
};

/** 静态组件表：所有引用为模块级身份，流式重渲染不会触发消息子树卸载。 */
const STATIC_COMPONENTS: ThreadComponents = {
  ToolFallback: WorkspaceToolFallback,
  ToolGroup: WorkspaceToolGroup,
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
        footerExtra={
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
        }
      />
    </OnOpenArtifactContext.Provider>
  );
}
