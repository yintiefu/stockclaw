import { useEffect, useRef, useState } from "react";
import { Plus, MessageSquare, Trash2, Pencil, Check, X } from "lucide-react";
import { useAgentStore } from "@/lib/stores/agent";
import { agentApi } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { AgentThread, ChatMessage } from "@/lib/types/agent";

export function AgentSidebar() {
  const threads = useAgentStore((s) => s.threads);
  const currentThreadId = useAgentStore((s) => s.currentThreadId);
  const setCurrentThread = useAgentStore((s) => s.setCurrentThread);
  const loadThreads = useAgentStore((s) => s.loadThreads);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draftTitle, setDraftTitle] = useState("");
  const loadedOnce = useRef(false);

  // mount 时拉历史会话
  useEffect(() => {
    agentApi.listThreads()
      .then((items: AgentThread[]) => {
        if (loadedOnce.current) return;  // 后到的响应丢弃，避免覆盖新建的 thread
        loadedOnce.current = true;
        // 保留用户刚 click 新建但后端还没建好的 local-xxx thread
        const localOnly = useAgentStore.getState().threads.filter(
          (t) => t.id.startsWith("local-") || !items.some((r) => r.id === t.id)
        );
        loadThreads([...items, ...localOnly]);
      })
      .catch(() => {});
  }, [loadThreads]);

  const newThread = () => {
    const tid = (globalThis.crypto?.randomUUID?.() ?? `local-${Date.now()}`);
    loadThreads([
      { id: tid, title: "新会话", model: "", created_at: Date.now(), updated_at: Date.now() },
      ...useAgentStore.getState().threads,
    ]);
    setCurrentThread(tid);
    useAgentStore.setState((s) => ({
      messagesByThread: { ...s.messagesByThread, [tid]: [] },
    }));
    // 立刻同步到后端——否则后续 saveMessage 会 FK 违反（thread_id 不存在）
    // 失败时本地仍可用，但刷新会丢；console.error 让用户能定位
    agentApi.createThread("新会话", "", tid).catch((e) => {
      console.error("newThread 后端创建失败：", e);
    });
  };

  const switchThread = async (tid: string) => {
    setCurrentThread(tid);
    // 如果 store 里没这个 thread 的消息，从后端拉
    const existing = useAgentStore.getState().messagesByThread[tid];
    if (!existing || existing.length === 0) {
      try {
        const msgs = await agentApi.listMessages(tid);
        const chatMsgs: ChatMessage[] = (msgs || []).map((m: any) => ({
          id: m.id,
          role: m.role as "user" | "assistant",
          content: m.content || "",
          toolTraces: [],
        }));
        useAgentStore.setState((s) => ({
          messagesByThread: { ...s.messagesByThread, [tid]: chatMsgs },
        }));
      } catch (e) {
        console.error("loadMessages 失败：", e);
      }
    }
  };

  const commitRename = async (tid: string) => {
    const t = draftTitle.trim();
    if (!t) { setEditingId(null); return; }
    loadThreads(useAgentStore.getState().threads.map((x) => x.id === tid ? { ...x, title: t } : x));
    setEditingId(null);
    if (!tid.startsWith("local-")) {
      try { await agentApi.renameThread(tid, t); } catch (e) {
        console.error("renameThread 失败：", e);
      }
    }
  };

  const remove = async (tid: string) => {
    const state = useAgentStore.getState();
    loadThreads(state.threads.filter((x) => x.id !== tid));
    // 清理孤儿状态
    useAgentStore.setState((s) => {
      const newMessages = { ...s.messagesByThread };
      delete newMessages[tid];
      return {
        messagesByThread: newMessages,
        currentThreadId: s.currentThreadId === tid ? null : s.currentThreadId,
      };
    });
    if (!tid.startsWith("local-")) {
      try { await agentApi.deleteThread(tid); } catch (e) {
        console.error("deleteThread 失败：", e);
      }
    }
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
          <div
            key={t.id}
            className={cn(
              "mb-1 flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-sm transition-colors group",
              t.id === currentThreadId
                ? "bg-primary/15 font-medium text-primary"
                : "text-muted-foreground hover:bg-muted/50 hover:text-foreground",
            )}
          >
            <MessageSquare className="h-3.5 w-3.5 shrink-0" />
            {editingId === t.id ? (
              <>
                <input
                  value={draftTitle}
                  onChange={(e) => setDraftTitle(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter") commitRename(t.id); if (e.key === "Escape") setEditingId(null); }}
                  className="flex-1 bg-transparent text-xs outline-none"
                  autoFocus
                />
                <button onClick={() => commitRename(t.id)} className="text-emerald-500"><Check className="h-3 w-3" /></button>
                <button onClick={() => setEditingId(null)} className="text-muted-foreground"><X className="h-3 w-3" /></button>
              </>
            ) : (
              <>
                <button onClick={() => switchThread(t.id)} className="flex-1 truncate text-left">
                  {t.title}
                </button>
                <button
                  onClick={() => { setEditingId(t.id); setDraftTitle(t.title); }}
                  className="hidden group-hover:block text-muted-foreground hover:text-foreground"
                  title="重命名"
                >
                  <Pencil className="h-3 w-3" />
                </button>
                <button
                  onClick={() => remove(t.id)}
                  className="hidden group-hover:block text-muted-foreground hover:text-red-500"
                  title="删除"
                >
                  <Trash2 className="h-3 w-3" />
                </button>
              </>
            )}
          </div>
        ))}
      </div>
    </aside>
  );
}
