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
      const threads = await client.threads.search({ limit: 100, sortBy: "updated_at", sortOrder: "desc" });
      return { threads: threads.map(toRemote) };
    },
    async fetch(id) { return toRemote(await client.threads.get(id)); },
    async initialize() {
      // LangGraph Server 只接受 UUID thread_id，assistant-ui 传入的 __LOCALID_* 必须忽略，
      // 让服务端分配规范 UUID。
      const thread = await client.threads.create();
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

export const langGraphClient = new Client({ apiUrl: "/agent-api" });
export const langGraphThreadAdapter = createLangGraphThreadAdapter(langGraphClient);
