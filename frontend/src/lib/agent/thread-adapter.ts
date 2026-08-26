import { Client } from "@langchain/langgraph-sdk";
import type { Thread } from "@langchain/langgraph-sdk";
import type { RemoteThreadListAdapter } from "@assistant-ui/react";
import { createAssistantStream } from "assistant-stream";

// 工厂只依赖 SDK Client 的 threads 子集，测试注入小型替身即可覆盖全部契约。
export type AgentThreadClient = Pick<Client, "threads">;

// 从 adapter 接口反解元数据类型，避免依赖未再导出的内部类型
type RemoteThreadMetadata = Awaited<ReturnType<RemoteThreadListAdapter["fetch"]>>;

const metadataOf = (thread: Thread): Record<string, unknown> =>
  (thread.metadata ?? {}) as Record<string, unknown>;

const titleOf = (metadata: Record<string, unknown>) =>
  typeof metadata.title === "string" ? metadata.title : undefined;

const toRemote = (thread: Thread): RemoteThreadMetadata => ({
  remoteId: thread.thread_id,
  externalId: thread.thread_id,
  status: metadataOf(thread).archived === true ? "archived" : "regular",
  title: titleOf(metadataOf(thread)),
  lastMessageAt: new Date(thread.updated_at),
  custom: metadataOf(thread),
});

async function mergeMetadata(client: AgentThreadClient, id: string, patch: Record<string, unknown>) {
  const current = await client.threads.get(id);
  await client.threads.update(id, { metadata: { ...metadataOf(current), ...patch } });
}

export function createLangGraphThreadAdapter(client: AgentThreadClient): RemoteThreadListAdapter {
  return {
    async list() {
      const query = { limit: 100, sortBy: "updated_at" as const, sortOrder: "desc" as const };
      const [workspace, firstGlobalPage] = await Promise.all([
        client.threads.search({ ...query, metadata: { channel: "workspace" } }),
        client.threads.search({ ...query, offset: 0 }),
      ]);
      const legacy = new Map<string, Thread>();
      let globalPage = firstGlobalPage;
      let offset = 0;
      for (;;) {
        for (const candidate of globalPage) {
          if (!metadataOf(candidate).channel) legacy.set(candidate.thread_id, candidate);
        }
        offset += globalPage.length;
        if (legacy.size >= 100 || globalPage.length < query.limit) break;
        globalPage = await client.threads.search({ ...query, offset });
      }
      const merged = new Map<string, Thread>();
      for (const item of [...workspace, ...legacy.values()]) {
        merged.set(item.thread_id, item);
      }
      const threads = [...merged.values()]
        .sort((left, right) => Date.parse(right.updated_at) - Date.parse(left.updated_at))
        .slice(0, 100);
      return { threads: threads.map(toRemote) };
    },
    async fetch(id) { return toRemote(await client.threads.get(id)); },
    async initialize() {
      // 显式打上 channel: "workspace" 隔离元数据
      const thread = await client.threads.create({ metadata: { channel: "workspace" } });
      return { remoteId: thread.thread_id, externalId: thread.thread_id };
    },
    async rename(id, title) { await mergeMetadata(client, id, { title }); },
    async archive(id) { await mergeMetadata(client, id, { archived: true }); },
    async unarchive(id) { await mergeMetadata(client, id, { archived: false }); },
    async delete(id) { await client.threads.delete(id); },
    async generateTitle(id, messages) {
      const user = messages.find((message) => message.role === "user");
      const title = (user?.content ?? [])
        .filter((part): part is Extract<typeof part, { type: "text" }> => part.type === "text")
        .map((part) => part.text).join(" ").trim().slice(0, 60) || "新会话";
      await mergeMetadata(client, id, { title });
      return createAssistantStream((controller) => controller.appendText(title));
    },
  };
}

// langgraph-sdk 的 Client / @langchain/react 的 useStream 都需要绝对 URL
// （内部直接 new URL）；相对 /agent-api 经 Vite 代理转发，浏览器里锚定到当前源。
export function resolveAgentApiUrl(): string {
  if (typeof window !== "undefined") {
    return new URL("/agent-api", window.location.origin).toString();
  }
  return "/agent-api";
}

export const langGraphClient = new Client({ apiUrl: resolveAgentApiUrl() });
export const langGraphThreadAdapter = createLangGraphThreadAdapter(langGraphClient);
