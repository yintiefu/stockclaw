// 页面级 Ask-AI 嵌入式客户端：与 embedded_agent 图通信（LangGraph SDK）。
// 会话按 (route, scopeKey) 隔离在 Server checkpoint 中：
// - 打开抽屉只按精确 metadata 搜索恢复，绝不创建空 thread；
// - 首次发送才创建（metadata 仅 channel/route/scope_key/title，不含任何密钥）；
// - 「清空本页对话」删除精确匹配的 thread；
// - 本地中止只断开消费（onDisconnect: continue），从不取消 Server run。
// 旧版浏览器对话键与模型配置键不读取、不迁移、不删除。

import type { Client, Thread } from "@langchain/langgraph-sdk";
import { langGraphClient } from "./thread-adapter.ts";

/** 工厂只依赖 SDK Client 的子集，测试注入小型替身即可覆盖全部契约。 */
export type EmbeddedClientSubset = {
  threads: Pick<Client["threads"], "create" | "delete" | "getState" | "search">;
  runs: Pick<Client["runs"], "stream">;
};

export interface EmbeddedPageContext {
  /** 页面数据的口径时间（前端数据源时间），与 Server 盖章的 captured_at 分离。 */
  sourceAsOf: string;
  /** 当前页面快照正文，必须非空。 */
  content: string;
}

export interface EmbeddedToolUse {
  name: string;
  arg: string;
}

export interface EmbeddedMessage {
  id?: string;
  role: "user" | "assistant";
  content: string;
  tools?: EmbeddedToolUse[];
  /** 流式期间的临时回答，checkpoint 回填后整体替换。 */
  partial?: boolean;
}

export interface EmbeddedSendOptions {
  route: string;
  /** 同一路由内的细分（如个股代码）；缺省归一化为 route 本身，保证 scope_key 非空。 */
  scopeKey?: string;
  /** 已知的当前 thread id；提供时跳过搜索（重复发送/重开复用）。 */
  threadId?: string | null;
  pageContext: EmbeddedPageContext;
  message: string;
  onDelta?: (delta: string) => void;
  onTool?: (name: string, arg: string) => void;
  signal?: AbortSignal;
}

export interface EmbeddedSendResult {
  threadId: string;
  /** 流结束后的权威 checkpoint 消息（临时流式文本已被替换）。 */
  messages: EmbeddedMessage[];
}

type StreamChunk = { event: string; data: unknown };

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const asString = (value: unknown): string =>
  typeof value === "string" ? value : "";

/** scope_key 必须非空：无细分 scope 的页面直接用 route 隔离。 */
function normalizeScope(route: string, scopeKey?: string): string {
  const scope = (scopeKey ?? "").trim();
  return scope || route.trim();
}

/** 工具参数压成短展示串（旧 UI 的工具芯片只显示代码等关键参数）。 */
function toolArgText(args: unknown): string {
  if (!isRecord(args)) return "";
  const values = Object.values(args).flatMap((value) =>
    Array.isArray(value) ? value.map(String) : typeof value === "object" ? [] : [String(value)]);
  return values.filter((part) => part && part !== "[object Object]").join(" ").slice(0, 40);
}

/** LangChain 序列化消息的 content 可能是字符串或 content block 数组。 */
function contentText(content: unknown): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content.map((block) =>
      isRecord(block) && typeof block.text === "string" ? block.text : "",
    ).join("");
  }
  return "";
}

function toolUsesOf(message: Record<string, unknown>): EmbeddedToolUse[] | undefined {
  const calls = message.tool_calls;
  if (!Array.isArray(calls) || calls.length === 0) return undefined;
  const tools = calls.filter(isRecord).map((call) => ({
    name: asString(call.name),
    arg: toolArgText(call.args),
  })).filter((tool) => tool.name);
  return tools.length ? tools : undefined;
}

/** checkpoint 消息 → 抽屉消息（human→user、ai→assistant，跳过 system/tool 中间态）。 */
export function toEmbeddedMessages(values: unknown): EmbeddedMessage[] {
  const messages = isRecord(values) && Array.isArray(values.messages) ? values.messages : [];
  const mapped: EmbeddedMessage[] = [];
  for (const raw of messages) {
    if (!isRecord(raw)) continue;
    const type = asString(raw.type);
    const content = contentText(raw.content);
    if (type === "human") {
      mapped.push({ id: asString(raw.id) || undefined, role: "user", content });
    } else if (type === "ai") {
      const tools = toolUsesOf(raw);
      const previous = mapped[mapped.length - 1];
      // 带工具调用的中间 AI 消息（正文为空）与最终回答合并成一个气泡，
      // 与流式期间「工具芯片挂在回答气泡上」的展示保持一致。
      if (previous && previous.role === "assistant" && !content) {
        if (tools) previous.tools = [...(previous.tools ?? []), ...tools];
        continue;
      }
      if (previous && previous.role === "assistant" && !previous.content) {
        previous.content = content;
        if (tools) previous.tools = [...(previous.tools ?? []), ...tools];
        continue;
      }
      mapped.push({ id: asString(raw.id) || undefined, role: "assistant", content, tools });
    }
  }
  return mapped;
}

const metadataOf = (thread: Thread): Record<string, unknown> =>
  (thread.metadata ?? {}) as Record<string, unknown>;

/** 搜索结果必须逐字段精确匹配，防止 Server 端 metadata 过滤语义漂移串历史。 */
function matchesScope(thread: Thread, route: string, scopeKey: string): boolean {
  const metadata = metadataOf(thread);
  return metadata.channel === "embedded"
    && metadata.route === route
    && metadata.scope_key === scopeKey;
}

export function createEmbeddedClient(client: EmbeddedClientSubset) {
  async function findThread(route: string, scopeKey?: string): Promise<string | null> {
    const normalizedRoute = route.trim();
    const scope = normalizeScope(normalizedRoute, scopeKey);
    const found = await client.threads.search({
      metadata: { channel: "embedded", route: normalizedRoute, scope_key: scope },
      limit: 1,
      sortBy: "updated_at",
      sortOrder: "desc",
    });
    const newest = found[0];
    if (newest && matchesScope(newest, normalizedRoute, scope)) return newest.thread_id;
    return null;
  }

  async function loadMessages(threadId: string): Promise<EmbeddedMessage[]> {
    const state = await client.threads.getState(threadId);
    return toEmbeddedMessages(state.values);
  }

  async function send(options: EmbeddedSendOptions): Promise<EmbeddedSendResult> {
    const route = options.route.trim();
    const message = options.message.trim();
    const sourceAsOf = options.pageContext.sourceAsOf.trim();
    const content = options.pageContext.content.trim();
    if (!route) throw new Error("嵌入式对话缺少页面路由 (route)");
    if (!message) throw new Error("提问内容为空");
    if (!sourceAsOf) throw new Error("页面快照缺少数据时间 (source_as_of)");
    if (!content) throw new Error("页面快照内容为空 (content)");
    const scope = normalizeScope(route, options.scopeKey);

    let threadId = options.threadId ?? null;
    if (!threadId) {
      threadId = await findThread(route, scope);
      if (!threadId) {
        const created = await client.threads.create({
          metadata: {
            channel: "embedded",
            route,
            scope_key: scope,
            title: `问 AI · ${scope}`,
          },
        });
        threadId = created.thread_id;
      }
    }

    const source = client.runs.stream(threadId, "embedded_agent", {
      input: {
        messages: [{ role: "user", content: message }],
        page_context: { route, scope_key: scope, source_as_of: sourceAsOf, content },
      },
      streamMode: ["messages", "updates"],
      streamResumable: true,
      onDisconnect: "continue",
      durability: "sync",
      ...(options.signal ? { signal: options.signal } : {}),
    }) as AsyncIterable<StreamChunk>;

    for await (const chunk of source) {
      if (options.signal?.aborted) throw new DOMException("Aborted", "AbortError");
      if (chunk.event !== "messages" || !Array.isArray(chunk.data)) continue;
      const part = chunk.data[0];
      if (!isRecord(part)) continue;
      const type = asString(part.type);
      const isAssistant = type === "ai" || type.startsWith("AI") || asString(part.role) === "assistant";
      if (!isAssistant) continue;
      const text = contentText(part.content);
      if (text) options.onDelta?.(text);
      const tools = toolUsesOf(part);
      if (tools) for (const tool of tools) options.onTool?.(tool.name, tool.arg);
    }

    // 流结束后的 checkpoint 才是权威回答；临时 delta 只用于打字机展示。
    if (options.signal?.aborted) throw new DOMException("Aborted", "AbortError");
    const messages = await loadMessages(threadId);
    return { threadId, messages };
  }

  async function deleteThread(route: string, scopeKey?: string): Promise<void> {
    const normalizedRoute = route.trim();
    const scope = normalizeScope(normalizedRoute, scopeKey);
    const found = await client.threads.search({
      metadata: { channel: "embedded", route: normalizedRoute, scope_key: scope },
      sortBy: "updated_at",
      sortOrder: "desc",
    });
    for (const thread of found) {
      if (matchesScope(thread, normalizedRoute, scope)) {
        await client.threads.delete(thread.thread_id);
      }
    }
  }

  return { findThread, loadMessages, send, deleteThread };
}

const embeddedClient = createEmbeddedClient(langGraphClient);

/** 抽屉打开时恢复当前 scope 的最新 thread；不存在返回 null，绝不创建。 */
export function findEmbeddedThread(route: string, scopeKey?: string): Promise<string | null> {
  return embeddedClient.findThread(route, scopeKey);
}

export function loadEmbeddedMessages(threadId: string): Promise<EmbeddedMessage[]> {
  return embeddedClient.loadMessages(threadId);
}

export function sendEmbeddedMessage(options: EmbeddedSendOptions): Promise<EmbeddedSendResult> {
  return embeddedClient.send(options);
}

/** 清空本页对话：删除精确匹配 (route, scope_key) 的 embedded thread。 */
export function deleteEmbeddedThread(route: string, scopeKey?: string): Promise<void> {
  return embeddedClient.deleteThread(route, scopeKey);
}
