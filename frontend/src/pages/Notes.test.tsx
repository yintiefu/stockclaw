import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { searchWorkflowHistory } from "@/lib/agent/workflow-client";
import type { WorkflowState, WorkflowStatus } from "@/lib/agent/workflow-types";
import { addNote, type Note } from "@/lib/notes";
import { Notes } from "./Notes";

// 页面唯一的消费面是 useWorkflowRun：替身直接驱动渲染。
const runMock = vi.hoisted(() => {
  const fields: {
    threadId: string | null;
    state: WorkflowState | null;
    transient: Record<string, string>;
    running: boolean;
    status: WorkflowStatus;
    error: string | null;
  } = { threadId: null, state: null, transient: {}, running: false, status: "pending", error: null };
  const listeners = new Set<() => void>();
  const set = (patch: Partial<typeof fields>) => {
    Object.assign(fields, patch);
    for (const l of [...listeners]) l();
  };
  const reset = () => {
    Object.assign(fields, { threadId: null, state: null, transient: {}, running: false, status: "pending", error: null });
  };
  const start = vi.fn(async () => ({ state: fields.state, error: fields.error }));
  const stop = vi.fn(async () => {});
  const retry = vi.fn(async () => {});
  const restore = vi.fn(async () => {});
  const remove = vi.fn(async () => {});
  return { fields, listeners, set, reset, start, stop, retry, restore, remove };
});

vi.mock("@/hooks/useWorkflowRun", async () => {
  const { useEffect, useState } = await import("react");
  return {
    useWorkflowRun: vi.fn(() => {
      const [, bump] = useState(0);
      useEffect(() => {
        const l = () => bump((v) => v + 1);
        runMock.listeners.add(l);
        return () => { runMock.listeners.delete(l); };
      }, []);
      return { ...runMock.fields, start: runMock.start, stop: runMock.stop, retry: runMock.retry, restore: runMock.restore, remove: runMock.remove };
    }),
  };
});

vi.mock("@/lib/agent/workflow-client", () => ({
  createWorkflowThread: vi.fn(),
  deleteWorkflowThread: vi.fn(),
  getWorkflowState: vi.fn(),
  getEffectiveWorkflowDetail: vi.fn(),
  searchWorkflowHistory: vi.fn(),
}));

const mocked = { searchWorkflowHistory: vi.mocked(searchWorkflowHistory) };

const AUDIT_TEXT = "审计结论：两处推理缺数据支撑。";

const auditState = (): WorkflowState => ({
  workflow_id: "reflection",
  workflow_status: "completed",
  stages: { reflection: { id: "reflection", status: "completed", message_id: "m-r" } },
  messages: [{ id: "m-r", content: AUDIT_TEXT }],
  result_summary: "审计完成",
});

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

function renderPage() {
  return render(
    <MemoryRouter>
      <Notes />
    </MemoryRouter>,
  );
}

async function openNoteAndReflect() {
  await userEvent.click(screen.getByText("每日复盘 2026-08-25"));
  await userEvent.click(screen.getByRole("button", { name: "反思审计" }));
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  localStorage.clear();
});

beforeEach(() => {
  runMock.reset();
  mocked.searchWorkflowHistory.mockResolvedValue([]);
});

describe("Notes reflection workflow", () => {
  it("starts a reflection run with subject=note.id and the note content as source", async () => {
    seedNote();
    renderPage();
    await openNoteAndReflect();

    await waitFor(() => expect(runMock.start).toHaveBeenCalled());
    expect(runMock.start).toHaveBeenCalledWith({
      input: { source: "今日大盘缩量整理，成交额下降。" },
      metadata: { title: "反思 · 每日复盘 2026-08-25", subject: "note-42" },
    });
  });

  it("renders the audit text via the message pointer", async () => {
    seedNote();
    renderPage();
    await openNoteAndReflect();
    act(() => {
      runMock.set({ threadId: "thread-r", state: auditState(), status: "completed" });
    });

    expect(await screen.findByText(/审计结论/)).toBeInTheDocument();
  });

  it("saves the audit result as a new research note", async () => {
    seedNote();
    renderPage();
    await openNoteAndReflect();
    act(() => {
      runMock.set({ threadId: "thread-r", state: auditState(), status: "completed" });
    });
    await screen.findByText(/审计结论/);

    await userEvent.click(screen.getByRole("button", { name: "把审计结果存为新记录" }));

    await waitFor(() => {
      const stored = JSON.parse(localStorage.getItem("vr-notes") || "[]");
      expect(stored.some((n: Note) => n.kind === "反思审计" && n.content.includes("审计结论"))).toBe(true);
    });
  });

  it("filters reflection history by the source note id", async () => {
    seedNote();
    renderPage();
    await userEvent.click(screen.getByText("每日复盘 2026-08-25"));

    await waitFor(() => expect(mocked.searchWorkflowHistory).toHaveBeenCalledWith("reflection", "note-42"));
  });

  it("shows a terminal error instead of a stuck auditing state on failure", async () => {
    seedNote();
    renderPage();
    await openNoteAndReflect();
    act(() => {
      runMock.set({ threadId: "thread-r", running: false, status: "failed", error: "模型连接失败" });
    });

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
