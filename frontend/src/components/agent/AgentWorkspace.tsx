/** 工作台两栏壳：桌面左线程右聊天，移动端线程入抽屉；审批始终在聊天列内。 */
import { useState, type ReactNode } from "react";
import { MessagesSquare } from "lucide-react";

import { WorkspaceDrawer } from "./WorkspaceDrawer";

export function AgentWorkspace({ desktop, threads, chat, approval }: {
  desktop: boolean;
  threads: ReactNode;
  chat: ReactNode;
  approval?: ReactNode;
}) {
  const [threadsOpen, setThreadsOpen] = useState(false);

  return (
    <>
      <div
        data-testid="agent-workspace"
        className={
          desktop
            ? "grid h-full min-h-0 grid-cols-[240px_minmax(0,1fr)] overflow-hidden"
            : "flex h-full min-h-0 flex-col overflow-hidden"
        }
      >
        {desktop ? (
          <aside
            data-testid="agent-threads-column"
            aria-label="会话列表"
            className="min-h-0 overflow-y-auto border-r border-border/70"
          >
            {threads}
          </aside>
        ) : null}

        <section data-testid="agent-chat-column" className="flex min-h-0 min-w-0 flex-col overflow-hidden">
          {!desktop ? (
            <header className="flex h-12 shrink-0 items-center gap-2 border-b border-border/70 px-3">
              <button
                type="button"
                aria-label="打开会话列表"
                title="打开会话列表"
                onClick={() => setThreadsOpen(true)}
                className="grid h-8 w-8 place-items-center rounded-md text-muted-foreground hover:bg-muted/60 hover:text-foreground focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-primary/60"
              >
                <MessagesSquare className="h-4 w-4" aria-hidden />
              </button>
              <h1 className="min-w-0 flex-1 truncate text-sm font-semibold">Agent 工作台</h1>
            </header>
          ) : null}
          {approval ? <div className="max-h-[45%] shrink-0 overflow-y-auto px-3 pt-3">{approval}</div> : null}
          <div className="min-h-0 flex-1 overflow-hidden p-3">{chat}</div>
        </section>
      </div>

      {!desktop ? (
        <WorkspaceDrawer open={threadsOpen} onClose={() => setThreadsOpen(false)} title="会话" side="left">
          {threads}
        </WorkspaceDrawer>
      ) : null}
    </>
  );
}
