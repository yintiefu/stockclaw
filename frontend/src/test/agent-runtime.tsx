import { useMemo, type ReactNode } from "react";
import {
  AssistantRuntimeProvider,
  useLocalRuntime,
  useRemoteThreadListRuntime,
  type ChatModelAdapter,
  type RemoteThreadListAdapter,
  type ThreadMessage,
} from "@assistant-ui/react";
import { createAssistantStream } from "assistant-stream";

type RemoteThreadMetadata = Awaited<ReturnType<RemoteThreadListAdapter["fetch"]>>;
type RemoteThreadListResponse = Awaited<ReturnType<RemoteThreadListAdapter["list"]>>;

/** 测试用脚本化模型：回显最后一条用户消息，零网络。 */
export const scriptedEchoModel: ChatModelAdapter = {
  async *run({ messages }) {
    const last = messages[messages.length - 1];
    const text = last?.role === "user"
      ? String(last.content[0]?.type === "text" ? last.content[0].text : "")
      : "客观回复";
    yield { content: [{ type: "text" as const, text: `回复：${text}` }] };
  },
};

/** 有状态的内存线程列表适配器：list/rename/delete 真正生效，测试可断言。 */
class StatefulThreadListAdapter implements RemoteThreadListAdapter {
  private threads = new Map<string, { title: string; archived: boolean; updatedAt: Date }>();
  private counter = 0;

  list(): Promise<RemoteThreadListResponse> {
    return Promise.resolve({
      threads: [...this.threads.entries()].map(([remoteId, thread]): RemoteThreadMetadata => ({
        remoteId,
        externalId: remoteId,
        status: thread.archived ? "archived" : "regular",
        title: thread.title,
        lastMessageAt: thread.updatedAt,
      })),
    });
  }

  rename(remoteId: string, newTitle: string): Promise<void> {
    const thread = this.threads.get(remoteId);
    if (thread) thread.title = newTitle;
    return Promise.resolve();
  }

  archive(remoteId: string): Promise<void> {
    const thread = this.threads.get(remoteId);
    if (thread) thread.archived = true;
    return Promise.resolve();
  }

  unarchive(remoteId: string): Promise<void> {
    const thread = this.threads.get(remoteId);
    if (thread) thread.archived = false;
    return Promise.resolve();
  }

  delete(remoteId: string): Promise<void> {
    this.threads.delete(remoteId);
    return Promise.resolve();
  }

  initialize(): Promise<{ remoteId: string; externalId: string }> {
    this.counter += 1;
    const remoteId = `th-${this.counter}`;
    this.threads.set(remoteId, { title: "新会话", archived: false, updatedAt: new Date() });
    return Promise.resolve({ remoteId, externalId: remoteId });
  }

  generateTitle(remoteId: string, messages: readonly ThreadMessage[]) {
    const user = messages.find((message) => message.role === "user");
    const title = (user?.content ?? [])
      .filter((part): part is Extract<typeof part, { type: "text" }> => part.type === "text")
      .map((part) => part.text).join(" ").trim().slice(0, 60) || "新会话";
    this.threads.set(remoteId, { title, archived: false, updatedAt: new Date() });
    return Promise.resolve(createAssistantStream((controller) => controller.appendText(title)));
  }

  fetch(threadId: string): Promise<RemoteThreadMetadata> {
    const thread = this.threads.get(threadId);
    if (!thread) return Promise.reject(new Error(`线程不存在：${threadId}`));
    return Promise.resolve({
      remoteId: threadId,
      externalId: threadId,
      status: thread.archived ? "archived" : "regular",
      title: thread.title,
      lastMessageAt: thread.updatedAt,
    });
  }
}

/** 复刻原生 runtime 边界的测试替身：真实 assistant-ui 运行时 + 内存线程列表适配器。 */
export function TestAgentRuntimeProvider({ children }: { children: ReactNode }) {
  const adapter = useMemo(() => new StatefulThreadListAdapter(), []);
  const runtime = useRemoteThreadListRuntime({
    runtimeHook: () => useLocalRuntime(scriptedEchoModel),
    adapter,
  });
  return <AssistantRuntimeProvider runtime={runtime}>{children}</AssistantRuntimeProvider>;
}
