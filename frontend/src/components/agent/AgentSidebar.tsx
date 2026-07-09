import { Plus, MessageSquare } from "lucide-react";
import { useAgentStore } from "@/lib/stores/agent";
import { cn } from "@/lib/utils";

export function AgentSidebar() {
  const threads = useAgentStore((s) => s.threads);
  const currentThreadId = useAgentStore((s) => s.currentThreadId);
  const setCurrentThread = useAgentStore((s) => s.setCurrentThread);

  const newThread = () => {
    const tid = `local-${Date.now()}`;
    useAgentStore.setState((s) => ({
      threads: [
        { id: tid, title: "新会话", model: "", created_at: Date.now(), updated_at: Date.now() },
        ...s.threads,
      ],
      currentThreadId: tid,
      messagesByThread: { ...s.messagesByThread, [tid]: [] },
    }));
  };

  return (
    <aside className="glass flex w-60 flex-col rounded-2xl">
      <button
        onClick={newThread}
        className="m-2 flex items-center justify-center gap-2 rounded-lg bg-primary/10 px-3 py-2 text-sm font-medium text-primary hover:bg-primary/20"
      >
        <Plus className="h-4 w-4" /> 新建会话
      </button>
      <div className="flex-1 overflow-auto p-2">
        {threads.length === 0 && (
          <p className="px-2 py-4 text-xs text-muted-foreground">暂无会话</p>
        )}
        {threads.map((t) => (
          <button
            key={t.id}
            onClick={() => setCurrentThread(t.id)}
            className={cn(
              "mb-1 flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-sm transition-colors",
              t.id === currentThreadId
                ? "bg-primary/15 font-medium text-primary"
                : "text-muted-foreground hover:bg-muted/50 hover:text-foreground",
            )}
          >
            <MessageSquare className="h-3.5 w-3.5 shrink-0" />
            <span className="flex-1 truncate">{t.title}</span>
          </button>
        ))}
      </div>
    </aside>
  );
}
