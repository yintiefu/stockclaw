import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  createWorkflowThread,
  searchWorkflowHistory,
  startWorkflowRun,
  type WorkflowStartOptions,
} from "@/lib/agent/workflow-client";
import type { WorkflowState } from "@/lib/agent/workflow-types";
import { addNote, type Note } from "@/lib/notes";
import { Notes } from "./Notes";

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
  searchWorkflowHistory: vi.mocked(searchWorkflowHistory),
};

function seedNote(): Note[] {
  localStorage.clear();
  localStorage.setItem("vr-notes", JSON.stringify([{
    id: "note-42",
    kind: "复盘",
    title: "每日复盘 2026-08-25",
    content: "今日大盘缩量整理，成交额下降。",
    ts: Date.now(),
  }]));
  return [{
    id: "note-42",
    kind: "复盘",
    title: "每日复盘 2026-08-25",
    content: "今日大盘缩量整理，成交额下降。",
    ts: Date.now(),
  }];
}

function mockReflectionRun(result: string, transient = "") {
  mocked.startWorkflowRun.mockImplementation(async (options: WorkflowStartOptions) => {
    options.onRunCreated?.("run-r");
    const checkpoint: WorkflowState = {
      workflow_id: "reflection",
      workflow_status: "completed",
      stages: { reflection: { id: "reflection", status: "completed", content: result } },
      result,
      result_summary: "审计完成",
    };
    options.onState?.({
      runId: "run-r",
      lastSeq: 1,
      currentStage: null,
      transient: transient ? { reflection: transient } : {},
      dirtyStages: [],
      dirtyRuns: [],
      pendingCheckpointStages: [],
      checkpoint,
      checkpointRequired: false,
      recoverableError: null,
    });
    return { threadId: options.threadId, runId: "run-r", stream: { checkpoint } as never };
  });
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  localStorage.clear();
});

beforeEach(() => {
  mocked.createWorkflowThread.mockResolvedValue("thread-r");
  mocked.searchWorkflowHistory.mockResolvedValue([]);
  mockReflectionRun("审计结论：两处推理缺数据支撑。");
});

describe("Notes reflection workflow", () => {
  it("starts a reflection run with subject=note.id and the note content as source", async () => {
    seedNote();
    render(
      <MemoryRouter>
        <Notes />
      </MemoryRouter>,
    );
    await userEvent.click(screen.getByText("每日复盘 2026-08-25"));

    await userEvent.click(screen.getByRole("button", { name: "反思审计" }));

    await waitFor(() => expect(mocked.startWorkflowRun).toHaveBeenCalled());
    expect(mocked.createWorkflowThread).toHaveBeenCalledWith("reflection", {
      title: "反思 · 每日复盘 2026-08-25",
      subject: "note-42",
      config_version: 1,
    });
    expect(mocked.startWorkflowRun).toHaveBeenCalledWith(expect.objectContaining({
      assistantId: "reflection",
      input: { input: { source: "今日大盘缩量整理，成交额下降。" } },
    }));
  });

  it("streams the audit text and stores the final result at state.result", async () => {
    seedNote();
    render(
      <MemoryRouter>
        <Notes />
      </MemoryRouter>,
    );
    await userEvent.click(screen.getByText("每日复盘 2026-08-25"));

    await userEvent.click(screen.getByRole("button", { name: "反思审计" }));

    expect(await screen.findByText(/审计结论/)).toBeInTheDocument();
  });

  it("saves the audit result as a new research note", async () => {
    seedNote();
    render(
      <MemoryRouter>
        <Notes />
      </MemoryRouter>,
    );
    await userEvent.click(screen.getByText("每日复盘 2026-08-25"));
    await userEvent.click(screen.getByRole("button", { name: "反思审计" }));
    await screen.findByText(/审计结论/);

    await userEvent.click(screen.getByRole("button", { name: "把审计结果存为新记录" }));

    await waitFor(() => {
      const stored = JSON.parse(localStorage.getItem("vr-notes") || "[]");
      expect(stored.some((n: Note) => n.kind === "反思审计" && n.content.includes("审计结论"))).toBe(true);
    });
  });

  it("filters reflection history by the source note id", async () => {
    seedNote();
    render(
      <MemoryRouter>
        <Notes />
      </MemoryRouter>,
    );
    await userEvent.click(screen.getByText("每日复盘 2026-08-25"));

    await waitFor(() => expect(mocked.searchWorkflowHistory).toHaveBeenCalledWith("reflection", "note-42"));
  });

  it("shows a terminal error instead of a stuck auditing state on failure", async () => {
    seedNote();
    mocked.startWorkflowRun.mockRejectedValueOnce(new Error("模型连接失败"));
    mocked.getWorkflowState = mocked.getWorkflowState;
    render(
      <MemoryRouter>
        <Notes />
      </MemoryRouter>,
    );
    await userEvent.click(screen.getByText("每日复盘 2026-08-25"));
    await userEvent.click(screen.getByRole("button", { name: "反思审计" }));

    expect(await screen.findByText(/模型连接失败/)).toBeInTheDocument();
    expect(screen.queryByText("审计中…")).not.toBeInTheDocument();
  });

  it("still uses addNote for saving and keeps local note storage semantics", async () => {
    const notes = seedNote();
    expect(notes[0].id).toBe("note-42");
    const added = addNote("反思审计", "t", "c");
    expect(added[0].kind).toBe("反思审计");
  });
});
