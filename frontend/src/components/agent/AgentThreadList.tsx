import { useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, Check, MessageSquarePlus, MoreHorizontal, Pencil, Search, Trash2, X } from "lucide-react";

import type { AgentRecoveryWarning, AgentThreadSummary } from "@/lib/agent/types";

const STATUS_LABEL = {
  running: "运行中",
  awaiting_approval: "待审批",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
  interrupted: "已中断",
} as const;

type ThreadGroup = "today" | "yesterday" | "week" | "earlier";

const GROUP_ORDER: ThreadGroup[] = ["today", "yesterday", "week", "earlier"];
const GROUP_LABEL: Record<ThreadGroup, string> = {
  today: "今天",
  yesterday: "昨天",
  week: "近 7 天",
  earlier: "更早",
};

function updatedLabel(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "时间未知";
  return date.toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" });
}

function groupOf(value: string, now: Date): ThreadGroup {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "earlier";
  const startOfDay = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const days = Math.floor((startOfDay(now) - startOfDay(date)) / 86_400_000);
  if (days <= 0) return "today";
  if (days === 1) return "yesterday";
  if (days <= 7) return "week";
  return "earlier";
}

function threadBusy(thread: AgentThreadSummary) {
  return thread.last_run?.status === "running" || thread.last_run?.status === "awaiting_approval";
}

/** 左栏线程列表：时间分组 + 行内三点菜单（重命名/删除）；写操作仍由页面携带权威 revision 执行。 */
export function AgentThreadList({
  threads,
  activeThreadId,
  warnings,
  onSelect,
  onCreate,
  onRename,
  onDelete,
}: {
  threads: AgentThreadSummary[];
  activeThreadId: string | null;
  warnings: AgentRecoveryWarning[];
  onSelect: (threadId: string) => void;
  onCreate: () => void;
  onRename: (threadId: string, title: string) => void;
  onDelete: (threadId: string) => void;
}) {
  const [editing, setEditing] = useState<string | null>(null);
  const [draftTitle, setDraftTitle] = useState("");
  const [query, setQuery] = useState("");
  // 当前展开菜单的线程：同一时刻至多一个
  const [menuFor, setMenuFor] = useState<string | null>(null);
  const kebabRef = useRef<HTMLButtonElement | null>(null);

  const filtered = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    return needle ? threads.filter((thread) => thread.title.toLocaleLowerCase().includes(needle)) : threads;
  }, [query, threads]);

  const grouped = useMemo(() => {
    const now = new Date();
    const buckets = new Map<ThreadGroup, AgentThreadSummary[]>();
    for (const thread of filtered) {
      const key = groupOf(thread.updated_at, now);
      buckets.set(key, [...buckets.get(key) ?? [], thread]);
    }
    return GROUP_ORDER.filter((key) => buckets.has(key))
      .map((key) => ({ key, items: buckets.get(key)! }));
  }, [filtered]);

  // 菜单外点击 / Escape 关闭；Escape 关闭后焦点回到三点按钮
  useEffect(() => {
    if (menuFor === null) return;
    const row = `[data-thread-row="${menuFor}"]`;
    const onPointerDown = (event: PointerEvent) => {
      if (!event.target || !(event.target as Element).closest?.(row)) setMenuFor(null);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setMenuFor(null);
      kebabRef.current?.focus();
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [menuFor]);

  const startRename = (thread: AgentThreadSummary) => {
    setMenuFor(null);
    setEditing(thread.id);
    setDraftTitle(thread.title);
  };

  const confirmRename = () => {
    if (editing && draftTitle.trim()) onRename(editing, draftTitle.trim());
    setEditing(null);
  };

  const requestDelete = (thread: AgentThreadSummary) => {
    setMenuFor(null);
    if (threadBusy(thread)) return;
    if (window.confirm(`确认删除会话「${thread.title}」？`)) onDelete(thread.id);
  };

  return (
    <div className="min-h-full">
      <div className="sticky top-0 z-2 space-y-2 border-b border-border/70 bg-background p-3">
        <div className="flex items-center justify-between gap-2">
          <h2 className="text-sm font-semibold">会话</h2>
          <button title="新建会话" aria-label="新建会话" onClick={onCreate}
            className="grid h-8 w-8 place-items-center rounded-md text-muted-foreground hover:bg-muted/60 hover:text-primary">
            <MessageSquarePlus className="h-4 w-4" />
          </button>
        </div>
        <label className="flex items-center gap-2 rounded-md border border-border bg-black/20 px-2.5 py-1.5 focus-within:border-primary/50">
          <Search className="h-3.5 w-3.5 text-muted-foreground" aria-hidden />
          <input
            type="search"
            aria-label="搜索会话"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            className="min-w-0 flex-1 bg-transparent text-sm outline-hidden"
            placeholder="搜索标题"
          />
        </label>
      </div>

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
        {grouped.map(({ key, items }) => (
          <section key={key} aria-label={GROUP_LABEL[key]} className="mb-1">
            <h3 className="px-2.5 pb-1 pt-2 text-xs font-medium text-muted-foreground">{GROUP_LABEL[key]}</h3>
            {items.map((thread) => {
              const selected = thread.id === activeThreadId;
              const busy = threadBusy(thread);
              const menuOpen = menuFor === thread.id;
              return (
                <div
                  key={thread.id}
                  data-thread-row={thread.id}
                  className={`group/thread relative mb-0.5 flex items-center gap-1 rounded-md pr-1 ${
                    selected ? "bg-primary/10 ring-1 ring-primary/30" : "hover:bg-muted/50"
                  }`}
                >
                  {editing === thread.id ? (
                    <div className="flex min-w-0 flex-1 items-center gap-1 px-1.5 py-1.5">
                      <input
                        aria-label="新会话标题"
                        autoFocus
                        value={draftTitle}
                        onChange={(event) => setDraftTitle(event.target.value)}
                        onKeyDown={(event) => {
                          if (event.key === "Enter") confirmRename();
                          if (event.key === "Escape") setEditing(null);
                        }}
                        className="min-w-0 flex-1 rounded-md border border-border bg-black/20 px-2 py-1 text-sm outline-hidden focus:border-primary/50"
                      />
                      <button
                        type="button"
                        aria-label="确认重命名"
                        disabled={!draftTitle.trim()}
                        onClick={confirmRename}
                        className="grid h-7 w-7 shrink-0 place-items-center rounded-md text-primary hover:bg-primary/15 disabled:opacity-40"
                      >
                        <Check className="h-4 w-4" />
                      </button>
                      <button
                        type="button"
                        aria-label="取消重命名"
                        onClick={() => setEditing(null)}
                        className="grid h-7 w-7 shrink-0 place-items-center rounded-md text-muted-foreground hover:bg-muted/60"
                      >
                        <X className="h-4 w-4" />
                      </button>
                    </div>
                  ) : (
                    <>
                      <button
                        type="button"
                        aria-current={selected ? "true" : undefined}
                        aria-label={`${thread.title}，${thread.last_run ? STATUS_LABEL[thread.last_run.status] : "未运行"}`}
                        onClick={() => onSelect(thread.id)}
                        className="min-w-0 flex-1 px-2.5 py-2 text-left focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-primary/60"
                      >
                        <span className="block truncate text-sm font-medium" title={thread.title}>{thread.title}</span>
                        <span className="mt-0.5 flex items-center justify-between gap-2 text-xs text-muted-foreground">
                          <time dateTime={thread.updated_at}>{updatedLabel(thread.updated_at)}</time>
                          <span>{thread.last_run ? STATUS_LABEL[thread.last_run.status] : "未运行"}</span>
                        </span>
                      </button>
                      <button
                        type="button"
                        aria-label="会话操作"
                        title="会话操作"
                        aria-haspopup="menu"
                        aria-expanded={menuOpen}
                        ref={menuOpen ? kebabRef : undefined}
                        onClick={() => setMenuFor(menuOpen ? null : thread.id)}
                        className={`grid h-7 w-7 shrink-0 place-items-center rounded-md transition-opacity hover:bg-muted focus-visible:opacity-100 focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-primary/60 group-hover/thread:opacity-100 max-xl:opacity-100 ${
                          menuOpen ? "bg-muted text-foreground opacity-100" : "text-muted-foreground opacity-0"
                        }`}
                      >
                        <MoreHorizontal className="h-4 w-4" />
                      </button>
                      {menuOpen && (
                        <div
                          role="menu"
                          aria-label="会话菜单"
                          className="absolute right-1 top-full z-20 mt-1 min-w-[128px] overflow-hidden rounded-md border border-border bg-background py-1 shadow-lg"
                        >
                          <button
                            type="button"
                            role="menuitem"
                            autoFocus
                            onClick={() => startRename(thread)}
                            className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm hover:bg-muted/60 focus-visible:outline-hidden focus-visible:bg-muted/60"
                          >
                            <Pencil className="h-3.5 w-3.5 text-muted-foreground" aria-hidden />
                            重命名
                          </button>
                          <button
                            type="button"
                            role="menuitem"
                            disabled={busy}
                            title={busy ? "运行中的会话不可删除" : "删除会话"}
                            onClick={() => requestDelete(thread)}
                            className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm text-destructive hover:bg-destructive/10 disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent focus-visible:outline-hidden focus-visible:bg-destructive/10"
                          >
                            <Trash2 className="h-3.5 w-3.5" aria-hidden />
                            删除
                          </button>
                        </div>
                      )}
                    </>
                  )}
                </div>
              );
            })}
          </section>
        ))}
        {filtered.length === 0 && (
          <p className="px-2 py-6 text-center text-xs text-muted-foreground">没有匹配的会话</p>
        )}
      </div>
    </div>
  );
}
