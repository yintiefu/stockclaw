// 页面级 Ask-AI 嵌入式客户端：与 embedded_agent 图通信。
// 通过 (route, scopeKey) 隔离会话，维护页面快照 page_context，不携带 Bearer 鉴权。

import { langGraphClient, resolveAgentApiUrl } from "./thread-adapter.ts";
import { storageGet, storageSet, storageRemove } from "../storage.ts";

export interface PageContextInput {
  route: string;
  scope_key: string;
  source_as_of: string;
  content: string;
}

export interface AskAiMessage {
  id?: string;
  role: "user" | "assistant" | "system";
  content: string;
}

export interface AskAiStreamOptions {
  route: string;
  scopeKey?: string;
  context: string;
  message: string;
  history?: AskAiMessage[];
  onDelta?: (delta: string) => void;
  onComplete?: (fullText: string) => void;
  onError?: (err: Error) => void;
  signal?: AbortSignal;
}

const THREAD_KEY_PREFIX = "vr-askai-thread:";

function getThreadStorageKey(route: string, scopeKey?: string): string {
  return `${THREAD_KEY_PREFIX}${route}${scopeKey ? `#${scopeKey}` : ""}`;
}

export async function getOrCreateEmbeddedThread(route: string, scopeKey?: string): Promise<string> {
  const key = getThreadStorageKey(route, scopeKey);
  const existingThreadId = storageGet(key);

  if (existingThreadId) {
    try {
      const thread = await langGraphClient.threads.get(existingThreadId);
      if (thread && thread.thread_id) {
        return thread.thread_id;
      }
    } catch {
      storageRemove(key);
    }
  }

  const thread = await langGraphClient.threads.create({
    metadata: {
      channel: "embedded",
      route,
      scope_key: scopeKey || "",
    },
  });

  storageSet(key, thread.thread_id);
  return thread.thread_id;
}

export function clearEmbeddedThread(route: string, scopeKey?: string): void {
  const key = getThreadStorageKey(route, scopeKey);
  storageRemove(key);
}

/** 流式向 embedded_agent 发送提问并接收回答。 */
export async function streamEmbeddedChat(options: AskAiStreamOptions): Promise<string> {
  const { route, scopeKey = "", context, message, history = [], onDelta, onComplete, signal } = options;

  const threadId = await getOrCreateEmbeddedThread(route, scopeKey);
  const apiUrl = resolveAgentApiUrl();
  const streamUrl = `${apiUrl.replace(/\/$/, "")}/threads/${threadId}/runs/stream`;

  const pageContext: PageContextInput = {
    route,
    scope_key: scopeKey,
    source_as_of: new Date().toLocaleTimeString("zh-CN"),
    content: context,
  };

  const messagesPayload = [
    ...history.map((h) => ({ role: h.role, content: h.content })),
    { role: "user", content: message },
  ];

  const payload = {
    assistant_id: "embedded_agent",
    input: {
      messages: messagesPayload,
      page_context: pageContext,
    },
    stream_mode: ["messages", "updates"],
  };

  const resp = await fetch(streamUrl, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
    signal,
  });

  if (!resp.ok) {
    let errDetail = `HTTP ${resp.status}`;
    try {
      const errJson = await resp.json();
      errDetail = errJson.detail || errJson.message || errDetail;
    } catch {
      /* ignore */
    }
    throw new Error(`Ask-AI 响应失败: ${errDetail}`);
  }

  if (!resp.body) {
    throw new Error("后端无响应流");
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let fullText = "";

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";

    let currentEventName = "message";
    for (const line of lines) {
      const trimmed = line.trimEnd();
      if (!trimmed || trimmed.startsWith(":")) continue;

      if (trimmed.startsWith("event:")) {
        currentEventName = trimmed.slice(6).trim();
        continue;
      }

      if (trimmed.startsWith("data:")) {
        const dataStr = trimmed.slice(5).trim();
        if (!dataStr) continue;

        try {
          const parsed = JSON.parse(dataStr);
          if (currentEventName === "messages" || currentEventName === "messages/complete") {
            if (Array.isArray(parsed)) {
              for (const msgChunk of parsed) {
                if (msgChunk.role === "assistant" || msgChunk.type === "ai" || msgChunk.type === "AIMessageChunk") {
                  const content = msgChunk.content || "";
                  if (typeof content === "string" && content) {
                    fullText += content;
                    onDelta?.(content);
                  }
                }
              }
            } else if (parsed && typeof parsed === "object") {
              const content = parsed.content || (parsed.delta && parsed.delta.content) || "";
              if (typeof content === "string" && content) {
                fullText += content;
                onDelta?.(content);
              }
            }
          }
        } catch {
          // 忽略非 JSON 行
        }
      }
    }
  }

  onComplete?.(fullText);
  return fullText;
}
