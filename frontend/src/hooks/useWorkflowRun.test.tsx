import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ThreadStatus } from "@langchain/langgraph-sdk";
import {
  cancelWorkflowRun,
  createWorkflowThread,
  deleteWorkflowThread,
  getEffectiveWorkflowDetail,
  getWorkflowState,
  reconnectWorkflowRun,
  retryWorkflowRun,
  startWorkflowRun,
  type WorkflowClientEvent,
  type WorkflowStartOptions,
  type WorkflowStreamResult,
} from "@/lib/agent/workflow-client";
import type { WorkflowStreamState } from "@/lib/agent/workflow-stream";
import type { WorkflowState } from "@/lib/agent/workflow-types";
import { saveWorkflowStreamCursor } from "@/lib/storage";
import { useWorkflowRun, type WorkflowStartParams } from "./useWorkflowRun";

vi.mock("@/lib/agent/workflow-client", () => ({
  createWorkflowThread: vi.fn(),
  startWorkflowRun: vi.fn(),
  cancelWorkflowRun: vi.fn(),
  retryWorkflowRun: vi.fn(),
  getEffectiveWorkflowDetail: vi.fn(),
  getWorkflowState: vi.fn(),
  reconnectWorkflowRun: vi.fn(),
  deleteWorkflowThread: vi.fn(),
  searchWorkflowHistory: vi.fn(),
}));

const mocked = {
  createWorkflowThread: vi.mocked(createWorkflowThread),
  startWorkflowRun: vi.mocked(startWorkflowRun),
  cancelWorkflowRun: vi.mocked(cancelWorkflowRun),
  retryWorkflowRun: vi.mocked(retryWorkflowRun),
  getEffectiveWorkflowDetail: vi.mocked(getEffectiveWorkflowDetail),
  getWorkflowState: vi.mocked(getWorkflowState),
  reconnectWorkflowRun: vi.mocked(reconnectWorkflowRun),
  deleteWorkflowThread: vi.mocked(deleteWorkflowThread),
};

const finishedState: WorkflowState = {
  workflow_id: "debate",
  workflow_status: "completed",
  stages: {
    bull: { id: "bull", status: "completed", content: "多方观点" },
    referee: { id: "referee", status: "completed", content: "归纳分歧" },
  },
  result: "归纳分歧",
  result_summary: "已完成 3 阶段",
};

const streamOf = (
  checkpoint: WorkflowState | null,
  transient: Record<string, string> = {},
  runId = "run-1",
): WorkflowStreamState => ({
  runId,
  lastSeq: 4,
  currentStage: checkpoint?.current_stage ?? null,
  transient,
  dirtyStages: [],
  dirtyRuns: [],
  pendingCheckpointStages: [],
  checkpoint,
  checkpointRequired: false,
  recoverableError: null,
});

/** 让 startWorkflowRun 在流内同步派发事件/状态后再返回，模拟真实流的回调时序。 */
function mockStart(
  checkpoint: WorkflowState | null,
  events: WorkflowClientEvent[] = [],
  transient: Record<string, string> = {},
) {
  mocked.startWorkflowRun.mockImplementation(async (options: WorkflowStartOptions) => {
    options.onRunCreated?.("run-1");
    for (const event of events) options.onEvent?.(event);
    const stream = streamOf(checkpoint, transient);
    options.onState?.(stream);
    const result: WorkflowStreamResult = {
      threadId: options.threadId, runId: "run-1", stream,
    };
    return result;
  });
}

const deltaEvent = (stageId: string, delta: string): WorkflowClientEvent => ({
  type: "stage.delta",
  stage_id: stageId,
  delta,
  workflow_id: "debate",
  run_id: "run-1",
  seq: 3,
  emitted_at: "2026-08-25T12:00:00Z",
});

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  mocked.createWorkflowThread.mockResolvedValue("thread-1");
  mockStart(finishedState, [deltaEvent("bull", "多方")], { bull: "多方" });
});

const debateParams: WorkflowStartParams = {
  input: { code: "600519" },
  variant: "cross_exam",
  metadata: { title: "多空辩论 · 600519", subject: "600519" },
};

describe("useWorkflowRun start", () => {
  it("creates a workflow thread with channel metadata and starts the graph run", async () => {
    const { result } = renderHook(() => useWorkflowRun({ assistantId: "debate" }));

    await act(() => result.current.start(debateParams));

    expect(mocked.createWorkflowThread).toHaveBeenCalledWith("debate", {
      title: "多空辩论 · 600519",
      subject: "600519",
      config_version: 1,
    });
    expect(mocked.startWorkflowRun).toHaveBeenCalledWith(expect.objectContaining({
      threadId: "thread-1",
      assistantId: "debate",
      input: { input: { code: "600519" }, variant: "cross_exam" },
    }));
    expect(result.current.threadId).toBe("thread-1");
    expect(result.current.runId).toBe("run-1");
    expect(result.current.running).toBe(false);
  });

  it("exposes authoritative checkpoint state and transient per-stage text during the run", async () => {
    const { result } = renderHook(() => useWorkflowRun({ assistantId: "debate" }));

    await act(() => result.current.start(debateParams));

    expect(result.current.state).toEqual(finishedState);
    expect(result.current.transient).toEqual({ bull: "多方" });
    expect(result.current.status).toBe("completed");
  });

  it("forwards typed events to the page handler", async () => {
    const onEvent = vi.fn();
    const { result } = renderHook(() => useWorkflowRun({ assistantId: "debate", onEvent }));

    await act(() => result.current.start(debateParams));

    expect(onEvent).toHaveBeenCalledWith(expect.objectContaining({
      type: "stage.delta", stage_id: "bull", delta: "多方",
    }));
  });

  it("restarting always creates a new thread so the old result is preserved", async () => {
    mocked.createWorkflowThread.mockResolvedValueOnce("thread-a").mockResolvedValueOnce("thread-b");
    const { result } = renderHook(() => useWorkflowRun({ assistantId: "debate" }));

    await act(() => result.current.start(debateParams));
    await act(() => result.current.start(debateParams));

    expect(mocked.createWorkflowThread).toHaveBeenCalledTimes(2);
    expect(result.current.threadId).toBe("thread-b");
  });

  it("surfaces a terminal error and refreshes checkpoint state on failure", async () => {
    mocked.startWorkflowRun.mockRejectedValueOnce(Object.assign(new Error("模型不可用"), { code: "MODEL_ERROR" }));
    mocked.getWorkflowState.mockResolvedValue({
      ...finishedState,
      workflow_status: "failed",
      stages: {
        bull: { id: "bull", status: "failed", content: null, error: { code: "MODEL_ERROR", message: "模型不可用", retryable: true } },
      },
    });
    const { result } = renderHook(() => useWorkflowRun({ assistantId: "debate" }));

    await act(() => result.current.start(debateParams));

    expect(result.current.error).toContain("模型不可用");
    expect(result.current.running).toBe(false);
    expect(result.current.state?.workflow_status).toBe("failed");
    expect(mocked.getWorkflowState).toHaveBeenCalledWith("thread-1");
  });
});

describe("useWorkflowRun stop and retry", () => {
  it("stops by cancelling the server run and refreshing the cancelled checkpoint", async () => {
    mocked.cancelWorkflowRun.mockResolvedValue(undefined);
    mocked.getWorkflowState.mockResolvedValue({
      ...finishedState,
      workflow_status: "cancelled",
      stages: {
        bull: { id: "bull", status: "cancelled", content: null },
      },
    });
    const { result } = renderHook(() => useWorkflowRun({ assistantId: "debate" }));
    await act(() => result.current.start(debateParams));

    await act(() => result.current.stop());

    expect(mocked.cancelWorkflowRun).toHaveBeenCalledWith("thread-1", "run-1");
    expect(result.current.state?.workflow_status).toBe("cancelled");
    expect(result.current.running).toBe(false);
  });

  it("retries the same thread via the workflow client without a new thread", async () => {
    mocked.retryWorkflowRun.mockImplementation(async (threadId, _assistantId, options) => {
      options?.onRunCreated?.("run-2");
      options?.onState?.(streamOf({ ...finishedState, workflow_status: "running" }, { bear: "空方" }, "run-2"));
      return { threadId, runId: "run-2", stream: streamOf(finishedState, {}, "run-2") };
    });
    const { result } = renderHook(() => useWorkflowRun({ assistantId: "debate" }));
    await act(() => result.current.start(debateParams));

    await act(() => result.current.retry());

    expect(mocked.retryWorkflowRun).toHaveBeenCalledWith("thread-1", "debate", expect.objectContaining({
      onEvent: expect.any(Function),
      onState: expect.any(Function),
    }));
    expect(mocked.createWorkflowThread).toHaveBeenCalledTimes(1);
    expect(result.current.runId).toBe("run-2");
  });
});

describe("useWorkflowRun restore and remove", () => {
  it("restores history detail once through the effective projection", async () => {
    mocked.getEffectiveWorkflowDetail.mockResolvedValue({
      state: finishedState,
      threadStatus: "idle" as ThreadStatus,
      workflowStatus: "completed",
      status: "completed",
    });
    const { result } = renderHook(() => useWorkflowRun({ assistantId: "debate" }));

    await act(() => result.current.restore("thread-9"));

    expect(mocked.getEffectiveWorkflowDetail).toHaveBeenCalledTimes(1);
    expect(mocked.getEffectiveWorkflowDetail).toHaveBeenCalledWith("thread-9");
    expect(result.current.threadId).toBe("thread-9");
    expect(result.current.state).toEqual(finishedState);
    expect(result.current.status).toBe("completed");
  });

  it("derives an orphan running checkpoint as interrupted, never permanent running", async () => {
    mocked.getEffectiveWorkflowDetail.mockResolvedValue({
      state: { ...finishedState, workflow_status: "running" },
      threadStatus: "idle" as ThreadStatus,
      workflowStatus: "running",
      status: "interrupted",
    });
    const { result } = renderHook(() => useWorkflowRun({ assistantId: "debate" }));

    await act(() => result.current.restore("thread-9"));

    expect(result.current.status).toBe("interrupted");
  });

  it("reconnects a still-running run from the saved cursor on restore", async () => {
    saveWorkflowStreamCursor("thread-9", { runId: "run-9", eventId: "5", lastSeq: 5 });
    mocked.reconnectWorkflowRun.mockResolvedValue({
      threadId: "thread-9",
      runId: "run-9",
      stream: streamOf(finishedState, {}, "run-9"),
    });
    const { result } = renderHook(() => useWorkflowRun({ assistantId: "debate" }));

    await act(() => result.current.restore("thread-9"));

    expect(mocked.reconnectWorkflowRun).toHaveBeenCalledWith("thread-9", expect.objectContaining({
      runId: "run-9",
      onEvent: expect.any(Function),
      onState: expect.any(Function),
    }));
    expect(mocked.getEffectiveWorkflowDetail).not.toHaveBeenCalled();
    expect(result.current.runId).toBe("run-9");
  });

  it("falls back to the effective detail when reconnection fails", async () => {
    saveWorkflowStreamCursor("thread-9", { runId: "run-9", eventId: "5", lastSeq: 5 });
    mocked.reconnectWorkflowRun.mockRejectedValue(new Error("流缓冲不存在"));
    mocked.getEffectiveWorkflowDetail.mockResolvedValue({
      state: finishedState,
      threadStatus: "idle" as ThreadStatus,
      workflowStatus: "completed",
      status: "completed",
    });
    const { result } = renderHook(() => useWorkflowRun({ assistantId: "debate" }));

    await act(() => result.current.restore("thread-9"));

    expect(mocked.getEffectiveWorkflowDetail).toHaveBeenCalledWith("thread-9");
    expect(result.current.state).toEqual(finishedState);
  });

  it("removes the selected workflow thread", async () => {
    mocked.deleteWorkflowThread.mockResolvedValue(undefined);
    const { result } = renderHook(() => useWorkflowRun({ assistantId: "debate" }));
    await act(() => result.current.start(debateParams));

    await act(() => result.current.remove());

    expect(mocked.deleteWorkflowThread).toHaveBeenCalledWith("thread-1");
    expect(result.current.threadId).toBeNull();
    expect(result.current.state).toBeNull();
  });

  it("ignores stop/retry/remove when no run is active", async () => {
    const { result } = renderHook(() => useWorkflowRun({ assistantId: "debate" }));

    await act(() => result.current.stop());
    await act(() => result.current.retry());
    await act(() => result.current.remove());

    expect(mocked.cancelWorkflowRun).not.toHaveBeenCalled();
    expect(mocked.retryWorkflowRun).not.toHaveBeenCalled();
    expect(mocked.deleteWorkflowThread).not.toHaveBeenCalled();
  });
});

describe("useWorkflowRun variant handling", () => {
  it("omits the variant key for single-pass workflows", async () => {
    mocked.createWorkflowThread.mockResolvedValue("thread-r");
    const { result } = renderHook(() => useWorkflowRun({ assistantId: "reflection" }));

    await act(() => result.current.start({
      input: { source: "一段已有分析" },
      metadata: { title: "反思 · 记录", subject: "note-1" },
    }));

    expect(mocked.startWorkflowRun).toHaveBeenCalledWith(expect.objectContaining({
      assistantId: "reflection",
      input: { input: { source: "一段已有分析" } },
    }));
    await waitFor(() => expect(result.current.running).toBe(false));
  });
});
