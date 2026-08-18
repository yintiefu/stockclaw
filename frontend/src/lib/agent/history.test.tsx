import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AgentHistoryController, exportRepositoryOf } from "./history";
import { AgentApiError, agentApi } from "./api";
import type { AgentThread, AgentThreadListResponse } from "./types";

vi.mock("./api", () => ({
  AgentApiError: class extends Error {
    status: number;
    constructor(status: number, payload: Record<string, unknown>) {
      super(String(payload.detail ?? "conflict"));
      this.status = status;
    }
  },
  agentApi: {
    listThreads: vi.fn(),
    createThread: vi.fn(),
    getThread: vi.fn(),
    patchThread: vi.fn(),
    deleteThread: vi.fn(),
    cancelRun: vi.fn(),
  },
}));

const threaded = (id: string, revision: number, updatedAt: string): AgentThread => ({
  schema_version: 1,
  id,
  title: id,
  created_at: updatedAt,
  updated_at: updatedAt,
  revision,
  selected_skills: [],
  messages: [],
  artifact_ids: [],
  last_run: null,
});

const listResponse = (threads: AgentThreadListResponse["threads"], warnings: AgentThreadListResponse["warnings"] = []): AgentThreadListResponse => ({
  threads,
  warnings,
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

describe("AgentHistoryController", () => {
  beforeEach(() => {
    vi.mocked(agentApi.listThreads).mockReset();
    vi.mocked(agentApi.getThread).mockReset();
    vi.mocked(agentApi.createThread).mockReset();
    vi.mocked(agentApi.patchThread).mockReset();
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("list 按服务端顺序返回并保留恢复警告", async () => {
    vi.mocked(agentApi.listThreads).mockResolvedValue(listResponse(
      [
        { id: "th-new", title: "新", updated_at: "2026-08-15T12:00:00Z", revision: 3, last_run: null },
        { id: "th-old", title: "旧", updated_at: "2026-08-14T00:00:00Z", revision: 1, last_run: null },
      ],
      [{ code: "DOCUMENT_CORRUPT", document_type: "thread", filename: "th-bad.json.corrupt-1" }],
    ));
    const controller = new AgentHistoryController();
    const list = await controller.refreshList();
    expect(list.threads.map((t) => t.id)).toEqual(["th-new", "th-old"]);
    expect(controller.getWarnings()).toEqual([
      { code: "DOCUMENT_CORRUPT", document_type: "thread", filename: "th-bad.json.corrupt-1" },
    ]);
  });

  it("revision 4 之后迟到的 3 不会回退本地值", () => {
    const controller = new AgentHistoryController();
    expect(controller.applyRevision("th-1", 4)).toBe(true);
    expect(controller.applyRevision("th-1", 3)).toBe(false);
    expect(controller.getRevision("th-1")).toBe(4);
  });

  it("reload 恰好替换一次本地视图（409 收敛路径）", async () => {
    vi.mocked(agentApi.getThread).mockResolvedValue({
      ...threaded("th-1", 5, "2026-08-15T12:00:00Z"),
      messages: [{ id: "u1", role: "user", content: "问题", partial: false, pending_interrupt: false, interrupts: [], tool_calls: [], tool_call_id: null, created_at: null }],
    });
    vi.mocked(agentApi.listThreads).mockResolvedValue(listResponse([]));
    const controller = new AgentHistoryController();
    const thread = await controller.reload("th-1");
    expect(thread.revision).toBe(5);
    expect(controller.getActiveThread()?.id).toBe("th-1");
    expect(agentApi.getThread).toHaveBeenCalledTimes(1);
  });

  it("out-of-order reload responses cannot regress the active thread document", async () => {
    const older = deferred<AgentThread>();
    const newer = deferred<AgentThread>();
    vi.mocked(agentApi.getThread)
      .mockReturnValueOnce(older.promise)
      .mockReturnValueOnce(newer.promise);
    vi.mocked(agentApi.listThreads).mockResolvedValue(listResponse([]));
    const controller = new AgentHistoryController();

    const first = controller.reload("th-1");
    const second = controller.reload("th-1");
    newer.resolve({
      ...threaded("th-1", 2, "2026-08-15T12:00:02Z"),
      messages: [{ id: "new", role: "assistant", content: "new", partial: false, pending_interrupt: false, interrupts: [], tool_calls: [], tool_call_id: null, created_at: null }],
    });
    await second;
    older.resolve({
      ...threaded("th-1", 1, "2026-08-15T12:00:01Z"),
      messages: [{ id: "old", role: "assistant", content: "old", partial: false, pending_interrupt: false, interrupts: [], tool_calls: [], tool_call_id: null, created_at: null }],
    });
    await first;

    expect(controller.getActiveThread()?.revision).toBe(2);
    expect(controller.getActiveThread()?.messages[0]?.id).toBe("new");
    expect(controller.getRevision("th-1")).toBe(2);
  });

  it("history append/update 不发起第二次写请求", async () => {
    vi.mocked(agentApi.getThread).mockResolvedValue(threaded("th-1", 1, "2026-08-15T12:00:00Z"));
    const controller = new AgentHistoryController();
    await controller.reload("th-1");
    const adapter = controller.historyAdapter();
    const before = vi.mocked(agentApi.patchThread).mock.calls.length
      + vi.mocked(agentApi.listThreads).mock.calls.length;
    await adapter.append({} as never);
    await adapter.update({} as never, "local-id");
    const after = vi.mocked(agentApi.patchThread).mock.calls.length
      + vi.mocked(agentApi.listThreads).mock.calls.length;
    expect(after).toBe(before);
  });

  it("切换线程水合稳定消息 ID 与 partial 状态", async () => {
    const doc: AgentThread = {
      ...threaded("th-2", 2, "2026-08-15T12:00:00Z"),
      messages: [
        { id: "u1", role: "user", content: "问题", partial: false, pending_interrupt: false, interrupts: [], tool_calls: [], tool_call_id: null, created_at: "2026-08-15T12:00:00Z" },
        { id: "a1", role: "assistant", content: "部分回答", partial: true, pending_interrupt: false, interrupts: [], tool_calls: [], tool_call_id: null, created_at: "2026-08-15T12:00:01Z" },
      ],
    };
    vi.mocked(agentApi.getThread).mockResolvedValue(doc);
    const controller = new AgentHistoryController();
    await controller.switchTo("th-2");
    const repo = controller.historyAdapter();
    const loaded = await repo.load();
    expect(loaded.messages.map((item) => item.message.id)).toEqual(["u1", "a1"]);
    expect(loaded.messages[1].message.status).toEqual({ type: "incomplete", reason: "cancelled" });
  });

  it("导出仓库保留消息 ID 且 tool 结果并入 tool-call part", () => {
    const doc: AgentThread = {
      ...threaded("th-3", 3, "2026-08-15T12:00:00Z"),
      messages: [
        { id: "u1", role: "user", content: "问题", partial: false, pending_interrupt: false, interrupts: [], tool_calls: [], tool_call_id: null, created_at: null },
        {
          id: "a1", role: "assistant", content: "带工具的回答", partial: false, pending_interrupt: false,
          interrupts: [], tool_calls: [{ id: "call-1", name: "quote", args: "{\"code\":\"600519\"}" }], tool_call_id: null, created_at: null,
        },
        { id: "t1", role: "tool", content: "journal-result", partial: false, pending_interrupt: false, interrupts: [], tool_calls: [], tool_call_id: "call-1", created_at: null },
      ],
    };
    const repo = exportRepositoryOf(doc);
    expect(repo.messages.map((item) => item.message.id)).toEqual(["u1", "a1"]);
    const assistant = repo.messages[1].message;
    const parts = assistant.content as Array<Record<string, unknown>>;
    const toolPart = parts.find((part) => part.type === "tool-call") as Record<string, unknown>;
    expect(toolPart.toolName).toBe("quote");
    expect(toolPart.result).toBe("journal-result");
    expect(toolPart.args).toEqual({ code: "600519" });
  });

  it("rename/delete 携带当前 revision；rename 冲突时抛出 AgentApiError", async () => {
    vi.mocked(agentApi.patchThread).mockResolvedValue(threaded("th-1", 4, "2026-08-15T12:00:00Z"));
    vi.mocked(agentApi.listThreads).mockResolvedValue(listResponse([]));
    const controller = new AgentHistoryController();
    controller.applyRevision("th-1", 3);
    await controller.rename("th-1", "新标题");
    expect(agentApi.patchThread).toHaveBeenCalledWith("th-1", 3, { title: "新标题" });
    expect(controller.getRevision("th-1")).toBe(4);

    vi.mocked(agentApi.patchThread).mockRejectedValue(
      new AgentApiError(409, { code: "THREAD_REVISION_CONFLICT", detail: "revision 冲突" }),
    );
    await expect(controller.rename("th-1", "再次改名")).rejects.toBeInstanceOf(AgentApiError);
  });
});

it("水合 pending interrupt 元数据到 metadata.custom.agui.interrupts", async () => {
  const doc: AgentThread = {
    ...threaded("th-4", 2, "2026-08-15T12:00:00Z"),
    last_run: { id: "r-1", status: "awaiting_approval", updated_at: "2026-08-15T12:00:00Z", retry_of: null },
    resume_available: true,
    messages: [{
      id: "a-pending", role: "assistant", content: "", partial: false, pending_interrupt: true,
      interrupts: [{ id: "int-1", reason: "tool_call", toolCallId: "call-1", responseSchema: { type: "object" } }],
      tool_calls: [], tool_call_id: null, created_at: null,
    }],
  };
  const repo = exportRepositoryOf(doc);
  const pending = repo.messages[0].message;
  expect(pending.status).toEqual({ type: "requires-action", reason: "interrupt" });
  const custom = (pending.metadata as { custom?: Record<string, { interrupts?: unknown[] }> } | undefined)?.custom;
  const interrupts = custom?.agui?.interrupts as Array<Record<string, unknown>>;
  expect(interrupts?.[0]).toMatchObject({ id: "int-1", reason: "tool_call", toolCallId: "call-1" });
});
