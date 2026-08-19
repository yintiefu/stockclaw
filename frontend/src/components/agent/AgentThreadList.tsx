import { useMemo, useState } from "react";
import { AlertTriangle, MessageSquarePlus, Pencil, Search, Trash2 } from "lucide-react";

import type { AgentRecoveryWarning, AgentThreadSummary } from "@/lib/agent/types";

const STATUS_LABEL = {
  running: "运行中",
  awaiting_approval: "待审批",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
  interrupted: "已中断",
} as const;

function updatedLabel(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "时间未知";
  return date.toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" });
}

/** 左栏线程列表：本地筛选；所有写操作仍由页面携带权威 revision 执行。 */
export function AgentThreadList({
  threads,
  activeThreadId,
  warnings,
  canDeleteActive,
  onSelect,
  onCreate,
  onRename,
  onDelete,
}: {
  threads: AgentThreadSummary[];
  activeThreadId: string | null;
  warnings: AgentRecoveryWarning[];
  canDeleteActive: boolean;
  onSelect: (threadId: string) => void;
  onCreate: () => void;
  onRename: (threadId: string, title: string) => void;
  onDelete: (threadId: string) => void;
}) {
  const [editing, setEditing] = useState<string | null>(null);
  const [draftTitle, setDraftTitle] = useState("");
  const [query, setQuery] = useState("");

  const active = threads.find((t) => t.id === activeThreadId) ?? null;
  const filtered = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    return needle ? threads.filter((thread) => thread.title.toLocaleLowerCase().includes(needle)) : threads;
  }, [query, threads]);

  return (
    <div className="min-h-full">
      <div className="sticky top-0 z-[1] space-y-2 border-b border-border/70 bg-background p-3">
        <div className="flex items-center justify-between gap-2">
          <h2 className="text-sm font-semibold">会话</h2>
          <div className="flex items-center gap-1">
            <button title="新建会话" aria-label="新建会话" onClick={onCreate}
              className="grid h-8 w-8 place-items-center rounded-md text-muted-foreground hover:bg-muted/60 hover:text-primary">
              <MessageSquarePlus className="h-4 w-4" />
            </button>
            <button
              title="重命名会话" aria-label="重命名会话"
              disabled={!activeThreadId}
              onClick={() => {
                if (!active) return;
                setEditing(active.id);
                setDraftTitle(active.title);
              }}
              className="grid h-8 w-8 place-items-center rounded-md text-muted-foreground hover:bg-muted/60 hover:text-primary disabled:opacity-40"
            >
              <Pencil className="h-4 w-4" />
            </button>
            <button
              title={canDeleteActive ? "删除会话" : "运行中的会话不可删除"}
              aria-label="删除会话"
              disabled={!activeThreadId || !canDeleteActive}
              onClick={() => {
                if (activeThreadId && window.confirm(`确认删除会话「${active?.title ?? ""}」？`)) onDelete(activeThreadId);
              }}
              className="grid h-8 w-8 place-items-center rounded-md text-muted-foreground hover:bg-muted/60 hover:text-primary disabled:opacity-40"
            >
              <Trash2 className="h-4 w-4" />
            </button>
          </div>
        </div>
        <label className="flex items-center gap-2 rounded-md border border-border bg-black/20 px-2.5 py-1.5 focus-within:border-primary/50">
          <Search className="h-3.5 w-3.5 text-muted-foreground" aria-hidden />
          <input
            type="search"
            aria-label="搜索会话"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            className="min-w-0 flex-1 bg-transparent text-sm outline-none"
            placeholder="搜索标题"
          />
        </label>
      </div>

      {editing && (
        <div className="flex items-center gap-2 border-b border-border/70 p-2">
          <input
            aria-label="新会话标题"
            value={draftTitle}
            onChange={(e) => setDraftTitle(e.target.value)}
            className="min-w-0 flex-1 rounded-md border border-border bg-black/20 px-2 py-1.5 text-sm outline-none focus:border-primary/50"
          />
          <button
            onClick={() => {
              if (draftTitle.trim()) onRename(editing, draftTitle.trim());
              setEditing(null);
            }}
            aria-label="确认重命名"
            className="rounded-md bg-primary/15 px-2 py-1.5 text-xs font-medium text-primary hover:bg-primary/25"
          >
            确认
          </button>
          <button onClick={() => setEditing(null)}
            className="rounded-md border border-border px-2 py-1.5 text-xs text-muted-foreground">
            取消
          </button>
        </div>
      )}

      {warnings.length > 0 && (
        <div className="border-b border-border/70 px-3 py-2 text-xs text-muted-foreground">
          {warnings.map((warning) => (
            <div key={`${warning.document_type}-${warning.filename}`} className="flex items-start gap-1.5">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warning" aria-hidden />
              <span>
                检测到损坏的{warning.document_type === "thread" ? "线程" : "运行"}文件 {warning.filename}，已隔离；健康会话不受影响。
              </span>
            </div>
          ))}
        </div>
      )}

      <div className="p-2">
        {filtered.map((thread) => {
          const selected = thread.id === activeThreadId;
          return (
            <button
              key={thread.id}
              type="button"
              aria-current={selected ? "true" : undefined}
              aria-label={`${thread.title}，${thread.last_run ? STATUS_LABEL[thread.last_run.status] : "未运行"}`}
              onClick={() => onSelect(thread.id)}
              className={`mb-1 w-full min-w-0 rounded-md border px-2.5 py-2 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60 ${
                selected ? "border-primary/50 bg-primary/10 ring-1 ring-primary/30" : "border-transparent hover:bg-muted/50"
              }`}
            >
              <span className="block truncate text-sm font-medium" title={thread.title}>{thread.title}</span>
              <span className="mt-1 flex items-center justify-between gap-2 text-xs text-muted-foreground">
                <time dateTime={thread.updated_at}>{updatedLabel(thread.updated_at)}</time>
                <span>{thread.last_run ? STATUS_LABEL[thread.last_run.status] : "未运行"}</span>
              </span>
            </button>
          );
        })}
        {filtered.length === 0 && (
          <p className="px-2 py-6 text-center text-xs text-muted-foreground">没有匹配的会话</p>
        )}
      </div>
    </div>
  );
}
