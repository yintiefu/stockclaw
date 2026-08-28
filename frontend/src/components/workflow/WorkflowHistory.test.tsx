import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  deleteWorkflowThread,
  getWorkflowState,
  searchWorkflowHistory,
  type WorkflowThreadProjection,
} from "@/lib/agent/workflow-client";
import type { WorkflowState } from "@/lib/agent/workflow-types";
import { WorkflowHistory } from "./WorkflowHistory";

vi.mock("@/lib/agent/workflow-client", () => ({
  searchWorkflowHistory: vi.fn(),
  getWorkflowState: vi.fn(),
  deleteWorkflowThread: vi.fn(),
}));

const mocked = {
  searchWorkflowHistory: vi.mocked(searchWorkflowHistory),
  getWorkflowState: vi.mocked(getWorkflowState),
  deleteWorkflowThread: vi.mocked(deleteWorkflowThread),
};

const row = (overrides: Partial<WorkflowThreadProjection> = {}): WorkflowThreadProjection => ({
  threadId: "wf-1",
  title: "多空辩论 · 600519",
  subject: "600519",
  workflowType: "debate",
  createdAt: "2026-08-25T10:00:00Z",
  updatedAt: "2026-08-25T12:00:00Z",
  threadStatus: "idle",
  workflowStatus: "completed",
  status: "completed",
  resultSummary: "已完成 3 阶段，2 个分歧点",
  ...overrides,
});

const state = (status: WorkflowState["workflow_status"]): WorkflowState => ({
  workflow_id: "debate",
  workflow_status: status,
  stages: { bull: { id: "bull", status: "completed", message_id: "m-bull" } },
  messages: [{ id: "m-bull", content: "多方观点" }],
  result_summary: "已完成 3 阶段",
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

beforeEach(() => {
  mocked.searchWorkflowHistory.mockResolvedValue([]);
});

describe("WorkflowHistory listing", () => {
  it("searches once with the workflow type and optional subject and never fetches state per row", async () => {
    mocked.searchWorkflowHistory.mockResolvedValue([row(), row({ threadId: "wf-2" })]);

    render(<WorkflowHistory workflowType="debate" />);

    await waitFor(() => expect(screen.findAllByText("多空辩论 · 600519")).resolves.toHaveLength(2));
    expect(mocked.searchWorkflowHistory).toHaveBeenCalledTimes(1);
    expect(mocked.searchWorkflowHistory).toHaveBeenCalledWith("debate", undefined);
    expect(mocked.getWorkflowState).not.toHaveBeenCalled();
    expect(screen.getAllByText("已完成").length).toBe(2);
  });

  it("filters by subject for per-source histories", async () => {
    mocked.searchWorkflowHistory.mockResolvedValue([]);

    render(<WorkflowHistory workflowType="reflection" subject="note-42" />);

    await waitFor(() => expect(mocked.searchWorkflowHistory).toHaveBeenCalledWith("reflection", "note-42"));
  });

  it("shows status, 80-character summary, and update time for each row", async () => {
    mocked.searchWorkflowHistory.mockResolvedValue([
      row({
        status: "interrupted",
        resultSummary: "x".repeat(120),
        updatedAt: "2026-08-24T08:30:00Z",
      }),
    ]);

    render(<WorkflowHistory workflowType="debate" />);

    await waitFor(() => expect(screen.getByText("已中断")).toBeInTheDocument());
    const cell = screen.getByText(/x+ ·/);
    const summary = cell.textContent?.match(/x+/)?.[0] ?? "";
    expect(summary.length).toBeLessThanOrEqual(80);
    expect(cell.textContent).toContain("24");
  });

  it("re-searches when the refresh key changes", async () => {
    mocked.searchWorkflowHistory.mockResolvedValue([]);
    const { rerender } = render(<WorkflowHistory workflowType="debate" refreshKey={0} />);
    await waitFor(() => expect(mocked.searchWorkflowHistory).toHaveBeenCalledTimes(1));

    rerender(<WorkflowHistory workflowType="debate" refreshKey={1} />);

    await waitFor(() => expect(mocked.searchWorkflowHistory).toHaveBeenCalledTimes(2));
  });

  it("renders an empty hint when the page has no history yet", async () => {
    render(<WorkflowHistory workflowType="daily_review" />);

    await waitFor(() => expect(screen.getByText("暂无历史记录")).toBeInTheDocument());
  });
});

describe("WorkflowHistory actions", () => {
  it("opens a row with exactly one getState and hands the authoritative state over", async () => {
    mocked.searchWorkflowHistory.mockResolvedValue([row()]);
    mocked.getWorkflowState.mockResolvedValue(state("completed"));
    const onOpen = vi.fn();
    render(<WorkflowHistory workflowType="debate" onOpen={onOpen} />);
    await waitFor(() => expect(screen.getByText("多空辩论 · 600519")).toBeInTheDocument());

    await userEvent.click(screen.getByRole("button", { name: "查看" }));

    await waitFor(() => expect(onOpen).toHaveBeenCalledTimes(1));
    expect(mocked.getWorkflowState).toHaveBeenCalledTimes(1);
    expect(mocked.getWorkflowState).toHaveBeenCalledWith("wf-1");
    const [thread, opened] = onOpen.mock.calls[0];
    expect(thread.threadId).toBe("wf-1");
    expect(opened.workflow_status).toBe("completed");
  });

  it("reruns from history with one getState providing the original input", async () => {
    mocked.searchWorkflowHistory.mockResolvedValue([row()]);
    mocked.getWorkflowState.mockResolvedValue({
      ...state("failed"),
      input: { code: "600519" },
      variant: "standard",
    });
    const onRerun = vi.fn();
    render(<WorkflowHistory workflowType="debate" onRerun={onRerun} />);
    await waitFor(() => expect(screen.getByText("多空辩论 · 600519")).toBeInTheDocument());

    await userEvent.click(screen.getByRole("button", { name: "重新运行" }));

    await waitFor(() => expect(onRerun).toHaveBeenCalledTimes(1));
    expect(mocked.getWorkflowState).toHaveBeenCalledTimes(1);
    const [, rerunState] = onRerun.mock.calls[0];
    expect(rerunState.input).toEqual({ code: "600519" });
    expect(rerunState.variant).toBe("standard");
  });

  it("deletes only the selected thread and refreshes the list", async () => {
    mocked.searchWorkflowHistory
      .mockResolvedValueOnce([row(), row({ threadId: "wf-2", title: "多空辩论 · 000001" })])
      .mockResolvedValueOnce([row({ threadId: "wf-2", title: "多空辩论 · 000001" })]);
    mocked.deleteWorkflowThread.mockResolvedValue(undefined);
    render(<WorkflowHistory workflowType="debate" />);
    await waitFor(() => expect(screen.getByText("多空辩论 · 600519")).toBeInTheDocument());

    await userEvent.click(screen.getAllByRole("button", { name: "删除" })[0]);

    await waitFor(() => expect(mocked.deleteWorkflowThread).toHaveBeenCalledWith("wf-1"));
    await waitFor(() => expect(mocked.searchWorkflowHistory).toHaveBeenCalledTimes(2));
    await waitFor(() => {
      expect(screen.queryByText("多空辩论 · 600519")).not.toBeInTheDocument();
      expect(screen.getByText("多空辩论 · 000001")).toBeInTheDocument();
    });
  });
});
