/** 聊天线程：共享 Thread / 工具渲染 + 标准发送/取消 Composer，无自定义运行态。 */
import type { ComponentType, PropsWithChildren } from "react";
import {
  AuiIf,
  ComposerPrimitive,
  useAuiState,
} from "@assistant-ui/react";
import { ArrowUpIcon, SquareIcon } from "lucide-react";

import { Thread, type ThreadGroupPart, type ThreadComponents } from "@/components/assistant-ui/thread";
import { ToolFallback as DemoToolFallback } from "@/components/assistant-ui/tool-fallback";
import {
  ToolGroupContent,
  ToolGroupRoot,
  ToolGroupTrigger,
} from "@/components/assistant-ui/tool-group";
import { TooltipIconButton } from "@/components/assistant-ui/tooltip-icon-button";
import { Button } from "@/components/ui/button";

/** 工作台 ToolGroup：demo 折叠组结构。 */
const WorkspaceToolGroup: ComponentType<PropsWithChildren<{ group: ThreadGroupPart }>> = ({
  group,
  children,
}) => (
  <div className="w-full">
    <ToolGroupRoot variant="ghost">
      <ToolGroupTrigger
        count={group.indices.length}
        active={group.status.type === "running"}
      />
      <ToolGroupContent>{children}</ToolGroupContent>
    </ToolGroupRoot>
  </div>
);

/** 静态组件表：所有引用为模块级身份，流式重渲染不会触发消息子树卸载。 */
const STATIC_COMPONENTS: ThreadComponents = {
  ToolFallback: DemoToolFallback,
  ToolGroup: WorkspaceToolGroup,
};

/** 标准 Composer：中文占位、运行中锁输入并显示停止。 */
function WorkspaceComposer() {
  const isRunning = useAuiState((s) => s.thread.isRunning);
  return (
    <ComposerPrimitive.Root className="aui-composer-root relative flex w-full flex-col">
      <div
        data-slot="aui_composer-shell"
        className="border-border/60 focus-within:border-border dark:border-muted-foreground/15 dark:focus-within:border-muted-foreground/30 flex w-full cursor-text flex-col gap-2 rounded-(--composer-radius) border bg-(--composer-bg) p-(--composer-padding) transition-[border-color]"
      >
        <ComposerPrimitive.Input
          placeholder="输入投研问题"
          className="aui-composer-input caret-primary placeholder:text-muted-foreground/60 max-h-48 min-h-10 w-full resize-none bg-transparent px-2.5 py-1 text-base leading-6 outline-none"
          rows={1}
          enterKeyHint="send"
          aria-label="Agent 消息"
          disabled={isRunning}
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

export function AgentThread() {
  return <Thread components={STATIC_COMPONENTS} composer={<WorkspaceComposer />} />;
}
