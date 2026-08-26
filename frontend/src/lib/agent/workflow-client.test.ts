import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ThreadState } from "@langchain/langgraph-sdk";
import type { WorkflowState } from "./workflow-types";
import {
  WorkflowClientEventError,
  createWorkflowClient,
  type WorkflowClientSubset,
} from "./workflow-client";
import {
  clearWorkflowStreamCursor,
  loadWorkflowStreamCursor,
  saveWorkflowStreamCursor,
} from "../storage";

const now = "2026-08-25T12:00:00Z";

const checkpoint = (overrides: Partial<WorkflowState> = {}): WorkflowState => ({
  workflow_id: "debate",
  workflow_status: "running",
  current_stage: "bull",
  event_run_id: "run-1",
  event_seq: 0,
  stages: { bull: { id: "bull", status: "running", content: "已提交" } },
  ...overrides,
});

const stateResponse = (
  values: WorkflowState,
  checkpointId = "checkpoint-1",
  next: string[] = [],
): ThreadState<WorkflowState> => ({
  values,
  next,
  checkpoint: {
    thread_id: "thread-1",
    checkpoint_ns: "",
    checkpoint_id: checkpointId,
    checkpoint_map: null,
  },
  metadata: null,
  created_at: now,
  parent_checkpoint: null,
  tasks: [],
});

const thread = (overrides: Record<string, unknown> = {}) => ({
  thread_id: "thread-1",
  created_at: now,
  updated_at: now,
  state_updated_at: now,
  metadata: { channel: "workflow", workflow_type: "debate", title: "贵州茅台" },
  status: "idle",
  values: {},
  interrupts: {},
  ...overrides,
});

function asyncChunks(
  chunks: Array<{ id?: string; event: string; data: unknown }>,
  before?: () => void,
) {
  return (async function* () {
    before?.();
    for (const chunk of chunks) yield chunk;
  })();
}

function makeClient() {
  const client = {
    threads: {
      search: vi.fn(),
      get: vi.fn(),
      create: vi.fn(),
      getState: vi.fn(),
      updateState: vi.fn(),
      delete: vi.fn(),
    },
    runs: {
      stream: vi.fn(),
      joinStream: vi.fn(),
      get: vi.fn(),
      cancel: vi.fn(),
    },
  };
  return client as unknown as WorkflowClientSubset & {
    threads: Record<string, ReturnType<typeof vi.fn>>;
    runs: Record<string, ReturnType<typeof vi.fn>>;
  };
}

const delta = (seq: number, text = "观点") => ({
  type: "stage.delta",
  workflow_id: "debate",
  run_id: "run-1",
  seq,
  emitted_at: now,
  stage_id: "bull",
  delta: text,
});

describe("workflow history and thread metadata", () => {
  it("searches once with channel isolation and projects extracted values before fallback values", async () => {
    const client = makeClient();
    client.threads.search.mockResolvedValue([
      thread({
        status: "busy",
        extracted: { workflow_status: "pending", result_summary: "extract 摘要" },
        values: { workflow_status: "failed", result_summary: "fallback 摘要" },
      }),
    ]);
    const workflow = createWorkflowClient(client);

    await expect(workflow.searchHistory("debate", "600519")).resolves.toEqual([
      expect.objectContaining({
        threadId: "thread-1",
        title: "贵州茅台",
        subject: undefined,
        threadStatus: "busy",
        workflowStatus: "pending",
        status: "running",
        resultSummary: "extract 摘要",
      }),
    ]);
    expect(client.threads.search).toHaveBeenCalledOnce();
    expect(client.threads.search).toHaveBeenCalledWith({
      metadata: { channel: "workflow", workflow_type: "debate", subject: "600519" },
      limit: 100,
      sortBy: "updated_at",
      sortOrder: "desc",
      extract: {
        workflow_status: "values.workflow_status",
        result_summary: "values.result_summary",
      },
    });
    expect(client.threads.getState).not.toHaveBeenCalled();
  });

  it("uses thread values when extracted fields are absent", async () => {
    const client = makeClient();
    client.threads.search.mockResolvedValue([
      thread({ values: { workflow_status: "completed", result_summary: "完成摘要" } }),
    ]);
    const [projected] = await createWorkflowClient(client).searchHistory("debate");
    expect(projected).toMatchObject({ workflowStatus: "completed", status: "completed", resultSummary: "完成摘要" });
    expect(client.threads.search).toHaveBeenCalledWith(expect.objectContaining({
      metadata: { channel: "workflow", workflow_type: "debate" },
    }));
  });

  it("loads selected detail with exactly one getState call", async () => {
    const client = makeClient();
    const values = checkpoint();
    client.threads.getState.mockResolvedValue(stateResponse(values));
    await expect(createWorkflowClient(client).getDetail("thread-1")).resolves.toBe(values);
    expect(client.threads.getState).toHaveBeenCalledOnce();
    expect(client.threads.getState).toHaveBeenCalledWith("thread-1");
  });

  it("derives orphan and terminal detail status with one thread and one state read", async () => {
    const client = makeClient();
    client.threads.get.mockResolvedValue(thread({ status: "idle" }));
    client.threads.getState.mockResolvedValue(stateResponse(checkpoint({ workflow_status: "running" })));
    await expect(createWorkflowClient(client).getEffectiveDetail("thread-1")).resolves.toMatchObject({
      threadStatus: "idle", workflowStatus: "running", status: "interrupted",
    });
    expect(client.threads.get).toHaveBeenCalledOnce();
    expect(client.threads.getState).toHaveBeenCalledOnce();

    client.threads.get.mockResolvedValue(thread({ status: "error" }));
    client.threads.getState.mockResolvedValue(stateResponse(checkpoint({ workflow_status: "completed" })));
    await expect(createWorkflowClient(client).getEffectiveDetail("thread-1")).resolves.toMatchObject({
      threadStatus: "error", workflowStatus: "completed", status: "completed",
    });
  });

  it("creates only the five allowed metadata fields and omits absent fields", async () => {
    const client = makeClient();
    client.threads.create.mockResolvedValue(thread());
    const metadata = {
      title: "标题",
      subject: "600519",
      config_version: 3,
      input: { apiKey: "secret" },
      result: "private",
      apiKey: "secret",
    } as never;
    await expect(createWorkflowClient(client).createThread("debate", metadata)).resolves.toBe("thread-1");
    expect(client.threads.create).toHaveBeenCalledWith({ metadata: {
      channel: "workflow",
      workflow_type: "debate",
      title: "标题",
      subject: "600519",
      config_version: 3,
    } });

    client.threads.create.mockClear();
    await createWorkflowClient(client).createThread("reflection", {});
    expect(client.threads.create).toHaveBeenCalledWith({ metadata: {
      channel: "workflow",
      workflow_type: "reflection",
    } });
  });
});

describe("workflow stream cursor", () => {
  beforeEach(() => localStorage.clear());

  it("round-trips only the strict cursor shape", () => {
    saveWorkflowStreamCursor("thread-1", {
      runId: "run-1", eventId: "evt-2", lastSeq: 2, secret: "must-not-persist",
    } as never);
    expect(loadWorkflowStreamCursor("thread-1")).toEqual({ runId: "run-1", eventId: "evt-2", lastSeq: 2 });
    expect(localStorage.getItem("vr-workflow-stream:thread-1")).toBe(
      '{"runId":"run-1","eventId":"evt-2","lastSeq":2}',
    );
    clearWorkflowStreamCursor("thread-1");
    expect(loadWorkflowStreamCursor("thread-1")).toBeNull();
  });

  it.each([
    "not-json",
    "null",
    "[]",
    '{"runId":"run-1","eventId":"evt-1","lastSeq":1,"secret":"x"}',
    '{"runId":"","eventId":"evt-1","lastSeq":1}',
    '{"runId":"run-1","eventId":3,"lastSeq":1}',
    '{"runId":"run-1","eventId":"evt-1","lastSeq":-1}',
  ])("rejects malformed cursor %s", (raw) => {
    localStorage.setItem("vr-workflow-stream:thread-1", raw);
    expect(loadWorkflowStreamCursor("thread-1")).toBeNull();
  });
});

describe("workflow run streaming", () => {
  beforeEach(() => localStorage.clear());

  it("uses the resumable SDK stream options, captures run id, ignores updates, and finalizes from checkpoint", async () => {
    const client = makeClient();
    const stale = checkpoint({ event_seq: 1 });
    const committed = checkpoint({
      event_seq: 2,
      stages: { bull: { id: "bull", status: "completed", content: "权威内容" } },
    });
    const final = checkpoint({
      workflow_status: "completed", current_stage: null, completed_at: now, event_seq: 2,
      stages: { bull: { id: "bull", status: "completed", content: "权威内容" } },
    });
    for (let index = 0; index < 5; index += 1) {
      client.threads.getState.mockResolvedValueOnce(stateResponse(stale));
    }
    client.threads.getState
      .mockResolvedValueOnce(stateResponse(committed))
      .mockResolvedValueOnce(stateResponse(final));
    client.runs.get.mockResolvedValue({ run_id: "run-1", status: "running" });
    client.runs.stream.mockImplementation((_threadId, _assistantId, options) => asyncChunks([
      { id: "evt-1", event: "custom", data: delta(1, "临时") },
      { id: "evt-updates", event: "updates", data: { leaked: "not an event" } },
      { id: "evt-2", event: "custom", data: {
        type: "stage.completed", workflow_id: "debate", run_id: "run-1", seq: 2,
        emitted_at: now, stage_id: "bull", truncated: false,
      } },
    ], () => options.onRunCreated({ run_id: "run-1", thread_id: "thread-1" })));
    const canonicalEvents: string[] = [];

    const result = await createWorkflowClient(client).start({
      threadId: "thread-1",
      assistantId: "debate",
      input: { code: "600519" },
      onEvent: (event) => canonicalEvents.push(event.type),
    });

    expect(client.runs.stream).toHaveBeenCalledWith("thread-1", "debate", {
      input: { code: "600519" },
      streamMode: ["custom", "updates"],
      streamResumable: true,
      onDisconnect: "continue",
      durability: "sync",
      onRunCreated: expect.any(Function),
    });
    expect(canonicalEvents).toEqual(["stage.delta", "stage.completed"]);
    expect(result.runId).toBe("run-1");
    expect(result.stream.checkpoint).toBe(final);
    expect(result.stream.transient).toEqual({});
    expect(client.threads.getState).toHaveBeenCalledTimes(7);
    expect(client.runs.get).toHaveBeenCalledTimes(5);
    expect(loadWorkflowStreamCursor("thread-1")).toBeNull();
  });

  it("keeps the equal-sequence cursor and exposes malformed events as identifiable recoverable errors", async () => {
    const client = makeClient();
    client.runs.stream.mockImplementation((_threadId, _assistantId, options) => asyncChunks([
      { id: "evt-bad", event: "custom", data: { ...delta(1), delta: undefined } },
    ], () => options.onRunCreated({ run_id: "run-1" })));

    await expect(createWorkflowClient(client).start({
      threadId: "thread-1", assistantId: "debate", input: {},
    })).rejects.toBeInstanceOf(WorkflowClientEventError);
    expect(loadWorkflowStreamCursor("thread-1")).toEqual({ runId: "run-1", eventId: "-1", lastSeq: 0 });
  });

  it("disconnects local consumption on AbortSignal without cancelling the server run", async () => {
    const client = makeClient();
    const controller = new AbortController();
    client.threads.getState.mockResolvedValue(stateResponse(checkpoint()));
    client.runs.stream.mockImplementation((_threadId, _assistantId, options) => (async function* () {
      options.onRunCreated({ run_id: "run-1" });
      yield { id: "evt-1", event: "custom", data: delta(1) };
      if (options.signal?.aborted) return;
    })());

    await expect(createWorkflowClient(client).start({
      threadId: "thread-1",
      assistantId: "debate",
      input: {},
      signal: controller.signal,
      onEvent: () => controller.abort(),
    })).rejects.toMatchObject({ name: "AbortError" });
    expect(client.runs.stream).toHaveBeenCalledWith("thread-1", "debate", expect.objectContaining({
      signal: controller.signal,
      onDisconnect: "continue",
    }));
    expect(client.runs.cancel).not.toHaveBeenCalled();
    expect(loadWorkflowStreamCursor("thread-1")).toEqual({ runId: "run-1", eventId: "-1", lastSeq: 0 });
  });

  it("persists the server run id when aborting before the first event", async () => {
    const client = makeClient();
    const controller = new AbortController();
    client.threads.getState.mockResolvedValue(stateResponse(checkpoint()));
    client.runs.stream.mockImplementation((_threadId, _assistantId, options) => (async function* () {
      options.onRunCreated({ run_id: "run-1" });
      controller.abort();
    })());

    await expect(createWorkflowClient(client).start({
      threadId: "thread-1", assistantId: "debate", input: {}, signal: controller.signal,
    })).rejects.toMatchObject({ name: "AbortError" });
    expect(client.threads.getState).not.toHaveBeenCalled();
    expect(loadWorkflowStreamCursor("thread-1")).toEqual({ runId: "run-1", eventId: "-1", lastSeq: 0 });
    expect(client.runs.cancel).not.toHaveBeenCalled();
  });

  it("warns and skips unknown custom events", async () => {
    const client = makeClient();
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    client.threads.getState.mockResolvedValue(stateResponse(checkpoint({ workflow_status: "completed" })));
    client.runs.stream.mockImplementation((_threadId, _assistantId, options) => asyncChunks([
      { id: "evt-1", event: "custom", data: { type: "future.event" } },
    ], () => options.onRunCreated({ run_id: "run-1" })));
    const result = await createWorkflowClient(client).start({ threadId: "thread-1", assistantId: "debate", input: {} });
    expect(warn).toHaveBeenCalledOnce();
    expect(result.stream.lastSeq).toBe(0);
  });

  it("emits only reducer-accepted deltas and delays completed until authoritative checkpoint content", async () => {
    const client = makeClient();
    const committed = checkpoint({
      workflow_status: "running",
      event_seq: 5,
      stages: { bull: { id: "bull", status: "completed", content: "完整权威内容" } },
    });
    const final = checkpoint({ ...committed, workflow_status: "completed", current_stage: null });
    client.threads.getState
      .mockResolvedValueOnce(stateResponse(committed))
      .mockResolvedValueOnce(stateResponse(final));
    client.runs.stream.mockImplementation((_threadId, _assistantId, options) => asyncChunks([
      { id: "evt-1", event: "custom", data: delta(1, "可见partial") },
      { id: "evt-dup", event: "custom", data: delta(1, "重复") },
      { id: "evt-3", event: "custom", data: delta(3, "gap后不可信") },
      { id: "evt-4", event: "custom", data: delta(4, "dirty后不可信") },
      { id: "evt-5", event: "custom", data: {
        type: "stage.completed", workflow_id: "debate", run_id: "run-1", seq: 5,
        emitted_at: now, stage_id: "bull", truncated: false,
      } },
    ], () => options.onRunCreated({ run_id: "run-1" })));
    const order: string[] = [];

    await createWorkflowClient(client).start({
      threadId: "thread-1", assistantId: "debate", input: {},
      onEvent: (event) => order.push(event.type === "stage.delta"
        ? `delta:${event.delta}`
        : `completed:${(event as { content?: string }).content ?? ""}`),
      onState: (state) => {
        if (state.checkpoint?.stages.bull?.content === "完整权威内容") order.push("checkpoint");
      },
    });

    expect(order).toEqual(["delta:可见partial", "checkpoint", "completed:完整权威内容", "checkpoint"]);
  });

  it("keeps the prior cursor when stage completion checkpoint reconciliation fails", async () => {
    const client = makeClient();
    const checkpointError = new Error("checkpoint temporarily unavailable");
    client.threads.getState.mockRejectedValue(checkpointError);
    client.runs.stream.mockImplementation((_threadId, _assistantId, options) => asyncChunks([
      { id: "evt-1", event: "custom", data: delta(1, "已确认") },
      { id: "evt-2", event: "custom", data: {
        type: "stage.completed", workflow_id: "debate", run_id: "run-1", seq: 2,
        emitted_at: now, stage_id: "bull", truncated: false,
      } },
    ], () => options.onRunCreated({ run_id: "run-1" })));

    await expect(createWorkflowClient(client).start({
      threadId: "thread-1", assistantId: "debate", input: {},
    })).rejects.toBe(checkpointError);

    expect(loadWorkflowStreamCursor("thread-1")).toEqual({ runId: "run-1", eventId: "evt-1", lastSeq: 1 });
  });

  it("times out finite checkpoint reconciliation without acknowledging stage completion", async () => {
    vi.useFakeTimers();
    try {
      const client = makeClient();
      const stale = checkpoint({ event_seq: 1 });
      let runPolls = 0;
      client.threads.getState.mockResolvedValue(stateResponse(stale));
      client.runs.get.mockImplementation(async () => {
        runPolls += 1;
        if (runPolls > 25) throw new Error("test safety cutoff");
        return { run_id: "run-1", status: "running" };
      });
      client.runs.stream.mockImplementation((_threadId, _assistantId, options) => asyncChunks([
        { id: "evt-1", event: "custom", data: delta(1, "已确认") },
        { id: "evt-2", event: "custom", data: {
          type: "stage.completed", workflow_id: "debate", run_id: "run-1", seq: 2,
          emitted_at: now, stage_id: "bull", truncated: false,
        } },
      ], () => options.onRunCreated({ run_id: "run-1" })));

      const promise = createWorkflowClient(client).start({
        threadId: "thread-1", assistantId: "debate", input: {},
      });
      const assertion = expect(promise).rejects.toMatchObject({
        code: "CHECKPOINT_RECONCILIATION_TIMEOUT",
        message: "权威检查点暂未就绪，请重新连接工作流",
        recoverable: true,
      });
      await vi.runAllTimersAsync();
      await assertion;

      expect(loadWorkflowStreamCursor("thread-1")).toEqual({
        runId: "run-1", eventId: "evt-1", lastSeq: 1,
      });
    } finally {
      vi.useRealTimers();
    }
  });

  it("keeps a visible event replayable when onEvent throws", async () => {
    const client = makeClient();
    const callbackError = new Error("event callback failed");
    client.runs.stream.mockImplementation((_threadId, _assistantId, options) => asyncChunks([
      { id: "evt-1", event: "custom", data: delta(1, "待重放") },
    ], () => options.onRunCreated({ run_id: "run-1" })));

    await expect(createWorkflowClient(client).start({
      threadId: "thread-1",
      assistantId: "debate",
      input: {},
      onEvent: () => { throw callbackError; },
    })).rejects.toBe(callbackError);

    expect(loadWorkflowStreamCursor("thread-1")).toEqual({ runId: "run-1", eventId: "-1", lastSeq: 0 });
  });

  it("keeps the run cursor when start finishes streaming before its matching checkpoint is terminal", async () => {
    const client = makeClient();
    const active = checkpoint({ event_run_id: "run-A", workflow_status: "running", event_seq: 2 });
    client.threads.getState.mockResolvedValue(stateResponse(active));
    client.runs.stream.mockImplementation((_threadId, _assistantId, options) =>
      asyncChunks([], () => options.onRunCreated({ run_id: "run-A" })));

    const result = await createWorkflowClient(client).start({
      threadId: "thread-1", assistantId: "debate", input: {},
    });

    expect(result.stream.checkpoint).toBe(active);
    expect(loadWorkflowStreamCursor("thread-1")).toEqual({ runId: "run-A", eventId: "-1", lastSeq: 0 });
  });

  it.each([
    ["foreign", checkpoint({
      event_run_id: "run-B", workflow_status: "completed", current_stage: null, result: "B分支结果",
    })],
    ["identity-less", { workflow_status: "completed", stages: {}, result: "无身份结果" } as WorkflowState],
  ])("ignores a %s final checkpoint and retains the started run cursor", async (_label, finalState) => {
    const client = makeClient();
    client.threads.getState.mockResolvedValue(stateResponse(finalState));
    client.runs.stream.mockImplementation((_threadId, _assistantId, options) =>
      asyncChunks([], () => options.onRunCreated({ run_id: "run-A" })));

    const result = await createWorkflowClient(client).start({
      threadId: "thread-1", assistantId: "debate", input: {},
    });

    expect(result.stream.checkpoint).toBeNull();
    expect(result.stream.runId).toBeNull();
    expect(loadWorkflowStreamCursor("thread-1")).toEqual({ runId: "run-A", eventId: "-1", lastSeq: 0 });
  });

  it("does not clear a newer run cursor after reconciling the started run as terminal", async () => {
    const client = makeClient();
    const terminalA = checkpoint({ event_run_id: "run-A", workflow_status: "completed", current_stage: null });
    client.threads.getState.mockResolvedValue(stateResponse(terminalA));
    client.runs.stream.mockImplementation((_threadId, _assistantId, options) => asyncChunks([], () => {
      options.onRunCreated({ run_id: "run-A" });
      saveWorkflowStreamCursor("thread-1", { runId: "run-B", eventId: "evt-B", lastSeq: 3 });
    }));

    await createWorkflowClient(client).start({
      threadId: "thread-1", assistantId: "debate", input: {},
    });

    expect(loadWorkflowStreamCursor("thread-1")).toEqual({ runId: "run-B", eventId: "evt-B", lastSeq: 3 });
  });

  it("ignores update and custom chunks before run creation, then accepts matching events", async () => {
    const client = makeClient();
    const rawCursorBeforeCreation: Array<string | null> = [];
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    client.threads.getState.mockResolvedValue(stateResponse(checkpoint({
      event_run_id: "run-1", event_seq: 1, workflow_status: "running",
    })));
    client.runs.stream.mockImplementation((_threadId, _assistantId, options) => (async function* () {
      yield { id: "evt-update-before", event: "updates", data: { ignored: true } };
      rawCursorBeforeCreation.push(localStorage.getItem("vr-workflow-stream:thread-1"));
      yield { id: "evt-custom-before", event: "custom", data: delta(1, "回调前") };
      rawCursorBeforeCreation.push(localStorage.getItem("vr-workflow-stream:thread-1"));
      options.onRunCreated({ run_id: "run-1" });
      yield { id: "evt-custom-after", event: "custom", data: delta(1, "回调后") };
    })());
    const visible: string[] = [];

    await createWorkflowClient(client).start({
      threadId: "thread-1",
      assistantId: "debate",
      input: {},
      onEvent: (event) => {
        if (event.type === "stage.delta") visible.push(event.delta);
      },
    });

    expect(rawCursorBeforeCreation).toEqual([null, null]);
    expect(visible).toEqual(["回调后"]);
    expect(warn).toHaveBeenCalledOnce();
    expect(loadWorkflowStreamCursor("thread-1")).toEqual({
      runId: "run-1", eventId: "evt-custom-after", lastSeq: 1,
    });
  });

  it("does not let tail chunks from run A overwrite a newer run B cursor", async () => {
    const client = makeClient();
    const cursorAfterTailChunks: Array<ReturnType<typeof loadWorkflowStreamCursor>> = [];
    client.threads.getState.mockResolvedValue(stateResponse(checkpoint({
      event_run_id: "run-A", event_seq: 1, workflow_status: "running",
    })));
    client.runs.stream.mockImplementation((_threadId, _assistantId, options) => (async function* () {
      options.onRunCreated({ run_id: "run-A" });
      saveWorkflowStreamCursor("thread-1", { runId: "run-B", eventId: "evt-B", lastSeq: 4 });
      yield { id: "evt-A-custom", event: "custom", data: { ...delta(1, "A尾部"), run_id: "run-A" } };
      cursorAfterTailChunks.push(loadWorkflowStreamCursor("thread-1"));
      yield { id: "evt-A-update", event: "updates", data: { ignored: true } };
      cursorAfterTailChunks.push(loadWorkflowStreamCursor("thread-1"));
    })());

    await createWorkflowClient(client).start({
      threadId: "thread-1", assistantId: "debate", input: {},
    });

    expect(cursorAfterTailChunks).toEqual([
      { runId: "run-B", eventId: "evt-B", lastSeq: 4 },
      { runId: "run-B", eventId: "evt-B", lastSeq: 4 },
    ]);
    expect(loadWorkflowStreamCursor("thread-1")).toEqual({ runId: "run-B", eventId: "evt-B", lastSeq: 4 });
  });

  it("does not let a stale same-run event move the cursor sequence backwards", async () => {
    const client = makeClient();
    let cursorAfterStaleEvent = loadWorkflowStreamCursor("thread-1");
    client.threads.getState.mockResolvedValue(stateResponse(checkpoint({
      event_run_id: "run-A", event_seq: 5, workflow_status: "running",
    })));
    client.runs.stream.mockImplementation((_threadId, _assistantId, options) => (async function* () {
      options.onRunCreated({ run_id: "run-A" });
      saveWorkflowStreamCursor("thread-1", { runId: "run-A", eventId: "evt-A5", lastSeq: 5 });
      yield { id: "evt-A3", event: "custom", data: { ...delta(3, "过期事件"), run_id: "run-A" } };
      cursorAfterStaleEvent = loadWorkflowStreamCursor("thread-1");
    })());

    await createWorkflowClient(client).start({
      threadId: "thread-1", assistantId: "debate", input: {},
    });

    expect(cursorAfterStaleEvent).toEqual({ runId: "run-A", eventId: "evt-A5", lastSeq: 5 });
    expect(loadWorkflowStreamCursor("thread-1")).toEqual({ runId: "run-A", eventId: "evt-A5", lastSeq: 5 });
  });
});

describe("workflow reconnect and lifecycle actions", () => {
  beforeEach(() => localStorage.clear());

  it("hides a foreign initial checkpoint until a checkpoint for the reconnecting run is available", async () => {
    const client = makeClient();
    const foreignA = checkpoint({
      event_run_id: "run-A",
      workflow_status: "completed",
      current_stage: null,
      result: "A分支结果",
      stages: { bull: { id: "bull", status: "completed", content: "A分支阶段" } },
    });
    const matchingB = checkpoint({
      event_run_id: "run-B",
      workflow_status: "completed",
      current_stage: null,
      result: "B分支结果",
      stages: { bear: { id: "bear", status: "completed", content: "B分支阶段" } },
    });
    saveWorkflowStreamCursor("thread-1", { runId: "run-B", eventId: "evt-B", lastSeq: 0 });
    client.threads.get.mockResolvedValue(thread({ status: "busy" }));
    client.runs.get.mockResolvedValue({ run_id: "run-B", status: "running" });
    client.threads.getState
      .mockResolvedValueOnce(stateResponse(foreignA, "checkpoint-A"))
      .mockResolvedValueOnce(stateResponse(matchingB, "checkpoint-B"));
    client.runs.joinStream.mockReturnValue(asyncChunks([]));
    const snapshots: Array<{
      checkpoint: WorkflowState | null;
      transient: Record<string, string>;
      currentStage: string | null;
    }> = [];

    const result = await createWorkflowClient(client).reconnect("thread-1", {
      onState: ({ checkpoint: visible, transient, currentStage }) => {
        snapshots.push({ checkpoint: visible, transient, currentStage });
      },
    });

    expect(snapshots[0]).toEqual({ checkpoint: null, transient: {}, currentStage: null });
    expect(snapshots[0].checkpoint?.stages).toBeUndefined();
    expect(snapshots[0].checkpoint?.result).toBeUndefined();
    expect(snapshots[1]?.checkpoint).toBe(matchingB);
    expect(result.stream.checkpoint).toBe(matchingB);
  });

  it("persists an explicit reconnect run before joining so a pre-event abort remains recoverable", async () => {
    const client = makeClient();
    const controller = new AbortController();
    let cursorAtJoin = loadWorkflowStreamCursor("thread-1");
    client.threads.get.mockResolvedValue(thread({ status: "busy" }));
    client.runs.get.mockResolvedValue({ run_id: "run-1", status: "running" });
    client.threads.getState.mockResolvedValue(stateResponse(checkpoint()));
    client.runs.joinStream.mockImplementation(() => {
      cursorAtJoin = loadWorkflowStreamCursor("thread-1");
      return (async function* () { controller.abort(); })();
    });

    await expect(createWorkflowClient(client).reconnect("thread-1", {
      runId: "run-1",
      signal: controller.signal,
    })).rejects.toMatchObject({ name: "AbortError" });

    expect(cursorAtJoin).toEqual({ runId: "run-1", eventId: "-1", lastSeq: 0 });
    expect(loadWorkflowStreamCursor("thread-1")).toEqual({ runId: "run-1", eventId: "-1", lastSeq: 0 });
  });

  it("prefers an explicit reconnect run over a saved cursor from another run", async () => {
    const client = makeClient();
    saveWorkflowStreamCursor("thread-1", { runId: "run-A", eventId: "evt-A", lastSeq: 7 });
    client.threads.get.mockResolvedValue(thread({ status: "busy" }));
    client.runs.get.mockResolvedValue({ run_id: "run-B", status: "running" });
    client.threads.getState.mockResolvedValue(stateResponse(checkpoint({
      event_run_id: "run-B", event_seq: 3, workflow_status: "running",
    })));
    client.runs.joinStream.mockReturnValue(asyncChunks([]));
    const initialSeqs: number[] = [];

    await createWorkflowClient(client).reconnect("thread-1", {
      runId: "run-B",
      onState: (state) => initialSeqs.push(state.lastSeq),
    });

    expect(client.runs.get).toHaveBeenCalledWith("thread-1", "run-B");
    expect(client.runs.joinStream).toHaveBeenCalledWith("thread-1", "run-B", {
      lastEventId: "-1",
      streamMode: ["custom", "updates"],
      signal: undefined,
    });
    expect(initialSeqs[0]).toBe(0);
    expect(loadWorkflowStreamCursor("thread-1")).toEqual({ runId: "run-B", eventId: "-1", lastSeq: 0 });
  });

  it("ignores a foreign custom event without advancing the reconnecting run cursor", async () => {
    const client = makeClient();
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    let cursorAfterForeign = loadWorkflowStreamCursor("thread-1");
    saveWorkflowStreamCursor("thread-1", { runId: "run-B", eventId: "evt-B0", lastSeq: 0 });
    client.threads.get.mockResolvedValue(thread({ status: "busy" }));
    client.runs.get.mockResolvedValue({ run_id: "run-B", status: "running" });
    client.threads.getState.mockResolvedValue(stateResponse(checkpoint({
      event_run_id: "run-B", event_seq: 1, workflow_status: "running",
    })));
    client.runs.joinStream.mockReturnValue((async function* () {
      yield { id: "evt-A1", event: "custom", data: { ...delta(1, "A事件"), run_id: "run-A" } };
      cursorAfterForeign = loadWorkflowStreamCursor("thread-1");
      yield { id: "evt-B1", event: "custom", data: { ...delta(1, "B事件"), run_id: "run-B" } };
    })());
    const visible: string[] = [];

    await createWorkflowClient(client).reconnect("thread-1", {
      onEvent: (event) => {
        if (event.type === "stage.delta") visible.push(event.delta);
      },
    });

    expect(cursorAfterForeign).toEqual({ runId: "run-B", eventId: "evt-B0", lastSeq: 0 });
    expect(visible).toEqual(["B事件"]);
    expect(warn).toHaveBeenCalledOnce();
    expect(loadWorkflowStreamCursor("thread-1")).toEqual({ runId: "run-B", eventId: "evt-B1", lastSeq: 1 });
  });

  it("loads thread, run, and checkpoint before joinStream, then reconciles a sequence gap", async () => {
    const client = makeClient();
    const calls: string[] = [];
    saveWorkflowStreamCursor("thread-1", { runId: "run-1", eventId: "evt-2", lastSeq: 2 });
    client.threads.get.mockImplementation(async () => { calls.push("thread"); return thread({ status: "busy" }); });
    client.runs.get.mockImplementation(async () => { calls.push("run"); return { run_id: "run-1", status: "running" }; });
    client.threads.getState
      .mockImplementationOnce(async () => { calls.push("checkpoint-before"); return stateResponse(checkpoint({ event_seq: 2 })); })
      .mockImplementationOnce(async () => {
        calls.push("checkpoint-final");
        return stateResponse(checkpoint({
          workflow_status: "completed",
          current_stage: null,
          event_seq: 4,
          stages: { bull: { id: "bull", status: "completed", content: "补齐后的权威内容" } },
        }));
      });
    client.runs.joinStream.mockImplementation(() => {
      calls.push("join");
      return asyncChunks([{ id: "evt-4", event: "custom", data: delta(4, "不可信尾部") }]);
    });
    const snapshots: Array<{ transient: Record<string, string> }> = [];

    const result = await createWorkflowClient(client).reconnect("thread-1", {
      onState: (state) => snapshots.push({ transient: state.transient }),
    });

    expect(calls).toEqual(["thread", "run", "checkpoint-before", "join", "checkpoint-final"]);
    expect(client.runs.joinStream).toHaveBeenCalledWith("thread-1", "run-1", {
      lastEventId: "evt-2",
      streamMode: ["custom", "updates"],
      signal: undefined,
    });
    expect(snapshots[0].transient).toEqual({});
    expect(result.stream.checkpoint?.stages.bull.content).toBe("补齐后的权威内容");
    expect(result.stream.transient).toEqual({});
    expect(loadWorkflowStreamCursor("thread-1")).toBeNull();
  });

  it("treats a terminal authoritative checkpoint as successful recovery after join failure", async () => {
    const client = makeClient();
    const original = new Error("stream buffer missing");
    const initial = checkpoint({ event_seq: 2 });
    const authoritative = checkpoint({
      workflow_status: "completed",
      current_stage: null,
      event_seq: 4,
      stages: { bull: { id: "bull", status: "completed", content: "服务端已提交" } },
    });
    saveWorkflowStreamCursor("thread-1", { runId: "run-1", eventId: "evt-2", lastSeq: 2 });
    client.threads.get.mockResolvedValue(thread({ status: "busy" }));
    client.runs.get.mockResolvedValue({ run_id: "run-1", status: "running" });
    client.threads.getState
      .mockResolvedValueOnce(stateResponse(initial))
      .mockResolvedValueOnce(stateResponse(authoritative));
    client.runs.joinStream.mockReturnValue((async function* () { throw original; })());
    const checkpoints: Array<WorkflowState | null> = [];

    await expect(createWorkflowClient(client).reconnect("thread-1", {
      onState: (state) => checkpoints.push(state.checkpoint),
    })).resolves.toMatchObject({ stream: { checkpoint: authoritative } });

    expect(client.threads.getState).toHaveBeenCalledTimes(2);
    expect(checkpoints).toEqual([initial, authoritative]);
    expect(loadWorkflowStreamCursor("thread-1")).toBeNull();
  });

  it("rethrows join failure and retains cursor when the reconciled checkpoint is still active", async () => {
    const client = makeClient();
    const original = new Error("stream timeout");
    saveWorkflowStreamCursor("thread-1", { runId: "run-1", eventId: "evt-2", lastSeq: 2 });
    client.threads.get.mockResolvedValue(thread({ status: "busy" }));
    client.runs.get.mockResolvedValue({ run_id: "run-1", status: "running" });
    client.threads.getState
      .mockResolvedValueOnce(stateResponse(checkpoint({ event_seq: 2 })))
      .mockResolvedValueOnce(stateResponse(checkpoint({ event_seq: 3, workflow_status: "running" })));
    client.runs.joinStream.mockReturnValue((async function* () { throw original; })());

    await expect(createWorkflowClient(client).reconnect("thread-1")).rejects.toBe(original);
    expect(loadWorkflowStreamCursor("thread-1")).toEqual({ runId: "run-1", eventId: "evt-2", lastSeq: 2 });
  });

  it.each([
    ["another run terminal", checkpoint({
      event_run_id: "run-A", workflow_status: "completed", current_stage: null,
    })],
    ["missing run identity and status", { stages: {} } as WorkflowState],
  ])("does not treat %s checkpoint as successful recovery for the current run", async (_label, foreignState) => {
    const client = makeClient();
    const original = new Error("join failed for run B");
    saveWorkflowStreamCursor("thread-1", { runId: "run-B", eventId: "evt-B", lastSeq: 0 });
    client.threads.get.mockResolvedValue(thread({ status: "busy" }));
    client.runs.get.mockResolvedValue({ run_id: "run-B", status: "running" });
    client.threads.getState.mockResolvedValue(stateResponse(foreignState));
    client.runs.joinStream.mockReturnValue((async function* () { throw original; })());
    const snapshots: Array<string | undefined> = [];

    await expect(createWorkflowClient(client).reconnect("thread-1", {
      onState: (state) => snapshots.push(state.runId ?? undefined),
    })).rejects.toBe(original);
    expect(snapshots).toEqual(["run-B"]);
    expect(loadWorkflowStreamCursor("thread-1")).toEqual({ runId: "run-B", eventId: "evt-B", lastSeq: 0 });
  });

  it("does not replace a join failure when final checkpoint reconciliation also fails", async () => {
    const client = makeClient();
    const original = new Error("server restarted");
    saveWorkflowStreamCursor("thread-1", { runId: "run-1", eventId: "evt-2", lastSeq: 2 });
    client.threads.get.mockResolvedValue(thread({ status: "busy" }));
    client.runs.get.mockResolvedValue({ run_id: "run-1", status: "running" });
    client.threads.getState
      .mockResolvedValueOnce(stateResponse(checkpoint({ event_seq: 2 })))
      .mockRejectedValueOnce(new Error("checkpoint unavailable"));
    client.runs.joinStream.mockReturnValue((async function* () { throw original; })());

    await expect(createWorkflowClient(client).reconnect("thread-1")).rejects.toBe(original);
    expect(client.threads.getState).toHaveBeenCalledTimes(2);
    expect(loadWorkflowStreamCursor("thread-1")).toEqual({ runId: "run-1", eventId: "evt-2", lastSeq: 2 });
  });

  it("uses -1 when reconnecting without a saved event id", async () => {
    const client = makeClient();
    localStorage.setItem("vr-workflow-stream:thread-1", JSON.stringify({ runId: "run-1", eventId: "", lastSeq: 0 }));
    client.threads.get.mockResolvedValue(thread({ status: "busy" }));
    client.runs.get.mockResolvedValue({ run_id: "run-1", status: "pending" });
    client.threads.getState.mockResolvedValue(stateResponse(checkpoint()));
    client.runs.joinStream.mockReturnValue(asyncChunks([]));
    await createWorkflowClient(client).reconnect("thread-1", { runId: "run-1" });
    expect(client.runs.joinStream).toHaveBeenCalledWith("thread-1", "run-1", {
      lastEventId: "-1",
      streamMode: ["custom", "updates"],
      signal: undefined,
    });
  });

  it("retains cursor when a normal empty join ends with an active checkpoint", async () => {
    const client = makeClient();
    saveWorkflowStreamCursor("thread-1", { runId: "run-1", eventId: "evt-2", lastSeq: 2 });
    client.threads.get.mockResolvedValue(thread({ status: "busy" }));
    client.runs.get.mockResolvedValue({ run_id: "run-1", status: "running" });
    client.threads.getState.mockResolvedValue(stateResponse(checkpoint({ event_seq: 2, workflow_status: "running" })));
    client.runs.joinStream.mockReturnValue(asyncChunks([]));
    await createWorkflowClient(client).reconnect("thread-1");
    expect(loadWorkflowStreamCursor("thread-1")).toEqual({ runId: "run-1", eventId: "evt-2", lastSeq: 2 });
  });

  it("cancels with interrupt before writing a checkpoint patch and does not use asNode", async () => {
    const client = makeClient();
    const calls: string[] = [];
    let serverHead = checkpoint({
      event_run_id: "run-1",
      stages: { bull: { id: "bull", status: "running", content: "A分支内容" } },
    });
    client.runs.cancel.mockImplementation(async () => { calls.push("cancel"); });
    client.threads.getState.mockImplementation(async () => {
      calls.push("state");
      const snapshotA = stateResponse(checkpoint({
        stages: {
          bull: { id: "bull", status: "running", content: "A分支内容" },
          dossier: { id: "dossier", status: "completed", content: "A分支完整内容", completed_at: now },
        },
      }), "checkpoint-A");
      serverHead = checkpoint({
        event_run_id: "run-B",
        stages: { bull: { id: "bull", status: "running", content: "B分支内容" } },
      });
      return snapshotA;
    });
    client.threads.updateState.mockImplementation(async () => {
      expect(serverHead.event_run_id).toBe("run-B");
      calls.push("update");
      return {};
    });
    const workflow = createWorkflowClient(client);
    const cancelWithStaleCallerState = workflow.cancel as unknown as (
      threadId: string, runId: string, stale: WorkflowState,
    ) => Promise<void>;
    await cancelWithStaleCallerState("thread-1", "run-1", checkpoint({
      stages: { bull: { id: "bull", status: "running", content: "旧内容" } },
    }));
    expect(calls).toEqual(["cancel", "state", "update"]);
    expect(client.runs.cancel).toHaveBeenCalledWith("thread-1", "run-1", true, "interrupt");
    const payload = client.threads.updateState.mock.calls[0][1];
    expect(payload).not.toHaveProperty("asNode");
    expect(payload.checkpointId).toBe("checkpoint-A");
    expect(payload.values).toMatchObject({
      workflow_status: "cancelled",
      current_stage: null,
      completed_at: expect.stringMatching(/^\d{4}-\d{2}-\d{2}T/),
      stages: {
        bull: { id: "bull", status: "cancelled", content: "A分支内容" },
        dossier: { id: "dossier", status: "completed", content: "A分支完整内容", completed_at: now },
      },
    });
  });

  it("preserves a stage completed on the server before cancellation was confirmed", async () => {
    const client = makeClient();
    client.runs.cancel.mockResolvedValue(undefined);
    client.threads.getState.mockResolvedValue(stateResponse(checkpoint({
      workflow_status: "running",
      stages: { bull: { id: "bull", status: "completed", content: "刚提交的权威内容", completed_at: now } },
    })));
    client.threads.updateState.mockResolvedValue({});
    await createWorkflowClient(client).cancel("thread-1", "run-1");
    expect(client.threads.updateState).toHaveBeenCalledWith("thread-1", {
      checkpointId: "checkpoint-1",
      values: expect.objectContaining({
        workflow_status: "cancelled",
        stages: { bull: { id: "bull", status: "completed", content: "刚提交的权威内容", completed_at: now } },
      }),
    });
  });

  it("does not read or write state when cancellation fails", async () => {
    const client = makeClient();
    client.runs.cancel.mockRejectedValue(new Error("cancel failed"));
    await expect(createWorkflowClient(client).cancel("thread-1", "run-1")).rejects.toThrow("cancel failed");
    expect(client.threads.getState).not.toHaveBeenCalled();
    expect(client.threads.updateState).not.toHaveBeenCalled();
  });

  it.each([
    ["a newer run", "run-B"],
    ["missing identity", undefined],
  ])("does not patch cancellation state for %s", async (_label, eventRunId) => {
    const client = makeClient();
    saveWorkflowStreamCursor("thread-1", { runId: "run-A", eventId: "evt-1", lastSeq: 1 });
    client.runs.cancel.mockResolvedValue(undefined);
    client.threads.getState.mockResolvedValue(stateResponse(checkpoint({ event_run_id: eventRunId })));
    await createWorkflowClient(client).cancel("thread-1", "run-A");
    expect(client.threads.updateState).not.toHaveBeenCalled();
    expect(loadWorkflowStreamCursor("thread-1")).toEqual({ runId: "run-A", eventId: "evt-1", lastSeq: 1 });
  });

  it("marks an orphan stage interrupted and resumes pending work without a command", async () => {
    const client = makeClient();
    const calls: string[] = [];
    client.threads.getState
      .mockImplementationOnce(async () => { calls.push("state-before"); return stateResponse(checkpoint({
        stages: {
          bull: { id: "bull", status: "completed", content: "完成内容", completed_at: now },
          bear: { id: "bear", status: "running", content: "保留部分内容" },
        },
      }), "checkpoint-before", ["run_bear"]); })
      .mockImplementationOnce(async () => { calls.push("state-final"); return stateResponse(checkpoint({ event_run_id: "run-2" })); });
    client.threads.updateState.mockImplementation(async () => { calls.push("update"); return {}; });
    client.runs.stream.mockImplementation((_threadId, _assistantId, options) => {
      calls.push("stream");
      return asyncChunks([], () => options.onRunCreated({ run_id: "run-2" }));
    });
    await createWorkflowClient(client).retry("thread-1", "debate");
    expect(calls).toEqual(["state-before", "update", "stream", "state-final"]);
    const patch = client.threads.updateState.mock.calls[0][1];
    expect(patch).not.toHaveProperty("asNode");
    expect(patch.checkpointId).toBe("checkpoint-before");
    expect(patch.values.stages).toMatchObject({
      bull: { status: "completed", content: "完成内容" },
      bear: { status: "interrupted", content: "保留部分内容" },
    });
    expect(client.runs.stream).toHaveBeenCalledWith("thread-1", "debate", {
      input: null,
      streamMode: ["custom", "updates"],
      streamResumable: true,
      onDisconnect: "continue",
      durability: "sync",
      onRunCreated: expect.any(Function),
    });
  });

  it("rejects retry when the checkpoint config version is incompatible", async () => {
    const client = makeClient();
    client.threads.getState.mockResolvedValueOnce(stateResponse(checkpoint({
      workflow_status: "failed",
      config_version: 99,
      stages: {
        bull: { id: "bull", status: "failed", content: null },
      },
    }), "checkpoint-old-version", []));

    await expect(createWorkflowClient(client).retry("thread-1", "debate"))
      .rejects.toThrow("配置版本不兼容");
    expect(client.threads.updateState).not.toHaveBeenCalled();
    expect(client.runs.stream).not.toHaveBeenCalled();
  });

  it("re-enters a failed stage from an ended checkpoint with a typed goto command", async () => {
    const client = makeClient();
    client.threads.getState
      .mockResolvedValueOnce(stateResponse(checkpoint({
        workflow_status: "failed",
        current_stage: "bull",
        stages: {
          bull: { id: "bull", status: "completed", content: "保留完成内容", completed_at: now },
          bear: { id: "bear", status: "failed", content: "失败部分" },
        },
      }), "checkpoint-failed", []))
      .mockResolvedValueOnce(stateResponse(checkpoint({
        event_run_id: "run-2", workflow_status: "running", current_stage: "bear",
      })));
    client.runs.stream.mockImplementation((_threadId, _assistantId, options) =>
      asyncChunks([], () => options.onRunCreated({ run_id: "run-2" })));

    await createWorkflowClient(client).retry("thread-1", "debate");

    expect(client.threads.updateState).not.toHaveBeenCalled();
    expect(client.runs.stream).toHaveBeenCalledWith("thread-1", "debate", {
      input: null,
      command: { goto: "start_bear" },
      streamMode: ["custom", "updates"],
      streamResumable: true,
      onDisconnect: "continue",
      durability: "sync",
      onRunCreated: expect.any(Function),
    });
  });

  it("retries the earliest ordered failed stage instead of current_stage", async () => {
    const client = makeClient();
    client.threads.getState
      .mockResolvedValueOnce(stateResponse(checkpoint({
        workflow_status: "failed",
        current_stage: "bear",
        stages: {
          bull: { id: "bull", status: "failed", content: "先失败" },
          bear: { id: "bear", status: "failed", content: "后失败" },
        },
      }), "checkpoint-multiple-failed", []))
      .mockResolvedValueOnce(stateResponse(checkpoint({
        event_run_id: "run-2", workflow_status: "running", current_stage: "bull",
      })));
    client.runs.stream.mockImplementation((_threadId, _assistantId, options) =>
      asyncChunks([], () => options.onRunCreated({ run_id: "run-2" })));

    await createWorkflowClient(client).retry("thread-1", "debate");

    expect(client.runs.stream).toHaveBeenCalledWith("thread-1", "debate", {
      input: null,
      command: { goto: "start_bull" },
      streamMode: ["custom", "updates"],
      streamResumable: true,
      onDisconnect: "continue",
      durability: "sync",
      onRunCreated: expect.any(Function),
    });
  });

  it("rejects staged in-place retry when a later stage is already completed", async () => {
    const client = makeClient();
    client.threads.getState.mockResolvedValue(stateResponse(checkpoint({
      workflow_status: "failed",
      current_stage: "bull",
      stages: {
        bull: { id: "bull", status: "failed", content: "失败部分" },
        bear: { id: "bear", status: "completed", content: "后续完成内容", completed_at: now },
      },
    }), "checkpoint-later-completed", []));

    await expect(createWorkflowClient(client).retry("thread-1", "debate"))
      .rejects.toThrow("后续阶段已完成，请重新发起新工作流");
    expect(client.runs.stream).not.toHaveBeenCalled();
  });

  it.each(["reflection", "daily_review", "news_digest"])(
    "retries ended single-pass workflow %s through start_stage",
    async (assistantId) => {
      const client = makeClient();
      client.threads.getState
        .mockResolvedValueOnce(stateResponse(checkpoint({
          workflow_id: assistantId,
          workflow_status: "failed",
          current_stage: assistantId,
          stages: { [assistantId]: { id: assistantId, status: "failed", content: "失败部分" } },
        }), "checkpoint-single-pass", []))
        .mockResolvedValueOnce(stateResponse(checkpoint({
          workflow_id: assistantId, event_run_id: "run-2", workflow_status: "running",
        })));
      client.runs.stream.mockImplementation((_threadId, _assistantId, options) =>
        asyncChunks([], () => options.onRunCreated({ run_id: "run-2" })));

      await createWorkflowClient(client).retry("thread-1", assistantId);

      expect(client.runs.stream).toHaveBeenCalledWith("thread-1", assistantId, {
        input: null,
        command: { goto: "start_stage" },
        streamMode: ["custom", "updates"],
        streamResumable: true,
        onDisconnect: "continue",
        durability: "sync",
        onRunCreated: expect.any(Function),
      });
    },
  );

  it("fails closed when retry is requested for an unknown assistant", async () => {
    const client = makeClient();
    client.threads.getState.mockResolvedValue(stateResponse(checkpoint({
      workflow_id: "unknown",
      workflow_status: "failed",
      current_stage: "analysis",
      stages: { analysis: { id: "analysis", status: "failed", content: "失败" } },
    }), "checkpoint-unknown", []));

    await expect(createWorkflowClient(client).retry("thread-1", "unknown"))
      .rejects.toThrow("未知工作流不可重试");
    expect(client.runs.stream).not.toHaveBeenCalled();
  });

  it("rejects retry when an ended checkpoint has no retryable stage", async () => {
    const client = makeClient();
    client.threads.getState.mockResolvedValue(stateResponse(checkpoint({
      workflow_status: "completed",
      current_stage: null,
      stages: { bull: { id: "bull", status: "completed", content: "完成", completed_at: now } },
    }), "checkpoint-completed", []));

    await expect(createWorkflowClient(client).retry("thread-1", "debate"))
      .rejects.toThrow("没有可重试阶段");
    expect(client.runs.stream).not.toHaveBeenCalled();
  });

  it("rejects an unsafe retry stage id before constructing a graph node", async () => {
    const client = makeClient();
    client.threads.getState.mockResolvedValue(stateResponse(checkpoint({
      workflow_status: "failed",
      current_stage: "Bear-1",
      stages: { "Bear-1": { id: "Bear-1", status: "failed", content: "失败" } },
    }), "checkpoint-unsafe", []));

    await expect(createWorkflowClient(client).retry("thread-1", "debate"))
      .rejects.toThrow("阶段 ID 不可重试");
    expect(client.runs.stream).not.toHaveBeenCalled();
  });

  it("deletes through the SDK and clears cursor only after success", async () => {
    const client = makeClient();
    saveWorkflowStreamCursor("thread-1", { runId: "run-1", eventId: "evt-1", lastSeq: 1 });
    client.threads.delete.mockResolvedValue(undefined);
    await createWorkflowClient(client).delete("thread-1");
    expect(client.threads.delete).toHaveBeenCalledWith("thread-1");
    expect(loadWorkflowStreamCursor("thread-1")).toBeNull();
  });
});
