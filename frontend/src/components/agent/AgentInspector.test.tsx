import { useLayoutEffect, useState } from "react";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AgentRunDetail, AgentRunListItem, AgentThread, ArtifactDetail, ArtifactMetadata } from "@/lib/agent/types";
import { createAgentWorkspaceStore } from "@/lib/agent/workspace";

const api = vi.hoisted(() => ({
  listRuns: vi.fn(),
  getRun: vi.fn(),
  listArtifacts: vi.fn(),
  getArtifact: vi.fn(),
  downloadArtifact: vi.fn(),
  deleteArtifact: vi.fn(),
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

const artifactMetadata = (
  id: string,
  runId: string,
  parentArtifactId: string | null,
  hasChildren = false,
): ArtifactMetadata => ({
  id,
  thread_id: "th-1",
  run_id: runId,
  type: "markdown",
  title: id,
  created_at: "2026-08-19T00:00:00Z",
  parent_artifact_id: parentArtifactId,
  source_count: 0,
  has_children: hasChildren,
});

const artifactDetail = (record: ArtifactMetadata): ArtifactDetail => ({
  schema_version: 1,
  id: record.id,
  thread_id: record.thread_id,
  run_id: record.run_id,
  type: "markdown",
  title: record.title,
  created_at: record.created_at,
  parent_artifact_id: record.parent_artifact_id,
  source_ids: [],
  content: { markdown: `# ${record.title}` },
});

type WorkspaceStore = ReturnType<typeof createAgentWorkspaceStore>;
type DetailProbeSnapshot = {
  sourceBadge: string;
  oldSource: boolean;
  oldTelemetry: boolean;
  oldActive: boolean;
  emptyState: string | null;
};

function DetailCommitProbe({
  activeThread,
  store,
  probeKey,
  onCommit,
}: {
  activeThread: AgentThread;
  store: WorkspaceStore;
  probeKey: number;
  onCommit: (snapshot: DetailProbeSnapshot) => void;
}) {
  useLayoutEffect(() => {
    if (probeKey === 0) return;
    onCommit({
      sourceBadge: screen.getByRole("tab", { name: /Sources/ }).textContent ?? "",
      oldSource: screen.queryByText("仅属于旧运行的来源") !== null,
      oldTelemetry: screen.queryByLabelText("运行遥测") !== null,
      oldActive: screen.queryByText("9 秒") !== null,
      emptyState: screen.queryByRole("status")?.textContent ?? null,
    });
  }, [onCommit, probeKey]);
  return <AgentInspector thread={activeThread} store={store} />;
}

beforeEach(() => {
  vi.clearAllMocks();
  api.listRuns.mockResolvedValue({ runs: [runItem("run-new")], next_before: null, warnings: [] });
  api.getRun.mockImplementation(async (id: string) => ({ ...runDetail(id), sources: [
    { id: "source-1", kind: "model_url", url: "https://example.test", label: null, created_at: "2026-08-19T00:00:01Z", verification: "model_provided_unverified" },
  ] }));
  api.listArtifacts.mockResolvedValue({ artifacts: [], warnings: [] });
  api.getArtifact.mockRejectedValue(new Error("unexpected artifact detail request"));
  api.downloadArtifact.mockResolvedValue({ blob: new Blob(["artifact"]), filename: "artifact-1.md" });
  api.deleteArtifact.mockResolvedValue({ thread_revision: 2 });
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

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

  it.each(["sources", "runs"] as const)(
    "does not render cached run detail on the first thread-switch commit in the %s tab",
    async (tab) => {
      const old = {
        ...runDetail("run-old", "th-1"),
        active_elapsed_ms: 9_000,
        sources: [{
          id: "source-old",
          kind: "model_url" as const,
          url: "https://old.example.test",
          label: "仅属于旧运行的来源",
          created_at: "2026-08-19T00:00:01Z",
          verification: "model_provided_unverified" as const,
        }],
      };
      const pendingDetail = new Promise<AgentRunDetail>(() => undefined);
      api.listRuns.mockImplementation(async (threadId: string) => ({
        runs: [runItem(threadId === "th-1" ? "run-old" : "run-next")],
        next_before: null,
        warnings: [],
      }));
      api.getRun.mockImplementation((runId: string) => (
        runId === old.id ? Promise.resolve(old) : pendingDetail
      ));
      const store = createAgentWorkspaceStore();
      store.getState().setTab(tab);
      const onCommit = vi.fn<(snapshot: DetailProbeSnapshot) => void>();
      const { rerender } = render(
        <DetailCommitProbe activeThread={thread("th-1", "run-old")} store={store} probeKey={0} onCommit={onCommit} />,
      );
      if (tab === "sources") {
        expect(await screen.findByText("仅属于旧运行的来源")).toBeInTheDocument();
      } else {
        await waitFor(() => expect(screen.getByText("Active").parentElement).toHaveTextContent("9 秒"));
      }
      expect(screen.getByRole("tab", { name: /Sources/ })).toHaveTextContent("1");

      rerender(
        <DetailCommitProbe activeThread={thread("th-2", "run-next")} store={store} probeKey={1} onCommit={onCommit} />,
      );

      expect(onCommit).toHaveBeenCalledWith({
        sourceBadge: "Sources",
        oldSource: false,
        oldTelemetry: false,
        oldActive: false,
        emptyState: "正在加载运行详情…",
      });
      expect(api.getRun).toHaveBeenCalledTimes(2);
    },
  );

  it.each(["sources", "runs"] as const)(
    "does not render cached run detail on the first historical-run switch commit in the %s tab",
    async (tab) => {
      const old = {
        ...runDetail("run-old"),
        active_elapsed_ms: 9_000,
        sources: [{
          id: "source-old",
          kind: "model_url" as const,
          url: "https://old.example.test",
          label: "仅属于旧运行的来源",
          created_at: "2026-08-19T00:00:01Z",
          verification: "model_provided_unverified" as const,
        }],
      };
      const pendingDetail = new Promise<AgentRunDetail>(() => undefined);
      api.listRuns.mockResolvedValue({
        runs: [runItem("run-new"), runItem("run-old")],
        next_before: null,
        warnings: [],
      });
      api.getRun.mockImplementation((runId: string) => (
        runId === old.id ? Promise.resolve(old) : pendingDetail
      ));
      const store = createAgentWorkspaceStore();
      store.getState().selectRun("th-1", old.id);
      store.getState().setTab(tab);
      const onCommit = vi.fn<(snapshot: DetailProbeSnapshot) => void>();
      const { rerender } = render(
        <DetailCommitProbe activeThread={thread()} store={store} probeKey={0} onCommit={onCommit} />,
      );
      if (tab === "sources") {
        expect(await screen.findByText("仅属于旧运行的来源")).toBeInTheDocument();
      } else {
        await waitFor(() => expect(screen.getByText("Active").parentElement).toHaveTextContent("9 秒"));
      }
      expect(screen.getByRole("tab", { name: /Sources/ })).toHaveTextContent("1");

      store.getState().selectRun("th-1", "run-new");
      rerender(<DetailCommitProbe activeThread={thread()} store={store} probeKey={1} onCommit={onCommit} />);

      expect(onCommit).toHaveBeenCalledWith({
        sourceBadge: "Sources",
        oldSource: false,
        oldTelemetry: false,
        oldActive: false,
        emptyState: "正在加载运行详情…",
      });
      expect(api.getRun).toHaveBeenCalledTimes(2);
    },
  );

  it("does not carry a failed run detail error into a pending historical run", async () => {
    const store = createAgentWorkspaceStore();
    store.getState().selectRun("th-1", "run-a");
    api.listRuns.mockResolvedValue({
      runs: [runItem("run-b"), runItem("run-a")],
      next_before: null,
      warnings: [],
    });
    const pendingDetail = new Promise<AgentRunDetail>(() => undefined);
    api.getRun
      .mockRejectedValueOnce(new AgentApiError(503, {
        code: "RUN_DETAIL_UNAVAILABLE",
        detail: "run-a 详情失败",
      }))
      .mockImplementationOnce(() => pendingDetail);
    const { rerender } = render(<AgentInspector thread={thread("th-1", "run-b")} store={store} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("run-a 详情失败");

    store.getState().selectRun("th-1", "run-b");
    rerender(<AgentInspector thread={thread("th-1", "run-b")} store={store} />);

    expect(screen.queryByText("run-a 详情失败")).toBeNull();
    expect(screen.getByRole("status")).toHaveTextContent("正在加载运行详情…");
    expect(screen.getByRole("tab", { name: /Sources/ })).toHaveTextContent(/^Sources$/);
    expect(api.getRun).toHaveBeenCalledTimes(2);
  });

  it("shows the current run detail error in Sources instead of an empty state", async () => {
    api.getRun.mockRejectedValueOnce(new AgentApiError(503, {
      code: "RUN_DETAIL_UNAVAILABLE",
      detail: "当前运行详情失败",
    }));
    const store = createAgentWorkspaceStore();
    store.getState().setTab("sources");
    render(<AgentInspector thread={thread()} store={store} />);

    await waitFor(() => expect(api.getRun).toHaveBeenCalledWith("run-new"));
    expect(screen.getByRole("alert")).toHaveTextContent("当前运行详情失败");
    expect(screen.queryByText("当前运行没有可查看的来源")).toBeNull();
    expect(screen.getByRole("tab", { name: /Sources/ })).toHaveTextContent(/^Sources$/);
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
  ])("recovers and owns later list errors when selected detail was %s before listing", async (_case, detailError) => {
    const pendingInitialList = new Promise<never>(() => undefined);
    const store = createAgentWorkspaceStore();
    store.getState().selectRun("th-1", "run-old");
    api.listRuns
      .mockImplementationOnce(() => pendingInitialList)
      .mockResolvedValueOnce({ runs: [runItem("run-new")], next_before: "page-2", warnings: [] })
      .mockRejectedValueOnce(new Error("恢复后分页失败"));
    api.getRun
      .mockRejectedValueOnce(detailError)
      .mockResolvedValueOnce({ ...runDetail("run-new"), active_elapsed_ms: 2_000 });

    render(<AgentInspector thread={thread()} store={store} />);

    await waitFor(() => expect(store.getState().selectedRunByThread["th-1"]).toBe("run-new"));
    await waitFor(() => expect(screen.getByText("Active").parentElement).toHaveTextContent("2 秒"));
    expect(screen.getByLabelText("历史运行")).toHaveValue("run-new");
    expect(screen.queryByRole("alert")).toBeNull();
    await userEvent.setup().click(screen.getByRole("button", { name: "加载更早运行" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("恢复后分页失败");
    expect(api.listRuns).toHaveBeenCalledTimes(3);
    expect(api.getRun).toHaveBeenNthCalledWith(1, "run-old");
    expect(api.getRun).toHaveBeenNthCalledWith(2, "run-new");
    expect(api.getRun).toHaveBeenCalledTimes(2);
  });

  it.each([
    ["deleted", new AgentApiError(404, { code: "DOCUMENT_NOT_FOUND", detail: "运行已不存在" })],
    ["quarantined", new AgentApiError(500, { code: "DOCUMENT_CORRUPT", detail: "运行已隔离" })],
  ])("shows only the owned recovery error when selected detail was %s and refresh fails", async (_case, detailError) => {
    const recoveryError = "运行历史恢复失败";
    const pendingInitialList = new Promise<never>(() => undefined);
    const pendingList = new Promise<never>(() => undefined);
    const pendingDetail = new Promise<AgentRunDetail>(() => undefined);
    const store = createAgentWorkspaceStore();
    store.getState().selectRun("th-1", "run-old");
    api.listRuns
      .mockImplementationOnce(() => pendingInitialList)
      .mockRejectedValueOnce(new Error(recoveryError))
      .mockImplementationOnce(() => pendingList);
    api.getRun
      .mockRejectedValueOnce(detailError)
      .mockImplementationOnce(() => pendingDetail);
    const snapshots: boolean[] = [];

    function RecoveryProbe({ activeThread, probeKey }: { activeThread: AgentThread; probeKey: number }) {
      useLayoutEffect(() => {
        if (probeKey > 0) snapshots.push(screen.queryByText(recoveryError) !== null);
      }, [probeKey]);
      return <AgentInspector thread={activeThread} store={store} />;
    }

    const { rerender } = render(<RecoveryProbe activeThread={thread("th-1", "run-old")} probeKey={0} />);
    await screen.findByText(recoveryError);
    const runState = {
      alerts: screen.queryAllByRole("alert").map((alert) => alert.textContent),
      loading: screen.queryByText("正在加载运行详情…") !== null,
    };

    await userEvent.setup().click(screen.getByRole("tab", { name: /Sources/ }));
    const sourceState = {
      alerts: screen.queryAllByRole("alert").map((alert) => alert.textContent),
      loading: screen.queryByText("正在加载运行详情…") !== null,
      empty: screen.queryByText("当前运行没有可查看的来源") !== null,
    };

    await userEvent.setup().click(screen.getByRole("tab", { name: /Run/ }));
    rerender(<RecoveryProbe activeThread={thread("th-2", "run-next")} probeKey={1} />);

    expect({ runState, sourceState, snapshots }).toEqual({
      runState: { alerts: [recoveryError], loading: false },
      sourceState: { alerts: [recoveryError], loading: false, empty: false },
      snapshots: [false],
    });
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

  it("shows selected-run artifacts first with only their thread-chain context", async () => {
    const parent = artifactMetadata("artifact-parent", "run-old", null, true);
    const current = artifactMetadata("artifact-current", "run-new", parent.id);
    const unrelated = artifactMetadata("artifact-unrelated", "run-old", null);
    api.listArtifacts.mockResolvedValue({ artifacts: [unrelated, parent, current], warnings: [] });
    api.getArtifact.mockImplementation(async (_threadId: string, artifactId: string) => artifactDetail(
      [parent, current, unrelated].find((item) => item.id === artifactId)!,
    ));
    render(<AgentInspector thread={thread()} store={createAgentWorkspaceStore()} />);

    await userEvent.setup().click(await screen.findByRole("tab", { name: /Artifact/ }));

    const preview = await screen.findByLabelText("Artifact 预览");
    expect(within(preview.querySelector("header")!).getByRole("heading", { name: "artifact-current" })).toBeInTheDocument();
    const versions = screen.getByRole("navigation", { name: "Artifact 版本" });
    expect(within(versions).getAllByRole("button").map((button) => button.textContent)).toEqual([
      expect.stringContaining("artifact-parent"),
      expect.stringContaining("artifact-current"),
    ]);
    expect(within(versions).queryByText("artifact-unrelated")).toBeNull();
    expect(api.listArtifacts).toHaveBeenCalledTimes(1);
    expect(api.listArtifacts).toHaveBeenCalledWith("th-1");
    expect(api.getArtifact).toHaveBeenCalledTimes(1);
  });

  it("distinguishes explicit cross-run artifact activation from a stale selection prop", async () => {
    const parent = artifactMetadata("artifact-a-parent", "run-base", null, true);
    const runA = artifactMetadata("artifact-a-current", "run-a", parent.id);
    const runB = artifactMetadata("artifact-b-current", "run-b", null);
    const runC = artifactMetadata("artifact-c-current", "run-c", null);
    api.listRuns.mockResolvedValue({
      runs: [runItem("run-c"), runItem("run-b"), runItem("run-a")],
      next_before: null,
      warnings: [],
    });
    api.listArtifacts.mockResolvedValue({ artifacts: [parent, runA, runB, runC], warnings: [] });
    api.getArtifact.mockImplementation(async (_threadId: string, artifactId: string) => artifactDetail(
      [parent, runA, runB, runC].find((item) => item.id === artifactId)!,
    ));
    const store = createAgentWorkspaceStore();
    store.getState().selectRun("th-1", "run-b");
    const snapshots: Array<{ oldPreview: boolean; oldDelete: boolean; oldVersions: boolean }> = [];

    function CommitProbe({ probeKey, inspectorKey = 0 }: { probeKey: number; inspectorKey?: number }) {
      const [selectedArtifactId, setSelectedArtifactId] = useState<string | null>(runA.id);
      const [activationKey, setActivationKey] = useState(1);
      useLayoutEffect(() => {
        if (probeKey === 0) return;
        const preview = screen.queryByLabelText("Artifact 预览");
        snapshots.push({
          oldPreview: preview
            ? within(preview.querySelector("header")!).queryByRole("heading", { name: runA.title }) !== null
            : false,
          oldDelete: screen.queryByRole("button", { name: "删除 Artifact" }) !== null,
          oldVersions: screen.queryByRole("navigation", { name: "Artifact 版本" })?.textContent?.includes(parent.title) === true,
        });
      }, [probeKey]);
      return (
        <>
          <button
            type="button"
            onClick={() => {
              setSelectedArtifactId(runA.id);
              setActivationKey((value) => value + 1);
            }}
          >显式打开 A</button>
          <AgentInspector
            key={inspectorKey}
            thread={thread("th-1", "run-c")}
            store={store}
            selectedArtifactId={selectedArtifactId}
            artifactActivationKey={activationKey}
            onSelectArtifact={setSelectedArtifactId}
          />
        </>
      );
    }

    const { rerender } = render(<CommitProbe probeKey={0} />);
    expect(await screen.findByRole("tab", { name: /Artifact/ })).toHaveAttribute("aria-selected", "true");
    await waitFor(() => expect(within(screen.getByLabelText("Artifact 预览").querySelector("header")!)
      .getByRole("heading", { name: runA.title })).toBeInTheDocument());
    await userEvent.setup().click(await screen.findByRole("button", { name: `查看版本：${parent.title}` }));
    await waitFor(() => expect(within(screen.getByLabelText("Artifact 预览").querySelector("header")!)
      .getByRole("heading", { name: parent.title })).toBeInTheDocument());
    await userEvent.setup().click(screen.getByRole("button", { name: `查看版本：${runA.title}` }));
    await waitFor(() => expect(within(screen.getByLabelText("Artifact 预览").querySelector("header")!)
      .getByRole("heading", { name: runA.title })).toBeInTheDocument());

    await userEvent.setup().click(screen.getByRole("tab", { name: /Run/ }));
    expect(screen.queryByLabelText("Artifact 预览")).toBeNull();
    await userEvent.setup().click(screen.getByRole("button", { name: "显式打开 A" }));
    expect(screen.getByRole("tab", { name: /Artifact/ })).toHaveAttribute("aria-selected", "true");
    await waitFor(() => expect(within(screen.getByLabelText("Artifact 预览").querySelector("header")!)
      .getByRole("heading", { name: runA.title })).toBeInTheDocument());

    store.getState().selectRun("th-1", "run-c");
    rerender(<CommitProbe probeKey={1} />);

    expect(snapshots).toEqual([{ oldPreview: false, oldDelete: false, oldVersions: false }]);
    await waitFor(() => expect(within(screen.getByLabelText("Artifact 预览").querySelector("header")!)
      .getByRole("heading", { name: runC.title })).toBeInTheDocument());
    expect(screen.queryByLabelText("Artifact")).toBeNull();
    expect(screen.queryByRole("navigation", { name: "Artifact 版本" })).toBeNull();
    expect(screen.queryByText(parent.title)).toBeNull();
    expect(screen.queryByText(runA.title)).toBeNull();
    expect(screen.getByRole("button", { name: "删除 Artifact" })).toBeEnabled();

    rerender(<CommitProbe probeKey={1} inspectorKey={1} />);

    await waitFor(() => expect(within(screen.getByLabelText("Artifact 预览").querySelector("header")!)
      .getByRole("heading", { name: runC.title })).toBeInTheDocument());
    expect(screen.queryByText(runA.title)).toBeNull();
    expect(screen.getByRole("button", { name: "删除 Artifact" })).toBeEnabled();
    expect(api.getArtifact).toHaveBeenLastCalledWith("th-1", runC.id);
  });

  it("waits for an invalidated artifact list before consuming an activation revision", async () => {
    const runA = artifactMetadata("artifact-a-current", "run-a", null);
    const runB = artifactMetadata("artifact-b-current", "run-b", null);
    let resolveRefresh: ((value: { artifacts: ArtifactMetadata[]; warnings: [] }) => void) | undefined;
    api.listRuns.mockResolvedValue({
      runs: [runItem("run-b"), runItem("run-a")],
      next_before: null,
      warnings: [],
    });
    api.listArtifacts
      .mockResolvedValueOnce({ artifacts: [runB], warnings: [] })
      .mockImplementationOnce(() => new Promise((resolve) => { resolveRefresh = resolve; }));
    api.getArtifact.mockImplementation(async (_threadId: string, artifactId: string) => artifactDetail(
      [runA, runB].find((item) => item.id === artifactId)!,
    ));
    const store = createAgentWorkspaceStore();
    store.getState().selectRun("th-1", "run-b");

    function ActivationProbe() {
      const [selectedArtifactId, setSelectedArtifactId] = useState<string | null>(null);
      const [activationKey, setActivationKey] = useState(0);
      const [invalidationKey, setInvalidationKey] = useState(0);
      return (
        <>
          <button
            type="button"
            onClick={() => {
              setSelectedArtifactId(runA.id);
              setActivationKey((value) => value + 1);
              setInvalidationKey((value) => value + 1);
            }}
          >刷新并打开 A</button>
          <AgentInspector
            thread={thread("th-1", "run-b")}
            store={store}
            invalidationKey={invalidationKey}
            selectedArtifactId={selectedArtifactId}
            artifactActivationKey={activationKey}
          />
        </>
      );
    }

    render(<ActivationProbe />);
    await userEvent.setup().click(await screen.findByRole("tab", { name: /Artifact/ }));
    await waitFor(() => expect(within(screen.getByLabelText("Artifact 预览").querySelector("header")!)
      .getByRole("heading", { name: runB.title })).toBeInTheDocument());

    await userEvent.setup().click(screen.getByRole("button", { name: "刷新并打开 A" }));
    await waitFor(() => expect(api.listArtifacts).toHaveBeenCalledTimes(2));
    resolveRefresh?.({ artifacts: [runA, runB], warnings: [] });

    await waitFor(() => expect(within(screen.getByLabelText("Artifact 预览").querySelector("header")!)
      .getByRole("heading", { name: runA.title })).toBeInTheDocument());
  });

  it("does not render artifacts from the previous thread in the first new-thread commit", async () => {
    const parent = artifactMetadata("artifact-old-parent", "run-old", null, true);
    const leaf = artifactMetadata("artifact-old-leaf", "run-new", parent.id);
    const pendingList = new Promise<never>(() => undefined);
    const pendingDetail = new Promise<never>(() => undefined);
    api.listArtifacts.mockImplementation((threadId: string) => (
      threadId === "th-1" ? Promise.resolve({ artifacts: [parent, leaf], warnings: [] }) : pendingList
    ));
    api.getArtifact.mockImplementation((threadId: string) => (
      threadId === "th-1" ? Promise.resolve(artifactDetail(leaf)) : pendingDetail
    ));
    const snapshots: Array<{
      preview: boolean;
      selector: boolean;
      versions: boolean;
      download: boolean;
      remove: boolean;
      oldTitle: boolean;
    }> = [];
    const store = createAgentWorkspaceStore();

    function CommitProbe({ activeThread }: { activeThread: AgentThread }) {
      useLayoutEffect(() => {
        if (activeThread.id !== "th-2") return;
        snapshots.push({
          preview: screen.queryByLabelText("Artifact 预览") !== null,
          selector: screen.queryByLabelText("Artifact") !== null,
          versions: screen.queryByRole("navigation", { name: "Artifact 版本" }) !== null,
          download: screen.queryByRole("button", { name: "下载 Artifact" }) !== null,
          remove: screen.queryByRole("button", { name: "删除 Artifact" }) !== null,
          oldTitle: document.body.textContent?.includes(leaf.title) === true,
        });
      }, [activeThread.id]);
      return <AgentInspector thread={activeThread} store={store} />;
    }

    const { rerender } = render(<CommitProbe activeThread={thread()} />);
    await userEvent.setup().click(await screen.findByRole("tab", { name: /Artifact/ }));
    const preview = await screen.findByLabelText("Artifact 预览");
    expect(within(preview.querySelector("header")!).getByRole("heading", { name: leaf.title })).toBeInTheDocument();
    expect(screen.getByLabelText("Artifact")).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Artifact 版本" })).toBeInTheDocument();

    rerender(<CommitProbe activeThread={thread("th-2", "run-other")} />);

    expect(snapshots).toEqual([{
      preview: false,
      selector: false,
      versions: false,
      download: false,
      remove: false,
      oldTitle: false,
    }]);
  });

  it("downloads the backend blob with its response filename", async () => {
    const item = artifactMetadata("artifact-current", "run-new", null);
    api.listArtifacts.mockResolvedValue({ artifacts: [item], warnings: [] });
    api.getArtifact.mockResolvedValue(artifactDetail(item));
    const blob = new Blob(["safe"]);
    api.downloadArtifact.mockResolvedValue({ blob, filename: "artifact-current.md" });
    const createObjectURL = vi.fn(() => "blob:artifact-download");
    const revokeObjectURL = vi.fn();
    vi.stubGlobal("URL", { ...URL, createObjectURL, revokeObjectURL });
    let downloadedAs = "";
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function captureDownload() {
      downloadedAs = this.download;
    });
    render(<AgentInspector thread={thread()} store={createAgentWorkspaceStore()} />);
    await userEvent.setup().click(await screen.findByRole("tab", { name: /Artifact/ }));
    await userEvent.setup().click(await screen.findByRole("button", { name: "下载 Artifact" }));

    await waitFor(() => expect(api.downloadArtifact).toHaveBeenCalledWith("th-1", "artifact-current"));
    expect(createObjectURL).toHaveBeenCalledWith(blob);
    expect(downloadedAs).toBe("artifact-current.md");
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:artifact-download");
  });

  it("deletes only a leaf with the current revision and reloads thread, list, and parent detail", async () => {
    const parent = artifactMetadata("artifact-parent", "run-old", null, true);
    const leaf = artifactMetadata("artifact-leaf", "run-new", parent.id);
    api.listArtifacts
      .mockResolvedValueOnce({ artifacts: [parent, leaf], warnings: [] })
      .mockResolvedValueOnce({ artifacts: [{ ...parent, has_children: false }], warnings: [] });
    api.getArtifact.mockImplementation(async (_threadId: string, artifactId: string) => artifactDetail(
      artifactId === leaf.id ? leaf : { ...parent, has_children: false },
    ));
    const onReloadThread = vi.fn().mockResolvedValue(undefined);
    const onSelectArtifact = vi.fn();
    render(
      <AgentInspector
        thread={{ ...thread(), revision: 7 }}
        store={createAgentWorkspaceStore()}
        selectedArtifactId={leaf.id}
        onReloadThread={onReloadThread}
        onSelectArtifact={onSelectArtifact}
      />,
    );
    await userEvent.setup().click(await screen.findByRole("tab", { name: /Artifact/ }));
    await userEvent.setup().click(await screen.findByRole("button", { name: "删除 Artifact" }));

    await waitFor(() => expect(api.deleteArtifact).toHaveBeenCalledWith("th-1", leaf.id, 7));
    expect(onReloadThread).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(api.listArtifacts).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(api.getArtifact).toHaveBeenLastCalledWith("th-1", parent.id));
    expect(onSelectArtifact).toHaveBeenLastCalledWith(parent.id);
    const preview = await screen.findByLabelText("Artifact 预览");
    expect(within(preview.querySelector("header")!).getByRole("heading", { name: parent.title })).toBeInTheDocument();
  });

  it("keeps a newer invalidation authoritative while artifact reload waits for the thread", async () => {
    const stale = artifactMetadata("artifact-stale", "run-new", null);
    const current = artifactMetadata("artifact-current", "run-new", null);
    api.listArtifacts
      .mockResolvedValueOnce({ artifacts: [stale], warnings: [] })
      .mockResolvedValueOnce({ artifacts: [current], warnings: [] })
      .mockResolvedValueOnce({ artifacts: [stale], warnings: [] });
    api.getArtifact.mockImplementation(async (_threadId: string, artifactId: string) => artifactDetail(
      artifactId === stale.id ? stale : current,
    ));
    let resolveReload: (() => void) | undefined;
    const onReloadThread = vi.fn(() => new Promise<void>((resolve) => { resolveReload = resolve; }));
    const store = createAgentWorkspaceStore();

    function InvalidationProbe() {
      const [invalidationKey, setInvalidationKey] = useState(0);
      return (
        <>
          <button type="button" onClick={() => setInvalidationKey((value) => value + 1)}>
            刷新 Artifact 列表
          </button>
          <AgentInspector
            thread={thread()}
            store={store}
            invalidationKey={invalidationKey}
            onReloadThread={onReloadThread}
          />
        </>
      );
    }

    render(<InvalidationProbe />);
    await userEvent.setup().click(await screen.findByRole("tab", { name: /Artifact/ }));
    await userEvent.setup().click(await screen.findByRole("button", { name: "删除 Artifact" }));
    await waitFor(() => expect(onReloadThread).toHaveBeenCalledTimes(1));

    await userEvent.setup().click(screen.getByRole("button", { name: "刷新 Artifact 列表" }));
    await waitFor(() => expect(within(screen.getByLabelText("Artifact 预览").querySelector("header")!)
      .getByRole("heading", { name: current.title })).toBeInTheDocument());
    resolveReload?.();

    await waitFor(() => expect(screen.getByRole("button", { name: "删除 Artifact" })).toBeEnabled());
    expect(api.listArtifacts).toHaveBeenCalledTimes(2);
    expect(within(screen.getByLabelText("Artifact 预览").querySelector("header")!)
      .getByRole("heading", { name: current.title })).toBeInTheDocument();
  });

  it("immediately discards stale artifact state while a 409 recovery reload is pending", async () => {
    const parent = artifactMetadata("artifact-parent", "run-old", null, true);
    const stale = artifactMetadata("artifact-stale", "run-new", parent.id);
    const current = artifactMetadata("artifact-current", "run-new", null);
    api.listArtifacts
      .mockResolvedValueOnce({ artifacts: [parent, stale], warnings: [] })
      .mockResolvedValueOnce({ artifacts: [current], warnings: [] });
    api.getArtifact.mockImplementation(async (_threadId: string, artifactId: string) => artifactDetail(
      artifactId === stale.id ? stale : current,
    ));
    api.deleteArtifact.mockRejectedValueOnce(new AgentApiError(409, {
      code: "THREAD_REVISION_CONFLICT",
      detail: "线程 revision 已变化",
    }));
    let resolveReload: (() => void) | undefined;
    const pendingReload = new Promise<void>((resolve) => { resolveReload = resolve; });
    const onReloadThread = vi.fn(() => pendingReload);
    const onSelectArtifact = vi.fn();
    render(
      <AgentInspector
        thread={{ ...thread(), revision: 3 }}
        store={createAgentWorkspaceStore()}
        onReloadThread={onReloadThread}
        onSelectArtifact={onSelectArtifact}
      />,
    );
    await userEvent.setup().click(await screen.findByRole("tab", { name: /Artifact/ }));
    expect(await screen.findByLabelText("Artifact")).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Artifact 版本" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "删除 Artifact" }));

    await waitFor(() => expect(onReloadThread).toHaveBeenCalledTimes(1));
    expect(screen.queryByLabelText("Artifact 预览")).toBeNull();
    expect(screen.queryByLabelText("Artifact")).toBeNull();
    expect(screen.queryByRole("navigation", { name: "Artifact 版本" })).toBeNull();
    expect(screen.queryByRole("button", { name: "下载 Artifact" })).toBeNull();
    expect(screen.queryByRole("button", { name: "删除 Artifact" })).toBeNull();
    expect(screen.queryByText(stale.title)).toBeNull();
    expect(onSelectArtifact).toHaveBeenLastCalledWith(null);
    expect(api.listArtifacts).toHaveBeenCalledTimes(1);

    resolveReload?.();
    await waitFor(() => expect(api.listArtifacts).toHaveBeenCalledTimes(2));
    const preview = await screen.findByLabelText("Artifact 预览");
    const previewHeader = within(preview.querySelector("header")!);
    expect(previewHeader.getByRole("heading", { name: current.title })).toBeInTheDocument();
    expect(previewHeader.queryByRole("heading", { name: stale.title })).toBeNull();
    expect(screen.getByRole("alert")).toHaveTextContent("线程 revision 已变化，已重新加载权威状态");
  });

  it("immediately discards stale artifact state and reports an incomplete 500 recovery", async () => {
    const parent = artifactMetadata("artifact-parent", "run-old", null, true);
    const item = artifactMetadata("artifact-current", "run-new", parent.id);
    api.listArtifacts
      .mockResolvedValueOnce({ artifacts: [parent, item], warnings: [] })
      .mockResolvedValueOnce({ artifacts: [item], warnings: [{
        code: "ARTIFACT_ORPHAN",
        document_type: "artifact",
        filename: "th-1/artifact-current.json",
      }] });
    api.getArtifact.mockResolvedValue(artifactDetail(item));
    api.deleteArtifact.mockRejectedValueOnce(new AgentApiError(500, {
      code: "ARTIFACT_DELETE_FAILED",
      detail: "文件清理失败，已保留权威状态",
    }));
    let rejectReload: ((reason?: unknown) => void) | undefined;
    const pendingReload = new Promise<void>((_resolve, reject) => { rejectReload = reject; });
    const onReloadThread = vi.fn(() => pendingReload);
    const onSelectArtifact = vi.fn();
    render(
      <AgentInspector
        thread={thread()}
        store={createAgentWorkspaceStore()}
        onReloadThread={onReloadThread}
        onSelectArtifact={onSelectArtifact}
      />,
    );
    await userEvent.setup().click(await screen.findByRole("tab", { name: /Artifact/ }));
    expect(await screen.findByLabelText("Artifact")).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Artifact 版本" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "删除 Artifact" }));

    await waitFor(() => expect(onReloadThread).toHaveBeenCalledTimes(1));
    expect(screen.queryByLabelText("Artifact 预览")).toBeNull();
    expect(screen.queryByLabelText("Artifact")).toBeNull();
    expect(screen.queryByRole("navigation", { name: "Artifact 版本" })).toBeNull();
    expect(screen.queryByRole("button", { name: "下载 Artifact" })).toBeNull();
    expect(screen.queryByRole("button", { name: "删除 Artifact" })).toBeNull();
    expect(screen.queryByText(item.title)).toBeNull();
    expect(onSelectArtifact).toHaveBeenLastCalledWith(null);
    expect(api.listArtifacts).toHaveBeenCalledTimes(1);

    rejectReload?.(new Error("thread reload failed"));
    const warning = await screen.findByRole("alert");
    expect(warning).toHaveTextContent("删除结果需要核对；权威状态重载未完成");
    expect(warning).not.toHaveTextContent("仍被引用");
    expect(warning).not.toHaveTextContent("已保留权威状态");
    expect(api.listArtifacts).toHaveBeenCalledTimes(2);
    expect(screen.queryByLabelText("Artifact 预览")).toBeNull();
    expect(api.getArtifact).toHaveBeenCalledTimes(1);
  });

  it("ignores a delete response that arrives after switching threads", async () => {
    let resolveDelete: ((value: { thread_revision: number }) => void) | undefined;
    const pendingDelete = new Promise<{ thread_revision: number }>((resolve) => { resolveDelete = resolve; });
    const oldArtifact = artifactMetadata("artifact-old", "run-old", null);
    const newArtifact = { ...artifactMetadata("artifact-new", "run-new", null), thread_id: "th-2" };
    api.listRuns.mockImplementation(async (threadId: string) => ({
      runs: [runItem(threadId === "th-1" ? "run-old" : "run-new")],
      next_before: null,
      warnings: [],
    }));
    api.listArtifacts.mockImplementation(async (threadId: string) => ({
      artifacts: threadId === "th-1" ? [oldArtifact] : [newArtifact],
      warnings: [],
    }));
    api.getArtifact.mockImplementation(async (_threadId: string, artifactId: string) => artifactDetail(
      artifactId === oldArtifact.id ? oldArtifact : newArtifact,
    ));
    api.deleteArtifact.mockImplementationOnce(() => pendingDelete);
    const onReloadThread = vi.fn().mockResolvedValue(undefined);
    const store = createAgentWorkspaceStore();
    const { rerender } = render(
      <AgentInspector
        thread={thread("th-1", "run-old")}
        store={store}
        selectedArtifactId={oldArtifact.id}
        onReloadThread={onReloadThread}
      />,
    );
    await userEvent.setup().click(await screen.findByRole("tab", { name: /Artifact/ }));
    await userEvent.setup().click(await screen.findByRole("button", { name: "删除 Artifact" }));
    rerender(
      <AgentInspector
        thread={thread("th-2", "run-new")}
        store={store}
        onReloadThread={onReloadThread}
      />,
    );
    const nextPreview = await screen.findByLabelText("Artifact 预览");
    expect(within(nextPreview.querySelector("header")!).getByRole("heading", { name: newArtifact.title })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "删除 Artifact" })).toBeEnabled();

    resolveDelete?.({ thread_revision: 2 });
    await waitFor(() => expect(api.deleteArtifact).toHaveBeenCalledTimes(1));

    expect(onReloadThread).not.toHaveBeenCalled();
    expect(api.listArtifacts).toHaveBeenCalledTimes(2);
    expect(within(screen.getByLabelText("Artifact 预览").querySelector("header")!)
      .getByRole("heading", { name: newArtifact.title })).toBeInTheDocument();
  });

  it("shows missing-reference warnings and renders selected-run Sources facts", async () => {
    api.listArtifacts.mockResolvedValue({ artifacts: [], warnings: [{
      code: "ARTIFACT_MISSING_REF",
      document_type: "artifact",
      filename: "th-1/artifact-missing.json",
    }] });
    api.getRun.mockResolvedValue({ ...runDetail("run-new"), sources: [
      { id: "tool-1", kind: "tool_execution", tool_call_id: "call-1", tool_name: "get_quote", origin: "builtin", completed_at: "t", arguments_summary: "代码", result_summary: "行情", verification: "executed_record" },
      { id: "url-1", kind: "model_url", url: "https://example.test/report", label: "资料", created_at: "t", verification: "model_provided_unverified" },
    ] });
    render(<AgentInspector thread={thread()} store={createAgentWorkspaceStore()} selectedArtifactId="artifact-missing" />);

    await userEvent.setup().click(await screen.findByRole("tab", { name: /Artifact/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("artifact-missing.json");
    await userEvent.setup().click(screen.getByRole("tab", { name: /Sources/ }));
    expect(screen.getByRole("region", { name: "执行记录" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "模型提供，未验证" })).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/评分|排名|推荐|score|rank|stars?|quality/i);
  });
});
