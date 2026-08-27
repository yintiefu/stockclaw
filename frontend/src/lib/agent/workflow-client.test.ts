// 工作流线程簿记契约测试：metadata 打标、历史检索投影、权威详情派生、删除。
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ThreadState } from "@langchain/langgraph-sdk";
import type { WorkflowState } from "./workflow-types";
import {
  createWorkflowThreadClient,
  type WorkflowThreadClientSubset,
} from "./workflow-client";

const now = "2026-08-25T12:00:00Z";

const stateResponse = (
  values: WorkflowState,
  next: string[] = [],
): ThreadState<WorkflowState> => ({
  values,
  next,
  checkpoint: {
    thread_id: "thread-1",
    checkpoint_ns: "",
    checkpoint_id: "checkpoint-1",
    checkpoint_map: null,
  },
  metadata: null,
  created_at: now,
  parent_checkpoint: null,
  tasks: [],
});

function makeFakeClient() {
  const created: Array<Record<string, unknown>> = [];
  const deleted: string[] = [];
  const searches: Array<Record<string, unknown>> = [];
  const fake: WorkflowThreadClientSubset = {
    threads: {
      create: vi.fn(async ({ metadata }: { metadata?: Record<string, unknown> }) => {
        created.push(metadata ?? {});
        return {
          thread_id: "thread-new",
          created_at: now,
          updated_at: now,
          state_updated_at: now,
          metadata: metadata ?? {},
          status: "idle",
          values: {},
          interrupts: {},
        };
      }),
      delete: vi.fn(async (threadId: string) => {
        deleted.push(threadId);
      }),
      get: vi.fn(async () => ({
        thread_id: "thread-1",
        created_at: now,
        updated_at: now,
        state_updated_at: now,
        metadata: { channel: "workflow", workflow_type: "debate", title: "多空辩论 · 600519" },
        status: "busy",
        values: {},
        interrupts: {},
      })),
      getState: vi.fn(async () => stateResponse({
        workflow_status: "running",
        stages: {},
      })),
      search: vi.fn(async (params: Record<string, unknown>) => {
        searches.push(params);
        return [{
          thread_id: "thread-1",
          created_at: now,
          updated_at: now,
          state_updated_at: now,
          metadata: {
            channel: "workflow",
            workflow_type: "debate",
            subject: "600519",
            title: "多空辩论 · 600519",
          },
          status: "idle",
          values: {},
          interrupts: {},
          extracted: { workflow_status: "completed", result_summary: "debate 完成" },
        }];
      }),
    },
  };
  return { fake, created, deleted, searches };
}

describe("workflow thread client", () => {
  let ctx: ReturnType<typeof makeFakeClient>;
  beforeEach(() => { ctx = makeFakeClient(); });

  it("createThread stamps channel/workflow_type plus title and subject", async () => {
    const client = createWorkflowThreadClient(ctx.fake);
    const id = await client.createThread("debate", { title: "多空辩论 · 600519", subject: "600519" });
    expect(id).toBe("thread-new");
    expect(ctx.created).toEqual([{
      channel: "workflow",
      workflow_type: "debate",
      title: "多空辩论 · 600519",
      subject: "600519",
    }]);
  });

  it("createThread omits absent title/subject", async () => {
    const client = createWorkflowThreadClient(ctx.fake);
    await client.createThread("reflection");
    expect(ctx.created).toEqual([{ channel: "workflow", workflow_type: "reflection" }]);
  });

  it("searchHistory filters by subject and projects extracted fields", async () => {
    const client = createWorkflowThreadClient(ctx.fake);
    const rows = await client.searchHistory("debate", "600519");
    expect(ctx.searches).toHaveLength(1);
    expect(ctx.searches[0]).toMatchObject({
      metadata: { channel: "workflow", workflow_type: "debate", subject: "600519" },
      extract: { workflow_status: "values.workflow_status", result_summary: "values.result_summary" },
    });
    expect(rows).toEqual([{
      threadId: "thread-1",
      title: "多空辩论 · 600519",
      subject: "600519",
      workflowType: "debate",
      createdAt: now,
      updatedAt: now,
      threadStatus: "idle",
      workflowStatus: "completed",
      status: "completed",
      resultSummary: "debate 完成",
    }]);
  });

  it("getEffectiveDetail derives status from thread status + values", async () => {
    const client = createWorkflowThreadClient(ctx.fake);
    const detail = await client.getEffectiveDetail("thread-1");
    expect(detail.threadStatus).toBe("busy");
    expect(detail.workflowStatus).toBe("running");
    expect(detail.status).toBe("running");
    // 终态优先回归：values 已完成时陈旧 busy 不得遮蔽终态
    vi.mocked(ctx.fake.threads.getState).mockResolvedValue(stateResponse({
      workflow_status: "completed",
      stages: {},
    }));
    const terminal = await client.getEffectiveDetail("thread-1");
    expect(terminal.status).toBe("completed");
  });

  it("delete removes the thread", async () => {
    const client = createWorkflowThreadClient(ctx.fake);
    await client.delete("thread-1");
    expect(ctx.deleted).toEqual(["thread-1"]);
  });
});
