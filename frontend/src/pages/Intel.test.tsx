import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  createWorkflowThread,
  getWorkflowState,
  searchWorkflowHistory,
  startWorkflowRun,
  type WorkflowStartOptions,
} from "@/lib/agent/workflow-client";
import type { WorkflowState } from "@/lib/agent/workflow-types";
import { Intel } from "./Intel";

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

vi.mock("@/lib/api", () => ({
  api: {
    radar: vi.fn(),
  },
  ApiError: class ApiError extends Error {},
}));

import { api } from "@/lib/api";

vi.mock("@/lib/watchlist", () => ({
  loadWatch: vi.fn(() => []),
}));

const mocked = {
  createWorkflowThread: vi.mocked(createWorkflowThread),
  startWorkflowRun: vi.mocked(startWorkflowRun),
  searchWorkflowHistory: vi.mocked(searchWorkflowHistory),
  getWorkflowState: vi.mocked(getWorkflowState),
};

const digestText = "## 要点\n\n- AI 需求持续";

function mockDigestRun() {
  mocked.startWorkflowRun.mockImplementation(async (options: WorkflowStartOptions) => {
    options.onRunCreated?.("run-n");
    const checkpoint: WorkflowState = {
      workflow_id: "news_digest",
      workflow_status: "completed",
      stages: { news_digest: { id: "news_digest", status: "completed", content: digestText } },
      result: digestText,
      result_summary: "提炼完成",
    };
    options.onState?.({
      runId: "run-n",
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
    return { threadId: options.threadId, runId: "run-n", stream: { checkpoint } as never };
  });
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  localStorage.clear();
});

beforeEach(() => {
  vi.mocked(api.radar).mockResolvedValue({
    generated_at: "2026-08-25 11:00",
    recent_days: 3,
    stats: { total_sources: 108 },
    industries: [
      {
        key: "ai",
        name: "AI 算力",
        accent: "#f80",
        items: [
          { time: "08-24", source: "源A", title: "AI news one", url: "https://example.com/1" },
          { time: "08-25", source: "源B", title: "AI news two", url: "https://example.com/2" },
        ],
      },
      {
        key: "energy",
        name: "能源",
        accent: "#08f",
        items: [{ time: "08-25", source: "源C", title: "Energy news", url: "https://example.com/3" }],
      },
    ],
  } as never);
  mocked.createWorkflowThread.mockResolvedValue("thread-n");
  mocked.searchWorkflowHistory.mockResolvedValue([]);
  mockDigestRun();
});

async function renderIntelWithRadar() {
  render(
    <MemoryRouter>
      <Intel />
    </MemoryRouter>,
  );
  await waitFor(() => expect(screen.getByText("AI 算力")).toBeInTheDocument());
}

describe("Intel news digest workflow", () => {
  it("sends the industry's already-loaded news snapshot", async () => {
    await renderIntelWithRadar();

    await userEvent.click(screen.getByRole("button", { name: "让 AI 提炼今日要点" }));

    await waitFor(() => expect(mocked.startWorkflowRun).toHaveBeenCalled());
    expect(mocked.createWorkflowThread).toHaveBeenCalledWith("news_digest", {
      title: "AI 算力 今日要点",
      subject: "ai",
      config_version: 1,
    });
    const call = mocked.startWorkflowRun.mock.calls[0]?.[0];
    const snapshot = (call.input as { input: { news_snapshot?: string } }).input.news_snapshot;
    expect(snapshot).toContain("AI news one");
    expect(snapshot).toContain("AI news two");
    expect(snapshot).not.toContain("Energy news");
  });

  it("renders the digest markdown with save-note preserved", async () => {
    await renderIntelWithRadar();
    await userEvent.click(screen.getByRole("button", { name: "让 AI 提炼今日要点" }));

    expect(await screen.findByText("AI 需求持续")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /存入沉淀|保存/ })).toBeInTheDocument();
  });

  it("keeps industry-specific history when switching tracks", async () => {
    await renderIntelWithRadar();

    await waitFor(() => expect(mocked.searchWorkflowHistory).toHaveBeenCalledWith("news_digest", "ai"));

    await userEvent.click(screen.getByRole("button", { name: /能源/ }));

    await waitFor(() => expect(mocked.searchWorkflowHistory).toHaveBeenCalledWith("news_digest", "energy"));
  });

  it("shows a terminal error when the digest fails", async () => {
    mocked.startWorkflowRun.mockRejectedValueOnce(new Error("提炼失败"));
    await renderIntelWithRadar();

    await userEvent.click(screen.getByRole("button", { name: "让 AI 提炼今日要点" }));

    expect(await screen.findByText(/提炼失败/)).toBeInTheDocument();
  });

  it("opens a digest history record via 查看 and shows its content", async () => {
    mocked.searchWorkflowHistory.mockResolvedValue([
      {
        threadId: "thread-nh",
        title: "AI 算力 今日要点",
        subject: "ai",
        workflowType: "news_digest",
        createdAt: "2026-08-25T10:00:00Z",
        updatedAt: "2026-08-25T10:01:00Z",
        threadStatus: "idle",
        workflowStatus: "completed",
        status: "completed",
        resultSummary: "历史要点",
      },
    ]);
    mocked.getWorkflowState.mockResolvedValue({
      workflow_id: "news_digest",
      workflow_status: "completed",
      stages: {},
      result: digestText,
      result_summary: "提炼完成",
    } as WorkflowState);

    await renderIntelWithRadar();

    await userEvent.click(await screen.findByRole("button", { name: "查看" }));

    expect(mocked.getWorkflowState).toHaveBeenCalledWith("thread-nh");
    expect(await screen.findByText("AI 需求持续")).toBeInTheDocument();
  });
});
