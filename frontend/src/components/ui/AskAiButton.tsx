import { useState, useRef, useEffect, useCallback } from "react";
import { Link, useLocation } from "react-router-dom";
import { Sparkles, X, Settings, Send, Loader2, Wrench, AlertCircle, Trash2 } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { cn } from "@/lib/utils";
import {
  findEmbeddedThread,
  loadEmbeddedMessages,
  sendEmbeddedMessage,
  deleteEmbeddedThread,
  type EmbeddedMessage,
} from "@/lib/agent/embedded-client";
import { SaveNoteButton } from "@/components/ui/SaveNoteButton";

// 对话持久化（#19）：历史存到 LangGraph embedded thread 的 checkpoint 里，
// 不再写浏览器 localStorage——旧版浏览器对话键与模型配置键属于迁移前数据，
// 按约定不读取、不迁移、也不删除。
//
// 会话按 (route, scopeKey) 隔离：不同页面 / 不同标的各自一个 thread；
// 打开抽屉只搜索恢复，首次发送才创建，清空按钮删除该 scope 的 thread。

export type { EmbeddedMessage };

const TOOL_LABEL: Record<string, string> = {
  query_quote: "查行情",
  query_valuation: "查估值",
  query_reports: "查研报",
  query_news: "查新闻",
};

interface Props {
  // 本分栏/本页要喂给用户 AI 的上下文，作为页面快照发给 embedded_agent。
  context: string;
  suggestions?: string[];
  label?: string;
  // 用来在**同一路由内**再切分对话。不传则只按路由区分（scope_key 归一化为路由）。
  // ⚠️ 不换路由就能换标的的页面（如个股页）必须传，否则 A 标的的历史会被发给
  // 问 B 标的的模型。
  scopeKey?: string;
}

type DrawerStatus = "probing" | "ready" | "unreachable";

// 「问 AI」入口 —— 把当前分栏内容作为页面快照，交给本地 LangGraph 的
// embedded_agent；AI 可自行调 A股数据工具作答。结论由用户模型给出，本产品不校准、不负责。
export function AskAiButton({ context, suggestions = [], label = "问 AI", scopeKey }: Props) {
  const { pathname } = useLocation();

  const [open, setOpen] = useState(false);
  const [status, setStatus] = useState<DrawerStatus>("probing");
  const [msgs, setMsgs] = useState<EmbeddedMessage[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  // 在跑的流式消费：关面板/换 scope 时中止**本地消费**（onDisconnect: continue，
  // Server 上的 run 继续跑完并落 checkpoint，重开时从 checkpoint 恢复完整回答）。
  const abortRef = useRef<AbortController | null>(null);
  // 当前 scope 对应的 embedded thread；null = 尚不存在（首次发送时创建）。
  const threadIdRef = useRef<string | null>(null);
  // 始终镜像当前 scope，供异步回调判断「对话是否已被换掉」。
  const scopeRef = useRef(`${pathname}#${scopeKey ?? ""}`);
  scopeRef.current = `${pathname}#${scopeKey ?? ""}`;

  // 打开或换 scope：搜索恢复最新 thread，绝不创建空 thread。
  const restore = useCallback(async () => {
    const scope = `${pathname}#${scopeKey ?? ""}`;
    try {
      const found = await findEmbeddedThread(pathname, scopeKey);
      if (scopeRef.current !== scope) return;
      threadIdRef.current = found;
      if (found) {
        const history = await loadEmbeddedMessages(found);
        if (scopeRef.current !== scope) return;
        setMsgs(history);
      } else {
        setMsgs([]);
      }
      setStatus("ready");
      setErr(null);
    } catch (e) {
      if (scopeRef.current !== scope) return;
      threadIdRef.current = null;
      setMsgs([]);
      setStatus("unreachable");
      setErr(e instanceof Error ? e.message : "Agent 服务不可用");
    }
  }, [pathname, scopeKey]);

  // 换页面/换标的 = 换一份对话：中止本地流式消费，重新搜索目标 scope 的 thread。
  useEffect(() => {
    if (!open) return;
    abortRef.current?.abort();
    abortRef.current = null;
    setLoading(false);
    setMsgs([]);
    setErr(null);
    setStatus("probing");
    void restore();
  }, [open, restore]);

  useEffect(() => () => abortRef.current?.abort(), []); // 组件卸载兜底

  const clearChat = () => {
    abortRef.current?.abort();
    abortRef.current = null;
    setLoading(false);
    setErr(null);
    setMsgs([]);
    threadIdRef.current = null;
    deleteEmbeddedThread(pathname, scopeKey).catch(() => {});
  };

  const close = () => {
    // 只断开本地消费，不取消 Server run（disconnect-continue 语义）。
    abortRef.current?.abort();
    abortRef.current = null;
    setLoading(false);
    setOpen(false);
  };

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [msgs, loading]);

  const send = async (text: string) => {
    const q = text.trim();
    if (!q || loading) return;
    setInput("");
    setErr(null);
    // 先放用户气泡 + 一个空的 assistant 气泡，流式往里填；checkpoint 回来后整体替换。
    setMsgs((m) => [...m, { role: "user", content: q }, { role: "assistant", content: "", partial: true }]);
    setLoading(true);
    const patchLast = (fn: (msg: EmbeddedMessage) => EmbeddedMessage) =>
      setMsgs((m) => m.map((msg, i) => (i === m.length - 1 && msg.role === "assistant" ? fn(msg) : msg)));
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;
    const startedScope = scopeRef.current;   // 这次请求属于哪份对话
    const alive = () => abortRef.current === ac && !ac.signal.aborted;
    try {
      const result = await sendEmbeddedMessage({
        route: pathname,
        scopeKey,
        threadId: threadIdRef.current,
        pageContext: {
          sourceAsOf: new Date().toLocaleTimeString("zh-CN"),
          // 后端要求快照非空；个别页面没给 context 时用明确占位，不让模型瞎猜数据。
          content: context.trim() || "（本页暂无数据快照）",
        },
        message: q,
        onDelta: (t) => { if (alive()) patchLast((msg) => ({ ...msg, content: msg.content + t })); },
        onTool: (name, arg) => {
          if (!alive()) return;
          patchLast((msg) => ({ ...msg, tools: [...(msg.tools ?? []), { name, arg }] }));
        },
        signal: ac.signal,
      });
      // 权威 checkpoint 消息整体替换临时流式文本。
      if (scopeRef.current === startedScope) {
        threadIdRef.current = result.threadId;
        setMsgs(result.messages);
      }
    } catch (e) {
      const superseded = abortRef.current !== null && abortRef.current !== ac;
      if (!superseded && scopeRef.current === startedScope) {
        // 一个字都没收到时把空气泡连同提问一起移除，避免界面留下孤立提问；
        // 收到一半的回答保留展示（checkpoint 里不会有它，重开即消失）。
        setMsgs((m) => {
          const last = m[m.length - 1];
          if (!last || last.role !== "assistant" || last.content) return m;
          const dropUser = m[m.length - 2]?.role === "user";
          return m.slice(0, dropUser ? -2 : -1);
        });
        if (!ac.signal.aborted) setErr(e instanceof Error ? e.message : "对话失败");
      }
    } finally {
      if (abortRef.current === ac) {
        abortRef.current = null;
        setLoading(false);
      }
    }
  };

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        className="inline-flex items-center gap-1.5 rounded-lg bg-primary/15 px-3 py-1.5 text-sm font-medium text-primary shadow-glow transition-colors hover:bg-primary/25"
      >
        <Sparkles className="h-4 w-4" />
        {label}
      </button>

      {open && (
        <div className="fixed inset-0 z-50 flex justify-end">
          <div className="absolute inset-0 bg-black/50" onClick={close} />
          <aside className="glass relative m-3 flex w-full max-w-md flex-col rounded-2xl">
            <div className="flex items-center justify-between border-b border-border/60 p-4">
              <span className="flex items-center gap-2 font-semibold text-glow">
                <Sparkles className="h-4 w-4 text-primary" /> 问 AI · 本页上下文
              </span>
              <div className="flex items-center gap-1">
                {msgs.length > 0 && (
                  // 历史存在 Server checkpoint 里，用户得有办法明确删掉本 scope 的 thread。
                  <button
                    onClick={clearChat}
                    title="清空本页对话"
                    aria-label="清空本页对话"
                    className="text-muted-foreground hover:text-foreground"
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                )}
                <button onClick={close} className="text-muted-foreground hover:text-foreground" aria-label="关闭">
                  <X className="h-4 w-4" />
                </button>
              </div>
            </div>

            {status === "unreachable" ? (
              // Agent 服务不可达：引导去设置页查看启动方式（模型配置只存在服务端）。
              <div className="flex-1 space-y-4 overflow-auto p-4 text-sm">
                <div className="flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-xs text-destructive">
                  <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" /> {err || "Agent 服务不可用"}
                </div>
                <div className="rounded-lg border border-warning/30 bg-warning/5 p-3 text-xs text-muted-foreground">
                  分析结论由你自己配置的 AI 给出，本产品只负责把本页数据打包成上下文、并让 AI 能调数据工具，
                  <b className="text-foreground">不校准、不背书、不对结果负责</b>。
                </div>
                <Link to="/settings" className="flex items-center justify-center gap-2 rounded-lg bg-primary/15 px-3 py-2 text-sm font-medium text-primary hover:bg-primary/25">
                  <Settings className="h-4 w-4" /> 查看 Agent 服务配置
                </Link>
              </div>
            ) : (
              <>
                <div ref={scrollRef} className="flex-1 space-y-3 overflow-auto p-4 text-sm">
                  {status === "probing" && msgs.length === 0 && (
                    <div className="flex items-center gap-2 text-xs text-muted-foreground">
                      <Loader2 className="h-3.5 w-3.5 animate-spin" /> 正在恢复本页对话…
                    </div>
                  )}
                  {status === "ready" && msgs.length === 0 && (
                    <div className="rounded-lg border border-warning/30 bg-warning/5 p-3 text-xs text-muted-foreground">
                      AI 可基于本页上下文、并自行调取 A股行情/估值/研报数据作答。结论由你的模型给出，
                      <b className="text-foreground">不构成投资建议</b>。
                    </div>
                  )}
                  {msgs.map((m, i) => (
                    <div key={m.id ?? i} className={cn("flex", m.role === "user" ? "justify-end" : "justify-start")}>
                      <div className={cn(
                        "max-w-[85%] rounded-2xl px-3 py-2 leading-relaxed",
                        m.role === "user" ? "bg-primary/20 text-foreground" : "bg-muted/40 text-foreground",
                      )}>
                        {m.tools && m.tools.length > 0 && (
                          <div className="mb-1.5 flex flex-wrap items-center gap-1">
                            <span className="text-[10px] text-muted-foreground/70">数据来源</span>
                            {m.tools.map((t, j) => (
                              <span key={j} className="inline-flex items-center gap-0.5 rounded-full bg-primary/10 px-2 py-0.5 text-[10px] text-primary">
                                <Wrench className="h-2.5 w-2.5" /> {TOOL_LABEL[t.name] || t.name}{t.arg ? ` ${t.arg}` : ""}
                              </span>
                            ))}
                          </div>
                        )}
                        {m.role === "assistant" ? (
                          <div className="prose prose-sm dark:prose-invert max-w-none wrap-break-word text-foreground">
                            <ReactMarkdown remarkPlugins={[remarkGfm]}>{m.content}</ReactMarkdown>
                          </div>
                        ) : (
                          <p className="whitespace-pre-wrap wrap-break-word">{m.content}</p>
                        )}
                        {m.role === "assistant" && m.content && !(loading && i === msgs.length - 1) && (
                          <div className="mt-1.5"><SaveNoteButton kind="问AI" title={`问 AI · ${msgs[i - 1]?.content?.slice(0, 24) || "对话"}`} content={m.content} /></div>
                        )}
                      </div>
                    </div>
                  ))}
                  {loading && (
                    <div className="flex items-center gap-2 text-xs text-muted-foreground">
                      <Loader2 className="h-3.5 w-3.5 animate-spin" /> AI 正在思考 / 调取数据…
                    </div>
                  )}
                  {err && (
                    <div className="flex items-center gap-2 rounded-lg border border-destructive/30 bg-destructive/5 p-2 text-xs text-destructive">
                      <AlertCircle className="h-3.5 w-3.5 shrink-0" /> {err}
                    </div>
                  )}
                  {status === "ready" && msgs.length === 0 && suggestions.length > 0 && (
                    <div className="flex flex-wrap gap-1.5 pt-1">
                      {suggestions.map((s) => (
                        <button key={s} onClick={() => send(s)} className="rounded-full border border-border bg-muted/40 px-2.5 py-1 text-xs hover:border-primary/40 hover:text-primary">
                          {s}
                        </button>
                      ))}
                    </div>
                  )}
                </div>

                <div className="border-t border-border/60 p-3">
                  <div className="flex items-end gap-2">
                    <textarea
                      value={input}
                      onChange={(e) => setInput(e.target.value)}
                      onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(input); } }}
                      rows={1}
                      placeholder="就本页内容提问…"
                      className="flex-1 resize-none rounded-lg border border-border bg-black/20 px-3 py-2 text-sm outline-hidden focus:border-primary/50"
                    />
                    <button onClick={() => send(input)} disabled={loading || !input.trim()}
                      className="rounded-lg bg-primary/15 p-2 text-primary hover:bg-primary/25 disabled:opacity-40">
                      <Send className="h-4 w-4" />
                    </button>
                  </div>
                </div>
              </>
            )}
          </aside>
        </div>
      )}
    </>
  );
}
