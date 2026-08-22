import { useEffect, useState, type ReactNode } from "react";
import { PanelRight, Settings, MessagesSquare } from "lucide-react";

import { createAgentWorkspaceStore } from "@/lib/agent/workspace";
import { WorkspaceDrawer } from "./WorkspaceDrawer";

type WorkspaceStore = ReturnType<typeof createAgentWorkspaceStore>;

type Props = {
  threadTitle: string;
  modelLabel: string;
  capabilityLabel: string;
  /** ≥xl 三栏常驻；以下仅聊天列，线程/Inspector 进左右抽屉，设置为右覆盖抽屉。 */
  desktop: boolean;
  store: WorkspaceStore;
  threads: ReactNode;
  chat: ReactNode;
  inspector: ReactNode;
  settings: ReactNode;
  alerts?: ReactNode;
  selectedArtifactId?: string | null;
};

function CommandButton({ label, onClick, children }: {
  label: string;
  onClick?: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      onClick={onClick}
      className="grid h-8 w-8 shrink-0 place-items-center rounded-md text-muted-foreground hover:bg-muted/60 hover:text-foreground focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-primary/60"
    >
      {children}
    </button>
  );
}

export function AgentWorkspace({
  threadTitle,
  modelLabel,
  capabilityLabel,
  desktop,
  store,
  threads,
  chat,
  inspector,
  settings,
  alerts,
  selectedArtifactId = null,
}: Props) {
  const [, forceRefresh] = useState(0);
  useEffect(() => store.subscribe(() => forceRefresh((value) => value + 1)), [store]);
  const drawer = store.getState().drawer;
  const closeDrawer = () => store.getState().openDrawer(null);
  const openDrawer = (target: Exclude<typeof drawer, null>) => () => store.getState().openDrawer(target);

  return (
    <>
      <div
        data-testid="agent-workspace"
        className="grid h-full min-h-0 min-w-0 grid-cols-1 overflow-hidden xl:grid-cols-[240px_minmax(480px,1fr)_320px]"
      >
        {desktop ? (
          <aside
            data-testid="agent-threads-column"
            aria-label="会话线程"
            className="hidden min-h-0 overflow-y-auto border-r border-border/70 xl:block"
          >
            {threads}
          </aside>
        ) : null}

        <section data-testid="agent-chat-column" className="flex min-h-0 min-w-0 flex-col overflow-hidden">
          <header className="flex h-14 shrink-0 items-center gap-3 border-b border-border/70 px-3">
            {!desktop ? (
              <CommandButton label="打开线程" onClick={openDrawer("threads")}>
                <MessagesSquare className="h-4 w-4" aria-hidden />
              </CommandButton>
            ) : null}
            <div className="min-w-0 flex-1">
              <h1 className="truncate text-sm font-semibold" title={threadTitle}>{threadTitle}</h1>
              <div className="flex min-w-0 items-center gap-2 text-xs text-muted-foreground">
                <span className="truncate">{modelLabel || "未配置模型"}</span>
                <span aria-hidden>·</span>
                <span className="truncate">{capabilityLabel}</span>
              </div>
            </div>
            {!desktop ? (
              <CommandButton label="打开 Inspector" onClick={openDrawer("inspector")}>
                <PanelRight className="h-4 w-4" aria-hidden />
              </CommandButton>
            ) : null}
            <CommandButton label="模型设置" onClick={openDrawer("settings")}>
              <Settings className="h-4 w-4" aria-hidden />
            </CommandButton>
          </header>
          <div className="min-h-10 shrink-0 border-b border-border/50 px-3 py-1.5" data-testid="agent-alert-area">
            {alerts}
          </div>
          <div className="min-h-0 flex-1 overflow-hidden px-3">{chat}</div>
        </section>

        {desktop ? (
          <aside
            data-testid="agent-inspector-column"
            aria-label="Inspector"
            className="hidden min-h-0 flex-col overflow-y-auto border-l border-border/70 xl:flex"
          >
            <div className="border-b border-border/70 px-3 py-3">
              <h2 className="text-sm font-semibold">Inspector</h2>
              {selectedArtifactId ? (
                <p className="mt-1 truncate text-xs text-primary" title={selectedArtifactId}>
                  Artifact · {selectedArtifactId}
                </p>
              ) : (
                <p className="mt-1 text-xs text-muted-foreground">运行、产物与来源</p>
              )}
            </div>
            <div className="min-h-0 flex-1">{inspector}</div>
          </aside>
        ) : null}
      </div>

      {!desktop ? (
        <>
          <WorkspaceDrawer open={drawer === "threads"} onClose={closeDrawer} title="会话线程" side="left">
            {threads}
          </WorkspaceDrawer>
          <WorkspaceDrawer open={drawer === "inspector"} onClose={closeDrawer} title="Inspector" side="right">
            <div className="px-3 pt-3">
              {selectedArtifactId ? (
                <p className="mb-2 truncate text-xs text-primary" title={selectedArtifactId}>
                  Artifact · {selectedArtifactId}
                </p>
              ) : null}
            </div>
            {inspector}
          </WorkspaceDrawer>
        </>
      ) : null}
      {settings}
    </>
  );
}
