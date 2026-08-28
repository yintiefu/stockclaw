import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { searchWorkflowHistory } from "@/lib/agent/workflow-client";
import type { WorkflowState, WorkflowStatus } from "@/lib/agent/workflow-types";
import { Debate } from "./Debate";

// 页面唯一的消费面是 useWorkflowRun：替身直接驱动渲染（values/transient/status/error），
// 不再触及 workflow-client 内部函数（client 只剩 WorkflowHistory 用的 search）。
const runMock = vi.hoisted(() => {
  const fields: {
    threadId: string | null;
    state: WorkflowState | null;
    transient: Record<string, string>;
    running: boolean;
    status: WorkflowStatus;
    error: string | null;
  } = {
    threadId: null,
    state: null,
    transient: {},
    running: false,
    status: "pending",
    error: null,
  };
  const listeners = new Set<() => void>();
  const listeners2 = new Set<(event: { section_id: string; section_status: string; completed: number; total: number }) => void>();
  const set = (patch: Partial<typeof fields>) => {
    Object.assign(fields, patch);
    for (const l of [...listeners]) l();
  };
  const reset = () => {
    Object.assign(fields, { threadId: null, state: null, transient: {}, running: false, status: "pending", error: null });
  };
  const start = vi.fn(async () => ({ state: fields.state, error: null }));
  const stop = vi.fn(async () => {});
  const retry = vi.fn(async () => {});
  const restore = vi.fn(async () => {});
  const remove = vi.fn(async () => {});
  return { fields, listeners, listeners2, set, reset, start, stop, retry, restore, remove };
});

vi.mock("@/hooks/useWorkflowRun", async () => {
  const { useEffect, useState } = await import("react");
  return {
    useWorkflowRun: vi.fn((options: { onDossierProgress?: (e: never) => void }) => {
      const [, bump] = useState(0);
      useEffect(() => {
        const l = () => bump((v) => v + 1);
        runMock.listeners.add(l);
        const l2 = (e: never) => options.onDossierProgress?.(e);
        runMock.listeners2.add(l2);
        return () => { runMock.listeners.delete(l); runMock.listeners2.delete(l2); };
      }, [options]);
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
    bull: { id: "bull", status: "completed", message_id: "m-bull" },
    bear: { id: "bear", status: "completed", message_id: "m-bear" },
    referee: { id: "referee", status: "completed", message_id: "m-referee" },
  },
  messages: [
    { id: "m-bull", content: "多方观点" },
    { id: "m-bear", content: "空方观点" },
    { id: "m-referee", content: "归纳分歧与验证清单" },
  ],
  result_summary: "已完成 3 阶段",
});

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
  runMock.reset();
  mocked.searchWorkflowHistory.mockResolvedValue([]);
});

describe("Debate variant mapping", () => {
  it("maps one round to the standard variant", async () => {
    renderPage();
    await startDebate();

    await waitFor(() => expect(runMock.start).toHaveBeenCalled());
    expect(runMock.start).toHaveBeenCalledWith({
      input: { code: "600519" },
      variant: "standard",
      metadata: { title: "多空辩论 · 600519", subject: "600519" },
    });
  });

  it("maps two rounds to the cross_exam variant", async () => {
    renderPage();
    await userEvent.type(screen.getByPlaceholderText("6 位代码，如 600519"), "600519");
    await userEvent.selectOptions(screen.getByDisplayValue("一轮 · 各自陈述"), "2");
    await userEvent.click(screen.getByRole("button", { name: "开始辩论" }));

    await waitFor(() => expect(runMock.start).toHaveBeenCalledWith(expect.objectContaining({
      variant: "cross_exam",
    })));
  });

  it("rejects an invalid code before any workflow call", async () => {
    renderPage();
    await userEvent.type(screen.getByPlaceholderText("6 位代码，如 600519"), "123");
    await userEvent.click(screen.getByRole("button", { name: "开始辩论" }));

    expect(await screen.findByText("请输入 6 位 A 股代码")).toBeInTheDocument();
    expect(runMock.start).not.toHaveBeenCalled();
  });
});

describe("Debate streaming display", () => {
  it("shows dossier progress ticks from custom events and missing sections from values", async () => {
    renderPage();
    await startDebate();

    act(() => {
      for (const l of [...runMock.listeners2]) {
        l({ section_id: "quote", section_status: "completed", completed: 1, total: 13 });
        l({ section_id: "margin", section_status: "no_record", completed: 2, total: 13 });
      }
    });
    act(() => {
      runMock.set({
        threadId: "thread-1",
        running: true,
        status: "running",
        transient: { bull: "多方正在立论" },
        state: {
          workflow_status: "running",
          current_stage: "bull",
          stages: { bull: { id: "bull", status: "running" } },
        },
      });
    });

    expect(await screen.findByText("实时行情")).toBeInTheDocument();
    expect(screen.getByText("融资融券")).toBeInTheDocument();
    expect(screen.getByText("多方研究员")).toBeInTheDocument();
    expect(screen.getByText("多方正在立论")).toBeInTheDocument();
    expect(screen.getByText(/正在拉取客观事实底稿/)).toBeInTheDocument();

    // 底稿落进 checkpoint 后：缺口由 values 派生（dossier.ready 事件已消亡）
    act(() => {
      runMock.set({
        state: {
          workflow_status: "running",
          current_stage: "bull",
          dossier: {
            sections: [
              { id: "quote", tool: "query_quote", title: "实时行情", empty_policy: "gap_if_empty", status: "completed", summary: "", body: "" },
              { id: "concepts", tool: "query_concepts", title: "板块与概念归属", empty_policy: "gap_if_empty", status: "gap", summary: "", body: "" },
            ],
            summary: "",
            missing: ["板块与概念归属"],
            has_substantive_data: true,
          },
          stages: { bull: { id: "bull", status: "running" } },
        },
      });
    });
    expect(await screen.findByText(/未取到：板块与概念归属/)).toBeInTheDocument();
  });

  it("falls back to a static dossier-phase status when custom events never arrive (v2 runtime drops them)", async () => {
    renderPage();
    await startDebate();
    // 运行中但既无 dossier 快照也无 custom 事件（langgraph v3 流路径实测不转发）——
    // 页面仍必须有可感知的进度反馈，不能静默 35 秒。
    act(() => {
      runMock.set({
        threadId: "thread-1",
        running: true,
        status: "running",
        state: { workflow_status: "running", stages: {} },
      });
    });

    expect(await screen.findByText(/正在拉取客观事实底稿/)).toBeInTheDocument();
  });

  it("replaces transient text with authoritative message-pointer stage content", async () => {
    renderPage();
    await startDebate();
    act(() => {
      runMock.set({ threadId: "thread-1", state: completedState(), status: "completed" });
    });

    expect(await screen.findByText("多方观点")).toBeInTheDocument();
    expect(screen.getByText("空方观点")).toBeInTheDocument();
    expect(screen.getByText("归纳分歧与验证清单")).toBeInTheDocument();
  });

  it("never labels a winner — referee stays a neutral host", async () => {
    renderPage();
    await startDebate();
    act(() => {
      runMock.set({ threadId: "thread-1", state: completedState(), status: "completed" });
    });

    await waitFor(() => expect(screen.getByText("中立主持")).toBeInTheDocument());
    for (const banned of [/胜[者负]/, /赢家/, /获胜/]) {
      expect(screen.queryByText(banned)).not.toBeInTheDocument();
    }
  });
});

describe("Debate stop, failure, and history", () => {
  it("stops through the run controller when aborted", async () => {
    renderPage();
    await startDebate();
    act(() => {
      runMock.set({ threadId: "thread-1", running: true, status: "running" });
    });

    await waitFor(() => expect(screen.getByRole("button", { name: "中止" })).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: "中止" }));

    await waitFor(() => expect(runMock.stop).toHaveBeenCalled());
  });

  it("shows a terminal failure with a retry action instead of a permanent spinner", async () => {
    renderPage();
    await startDebate();
    act(() => {
      runMock.set({
        threadId: "thread-1",
        running: false,
        status: "failed",
        error: "模型推理执行异常",
        state: {
          workflow_status: "failed",
          stages: { bull: { id: "bull", status: "failed" } },
        },
      });
    });

    expect(await screen.findByText(/模型推理执行异常/)).toBeInTheDocument();
    expect(screen.queryByText("生成中…")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "从失败阶段重试" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "开始辩论" })).toBeInTheDocument();
  });

  it("renders isolated debate history rows", async () => {
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
    renderPage();
    await startDebate();
    act(() => {
      runMock.set({ threadId: "thread-1", state: completedState(), status: "completed" });
    });

    const save = await screen.findByRole("button", { name: "存入沉淀" });
    await userEvent.click(save);

    await waitFor(() => expect(screen.getByRole("button", { name: "已存入沉淀" })).toBeInTheDocument());
    const stored = JSON.parse(localStorage.getItem("vr-notes") || "[]");
    expect(stored[0]?.kind).toBe("多空辩论");
    expect(stored[0]?.content).toContain("多方观点");
  });
});
