/** 会话列表：assistant-ui 原生原语（新建/切换/重命名/删除），无归档控件。 */
import { useState } from "react";
import { MessageSquarePlus, Pencil, Trash2 } from "lucide-react";
import {
  ThreadListItemPrimitive,
  ThreadListPrimitive,
  useAui,
  useAuiState,
} from "@assistant-ui/react";

function RenameThreadButton() {
  const aui = useAui();
  const currentTitle = useAuiState((state) => state.threadListItem.title ?? "");
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");

  if (!editing) {
    return (
      <button
        type="button"
        aria-label="重命名会话"
        title="重命名会话"
        onClick={(event) => {
          event.stopPropagation();
          setDraft(currentTitle);
          setEditing(true);
        }}
        className="grid h-7 w-7 shrink-0 place-items-center rounded-md text-muted-foreground hover:bg-muted/60 hover:text-foreground"
      >
        <Pencil className="size-4" aria-hidden />
      </button>
    );
  }

  return (
    <form
      className="flex min-w-0 flex-1 items-center gap-1"
      onSubmit={async (event) => {
        event.preventDefault();
        event.stopPropagation();
        const title = draft.trim();
        if (!title) return;
        await aui.threadListItem.rename(title);
        setEditing(false);
      }}
    >
      <input
        autoFocus
        aria-label="会话标题"
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            event.preventDefault();
            setEditing(false);
          }
        }}
        className="min-w-0 flex-1 rounded-md border border-border bg-black/20 px-2 py-1 text-sm outline-hidden focus:border-primary/50"
      />
      <button
        type="submit"
        aria-label="确认重命名"
        title="确认重命名"
        disabled={!draft.trim()}
        className="shrink-0 rounded-md px-2 py-1 text-xs text-primary hover:bg-primary/15 disabled:opacity-40"
      >
        确认
      </button>
    </form>
  );
}

function ThreadListItem() {
  const itemId = useAuiState((state) => state.threadListItem.id);
  return (
    <ThreadListItemPrimitive.Root
      data-testid={`agent-thread-${itemId}`}
      className="group relative mb-0.5 flex items-center rounded-md pr-1 data-[active=true]:bg-primary/10 data-[active=true]:ring-1 data-[active=true]:ring-primary/30 hover:bg-muted/50"
    >
      <ThreadListItemPrimitive.Trigger className="min-w-0 flex-1 px-2.5 py-2 text-left focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-primary/60">
        <span className="block truncate text-sm font-medium">
          <ThreadListItemPrimitive.Title fallback="新会话" />
        </span>
      </ThreadListItemPrimitive.Trigger>
      <RenameThreadButton />
      <ThreadListItemPrimitive.Delete
        aria-label="删除会话"
        title="删除会话"
        className="grid h-7 w-7 shrink-0 place-items-center rounded-md text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
      >
        <Trash2 className="size-4" aria-hidden />
      </ThreadListItemPrimitive.Delete>
    </ThreadListItemPrimitive.Root>
  );
}

export function AgentThreadList() {
  return (
    <div className="min-h-full">
      <div className="sticky top-0 z-2 flex items-center justify-between gap-2 border-b border-border/70 bg-background p-3">
        <h2 className="text-sm font-semibold">会话</h2>
        <ThreadListPrimitive.New
          aria-label="新建会话"
          title="新建会话"
          className="grid h-8 w-8 place-items-center rounded-md text-muted-foreground hover:bg-muted/60 hover:text-primary focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-primary/60"
        >
          <MessageSquarePlus className="size-4" aria-hidden />
        </ThreadListPrimitive.New>
      </div>
      <div className="p-2">
        <ThreadListPrimitive.Items components={{ ThreadListItem }} />
      </div>
    </div>
  );
}
