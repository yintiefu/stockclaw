import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cancelWorkflowRun,
  createWorkflowThread,
  getWorkflowState,
  searchWorkflowHistory,
  startWorkflowRun,
  type WorkflowStartOptions,
} from "@/lib/agent/workflow-client";
import type { WorkflowState } from "@/lib/agent/workflow-types";
import { Debate } from "./Debate";

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
  getWorkflowState: vi.mocked(getWorkflowState),
  searchWorkflowHistory: vi.mocked(searchWorkflowHistory),
};

const base = {
  workflow_id: "debate",
  workflow_id_seq: undefined,
  run_id: "run-1",
  seq: 1,
  emitted_at: "2026-08-25T12:00:00Z",
};

const completedState = (): WorkflowState => ({
  workflow_id: "debate",
  workflow_status: "completed",
  variant: "standard",
  dossier: {
    sections: [
      { id: "quote", tool: "query_quote", title: "实时行情", empty_policy: "gap_if_empty", status: "completed", summary: "现价 1800", body: "" },
      { id: "margin", tool: "query_margin", title: "融资融券", empty_policy: "allow_no_record", status: "no_record", summary: "", body: "" },
    ],
    summary: "底稿摘要",
    missing: ["板块与概念归属"],
    has_substantive_data: true,
  },
  stages: {
    bull: { id: "bull", status: "completed", content: "多方观点" },
    bear: { id: "bear", status: "completed", content: "空方观点" },
    referee: { id: "referee", status: "completed", content: "归纳分歧与验证清单" },
  },
  result: "归纳分歧与验证清单",
  result_summary: "已完成 3 阶段",
});

/** 与真实客户端一致：先 onRunCreated，再派发事件与流状态，最后返回。 */
function mockRun(
  events: Parameters<NonNullable<WorkflowStartOptions["onEvent"]>>[0][],
  checkpoint: WorkflowState | null,
  transient: Record<string, string> = {},
) {
  mocked.startWorkflowRun.mockImplementation(async (options) => {
    options.onRunCreated?.("run-1");
    let seq = 0;
    for (const event of events) {
      seq += 1;
      options.onEvent?.({ ...event, run_id: "run-1", seq, emitted_at: "2026-08-25T12:00:00Z" } as never);
    }
    options.onState?.({
      runId: "run-1",
      lastSeq: seq,
      currentStage: checkpoint?.current_stage ?? null,
      transient,
      dirtyStages: [],
      dirtyRuns: [],
      pendingCheckpointStages: [],
      checkpoint,
      checkpointRequired: false,
      recoverableError: null,
    });
    return {
      threadId: options.threadId,
      runId: "run-1",
      stream: {
        runId: "run-1",
        lastSeq: seq,
        currentStage: checkpoint?.current_stage ?? null,
        transient,
        dirtyStages: [],
        dirtyRuns: [],
        pendingCheckpointStages: [],
        checkpoint,
        checkpointRequired: false,
        recoverableError: null,
      },
    };
  });
}

function renderPage() {
  return render(
    <MemoryRouter>
      <Debate />
    </MemoryRouter>,
  );
}

async function startDebate(code = "600519") {
  await userEvent.type(screen.getByPlaceholderText("6 位代码，如 600519"), code);
  await userEvent.click(screen.getByRole("button", { name: "开始辩论" }));
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  localStorage.clear();
});

beforeEach(() => {
  mocked.createWorkflowThread.mockResolvedValue("thread-1");
  mocked.searchWorkflowHistory.mockResolvedValue([]);
  mockRun([], completedState());
});

describe("Debate variant mapping", () => {
  it("maps one round to the standard variant", async () => {
    renderPage();
    await startDebate();

    await waitFor(() => expect(mocked.startWorkflowRun).toHaveBeenCalled());
    expect(mocked.createWorkflowThread).toHaveBeenCalledWith("debate", {
      title: "多空辩论 · 600519",
      subject: "600519",
      config_version: 1,
    });
    expect(mocked.startWorkflowRun).toHaveBeenCalledWith(expect.objectContaining({
      assistantId: "debate",
      input: { input: { code: "600519" }, variant: "standard" },
    }));
  });

  it("maps two rounds to the cross_exam variant", async () => {
    renderPage();
    await userEvent.type(screen.getByPlaceholderText("6 位代码，如 600519"), "600519");
    await userEvent.selectOptions(screen.getByDisplayValue("一轮 · 各自陈述"), "2");
    await userEvent.click(screen.getByRole("button", { name: "开始辩论" }));

    await waitFor(() => expect(mocked.startWorkflowRun).toHaveBeenCalled());
    expect(mocked.startWorkflowRun).toHaveBeenCalledWith(expect.objectContaining({
      input: { input: { code: "600519" }, variant: "cross_exam" },
    }));
  });

  it("rejects an invalid code before any workflow call", async () => {
    renderPage();
    await userEvent.type(screen.getByPlaceholderText("6 位代码，如 600519"), "123");
    await userEvent.click(screen.getByRole("button", { name: "开始辩论" }));

    expect(await screen.findByText("请输入 6 位 A 股代码")).toBeInTheDocument();
    expect(mocked.createWorkflowThread).not.toHaveBeenCalled();
  });
});

describe("Debate streaming display", () => {
  it("shows dossier progress, missing sections, stage labels, and streamed stage text", async () => {
    mockRun([
      { type: "dossier.progress", section_id: "quote", section_status: "completed", completed: 1, total: 13, workflow_id: "debate", ...base },
      { type: "dossier.progress", section_id: "margin", section_status: "no_record", completed: 2, total: 13, workflow_id: "debate", ...base },
      { type: "dossier.progress", section_id: "concepts", section_status: "gap", completed: 3, total: 13, workflow_id: "debate", ...base },
      { type: "dossier.ready", completed: 12, missing: ["板块与概念归属"], has_substantive_data: true, workflow_id: "debate", ...base },
      { type: "stage.started", stage_id: "bull", label: "多方研究员", workflow_id: "debate", ...base },
      { type: "stage.delta", stage_id: "bull", delta: "多方正在立论", workflow_id: "debate", ...base },
    ] as never, null, { bull: "多方正在立论" });
    renderPage();
    await startDebate();

    expect(await screen.findByText("实时行情")).toBeInTheDocument();
    expect(screen.getByText("融资融券")).toBeInTheDocument();
    expect(screen.getByText(/未取到：板块与概念归属/)).toBeInTheDocument();
    expect(screen.getByText("多方研究员")).toBeInTheDocument();
    expect(screen.getByText("多方正在立论")).toBeInTheDocument();
  });

  it("replaces transient text with authoritative checkpoint stage content", async () => {
    mockRun([], completedState());
    renderPage();
    await startDebate();

    expect(await screen.findByText("多方观点")).toBeInTheDocument();
    expect(screen.getByText("空方观点")).toBeInTheDocument();
    expect(screen.getByText("归纳分歧与验证清单")).toBeInTheDocument();
  });

  it("never labels a winner — referee stays a neutral host", async () => {
    mockRun([
      { type: "stage.started", stage_id: "referee", label: "中立主持", workflow_id: "debate", ...base },
    ] as never, completedState());
    renderPage();
    await startDebate();

    await waitFor(() => expect(screen.getByText("中立主持")).toBeInTheDocument());
    for (const banned of [/胜[者负]/, /赢家/, /获胜/]) {
      expect(screen.queryByText(banned)).not.toBeInTheDocument();
    }
  });

  it("restores stage display from history state including the dossier", async () => {
    mockRun([], null);
    renderPage();
    await startDebate();
    await waitFor(() => expect(mocked.startWorkflowRun).toHaveBeenCalled());

    // 恢复历史：直接用完成的 state 渲染（WorkflowHistory 打开路径）
    mockRun([], completedState());
    await userEvent.click(screen.getByRole("button", { name: "开始辩论" }));

    expect(await screen.findByText("归纳分歧与验证清单")).toBeInTheDocument();
  });
});

describe("Debate stop, failure, and history", () => {
  it("stops the server run through cancellation", async () => {
    mocked.cancelWorkflowRun.mockResolvedValue(undefined);
    mocked.getWorkflowState.mockResolvedValue({
      ...completedState(),
      workflow_status: "cancelled",
      stages: { bull: { id: "bull", status: "cancelled", content: null } },
    });
    // 真实辩论要跑几分钟：先绑定 run_id，再用一个不落地的 Promise 保持 running 状态。
    mocked.startWorkflowRun.mockImplementation((options) => {
      options.onRunCreated?.("run-1");
      return new Promise(() => {});
    });
    renderPage();
    await startDebate();

    await waitFor(() => expect(screen.getByRole("button", { name: "中止" })).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: "中止" }));

    await waitFor(() => expect(mocked.cancelWorkflowRun).toHaveBeenCalledWith("thread-1", "run-1"));
  });

  it("shows a terminal failure instead of a permanent spinner", async () => {
    mocked.startWorkflowRun.mockRejectedValueOnce(Object.assign(new Error("模型连接失败"), { code: "MODEL_ERROR" }));
    mocked.getWorkflowState.mockResolvedValue({
      ...completedState(),
      workflow_status: "failed",
      stages: { bull: { id: "bull", status: "failed", content: null } },
    });
    renderPage();
    await startDebate();

    expect(await screen.findByText(/模型连接失败/)).toBeInTheDocument();
    expect(screen.queryByText("生成中…")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "开始辩论" })).toBeInTheDocument();
  });

  it("renders isolated debate history and re-runs on a new thread", async () => {
    mocked.searchWorkflowHistory.mockResolvedValue([{
      threadId: "wf-old",
      title: "多空辩论 · 000001",
      subject: "000001",
      workflowType: "debate",
      createdAt: "2026-08-24T10:00:00Z",
      updatedAt: "2026-08-24T10:05:00Z",
      threadStatus: "idle",
      workflowStatus: "completed",
      status: "completed",
      resultSummary: "已完成 3 阶段",
    }]);
    renderPage();

    await waitFor(() => expect(mocked.searchWorkflowHistory).toHaveBeenCalledWith("debate", undefined));
    expect(await screen.findByText("多空辩论 · 000001")).toBeInTheDocument();
  });

  it("keeps the save-note action working on completed stages", async () => {
    mockRun([], completedState());
    renderPage();
    await startDebate();

    const save = await screen.findByRole("button", { name: "存入沉淀" });
    await userEvent.click(save);

    await waitFor(() => expect(screen.getByRole("button", { name: "已存入沉淀" })).toBeInTheDocument());
    const stored = JSON.parse(localStorage.getItem("vr-notes") || "[]");
    expect(stored[0]?.kind).toBe("多空辩论");
    expect(stored[0]?.content).toContain("多方观点");
  });
});
