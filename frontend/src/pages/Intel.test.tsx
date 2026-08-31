import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getWorkflowState, searchWorkflowHistory } from "@/lib/agent/workflow-client";
import type { WorkflowState, WorkflowStatus } from "@/lib/agent/workflow-types";
import { Intel } from "./Intel";

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

const mocked = { searchWorkflowHistory: vi.mocked(searchWorkflowHistory), getWorkflowState: vi.mocked(getWorkflowState) };

const digestText = "## 要点\n\n- AI 需求持续";

const digestState = (): WorkflowState => ({
  workflow_id: "news_digest",
  workflow_status: "completed",
  stages: { news_digest: { id: "news_digest", status: "completed", message_id: "m-n" } },
  messages: [{ id: "m-n", content: digestText }],
  result_summary: "提炼完成",
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  localStorage.clear();
});

beforeEach(() => {
  runMock.reset();
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
      {
        key: "space",
        name: "航天",
        accent: "#8f8",
        items: [],
      },
    ],
  } as never);
  mocked.searchWorkflowHistory.mockResolvedValue([]);
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
  it("sends only the industry track key (dossier fetches news server-side)", async () => {
    await renderIntelWithRadar();

    await userEvent.click(screen.getByRole("button", { name: "让 AI 提炼今日要点" }));

    await waitFor(() => expect(runMock.start).toHaveBeenCalled());
    const params = runMock.start.mock.calls[0]?.[0];
    expect(params?.metadata).toEqual({ title: "AI 算力 今日要点", subject: "ai" });
    expect(params?.input).toEqual({ track: "ai" });
  });

  it("does not start a run for an empty track", async () => {
    await renderIntelWithRadar();

    await userEvent.click(screen.getByRole("button", { name: /航天/ }));
    await userEvent.click(screen.getByRole("button", { name: "让 AI 提炼今日要点" }));

    expect(runMock.start).not.toHaveBeenCalled();
    expect(await screen.findByText(/暂无更新，无需提炼/)).toBeInTheDocument();
  });

  it("renders the digest markdown with save-note preserved", async () => {
    await renderIntelWithRadar();
    // 运行结束：outcome.state 经指针解析出正文（start 在 resolve 时读取当前 fields）
    act(() => {
      runMock.set({ threadId: "thread-n", state: digestState(), status: "completed" });
    });
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
    await renderIntelWithRadar();
    runMock.start.mockImplementation(async () => ({ state: null, error: "提炼失败" }));

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

    mocked.getWorkflowState.mockResolvedValue(digestState());

    await renderIntelWithRadar();

    await userEvent.click(await screen.findByRole("button", { name: "查看" }));

    // onOpen 经 getState 拿到指针态，stageContent 解析出正文
    expect(await screen.findByText("AI 需求持续")).toBeInTheDocument();
  });
});
