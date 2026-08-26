import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  createWorkflowThread,
  getEffectiveWorkflowDetail,
  searchWorkflowHistory,
  startWorkflowRun,
  type WorkflowStartOptions,
} from "@/lib/agent/workflow-client";
import type { WorkflowState } from "@/lib/agent/workflow-types";
import { DailyReview } from "./DailyReview";

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

vi.mock("@/lib/notes", () => ({ addNote: vi.fn() }));

vi.mock("@/lib/api", () => ({
  api: {
    indices: vi.fn(),
    globalIndices: vi.fn(),
    marketOverview: vi.fn(),
    emotion: vi.fn(),
    turnoverTop: vi.fn(),
    quote: vi.fn(),
  },
  ApiError: class ApiError extends Error {},
}));

import { api } from "@/lib/api";

const mocked = {
  createWorkflowThread: vi.mocked(createWorkflowThread),
  startWorkflowRun: vi.mocked(startWorkflowRun),
  searchWorkflowHistory: vi.mocked(searchWorkflowHistory),
  getEffectiveWorkflowDetail: vi.mocked(getEffectiveWorkflowDetail),
};

const reviewText = "## 复盘\n\n- 缩量整理，成交下降";

function mockReviewRun() {
  mocked.startWorkflowRun.mockImplementation(async (options: WorkflowStartOptions) => {
    options.onRunCreated?.("run-d");
    const checkpoint: WorkflowState = {
      workflow_id: "daily_review",
      workflow_status: "completed",
      stages: { daily_review: { id: "daily_review", status: "completed", content: reviewText } },
      result: reviewText,
      result_summary: "复盘完成",
    };
    options.onState?.({
      runId: "run-d",
      lastSeq: 1,
      currentStage: null,
      transient: {},
      dirtyStages: [],
      dirtyRuns: [],
      pendingCheckpointStages: [],
      checkpoint,
      checkpointRequired: false,
      recoverableError: null,
    });
    return { threadId: options.threadId, runId: "run-d", stream: { checkpoint } as never };
  });
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  localStorage.clear();
});

beforeEach(() => {
  vi.mocked(api.indices).mockResolvedValue([
    { name: "上证指数", price: "3000", change_pct: 1.2 },
    { name: "深证成指", price: "9000", change_pct: -0.5 },
  ]);
  vi.mocked(api.globalIndices).mockResolvedValue([]);
  vi.mocked(api.marketOverview).mockResolvedValue({ sentiment: null, sectors: [] });
  vi.mocked(api.emotion).mockResolvedValue(null);
  vi.mocked(api.turnoverTop).mockResolvedValue(null);
  vi.mocked(api.quote).mockResolvedValue({});
  mocked.createWorkflowThread.mockResolvedValue("thread-d");
  mocked.searchWorkflowHistory.mockResolvedValue([]);
  mockReviewRun();
});

describe("DailyReview workflow", () => {
  it("sends the already-rendered market snapshot; the graph never refetches it", async () => {
    render(
      <MemoryRouter>
        <DailyReview />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByText("上证指数")).toBeInTheDocument());

    await userEvent.click(screen.getByRole("button", { name: "让 AI 复盘今天" }));

    await waitFor(() => expect(mocked.startWorkflowRun).toHaveBeenCalled());
    expect(mocked.createWorkflowThread).toHaveBeenCalledWith("daily_review", {
      title: expect.stringContaining("每日复盘"),
      subject: expect.any(String),
      config_version: 1,
    });
    const call = mocked.startWorkflowRun.mock.calls[0]?.[0];
    const snapshot = (call.input as { input: { market_snapshot?: string } }).input.market_snapshot;
    expect(snapshot).toContain("上证指数 3000（+1.2%）");
    expect(snapshot).toContain("深证成指 9000（-0.5%）");
  });

  it("renders the completed review markdown and keeps save-note", async () => {
    render(
      <MemoryRouter>
        <DailyReview />
      </MemoryRouter>,
    );
    await userEvent.click(screen.getByRole("button", { name: "让 AI 复盘今天" }));

    expect(await screen.findByText("缩量整理，成交下降")).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: /存入沉淀|保存/ })).toBeInTheDocument();
  });

  it("shows a terminal error state on failure", async () => {
    mocked.startWorkflowRun.mockRejectedValueOnce(new Error("复盘失败"));
    mocked.getWorkflowState = mocked.getWorkflowState;
    render(
      <MemoryRouter>
        <DailyReview />
      </MemoryRouter>,
    );
    await userEvent.click(screen.getByRole("button", { name: "让 AI 复盘今天" }));

    expect(await screen.findByText(/复盘失败/)).toBeInTheDocument();
  });

  it("renders its own daily review history without other workflow types", async () => {
    render(
      <MemoryRouter>
        <DailyReview />
      </MemoryRouter>,
    );
    await waitFor(() => expect(mocked.searchWorkflowHistory).toHaveBeenCalledWith("daily_review", undefined));
  });

  it("opens a history record via 查看 and restores its review content", async () => {
    mocked.searchWorkflowHistory.mockResolvedValue([
      {
        threadId: "thread-h",
        title: "每日复盘 · 2026/08/25",
        subject: "2026/08/25",
        workflowType: "daily_review",
        createdAt: "2026-08-25T15:00:00Z",
        updatedAt: "2026-08-25T15:01:00Z",
        threadStatus: "idle",
        workflowStatus: "completed",
        status: "completed",
        resultSummary: "缩量整理",
      },
    ]);
    mocked.getEffectiveWorkflowDetail.mockResolvedValue({
      state: {
        workflow_id: "daily_review",
        workflow_status: "completed",
        stages: { daily_review: { id: "daily_review", status: "completed", content: reviewText } },
        result: reviewText,
        result_summary: "复盘完成",
      } as WorkflowState,
      threadStatus: "idle",
      workflowStatus: "completed",
      status: "completed",
    });

    render(
      <MemoryRouter>
        <DailyReview />
      </MemoryRouter>,
    );

    await userEvent.click(await screen.findByRole("button", { name: "查看" }));

    expect(mocked.getEffectiveWorkflowDetail).toHaveBeenCalledWith("thread-h");
    expect(await screen.findByText("缩量整理，成交下降")).toBeInTheDocument();
  });

  it("saving a viewed history record as note uses the record's own date, not today", async () => {
    const { addNote } = await import("@/lib/notes");
    mocked.searchWorkflowHistory.mockResolvedValue([
      {
        threadId: "thread-h",
        title: "每日复盘 · 2026/08/25",
        subject: "2026/08/25",
        workflowType: "daily_review",
        createdAt: "2026-08-25T15:00:00Z",
        updatedAt: "2026-08-25T15:01:00Z",
        threadStatus: "idle",
        workflowStatus: "completed",
        status: "completed",
        resultSummary: "缩量整理",
      },
    ]);
    mocked.getEffectiveWorkflowDetail.mockResolvedValue({
      state: {
        workflow_id: "daily_review",
        workflow_status: "completed",
        stages: {},
        result: reviewText,
        result_summary: "复盘完成",
      } as WorkflowState,
      threadStatus: "idle",
      workflowStatus: "completed",
      status: "completed",
    });

    render(
      <MemoryRouter>
        <DailyReview />
      </MemoryRouter>,
    );

    await userEvent.click(await screen.findByRole("button", { name: "查看" }));
    await screen.findByText("缩量整理，成交下降");
    await userEvent.click(screen.getByRole("button", { name: /存入沉淀/ }));

    expect(addNote).toHaveBeenCalledWith("复盘", "每日复盘 2026/08/25", reviewText);
  });
});
