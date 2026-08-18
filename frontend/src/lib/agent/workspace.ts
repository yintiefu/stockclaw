import { createStore } from "zustand/vanilla";

import type { AgentRunDetail, AgentRunListItem, AgentStreamEvent, AgentThread } from "./types";

export type AgentDrawer = "threads" | "inspector" | "settings" | null;
export type AgentWorkspaceTab = "runs" | "artifacts" | "sources";
type WatermarkEvent = AgentStreamEvent["name"];

type AgentWorkspaceState = {
  drawer: AgentDrawer;
  tab: AgentWorkspaceTab;
  selectedRunByThread: Record<string, string>;
  runIdsByThread: Record<string, string[]>;
  watermarks: Record<string, number>;
  staleRunIds: Record<string, true>;
  openDrawer: (drawer: Exclude<AgentDrawer, null> | null) => void;
  setTab: (tab: AgentWorkspaceTab) => void;
  selectRun: (threadId: string, runId: string | null) => void;
  replaceRunList: (threadId: string, runs: AgentRunListItem[]) => void;
  selectedRunId: (thread: AgentThread) => string | null;
  watermark: (event: WatermarkEvent, threadId: string, runId?: string) => number;
  replaceRunDetail: (detail: AgentRunDetail) => void;
  applyEvent: (event: AgentStreamEvent) => void;
  markRunStale: (threadId: string, runId: string) => void;
  isRunStale: (runId: string) => boolean;
};

function watermarkKey(event: WatermarkEvent, threadId: string, runId?: string) {
  return `${event}:${threadId}:${runId ?? ""}`;
}

function eventIdentity(event: AgentStreamEvent): { threadId: string; runId?: string; revision: number } {
  if (event.name === "thread.revision.updated") {
    return { threadId: event.value.threadId, revision: event.value.revision };
  }
  if (event.name === "artifact.created") {
    return { threadId: event.value.threadId, runId: event.value.runId, revision: event.value.threadRevision };
  }
  return { threadId: event.value.threadId, runId: event.value.runId, revision: event.value.controlRevision };
}

/** UI 临时态：刻意不使用 persist/localStorage。 */
export function createAgentWorkspaceStore() {
  return createStore<AgentWorkspaceState>((set, get) => ({
    drawer: null,
    tab: "runs",
    selectedRunByThread: {},
    runIdsByThread: {},
    watermarks: {},
    staleRunIds: {},
    openDrawer: (drawer) => set({ drawer }),
    setTab: (tab) => set({ tab }),
    selectRun: (threadId, runId) => set((state) => {
      const selectedRunByThread = { ...state.selectedRunByThread };
      if (runId) selectedRunByThread[threadId] = runId;
      else delete selectedRunByThread[threadId];
      return { selectedRunByThread };
    }),
    replaceRunList: (threadId, runs) => set((state) => {
      const runIdsByThread = { ...state.runIdsByThread, [threadId]: runs.map((run) => run.id) };
      const selectedRunByThread = { ...state.selectedRunByThread };
      if (selectedRunByThread[threadId] && !runIdsByThread[threadId].includes(selectedRunByThread[threadId])) {
        delete selectedRunByThread[threadId];
      }
      return { runIdsByThread, selectedRunByThread };
    }),
    selectedRunId: (thread) => {
      const selected = get().selectedRunByThread[thread.id];
      const runIds = get().runIdsByThread[thread.id];
      return selected && (!runIds || runIds.includes(selected)) ? selected : thread.last_run?.id ?? null;
    },
    watermark: (event, threadId, runId) => get().watermarks[watermarkKey(event, threadId, runId)] ?? 0,
    replaceRunDetail: (detail) => set((state) => {
      const watermarks = { ...state.watermarks };
      for (const event of ["budget.updated", "sources.updated"] as const) {
        const key = watermarkKey(event, detail.thread_id, detail.id);
        watermarks[key] = Math.max(watermarks[key] ?? 0, detail.control_revision);
      }
      const staleRunIds = { ...state.staleRunIds };
      delete staleRunIds[detail.id];
      return { watermarks, staleRunIds };
    }),
    applyEvent: (event) => {
      const { threadId, runId, revision } = eventIdentity(event);
      const current = get().watermark(event.name, threadId, runId);
      if (revision <= current) return;
      if (revision !== current + 1) {
        if (runId) get().markRunStale(threadId, runId);
        return;
      }
      set((state) => ({
        watermarks: { ...state.watermarks, [watermarkKey(event.name, threadId, runId)]: revision },
      }));
    },
    markRunStale: (_threadId, runId) => set((state) => ({
      staleRunIds: { ...state.staleRunIds, [runId]: true },
    })),
    isRunStale: (runId) => get().staleRunIds[runId] === true,
  }));
}
