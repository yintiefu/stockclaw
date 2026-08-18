/** 服务端权威历史的内存视图 + assistant-ui 适配器。

只保存当前页面生命周期的活跃文档与列表缓存；不使用 localStorage，
消息的唯一写入方是 /api/agent/run。
 */

import { ExportedMessageRepository, type ThreadMessageLike } from "@assistant-ui/core";

import { agentApi } from "./api";
import type { AgentMessage, AgentThread, AgentThreadListResponse, AgentThreadSummary } from "./types";

function textOf(content: unknown): string {
  if (typeof content === "string") return content;
  if (content == null) return "";
  try {
    return JSON.stringify(content);
  } catch {
    return String(content);
  }
}

function parseArgs(raw: unknown): unknown {
  if (typeof raw !== "string" || raw === "") return {};
  try {
    return JSON.parse(raw);
  } catch {
    return {};
  }
}

function dateOf(value: string | null): Date | undefined {
  if (!value) return undefined;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? undefined : parsed;
}

/** 持久化消息 → assistant-ui 消息；不改 ID，partial/pending 只影响展示状态。
 * tool 结果通过 toolResultsByCallId 合并进 assistant 的 tool-call part。
 */
export function toThreadMessageLike(
  message: AgentMessage,
  toolResultsByCallId: Map<string, unknown>,
  options: { actionable?: boolean } = {},
): ThreadMessageLike | null {
  if (message.role === "tool") {
    // 工具结果挂在 assistant 的 tool-call part 上，不单独成条
    return null;
  }
  if (message.role === "user") {
    return {
      id: message.id,
      role: "user",
      content: [{ type: "text", text: textOf(message.content) }],
      createdAt: dateOf(message.created_at),
    };
  }
  const content: Array<Record<string, unknown>> = [];
  if (textOf(message.content)) {
    content.push({ type: "text", text: textOf(message.content) });
  }
  for (const call of message.tool_calls ?? []) {
    const callId = String(call.id ?? "");
    content.push({
      type: "tool-call",
      toolCallId: callId,
      toolName: String(call.name ?? ""),
      args: parseArgs(call.args),
      ...(toolResultsByCallId.has(callId)
        ? { result: toolResultsByCallId.get(callId) }
        : {}),
      state: message.pending_interrupt
        ? { status: "requires-action", reason: "interrupt" }
        : { status: "completed" },
    });
  }
  if (!content.length) {
    content.push({ type: "text", text: "" });
  }
  // 锁定版本的 MessageStatus 语义：
  // partial（停止/断连后被保留的部分输出）→ incomplete+cancelled；
  // pending interrupt → requires-action（等待审批，不再是普通完成态）
  const status = message.pending_interrupt
    ? { type: "requires-action" as const, reason: "interrupt" as const }
    : message.partial
      ? { type: "incomplete" as const, reason: "cancelled" as const }
      : undefined;
  // 锁定 runtime 从 metadata.custom["ag-ui"].interrupts 恢复待审批中断：
  // 不写入则刷新后 getPendingInterrupts() 为空，无法 resume / steer-away
  const metadata = message.pending_interrupt && message.interrupts.length > 0 && options.actionable
    ? { custom: { agui: { interrupts: message.interrupts } } }
    : undefined;
  return {
    id: message.id,
    role: "assistant",
    content: content as unknown as ThreadMessageLike["content"],
    status,
    metadata,
    createdAt: dateOf(message.created_at),
  };
}

export function exportRepositoryOf(thread: AgentThread): ReturnType<typeof ExportedMessageRepository.fromBranchableArray> {
  const toolResults = new Map<string, unknown>();
  for (const message of thread.messages) {
    if (message.role === "tool" && message.tool_call_id) {
      toolResults.set(message.tool_call_id, message.content);
    }
  }
  // 只有 awaiting + 服务端确认可恢复时，interrupts 才是 actionable；
  // 否则 pending 内容可见但不注入恢复 metadata（刷新后不可误恢复）。
  const actionable = thread.last_run?.status === "awaiting_approval"
    && thread.resume_available === true;
  let parentId: string | null = null;
  const items = [] as Array<{ message: ThreadMessageLike; parentId: string | null }>;
  for (const message of thread.messages) {
    const converted = toThreadMessageLike(message, toolResults, { actionable });
    if (converted) {
      items.push({ message: converted, parentId });
      parentId = message.id;
    }
  }
  return ExportedMessageRepository.fromBranchableArray(items, { headId: parentId });
}

export class AgentHistoryController {
  private activeThread: AgentThread | null = null;
  private listCache: AgentThreadListResponse = { threads: [], warnings: [] };
  private revisionByThread = new Map<string, number>();
  private reloadGenerationByThread = new Map<string, number>();
  private revisionListeners = new Set<() => void>();

  getActiveThread(): AgentThread | null {
    return this.activeThread;
  }

  getActiveThreadId(): string | null {
    return this.activeThread?.id ?? null;
  }

  getRevision(threadId: string): number {
    return this.revisionByThread.get(threadId) ?? 0;
  }

  getThreads(): AgentThreadSummary[] {
    return this.listCache.threads;
  }

  getWarnings() {
    return this.listCache.warnings;
  }

  subscribe(listener: () => void): () => void {
    this.revisionListeners.add(listener);
    return () => this.revisionListeners.delete(listener);
  }

  private notify() {
    for (const listener of this.revisionListeners) listener();
  }

  /** 只接受比缓存更新的 revision（单调，乱序到达也收敛）。 */
  applyRevision(threadId: string, revision: number): boolean {
    const current = this.revisionByThread.get(threadId) ?? 0;
    if (!Number.isFinite(revision) || revision <= current) return false;
    this.revisionByThread.set(threadId, revision);
    if (this.activeThread?.id === threadId && this.activeThread.revision < revision) {
      this.activeThread = { ...this.activeThread, revision };
    }
    this.notify();
    return true;
  }

  async refreshList(): Promise<AgentThreadListResponse> {
    this.listCache = await agentApi.listThreads();
    for (const summary of this.listCache.threads) {
      this.applyRevision(summary.id, summary.revision);
    }
    return this.listCache;
  }

  /** 首次进入：选最新线程，没有则创建新会话。 */
  async selectInitialThread(): Promise<AgentThread> {
    const list = await this.refreshList();
    if (list.threads.length > 0) {
      return await this.reload(list.threads[0].id);
    }
    const created = await agentApi.createThread();
    this.activeThread = created;
    this.applyRevision(created.id, created.revision);
    await this.refreshList();
    return created;
  }

  /** 权威重载：任何 409 / 终局后调用，恰好替换一次本地视图。 */
  async reload(threadId: string): Promise<AgentThread> {
    const generation = (this.reloadGenerationByThread.get(threadId) ?? 0) + 1;
    this.reloadGenerationByThread.set(threadId, generation);
    const thread = await agentApi.getThread(threadId);
    const currentRevision = this.getRevision(threadId);
    if (
      this.reloadGenerationByThread.get(threadId) === generation
      && (this.activeThread === null || thread.revision >= currentRevision)
    ) {
      if (this.activeThread?.id === threadId || this.activeThread === null) {
        this.activeThread = thread;
      }
      this.applyRevision(threadId, thread.revision);
      await this.refreshList().catch(() => undefined);
    }
    return thread;
  }

  async switchTo(threadId: string): Promise<AgentThread> {
    const thread = await agentApi.getThread(threadId);
    this.activeThread = thread;
    this.applyRevision(threadId, thread.revision);
    return thread;
  }

  async rename(threadId: string, title: string): Promise<void> {
    const updated = await agentApi.patchThread(threadId, this.getRevision(threadId), { title });
    if (this.activeThread?.id === threadId) {
      this.activeThread = updated;
    }
    this.applyRevision(threadId, updated.revision);
    await this.refreshList().catch(() => undefined);
  }

  async remove(threadId: string, revision?: number): Promise<void> {
    await agentApi.deleteThread(threadId, revision ?? this.getRevision(threadId));
    this.revisionByThread.delete(threadId);
    this.reloadGenerationByThread.delete(threadId);
    if (this.activeThread?.id === threadId) {
      this.activeThread = null;
    }
    await this.refreshList().catch(() => undefined);
  }

  /** 历史适配器：读权威文档；写操作是故意的 no-op（run 是唯一写入方）。 */
  historyAdapter() {
    return {
      load: async () => exportRepositoryOf(
        this.activeThread ?? {
          schema_version: 1 as const,
          id: "empty",
          title: "",
          created_at: "",
          updated_at: "",
          revision: 0,
          selected_skills: [],
          messages: [],
          artifact_ids: [],
          last_run: null,
        },
      ),
      append: async () => {},
      update: async () => {},
    };
  }
}
