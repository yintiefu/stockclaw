import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from "react";

import { agentApi } from "@/lib/agent/api";
import type { AgentApiError } from "@/lib/agent/api";
import { createAgentWorkspaceStore } from "@/lib/agent/workspace";
import type { AgentRunDetail, AgentRunListItem, AgentThread } from "@/lib/agent/types";
import { ApprovalPanel } from "./ApprovalPanel";
import { RunInspector } from "./RunInspector";

type WorkspaceStore = ReturnType<typeof createAgentWorkspaceStore>;
type InspectorTab = "runs" | "approval" | "artifacts" | "sources";

const TABS: Array<{ id: InspectorTab; label: string }> = [
  { id: "runs", label: "Run" },
  { id: "approval", label: "Approval" },
  { id: "artifacts", label: "Artifact" },
  { id: "sources", label: "Sources" },
];

type Props = {
  thread: AgentThread;
  store: WorkspaceStore;
  invalidationKey?: number;
  approvalDisabled?: boolean;
  approvalConnected?: boolean;
  selectedArtifactId?: string | null;
  artifactActivationKey?: number;
};

function EmptyState({ children }: { children: string }) {
  return (
    <div role="status" className="flex min-h-24 items-center justify-center rounded-md border border-dashed border-border px-3 text-center text-xs text-muted-foreground">
      {children}
    </div>
  );
}

function unavailableRunDetail(error: unknown): error is AgentApiError {
  if (!(error instanceof Error) || error.name !== "AgentApiError") return false;
  const apiError = error as AgentApiError;
  return (apiError.status === 404 && apiError.code === "DOCUMENT_NOT_FOUND")
    || (apiError.status === 500 && apiError.code === "DOCUMENT_CORRUPT");
}

export function AgentInspector({
  thread,
  store,
  invalidationKey = 0,
  approvalDisabled = false,
  approvalConnected = true,
  selectedArtifactId = null,
  artifactActivationKey = 0,
}: Props) {
  const [, refreshWorkspace] = useState(0);
  const workspace = store.getState();
  const [activeTab, setActiveTab] = useState<InspectorTab>(workspace.tab ?? "runs");
  const [runs, setRuns] = useState<AgentRunListItem[]>([]);
  const [nextBefore, setNextBefore] = useState<string | null>(null);
  const [listLoaded, setListLoaded] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [recoveringList, setRecoveringList] = useState(false);
  const [detail, setDetail] = useState<AgentRunDetail | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const listRequest = useRef(0);
  const detailRequest = useRef(0);
  const recoveringListRequest = useRef<number | null>(null);
  const loadingMoreRequest = useRef<{ threadId: string; generation: number } | null>(null);
  const unavailableRunIds = useRef(new Set<string>());
  const lastDetailKey = useRef("");
  const lastWasStale = useRef(false);
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([]);

  useEffect(() => store.subscribe(() => refreshWorkspace((value) => value + 1)), [store]);

  useEffect(() => {
    if (workspace.tab) setActiveTab(workspace.tab);
  }, [workspace.tab]);

  useEffect(() => {
    if (artifactActivationKey > 0) setActiveTab("artifacts");
  }, [artifactActivationKey]);

  useEffect(() => {
    let disposed = false;
    detailRequest.current += 1;
    recoveringListRequest.current = null;
    loadingMoreRequest.current = null;
    unavailableRunIds.current.clear();
    lastDetailKey.current = "";
    setRuns([]);
    setNextBefore(null);
    setListLoaded(false);
    setLoadingMore(false);
    setRecoveringList(false);
    setDetail(null);
    setListError(null);
    setDetailError(null);
    void (async () => {
      const request = ++listRequest.current;
      try {
        const response = await agentApi.listRuns(thread.id, 50);
        if (disposed || request !== listRequest.current) return;
        listRequest.current += 1;
        setRuns(response.runs);
        setNextBefore(response.next_before);
        setListLoaded(true);
        store.getState().replaceRunList(thread.id, response.runs);
        const selected = store.getState().selectedRunByThread[thread.id];
        const fallback = selected && response.runs.some((run) => run.id === selected)
          ? selected
          : response.runs.some((run) => run.id === thread.last_run?.id)
            ? thread.last_run?.id ?? null
            : response.runs[0]?.id ?? null;
        store.getState().selectRun(thread.id, fallback);
      } catch (error) {
        if (!disposed && request === listRequest.current) {
          setListError(error instanceof Error ? error.message : "运行历史加载失败");
        }
      } finally {
        if (!disposed && request === listRequest.current) setListLoaded(true);
      }
    })();
    return () => { disposed = true; };
  }, [store, thread.id, thread.last_run?.id]);

  const selectedRunId = listLoaded
    ? workspace.selectedRunByThread[thread.id] ?? null
    : workspace.selectedRunByThread[thread.id] ?? thread.last_run?.id ?? null;
  const stale = selectedRunId ? workspace.staleRunIds[selectedRunId] === true : false;

  const fetchDetail = useCallback(async (runId: string) => {
    const request = ++detailRequest.current;
    try {
      const loaded = await agentApi.getRun(runId);
      if (request !== detailRequest.current) return;
      setDetail(loaded);
      setDetailError(null);
      store.getState().replaceRunDetail(loaded);
    } catch (error) {
      if (request === detailRequest.current) {
        setDetail(null);
        if (!unavailableRunDetail(error)) {
          setDetailError(error instanceof Error ? error.message : "运行详情加载失败");
          return;
        }
        setDetailError(null);
        unavailableRunIds.current.add(runId);
        const refresh = ++listRequest.current;
        recoveringListRequest.current = refresh;
        loadingMoreRequest.current = null;
        setLoadingMore(false);
        setRecoveringList(true);
        try {
          const response = await agentApi.listRuns(thread.id, 50);
          if (request !== detailRequest.current || refresh !== listRequest.current) return;
          listRequest.current += 1;
          const availableRuns = response.runs.filter((run) => !unavailableRunIds.current.has(run.id));
          setRuns(availableRuns);
          setNextBefore(response.next_before);
          setListLoaded(true);
          setListError(null);
          store.getState().replaceRunList(thread.id, availableRuns);
          const fallback = availableRuns.some((run) => run.id === thread.last_run?.id)
            ? thread.last_run?.id ?? null
            : availableRuns[0]?.id ?? null;
          store.getState().selectRun(thread.id, fallback);
        } catch (refreshError) {
          if (request === detailRequest.current && refresh === listRequest.current) {
            setListError(refreshError instanceof Error ? refreshError.message : "运行历史加载失败");
          }
        } finally {
          if (recoveringListRequest.current === refresh) {
            recoveringListRequest.current = null;
            setRecoveringList(false);
          }
        }
      }
    }
  }, [store, thread.id, thread.last_run?.id]);

  useEffect(() => {
    if (!selectedRunId) {
      detailRequest.current += 1;
      setDetail(null);
      lastDetailKey.current = "";
      lastWasStale.current = false;
      return;
    }
    const key = `${selectedRunId}:${invalidationKey}`;
    const baseChanged = key !== lastDetailKey.current;
    const becameStale = stale && !lastWasStale.current;
    lastDetailKey.current = key;
    lastWasStale.current = stale;
    if (!baseChanged && !becameStale) return;
    if (detail?.id !== selectedRunId) setDetail(null);
    void fetchDetail(selectedRunId);
  }, [detail?.id, fetchDetail, invalidationKey, selectedRunId, stale]);

  const loadMore = async () => {
    if (!nextBefore || loadingMore || recoveringListRequest.current !== null) return;
    const request = listRequest.current;
    const loadingRequest = { threadId: thread.id, generation: request };
    loadingMoreRequest.current = loadingRequest;
    setLoadingMore(true);
    try {
      const response = await agentApi.listRuns(thread.id, 50, nextBefore);
      if (request !== listRequest.current) return;
      const availableRuns = runs.filter((run) => !unavailableRunIds.current.has(run.id));
      const known = new Set(availableRuns.map((run) => run.id));
      const merged = [...availableRuns, ...response.runs.filter((run) => (
        !known.has(run.id) && !unavailableRunIds.current.has(run.id)
      ))];
      setRuns(merged);
      setNextBefore(response.next_before);
      setListError(null);
      store.getState().replaceRunList(thread.id, merged);
    } catch (error) {
      if (request === listRequest.current) {
        setListError(error instanceof Error ? error.message : "运行历史加载失败");
      }
    } finally {
      if (loadingMoreRequest.current?.threadId === loadingRequest.threadId
        && loadingMoreRequest.current.generation === loadingRequest.generation) {
        loadingMoreRequest.current = null;
        setLoadingMore(false);
      }
    }
  };

  const selectTab = (tab: InspectorTab) => {
    setActiveTab(tab);
    if (tab !== "approval") store.getState().setTab(tab);
  };

  const handleTabKey = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    const offset = event.key === "ArrowRight" ? 1 : -1;
    const next = (index + offset + TABS.length) % TABS.length;
    selectTab(TABS[next].id);
    tabRefs.current[next]?.focus();
  };

  const actionableApproval = selectedRunId === thread.last_run?.id
    && thread.last_run.status === "awaiting_approval"
    && thread.resume_available === true;
  const sourceCount = detail?.sources.length ?? 0;

  return (
    <div className="flex min-h-full flex-col">
      <div role="tablist" aria-label="Inspector 视图" className="grid grid-cols-4 border-b border-border/70">
        {TABS.map((tab, index) => {
          const selected = activeTab === tab.id;
          const badge = tab.id === "runs" ? runs.length
            : tab.id === "approval" ? (actionableApproval ? "待处理" : null)
              : tab.id === "artifacts" ? thread.artifact_ids.length
                : sourceCount;
          return (
            <button
              key={tab.id}
              ref={(node) => { tabRefs.current[index] = node; }}
              id={`agent-inspector-tab-${tab.id}`}
              role="tab"
              type="button"
              aria-selected={selected}
              aria-controls={`agent-inspector-panel-${tab.id}`}
              tabIndex={selected ? 0 : -1}
              onClick={() => selectTab(tab.id)}
              onKeyDown={(event) => handleTabKey(event, index)}
              className={`min-w-0 border-b-2 px-1 py-2 text-xs ${selected ? "border-primary text-foreground" : "border-transparent text-muted-foreground"} ${tab.id === "approval" && actionableApproval ? "text-primary" : ""}`}
            >
              <span className="block truncate">{tab.label}</span>
              {badge !== null ? <span className="mt-0.5 block text-[10px] tabular-nums">{badge}</span> : null}
            </button>
          );
        })}
      </div>

      <div
        id={`agent-inspector-panel-${activeTab}`}
        role="tabpanel"
        aria-labelledby={`agent-inspector-tab-${activeTab}`}
        className="min-h-0 flex-1 px-3 py-3"
      >
        {activeTab === "runs" ? (
          <div className="space-y-3">
            {runs.length > 0 ? (
              <label className="block text-xs text-muted-foreground">
                历史运行
                <select
                  aria-label="历史运行"
                  className="mt-1 w-full rounded-md border border-border bg-background px-2 py-1.5 text-xs text-foreground"
                  value={selectedRunId ?? ""}
                  onChange={(event) => store.getState().selectRun(thread.id, event.target.value)}
                >
                  {runs.map((run) => (
                    <option key={run.id} value={run.id}>{run.id} · {run.status}</option>
                  ))}
                </select>
              </label>
            ) : null}
            {nextBefore ? (
              <button type="button" onClick={() => void loadMore()} disabled={loadingMore || recoveringList} className="text-xs text-primary disabled:opacity-50">
                {loadingMore ? "正在加载…" : "加载更早运行"}
              </button>
            ) : null}
            {listError ? <p role="alert" className="text-xs text-destructive">{listError}</p> : null}
            {detailError ? <p role="alert" className="text-xs text-destructive">{detailError}</p> : null}
            {detail ? <RunInspector run={detail} /> : listLoaded && !listError && !detailError ? <EmptyState>没有可查看的运行</EmptyState> : null}
          </div>
        ) : null}
        {activeTab === "approval" ? (
          approvalConnected
            ? <ApprovalPanel disabled={approvalDisabled} actionable={actionableApproval} />
            : <EmptyState>当前没有可操作的审批</EmptyState>
        ) : null}
        {activeTab === "artifacts" ? (
          selectedArtifactId
            ? <EmptyState>{`已选择 Artifact：${selectedArtifactId}`}</EmptyState>
            : <EmptyState>当前运行没有可查看的 Artifact</EmptyState>
        ) : null}
        {activeTab === "sources" ? <EmptyState>当前运行没有可查看的来源</EmptyState> : null}
      </div>
    </div>
  );
}
