import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AgentRunDetail, AgentRunListItem, AgentThread } from "@/lib/agent/types";
import { createAgentWorkspaceStore } from "@/lib/agent/workspace";

const api = vi.hoisted(() => ({
  listRuns: vi.fn(),
  getRun: vi.fn(),
}));
vi.mock("@/lib/agent/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/lib/agent/api")>();
  return { ...original, agentApi: api };
});
vi.mock("@/lib/agent/approval", () => ({
  useApprovalBridge: () => ({ pending: [], resolveAll: vi.fn(), steerAway: vi.fn() }),
}));

import { AgentInspector } from "./AgentInspector";
import { AgentApiError } from "@/lib/agent/api";

const runItem = (id: string, status: AgentRunListItem["status"] = "completed"): AgentRunListItem => ({
  id,
  status,
  started_at: "2026-08-19T00:00:00Z",
  updated_at: "2026-08-19T00:00:01Z",
  ended_at: "2026-08-19T00:00:01Z",
  retry_of: null,
  error_code: null,
});

const runDetail = (id: string, threadId = "th-1", status: AgentRunDetail["status"] = "completed"): AgentRunDetail => ({
  schema_version: 1,
  id,
  thread_id: threadId,
  protocol_run_ids: [],
  trigger_message_id: "message-1",
  retry_of: null,
  status,
  started_at: "2026-08-19T00:00:00Z",
  updated_at: "2026-08-19T00:00:01Z",
  ended_at: "2026-08-19T00:00:01Z",
  elapsed_ms: 1_000,
  active_elapsed_ms: 800,
  approval_wait_ms: 200,
  budget_snapshot: {},
  control_revision: 1,
  context_truncation: { occurred: false, original_chars: null, retained_chars: null, removed_turns: null },
  model_ref: { provider: "fixture", baseURL: "https://example.test/v1", model: "fixture-model" },
  history_head_id: null,
  usage: { model_calls: 0, tool_calls: 0, input_tokens: null, output_tokens: null, total_tokens: null, token_status: "unavailable" },
  tool_summaries: [],
  sources: [],
  sources_truncated: false,
  error_code: null,
  error_message: null,
});

const thread = (id = "th-1", lastRunId: string | null = "run-new", status: AgentThread["last_run"] extends infer _T ? AgentRunListItem["status"] : never = "completed"): AgentThread => ({
  schema_version: 1,
  id,
  title: id,
  created_at: "2026-08-19T00:00:00Z",
  updated_at: "2026-08-19T00:00:01Z",
  revision: 1,
  selected_skills: [],
  messages: [],
  artifact_ids: ["artifact-1", "artifact-2"],
  last_run: lastRunId ? { id: lastRunId, status, updated_at: "2026-08-19T00:00:01Z", retry_of: null } : null,
});

beforeEach(() => {
  vi.clearAllMocks();
  api.listRuns.mockResolvedValue({ runs: [runItem("run-new")], next_before: null, warnings: [] });
  api.getRun.mockImplementation(async (id: string) => ({ ...runDetail(id), sources: [
    { id: "source-1", kind: "model_url", url: "https://example.test", label: null, created_at: "2026-08-19T00:00:01Z", verification: "model_provided_unverified" },
  ] }));
});
afterEach(cleanup);

describe("AgentInspector", () => {
  it("renders four accessible tabs with badges and supports roving arrow navigation", async () => {
    render(<AgentInspector thread={thread()} store={createAgentWorkspaceStore()} />);
    const tabs = await screen.findAllByRole("tab");

    expect(screen.getByRole("tablist", { name: "Inspector 视图" })).toBeInTheDocument();
    expect(tabs.map((tab) => tab.textContent)).toEqual([
      expect.stringContaining("Run"),
      expect.stringContaining("Approval"),
      expect.stringContaining("Artifact"),
      expect.stringContaining("Sources"),
    ]);
    expect(tabs[0]).toHaveAttribute("aria-selected", "true");
    expect(tabs[0]).toHaveAttribute("tabindex", "0");
    expect(tabs[1]).toHaveAttribute("tabindex", "-1");

    tabs[0].focus();
    fireEvent.keyDown(tabs[0], { key: "ArrowRight" });
    expect(tabs[1]).toHaveFocus();
    expect(tabs[1]).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tabpanel")).toHaveAttribute("aria-labelledby", tabs[1].id);

    fireEvent.keyDown(tabs[1], { key: "ArrowLeft" });
    expect(tabs[0]).toHaveFocus();
    expect(screen.getByRole("tab", { name: /Artifact/ })).toHaveTextContent("2");
    expect(screen.getByRole("tab", { name: /Sources/ })).toHaveTextContent("1");
  });

  it("defaults to last_run and preserves historical choices per thread", async () => {
    const store = createAgentWorkspaceStore();
    api.listRuns.mockImplementation(async (threadId: string) => ({
      runs: threadId === "th-1" ? [runItem("run-new"), runItem("run-old")] : [runItem("run-other")],
      next_before: null,
      warnings: [],
    }));
    const { rerender } = render(<AgentInspector thread={thread()} store={store} />);
    await waitFor(() => expect(store.getState().selectedRunByThread["th-1"]).toBe("run-new"));

    await userEvent.setup().selectOptions(screen.getByLabelText("历史运行"), "run-old");
    expect(store.getState().selectedRunByThread["th-1"]).toBe("run-old");

    rerender(<AgentInspector thread={thread("th-2", "run-other")} store={store} />);
    await waitFor(() => expect(store.getState().selectedRunByThread["th-2"]).toBe("run-other"));
    rerender(<AgentInspector thread={thread()} store={store} />);
    await waitFor(() => expect(screen.getByLabelText("历史运行")).toHaveValue("run-old"));
  });

  it("appends only the requested next history page", async () => {
    const firstPage = Array.from({ length: 50 }, (_, index) => runItem(`run-${index + 1}`));
    api.listRuns
      .mockResolvedValueOnce({ runs: firstPage, next_before: "cursor-2", warnings: [] })
      .mockResolvedValueOnce({ runs: [runItem("run-51")], next_before: null, warnings: [] });
    render(<AgentInspector thread={thread("th-1", "run-1")} store={createAgentWorkspaceStore()} />);

    await userEvent.setup().click(await screen.findByRole("button", { name: "加载更早运行" }));
    await waitFor(() => expect(screen.getByLabelText("历史运行").querySelectorAll("option")).toHaveLength(51));
    expect(api.listRuns).toHaveBeenNthCalledWith(1, "th-1", 50);
    expect(api.listRuns).toHaveBeenNthCalledWith(2, "th-1", 50, "cursor-2");
    expect(api.listRuns).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole("button", { name: "加载更早运行" })).toBeNull();
  });

  it("resets pagination loading on thread change without letting the old page settle the new one", async () => {
    let resolveOldPage: ((value: { runs: AgentRunListItem[]; next_before: string | null; warnings: string[] }) => void) | undefined;
    let resolveNewPage: ((value: { runs: AgentRunListItem[]; next_before: string | null; warnings: string[] }) => void) | undefined;
    const oldPage = new Promise<{ runs: AgentRunListItem[]; next_before: string | null; warnings: string[] }>((resolve) => {
      resolveOldPage = resolve;
    });
    const newPage = new Promise<{ runs: AgentRunListItem[]; next_before: string | null; warnings: string[] }>((resolve) => {
      resolveNewPage = resolve;
    });
    api.listRuns
      .mockResolvedValueOnce({ runs: [runItem("run-1")], next_before: "old-page", warnings: [] })
      .mockImplementationOnce(() => oldPage)
      .mockResolvedValueOnce({ runs: [runItem("run-2")], next_before: "new-page", warnings: [] })
      .mockImplementationOnce(() => newPage);
    const store = createAgentWorkspaceStore();
    const { rerender } = render(
      <AgentInspector thread={thread("th-1", "run-1")} store={store} />,
    );

    await userEvent.setup().click(await screen.findByRole("button", { name: "加载更早运行" }));
    rerender(<AgentInspector thread={thread("th-2", "run-2")} store={store} />);
    await waitFor(() => expect(api.listRuns).toHaveBeenCalledTimes(3));
    const loadMore = await screen.findByRole("button", { name: "加载更早运行" });
    expect(loadMore).not.toBeDisabled();

    await userEvent.setup().click(loadMore);
    await waitFor(() => expect(api.listRuns).toHaveBeenCalledTimes(4));
    resolveOldPage?.({ runs: [runItem("run-old-page")], next_before: null, warnings: [] });
    await waitFor(() => expect(screen.getByRole("button", { name: "正在加载…" })).toBeDisabled());
    resolveNewPage?.({ runs: [runItem("run-new-page")], next_before: null, warnings: [] });
    await waitFor(() => expect(screen.queryByRole("button", { name: "正在加载…" })).toBeNull());
  });

  it("falls back to the new last run when a historical selection disappears", async () => {
    const store = createAgentWorkspaceStore();
    store.getState().selectRun("th-1", "run-old");
    api.listRuns.mockResolvedValue({ runs: [runItem("run-new")], next_before: null, warnings: [] });

    render(<AgentInspector thread={thread()} store={store} />);

    await waitFor(() => expect(store.getState().selectedRunByThread["th-1"]).toBe("run-new"));
    expect(screen.getByLabelText("历史运行")).toHaveValue("run-new");
  });

  it.each([
    ["deleted", new AgentApiError(404, { code: "DOCUMENT_NOT_FOUND", detail: "运行已不存在" })],
    ["quarantined", new AgentApiError(500, { code: "DOCUMENT_CORRUPT", detail: "运行已隔离" })],
  ])("refreshes summaries and falls back when selected detail was %s after listing", async (_case, detailError) => {
    const store = createAgentWorkspaceStore();
    store.getState().selectRun("th-1", "run-old");
    api.listRuns
      .mockResolvedValueOnce({ runs: [runItem("run-new"), runItem("run-old")], next_before: null, warnings: [] })
      .mockResolvedValueOnce({ runs: [runItem("run-new")], next_before: null, warnings: [] });
    api.getRun
      .mockRejectedValueOnce(detailError)
      .mockResolvedValueOnce({ ...runDetail("run-new"), active_elapsed_ms: 2_000 });

    render(<AgentInspector thread={thread()} store={store} />);

    await waitFor(() => expect(store.getState().selectedRunByThread["th-1"]).toBe("run-new"));
    await waitFor(() => expect(screen.getByText("Active").parentElement).toHaveTextContent("2 秒"));
    expect(screen.getByLabelText("历史运行")).toHaveValue("run-new");
    expect(screen.queryByRole("alert")).toBeNull();
    expect(api.listRuns).toHaveBeenCalledTimes(2);
    expect(api.getRun).toHaveBeenNthCalledWith(1, "run-old");
    expect(api.getRun).toHaveBeenNthCalledWith(2, "run-new");
    expect(api.getRun).toHaveBeenCalledTimes(2);
  });

  it("keeps an unrelated 404 visible without changing the selection or summaries", async () => {
    const store = createAgentWorkspaceStore();
    store.getState().selectRun("th-1", "run-old");
    api.listRuns.mockResolvedValue({
      runs: [runItem("run-new"), runItem("run-old")], next_before: null, warnings: [],
    });
    api.getRun.mockRejectedValue(new AgentApiError(404, {
      code: "ROUTE_NOT_FOUND",
      detail: "运行详情路由不存在",
    }));

    render(<AgentInspector thread={thread()} store={store} />);

    expect(await screen.findByRole("alert")).toHaveTextContent("运行详情路由不存在");
    expect(store.getState().selectedRunByThread["th-1"]).toBe("run-old");
    expect(screen.getByLabelText("历史运行")).toHaveValue("run-old");
    expect(screen.getByLabelText("历史运行").querySelectorAll("option")).toHaveLength(2);
    expect(api.listRuns).toHaveBeenCalledTimes(1);
    expect(api.getRun).toHaveBeenCalledTimes(1);
  });

  it("skips every unavailable summary while recovering selected detail", async () => {
    const store = createAgentWorkspaceStore();
    store.getState().selectRun("th-1", "run-old");
    api.listRuns.mockResolvedValue({
      runs: [runItem("run-new"), runItem("run-old"), runItem("run-healthy")],
      next_before: null,
      warnings: [],
    });
    const missing = new AgentApiError(404, {
      code: "DOCUMENT_NOT_FOUND",
      detail: "运行已不存在",
    });
    api.getRun
      .mockRejectedValueOnce(missing)
      .mockRejectedValueOnce(missing)
      .mockResolvedValueOnce({ ...runDetail("run-healthy"), active_elapsed_ms: 3_000 });

    render(<AgentInspector thread={thread()} store={store} />);

    await waitFor(() => expect(store.getState().selectedRunByThread["th-1"]).toBe("run-healthy"));
    await waitFor(() => expect(screen.getByText("Active").parentElement).toHaveTextContent("3 秒"));
    expect(screen.getByLabelText("历史运行")).toHaveValue("run-healthy");
    expect(api.listRuns).toHaveBeenCalledTimes(3);
    expect(api.getRun).toHaveBeenNthCalledWith(1, "run-old");
    expect(api.getRun).toHaveBeenNthCalledWith(2, "run-new");
    expect(api.getRun).toHaveBeenNthCalledWith(3, "run-healthy");
    expect(api.getRun).toHaveBeenCalledTimes(3);
  });

  it("ignores a stale history page after detail recovery refreshes the list", async () => {
    let resolvePage: ((value: { runs: AgentRunListItem[]; next_before: string | null; warnings: string[] }) => void) | undefined;
    let rejectOldDetail: ((reason?: unknown) => void) | undefined;
    const page = new Promise<{ runs: AgentRunListItem[]; next_before: string | null; warnings: string[] }>((resolve) => {
      resolvePage = resolve;
    });
    const oldDetail = new Promise<AgentRunDetail>((_resolve, reject) => {
      rejectOldDetail = reject;
    });
    const store = createAgentWorkspaceStore();
    store.getState().selectRun("th-1", "run-old");
    api.listRuns
      .mockResolvedValueOnce({
        runs: [runItem("run-new"), runItem("run-old")], next_before: "page-2", warnings: [],
      })
      .mockImplementationOnce(() => page)
      .mockResolvedValueOnce({ runs: [runItem("run-new")], next_before: "fresh-cursor", warnings: [] })
      .mockResolvedValueOnce({ runs: [runItem("run-earlier")], next_before: null, warnings: [] });
    api.getRun
      .mockImplementationOnce(() => oldDetail)
      .mockResolvedValueOnce({ ...runDetail("run-new"), active_elapsed_ms: 4_000 });

    render(<AgentInspector thread={thread()} store={store} />);

    await userEvent.setup().click(await screen.findByRole("button", { name: "加载更早运行" }));
    await waitFor(() => expect(api.listRuns).toHaveBeenCalledTimes(2));
    rejectOldDetail?.(new AgentApiError(404, {
      code: "DOCUMENT_NOT_FOUND",
      detail: "运行已不存在",
    }));
    await waitFor(() => expect(screen.getByText("Active").parentElement).toHaveTextContent("4 秒"));
    resolvePage?.({
      runs: [runItem("run-old"), runItem("run-page-2")],
      next_before: "stale-cursor",
      warnings: [],
    });

    await waitFor(() => expect(screen.getByRole("button", { name: "加载更早运行" })).not.toBeDisabled());
    expect(Array.from(screen.getByLabelText("历史运行").querySelectorAll("option"), (option) => option.value)).toEqual(["run-new"]);
    await userEvent.setup().click(screen.getByRole("button", { name: "加载更早运行" }));
    await waitFor(() => expect(api.listRuns).toHaveBeenCalledTimes(4));
    expect(api.listRuns).toHaveBeenNthCalledWith(4, "th-1", 50, "fresh-cursor");
  });

  it("resets pagination loading when detail recovery supersedes a pending page", async () => {
    let resolveOldPage: ((value: { runs: AgentRunListItem[]; next_before: string | null; warnings: string[] }) => void) | undefined;
    let resolveFreshPage: ((value: { runs: AgentRunListItem[]; next_before: string | null; warnings: string[] }) => void) | undefined;
    let rejectOldDetail: ((reason?: unknown) => void) | undefined;
    const oldPage = new Promise<{ runs: AgentRunListItem[]; next_before: string | null; warnings: string[] }>((resolve) => {
      resolveOldPage = resolve;
    });
    const freshPage = new Promise<{ runs: AgentRunListItem[]; next_before: string | null; warnings: string[] }>((resolve) => {
      resolveFreshPage = resolve;
    });
    const oldDetail = new Promise<AgentRunDetail>((_resolve, reject) => {
      rejectOldDetail = reject;
    });
    const store = createAgentWorkspaceStore();
    store.getState().selectRun("th-1", "run-old");
    api.listRuns
      .mockResolvedValueOnce({
        runs: [runItem("run-new"), runItem("run-old")], next_before: "old-page", warnings: [],
      })
      .mockImplementationOnce(() => oldPage)
      .mockResolvedValueOnce({ runs: [runItem("run-new")], next_before: "fresh-page", warnings: [] })
      .mockImplementationOnce(() => freshPage);
    api.getRun
      .mockImplementationOnce(() => oldDetail)
      .mockResolvedValueOnce({ ...runDetail("run-new"), active_elapsed_ms: 7_000 });
    render(<AgentInspector thread={thread()} store={store} />);

    await userEvent.setup().click(await screen.findByRole("button", { name: "加载更早运行" }));
    rejectOldDetail?.(new AgentApiError(404, {
      code: "DOCUMENT_NOT_FOUND",
      detail: "运行已不存在",
    }));
    await waitFor(() => expect(screen.getByText("Active").parentElement).toHaveTextContent("7 秒"));
    const loadMore = screen.getByRole("button", { name: "加载更早运行" });
    expect(loadMore).not.toBeDisabled();

    await userEvent.setup().click(loadMore);
    await waitFor(() => expect(api.listRuns).toHaveBeenCalledTimes(4));
    resolveOldPage?.({ runs: [runItem("run-old-page")], next_before: null, warnings: [] });
    await waitFor(() => expect(screen.getByRole("button", { name: "正在加载…" })).toBeDisabled());
    resolveFreshPage?.({ runs: [runItem("run-fresh-page")], next_before: null, warnings: [] });
    await waitFor(() => expect(screen.queryByRole("button", { name: "正在加载…" })).toBeNull());
  });

  it("blocks pagination during detail recovery and enables the refreshed cursor afterward", async () => {
    let resolveRecovery: ((value: { runs: AgentRunListItem[]; next_before: string | null; warnings: string[] }) => void) | undefined;
    const recovery = new Promise<{ runs: AgentRunListItem[]; next_before: string | null; warnings: string[] }>((resolve) => {
      resolveRecovery = resolve;
    });
    const store = createAgentWorkspaceStore();
    store.getState().selectRun("th-1", "run-old");
    api.listRuns
      .mockResolvedValueOnce({
        runs: [runItem("run-new"), runItem("run-old")], next_before: "page-2", warnings: [],
      })
      .mockImplementationOnce(() => recovery)
      .mockResolvedValueOnce({
        runs: [runItem("run-old"), runItem("run-page-2")], next_before: "stale-cursor", warnings: [],
      });
    api.getRun
      .mockRejectedValueOnce(new AgentApiError(404, {
        code: "DOCUMENT_NOT_FOUND",
        detail: "运行已不存在",
      }))
      .mockResolvedValueOnce({ ...runDetail("run-new"), active_elapsed_ms: 6_000 });

    render(<AgentInspector thread={thread()} store={store} />);

    await waitFor(() => expect(api.listRuns).toHaveBeenCalledTimes(2));
    const loadMore = screen.getByRole("button", { name: "加载更早运行" });
    expect(loadMore).toBeDisabled();
    await userEvent.setup().click(loadMore);
    expect(api.listRuns).toHaveBeenCalledTimes(2);
    resolveRecovery?.({ runs: [runItem("run-new")], next_before: "fresh-cursor", warnings: [] });

    await waitFor(() => expect(store.getState().selectedRunByThread["th-1"]).toBe("run-new"));
    await waitFor(() => expect(screen.getByText("Active").parentElement).toHaveTextContent("6 秒"));
    expect(Array.from(screen.getByLabelText("历史运行").querySelectorAll("option"), (option) => option.value)).toEqual(["run-new"]);
    expect(screen.getByRole("button", { name: "加载更早运行" })).not.toBeDisabled();
    await userEvent.setup().click(screen.getByRole("button", { name: "加载更早运行" }));
    expect(api.listRuns).toHaveBeenCalledTimes(3);
    expect(api.listRuns).toHaveBeenNthCalledWith(3, "th-1", 50, "fresh-cursor");
    expect(api.getRun).toHaveBeenCalledTimes(2);
  });

  it("keeps pagination blocked until a failed detail recovery settles", async () => {
    let rejectRecovery: ((reason?: unknown) => void) | undefined;
    const recovery = new Promise<never>((_resolve, reject) => {
      rejectRecovery = reject;
    });
    const store = createAgentWorkspaceStore();
    store.getState().selectRun("th-1", "run-old");
    api.listRuns
      .mockResolvedValueOnce({
        runs: [runItem("run-new"), runItem("run-old")], next_before: "page-2", warnings: [],
      })
      .mockImplementationOnce(() => recovery)
      .mockResolvedValueOnce({ runs: [runItem("run-earlier")], next_before: null, warnings: [] });
    api.getRun.mockRejectedValueOnce(new AgentApiError(404, {
      code: "DOCUMENT_NOT_FOUND",
      detail: "运行已不存在",
    }));

    render(<AgentInspector thread={thread()} store={store} />);

    await waitFor(() => expect(api.listRuns).toHaveBeenCalledTimes(2));
    const loadMore = screen.getByRole("button", { name: "加载更早运行" });
    expect(loadMore).toBeDisabled();
    await userEvent.setup().click(loadMore);
    expect(api.listRuns).toHaveBeenCalledTimes(2);
    rejectRecovery?.(new Error("运行历史刷新失败"));
    expect(await screen.findByRole("alert")).toHaveTextContent("运行历史刷新失败");

    await waitFor(() => expect(screen.getByRole("button", { name: "加载更早运行" })).not.toBeDisabled());
    expect(store.getState().selectedRunByThread["th-1"]).toBe("run-old");
    expect(Array.from(screen.getByLabelText("历史运行").querySelectorAll("option"), (option) => option.value)).toEqual([
      "run-new",
      "run-old",
    ]);
    await userEvent.setup().click(screen.getByRole("button", { name: "加载更早运行" }));
    await waitFor(() => expect(api.listRuns).toHaveBeenCalledTimes(3));
    expect(api.listRuns).toHaveBeenNthCalledWith(3, "th-1", 50, "page-2");
    expect(api.getRun).toHaveBeenCalledTimes(1);
  });

  it("invalidates pending detail work when the last-run baseline changes", async () => {
    let rejectOldDetail: ((reason?: unknown) => void) | undefined;
    const oldDetail = new Promise<AgentRunDetail>((_resolve, reject) => {
      rejectOldDetail = reject;
    });
    const store = createAgentWorkspaceStore();
    store.getState().selectRun("th-1", "run-old");
    api.listRuns
      .mockResolvedValueOnce({
        runs: [runItem("run-last-v1"), runItem("run-old")], next_before: null, warnings: [],
      })
      .mockResolvedValueOnce({
        runs: [runItem("run-last-v2"), runItem("run-old")], next_before: null, warnings: [],
      });
    api.getRun
      .mockImplementationOnce(() => oldDetail)
      .mockResolvedValueOnce({ ...runDetail("run-old"), active_elapsed_ms: 5_000 });
    const { rerender } = render(<AgentInspector thread={thread("th-1", "run-last-v1")} store={store} />);
    await waitFor(() => expect(api.getRun).toHaveBeenCalledTimes(1));

    rerender(<AgentInspector thread={thread("th-1", "run-last-v2")} store={store} />);
    await waitFor(() => expect(api.listRuns).toHaveBeenCalledTimes(2));
    rejectOldDetail?.(new AgentApiError(404, {
      code: "DOCUMENT_NOT_FOUND",
      detail: "旧基线运行已不存在",
    }));

    await waitFor(() => expect(api.getRun).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.getByText("Active").parentElement).toHaveTextContent("5 秒"));
    expect(store.getState().selectedRunByThread["th-1"]).toBe("run-old");
    expect(Array.from(screen.getByLabelText("历史运行").querySelectorAll("option"), (option) => option.value)).toEqual([
      "run-last-v2",
      "run-old",
    ]);
    expect(api.listRuns).toHaveBeenCalledTimes(2);
    expect(api.getRun).toHaveBeenNthCalledWith(1, "run-old");
    expect(api.getRun).toHaveBeenNthCalledWith(2, "run-old");
  });

  it("keeps a transient detail error visible without changing the selection", async () => {
    const store = createAgentWorkspaceStore();
    store.getState().selectRun("th-1", "run-old");
    api.listRuns.mockResolvedValue({
      runs: [runItem("run-new"), runItem("run-old")], next_before: null, warnings: [],
    });
    api.getRun.mockRejectedValue(new AgentApiError(503, {
      code: "RUN_DETAIL_UNAVAILABLE",
      detail: "运行详情暂时不可用",
    }));

    render(<AgentInspector thread={thread()} store={store} />);

    expect(await screen.findByRole("alert")).toHaveTextContent("运行详情暂时不可用");
    expect(store.getState().selectedRunByThread["th-1"]).toBe("run-old");
    expect(api.listRuns).toHaveBeenCalledTimes(1);
    expect(api.getRun).toHaveBeenCalledTimes(1);
  });

  it("keeps a pagination error visible when detail finishes successfully", async () => {
    let resolveDetail: ((run: AgentRunDetail) => void) | undefined;
    api.listRuns
      .mockResolvedValueOnce({ runs: [runItem("run-new")], next_before: "page-2", warnings: [] })
      .mockRejectedValueOnce(new Error("更早运行加载失败"));
    api.getRun.mockImplementationOnce(() => new Promise<AgentRunDetail>((resolve) => {
      resolveDetail = resolve;
    }));
    render(<AgentInspector thread={thread()} store={createAgentWorkspaceStore()} />);

    await userEvent.setup().click(await screen.findByRole("button", { name: "加载更早运行" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("更早运行加载失败");
    resolveDetail?.({ ...runDetail("run-new"), active_elapsed_ms: 8_000 });

    await waitFor(() => expect(screen.getByText("Active").parentElement).toHaveTextContent("8 秒"));
    expect(screen.getByRole("alert")).toHaveTextContent("更早运行加载失败");
  });

  it("clears a pagination error after the retry succeeds", async () => {
    api.listRuns
      .mockResolvedValueOnce({ runs: [runItem("run-new")], next_before: "page-2", warnings: [] })
      .mockRejectedValueOnce(new Error("更早运行加载失败"))
      .mockResolvedValueOnce({ runs: [runItem("run-old")], next_before: null, warnings: [] });
    render(<AgentInspector thread={thread()} store={createAgentWorkspaceStore()} />);

    await userEvent.setup().click(await screen.findByRole("button", { name: "加载更早运行" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("更早运行加载失败");
    await userEvent.setup().click(screen.getByRole("button", { name: "加载更早运行" }));

    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
    expect(screen.getByLabelText("历史运行").querySelectorAll("option")).toHaveLength(2);
  });

  it("refreshes selected REST detail after invalidation and ignores an older request finishing later", async () => {
    let resolveOld: ((run: AgentRunDetail) => void) | undefined;
    api.getRun
      .mockImplementationOnce(() => new Promise<AgentRunDetail>((resolve) => { resolveOld = resolve; }))
      .mockResolvedValueOnce({ ...runDetail("run-new"), control_revision: 3, active_elapsed_ms: 3_000 });
    const store = createAgentWorkspaceStore();
    const { rerender } = render(<AgentInspector thread={thread()} store={store} invalidationKey={0} />);
    await waitFor(() => expect(api.getRun).toHaveBeenCalledTimes(1));

    store.getState().markRunStale("th-1", "run-new");
    rerender(<AgentInspector thread={thread()} store={store} invalidationKey={1} />);
    await waitFor(() => expect(screen.getByText("3 秒")).toBeInTheDocument());
    resolveOld?.({ ...runDetail("run-new"), control_revision: 1, active_elapsed_ms: 1_000 });

    await waitFor(() => expect(screen.getByText("Active").parentElement).toHaveTextContent("3 秒"));
    expect(store.getState().watermark("budget.updated", "th-1", "run-new")).toBe(3);
  });

  it("highlights Approval only for the current actionable awaiting run", async () => {
    api.listRuns.mockResolvedValue({ runs: [runItem("run-live", "awaiting_approval")], next_before: null, warnings: [] });
    api.getRun.mockResolvedValue(runDetail("run-live", "th-1", "awaiting_approval"));
    render(<AgentInspector thread={{ ...thread("th-1", "run-live", "awaiting_approval"), resume_available: true }} store={createAgentWorkspaceStore()} />);

    const approvalTab = await screen.findByRole("tab", { name: /Approval/ });
    expect(approvalTab).toHaveTextContent("待处理");
    expect(approvalTab).toHaveClass("text-primary");
  });
});
