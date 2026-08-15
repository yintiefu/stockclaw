import {
  ComposerPrimitive,
  MessagePrimitive,
  ThreadPrimitive,
} from "@assistant-ui/react";
import { Send, Square } from "lucide-react";

function UserMessage() {
  return (
    <MessagePrimitive.Root className="ml-auto max-w-[80%] rounded-md bg-primary px-3 py-2 text-sm text-primary-foreground">
      <MessagePrimitive.Content />
    </MessagePrimitive.Root>
  );
}

function AssistantMessage() {
  return (
    <MessagePrimitive.Root className="max-w-[88%] text-sm leading-6 text-foreground">
      <MessagePrimitive.Content />
    </MessagePrimitive.Root>
  );
}

export function AgentThread() {
  // 两个分支几何一致：running 状态不会撑动页面；队列在 runtime 层已禁用，
  // 禁用态无法提交第二次 start 请求。
  return (
    <ThreadPrimitive.Root className="flex min-h-[560px] flex-col">
      <ThreadPrimitive.Viewport className="flex flex-1 flex-col gap-4 overflow-y-auto px-1 py-4">
        <ThreadPrimitive.Empty>
          <p className="m-auto text-sm text-muted-foreground">开始一项投研任务</p>
        </ThreadPrimitive.Empty>
        <ThreadPrimitive.Messages components={{ UserMessage, AssistantMessage }} />
        <ThreadPrimitive.ViewportFooter className="sticky bottom-0 mt-auto bg-background pt-3">
          <ComposerPrimitive.Root className="flex min-h-12 items-end gap-2 rounded-md border border-border bg-background p-2">
            <ThreadPrimitive.If running={false}>
              <ComposerPrimitive.Input
                aria-label="Agent 消息"
                placeholder="输入投研问题"
                className="max-h-40 min-h-8 flex-1 resize-none bg-transparent px-1 py-1 text-sm outline-none"
              />
              <ComposerPrimitive.Send className="grid h-9 w-9 place-items-center rounded-md bg-primary text-primary-foreground" title="发送">
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
