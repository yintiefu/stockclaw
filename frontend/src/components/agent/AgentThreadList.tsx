import { useState } from "react";
import { MessageSquarePlus, Pencil, Trash2 } from "lucide-react";

import type { AgentRecoveryWarning, AgentThreadSummary } from "@/lib/agent/types";

/** 1B 最小线程控件：新建/切换/重命名/删除 + 损坏文件恢复提示。不引入最终三栏布局。 */
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

  const active = threads.find((t) => t.id === activeThreadId) ?? null;

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <select
          aria-label="会话线程"
          value={activeThreadId ?? ""}
          onChange={(e) => onSelect(e.target.value)}
          className="min-w-0 flex-1 rounded-lg border border-border bg-black/20 px-3 py-2 text-sm outline-none focus:border-primary/50"
        >
          {threads.map((thread) => (
            <option key={thread.id} value={thread.id}>
              {thread.title}
            </option>
          ))}
          {threads.length === 0 && <option value="">（暂无线程）</option>}
        </select>
        <button title="新建会话" aria-label="新建会话" onClick={onCreate}
          className="rounded-lg border border-border p-2 text-muted-foreground hover:text-primary">
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
          className="rounded-lg border border-border p-2 text-muted-foreground hover:text-primary disabled:opacity-40"
        >
          <Pencil className="h-4 w-4" />
        </button>
        <button
          title={canDeleteActive ? "删除会话" : "运行中的会话不可删除"}
          aria-label="删除会话"
          disabled={!activeThreadId || !canDeleteActive}
          onClick={() => activeThreadId && onDelete(activeThreadId)}
          className="rounded-lg border border-border p-2 text-muted-foreground hover:text-primary disabled:opacity-40"
        >
          <Trash2 className="h-4 w-4" />
        </button>
      </div>

      {editing && (
        <div className="flex items-center gap-2">
          <input
            aria-label="新会话标题"
            value={draftTitle}
            onChange={(e) => setDraftTitle(e.target.value)}
            className="min-w-0 flex-1 rounded-lg border border-border bg-black/20 px-3 py-2 text-sm outline-none focus:border-primary/50"
          />
          <button
            onClick={() => {
              if (draftTitle.trim()) onRename(editing, draftTitle.trim());
              setEditing(null);
            }}
            className="rounded-lg bg-primary/15 px-3 py-2 text-sm font-medium text-primary hover:bg-primary/25"
          >
            确认
          </button>
          <button onClick={() => setEditing(null)}
            className="rounded-lg border border-border px-3 py-2 text-sm text-muted-foreground">
            取消
          </button>
        </div>
      )}

      {warnings.length > 0 && (
        <div className="rounded-lg border border-border bg-black/20 px-3 py-2 text-xs text-muted-foreground">
          {warnings.map((warning) => (
            <div key={`${warning.document_type}-${warning.filename}`}>
              检测到损坏的{warning.document_type === "thread" ? "线程" : "运行"}文件 {warning.filename}，已隔离；健康会话不受影响。
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
