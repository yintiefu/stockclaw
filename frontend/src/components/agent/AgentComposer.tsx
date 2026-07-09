import { useState } from "react";
import { Send, Loader2 } from "lucide-react";
import { useAgentStore } from "@/lib/stores/agent";
import { useAgentStream } from "@/hooks/useAgentStream";

const QUICK_PROMPTS = [
  "分析茅台 给目标价止损止盈仓位节奏",
  "宁德时代 现在能买吗",
  "帮我对比下光伏板块几只龙头",
];

export function AgentComposer() {
  const [content, setContent] = useState("");
  const [style, setStyle] = useState<"conservative" | "balanced" | "aggressive">("balanced");
  const { send, abort } = useAgentStream();
  const streaming = useAgentStore((s) => s.streaming.active);
  const currentThreadId = useAgentStore((s) => s.currentThreadId);

  const submit = () => {
    if (!content.trim() || streaming) return;
    send({
      threadId: currentThreadId,
      content: content.trim(),
      contextCodes: [],
      style,
    });
    setContent("");
  };

  return (
    <div className="border-t border-border/50 p-3">
      <div className="mb-2 flex gap-1.5 flex-wrap">
        {QUICK_PROMPTS.map((q) => (
          <button
            key={q}
            onClick={() => setContent(q)}
            disabled={streaming}
            className="rounded-full bg-muted/40 px-2.5 py-1 text-[11px] text-muted-foreground hover:bg-muted/70 disabled:opacity-50"
          >
            {q}
          </button>
        ))}
      </div>
      <div className="flex items-end gap-2">
        <textarea
          value={content}
          onChange={(e) => setContent(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              submit();
            }
          }}
          placeholder="输入消息；Enter 发送，Shift+Enter 换行"
          rows={2}
          className="flex-1 resize-none rounded-lg border border-border/50 bg-transparent px-3 py-2 text-sm outline-none focus:border-primary/50"
          disabled={streaming}
        />
        <select
          value={style}
          onChange={(e) => setStyle(e.target.value as typeof style)}
          className="rounded-lg border border-border/50 bg-transparent px-2 py-2 text-xs"
        >
          <option value="conservative">保守</option>
          <option value="balanced">平衡</option>
          <option value="aggressive">激进</option>
        </select>
        {streaming ? (
          <button
            onClick={abort}
            className="rounded-lg bg-red-500/10 px-3 py-2 text-sm text-red-500 hover:bg-red-500/20"
          >
            <Loader2 className="h-4 w-4 animate-spin" />
          </button>
        ) : (
          <button
            onClick={submit}
            disabled={!content.trim()}
            className="rounded-lg bg-primary px-3 py-2 text-sm font-medium text-primary-foreground disabled:opacity-40"
          >
            <Send className="h-4 w-4" />
          </button>
        )}
      </div>
    </div>
  );
}
