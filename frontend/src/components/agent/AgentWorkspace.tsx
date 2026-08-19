import type { ReactNode } from "react";
import { PanelRight, Settings, MessagesSquare } from "lucide-react";

import type { AgentModelConfig } from "@/lib/agent/model-config";

const INPUT_CLASS =
  "w-full rounded-md border border-border bg-black/20 px-2.5 py-2 text-sm outline-none focus:border-primary/50";

type Props = {
  threadTitle: string;
  modelConfig: AgentModelConfig;
  modelLabel: string;
  configured: boolean;
  capabilityLabel: string;
  onModelConfigChange: (config: AgentModelConfig) => void;
  onSaveModel: () => void;
  onOpenThreads?: () => void;
  onOpenInspector?: () => void;
  onOpenSettings?: () => void;
  threads: ReactNode;
  chat: ReactNode;
  inspector: ReactNode;
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
      className="grid h-8 w-8 shrink-0 place-items-center rounded-md text-muted-foreground hover:bg-muted/60 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60"
    >
      {children}
    </button>
  );
}

export function AgentWorkspace({
  threadTitle,
  modelConfig,
  modelLabel,
  configured,
  capabilityLabel,
  onModelConfigChange,
  onSaveModel,
  onOpenThreads,
  onOpenInspector,
  onOpenSettings,
  threads,
  chat,
  inspector,
  alerts,
  selectedArtifactId = null,
}: Props) {
  const update = (field: keyof AgentModelConfig, value: string) => {
    onModelConfigChange({ ...modelConfig, [field]: value });
  };

  return (
    <div
      data-testid="agent-workspace"
      className="grid h-full min-h-0 min-w-0 grid-cols-1 overflow-hidden xl:grid-cols-[240px_minmax(480px,1fr)_320px]"
    >
      <aside
        data-testid="agent-threads-column"
        aria-label="会话线程"
        className="hidden min-h-0 overflow-y-auto border-r border-border/70 xl:block"
      >
        {threads}
      </aside>

      <section data-testid="agent-chat-column" className="flex min-h-0 min-w-0 flex-col overflow-hidden">
        <header className="flex h-14 shrink-0 items-center gap-3 border-b border-border/70 px-3">
          <CommandButton label="打开线程" onClick={onOpenThreads}>
            <MessagesSquare className="h-4 w-4" aria-hidden />
          </CommandButton>
          <div className="min-w-0 flex-1">
            <h1 className="truncate text-sm font-semibold" title={threadTitle}>{threadTitle}</h1>
            <div className="flex min-w-0 items-center gap-2 text-xs text-muted-foreground">
              <span className="truncate">{modelLabel || "未配置模型"}</span>
              <span aria-hidden>·</span>
              <span className="truncate">{capabilityLabel}</span>
            </div>
          </div>
          <CommandButton label="打开 Inspector" onClick={onOpenInspector}>
            <PanelRight className="h-4 w-4" aria-hidden />
          </CommandButton>
          <CommandButton label="模型设置" onClick={onOpenSettings}>
            <Settings className="h-4 w-4" aria-hidden />
          </CommandButton>
        </header>
        <div className="min-h-10 shrink-0 border-b border-border/50 px-3 py-1.5" data-testid="agent-alert-area">
          {alerts}
        </div>
        <div className="min-h-0 flex-1 overflow-hidden px-3">{chat}</div>
      </section>

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
        <form
          data-testid="agent-settings"
          className="mt-auto space-y-3 border-t border-border/70 p-3"
          onSubmit={(event) => { event.preventDefault(); onSaveModel(); }}
        >
          <div className="flex items-center justify-between gap-2">
            <h2 className="text-sm font-semibold">模型设置</h2>
            <span className="text-xs text-muted-foreground">{configured ? "已配置" : "未完成"}</span>
          </div>
          <label className="block text-xs text-muted-foreground" htmlFor="agent-provider">Provider</label>
          <input id="agent-provider" value={modelConfig.provider} onChange={(e) => update("provider", e.target.value)} className={INPUT_CLASS} />
          <label className="block text-xs text-muted-foreground" htmlFor="agent-base-url">Base URL</label>
          <input id="agent-base-url" value={modelConfig.baseURL} onChange={(e) => update("baseURL", e.target.value)} className={INPUT_CLASS} />
          <label className="block text-xs text-muted-foreground" htmlFor="agent-model">模型</label>
          <input id="agent-model" value={modelConfig.model} onChange={(e) => update("model", e.target.value)} className={INPUT_CLASS} />
          <label className="block text-xs text-muted-foreground" htmlFor="agent-api-key">API Key</label>
          <input id="agent-api-key" type="password" value={modelConfig.apiKey} onChange={(e) => update("apiKey", e.target.value)} className={INPUT_CLASS} />
          <button type="submit" className="w-full rounded-md bg-primary/15 px-3 py-2 text-sm font-medium text-primary hover:bg-primary/25">
            保存模型配置
          </button>
        </form>
      </aside>
    </div>
  );
}
