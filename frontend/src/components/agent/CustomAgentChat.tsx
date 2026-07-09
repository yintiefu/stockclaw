import { useEffect, useRef } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useAgentStore } from "@/lib/stores/agent";
import { ToolTrace } from "./ToolTrace";
import { DecisionCard } from "./DecisionCard";  // Task 12 实现但本任务先 import
import { Bot, User } from "lucide-react";
import type { ChatMessage } from "@/lib/types/agent";

// 模块级常量——selector 返回值必须保持引用稳定，
// 否则 useSyncExternalStore 每次都判"变化了"导致 Maximum update depth exceeded。
const EMPTY_MESSAGES: ChatMessage[] = [];

export function CustomAgentChat() {
  const currentThreadId = useAgentStore((s) => s.currentThreadId);
  const messages = useAgentStore((s) =>
    currentThreadId ? s.messagesByThread[currentThreadId] ?? EMPTY_MESSAGES : EMPTY_MESSAGES,
  );
  const scrollRef = useRef<HTMLDivElement>(null);

  // 新消息时自动滚到底
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  if (!currentThreadId) {
    return (
      <div className="flex flex-1 items-center justify-center p-6 text-sm text-muted-foreground">
        点击左侧「+ 新建会话」开始
      </div>
    );
  }

  return (
    <div ref={scrollRef} className="flex-1 overflow-auto px-4 py-3">
      {messages.map((m) => (
        <div key={m.id} className="mb-4 flex gap-3">
          <div className={["h-7 w-7 shrink-0 rounded-full flex items-center justify-center",
            m.role === "user" ? "bg-primary/20 text-primary" : "glass text-muted-foreground"].join(" ")}>
            {m.role === "user" ? <User className="h-3.5 w-3.5" /> : <Bot className="h-3.5 w-3.5" />}
          </div>
          <div className="flex-1 min-w-0">
            <div className="text-xs text-muted-foreground mb-1">
              {m.role === "user" ? "你" : "Agent"}
            </div>
            {m.content && (
              <div className="prose prose-sm dark:prose-invert max-w-none">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {m.content}
                </ReactMarkdown>
              </div>
            )}
            {m.toolTraces.map((t, i) => (
              <ToolTrace key={`${t.tool}-${i}`} trace={t} />
            ))}
            {m.decisionCard && <DecisionCard card={m.decisionCard} />}
            {m.citations && m.citations.length > 0 && (
              <div className="mt-2 text-[11px] text-muted-foreground">
                数据出处：{m.citations.map((c) => c.source).join("、")}
              </div>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}
