import {
  ComposerPrimitive,
  MessagePrimitive,
  ThreadPrimitive,
} from "@assistant-ui/react";
import { RotateCcw, Send, Square, Wrench } from "lucide-react";

import type { AgentThread } from "@/lib/agent/types";

function UserMessage() {
  return (
    <MessagePrimitive.Root className="ml-auto max-w-[80%] rounded-md bg-primary px-3 py-2 text-sm text-primary-foreground">
      <MessagePrimitive.Content />
    </MessagePrimitive.Root>
  );
}

/** 兜底工具渲染：未注册专用 UI 的工具调用显示名称/参数/结果，而不是空白。 */
function ToolFallback({
  toolName,
  args,
  result,
}: {
  toolName: string;
  args?: unknown;
  result?: unknown;
}) {
  return (
    <div className="flex items-start gap-2 rounded-md border border-border bg-black/20 px-3 py-2 text-xs text-muted-foreground">
      <Wrench className="mt-0.5 h-3.5 w-3.5 shrink-0" />
      <div className="min-w-0 flex-1">
        <span className="font-medium text-foreground">{toolName}</span>
        {args != null ? (
          <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-all">
            {typeof args === "string" ? args : JSON.stringify(args)}
          </pre>
        ) : null}
        {result != null ? (
          <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-all">
            {typeof result === "string" ? result : JSON.stringify(result)}
          </pre>
        ) : null}
      </div>
    </div>
  );
}

function AssistantMessage() {
  return (
    <MessagePrimitive.Root className="max-w-[88%] text-sm leading-6 text-foreground">
      <MessagePrimitive.Content components={{ tools: { Fallback: ToolFallback } }} />
    </MessagePrimitive.Root>
  );
}

/** 重试动作：仅 failed/cancelled/interrupted 的最新 run 可重试。 */
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
      onClick={() => {
        // 装填 retryOf 后由页面触发 startRun（requestInit 会置空 messages）
        onRetry(runId);
      }}
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
}: {
  activeThread?: AgentThread | null;
  onRetry?: (runId: string) => void;
  statusNote?: string | null;
  /** 权威收敛期间禁用输入（Stop/终态后等待取消持久化与 reload 完成） */
  composerDisabled?: boolean;
}) {
  // 两个分支几何一致：running 状态不会撑动页面；队列在 runtime 层已禁用，
  // 禁用态无法提交第二次 start 请求。
  return (
    <ThreadPrimitive.Root className="flex min-h-[560px] flex-col">
      <ThreadPrimitive.Viewport className="flex flex-1 flex-col gap-4 overflow-y-auto px-1 py-4">
        <ThreadPrimitive.Empty>
          <p className="m-auto text-sm text-muted-foreground">开始一项投研任务</p>
        </ThreadPrimitive.Empty>
        <ThreadPrimitive.Messages components={{ UserMessage, AssistantMessage }} />
        <ThreadPrimitive.ViewportFooter className="sticky bottom-0 mt-auto space-y-2 bg-background pt-3">
          {statusNote ? (
            <div className="rounded-lg border border-border bg-black/20 px-3 py-2 text-xs text-muted-foreground">
              {statusNote}
            </div>
          ) : null}
          <RetryAction activeThread={activeThread} onRetry={onRetry} />
          <ComposerPrimitive.Root className="flex min-h-12 items-end gap-2 rounded-md border border-border bg-background p-2">
            <ThreadPrimitive.If running={false}>
              <ComposerPrimitive.Input
                aria-label="Agent 消息"
                disabled={composerDisabled}
                placeholder={composerDisabled ? "正在同步会话状态…" : "输入投研问题"}
                className="max-h-40 min-h-8 flex-1 resize-none bg-transparent px-1 py-1 text-sm outline-none"
              />
              <ComposerPrimitive.Send disabled={composerDisabled} className="grid h-9 w-9 place-items-center rounded-md bg-primary text-primary-foreground disabled:opacity-40" title="发送">
                <Send className="h-4 w-4" />
              </ComposerPrimitive.Send>
            </ThreadPrimitive.If>
            <ThreadPrimitive.If running>
              <ComposerPrimitive.Input
                aria-label="Agent 消息"
                disabled
                className="max-h-40 min-h-8 flex-1 resize-none bg-transparent px-1 py-1 text-sm outline-none"
              />
              <ComposerPrimitive.Cancel className="grid h-9 w-9 place-items-center rounded-md border border-border" title="停止">
                <Square className="h-4 w-4" />
              </ComposerPrimitive.Cancel>
            </ThreadPrimitive.If>
          </ComposerPrimitive.Root>
        </ThreadPrimitive.ViewportFooter>
      </ThreadPrimitive.Viewport>
    </ThreadPrimitive.Root>
  );
}
