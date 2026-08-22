import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";

import { agentApi } from "@/lib/agent/api";
import type { AgentApiError } from "@/lib/agent/api";
import { createAgentWorkspaceStore } from "@/lib/agent/workspace";
import type {
  AgentRecoveryWarning,
  AgentRunDetail,
  AgentRunListItem,
  AgentThread,
  ArtifactDetail,
  ArtifactMetadata,
} from "@/lib/agent/types";
import { ApprovalPanel } from "./ApprovalPanel";
import { ArtifactViewer } from "./ArtifactViewer";
import { RunInspector } from "./RunInspector";
import { SourceInspector } from "./SourceInspector";

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
  onSelectArtifact?: (artifactId: string | null) => void;
  onReloadThread?: () => Promise<void>;
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

function artifactChain(artifacts: ArtifactMetadata[], artifactId: string): ArtifactMetadata[] {
  const byId = new Map(artifacts.map((artifact) => [artifact.id, artifact]));
  const children = new Map<string, ArtifactMetadata[]>();
  for (const artifact of artifacts) {
    if (!artifact.parent_artifact_id) continue;
    children.set(artifact.parent_artifact_id, [...(children.get(artifact.parent_artifact_id) ?? []), artifact]);
  }
  let root = byId.get(artifactId);
  const seen = new Set<string>();
  while (root?.parent_artifact_id && !seen.has(root.id)) {
    seen.add(root.id);
    root = byId.get(root.parent_artifact_id) ?? root;
    if (!byId.has(root.parent_artifact_id ?? "")) break;
  }
  if (!root) return [];
  const ordered: ArtifactMetadata[] = [];
  const visit = (item: ArtifactMetadata) => {
    if (ordered.some((known) => known.id === item.id)) return;
    ordered.push(item);
    for (const child of children.get(item.id) ?? []) visit(child);
  };
  visit(root);
  return ordered;
}

function artifactErrorStatus(error: unknown): number | null {
  if (!(error instanceof Error) || error.name !== "AgentApiError") return null;
  return (error as AgentApiError).status;
}

function referencedArtifacts(artifacts: ArtifactMetadata[], warnings: AgentRecoveryWarning[]) {
  const orphanFiles = new Set(warnings
    .filter((warning) => warning.code === "ARTIFACT_ORPHAN")
    .map((warning) => warning.filename.split("/").at(-1)));
  return artifacts.filter((artifact) => !orphanFiles.has(`${artifact.id}.json`));
}

export function AgentInspector({
  thread,
  store,
  invalidationKey = 0,
  approvalDisabled = false,
  approvalConnected = true,
  selectedArtifactId = null,
  artifactActivationKey = 0,
  onSelectArtifact,
  onReloadThread,
}: Props) {
  const [, refreshWorkspace] = useState(0);
  const workspace = store.getState();
  const [activeTab, setActiveTab] = useState<InspectorTab>(workspace.tab ?? "runs");
  const [runs, setRuns] = useState<AgentRunListItem[]>([]);
  const [nextBefore, setNextBefore] = useState<string | null>(null);
  const [listLoaded, setListLoaded] = useState(false);
  const [runListThreadId, setRunListThreadId] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [recoveringList, setRecoveringList] = useState(false);
  const [detail, setDetail] = useState<AgentRunDetail | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<{ threadId: string; runId: string; message: string } | null>(null);
  const [artifacts, setArtifacts] = useState<ArtifactMetadata[]>([]);
  const [artifactWarnings, setArtifactWarnings] = useState<AgentRecoveryWarning[]>([]);
  const [artifactsLoaded, setArtifactsLoaded] = useState(false);
  const [artifactStateThreadId, setArtifactStateThreadId] = useState<string | null>(null);
  const [artifactStateInvalidationKey, setArtifactStateInvalidationKey] = useState<number | null>(null);
  const [activeArtifactId, setActiveArtifactId] = useState<string | null>(null);
  const [activeArtifactRunId, setActiveArtifactRunId] = useState<string | null>(null);
  const [artifactDetail, setArtifactDetail] = useState<ArtifactDetail | null>(null);
  const [artifactError, setArtifactError] = useState<string | null>(null);
  const [artifactActionWarning, setArtifactActionWarning] = useState<string | null>(null);
  const [artifactBusy, setArtifactBusy] = useState(false);
  const listRequest = useRef(0);
  const detailRequest = useRef(0);
  const recoveringListRequest = useRef<number | null>(null);
  const loadingMoreRequest = useRef<{ threadId: string; generation: number } | null>(null);
  const unavailableRunIds = useRef(new Set<string>());
  const lastDetailKey = useRef("");
  const lastWasStale = useRef(false);
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const artifactListRequest = useRef(0);
  const artifactDetailRequest = useRef(0);
  const artifactSelectionRun = useRef<string | null>(null);
  const lastArtifactActivationKey = useRef(0);
  const currentThreadId = useRef(thread.id);
  currentThreadId.current = thread.id;

  useEffect(() => store.subscribe(() => refreshWorkspace((value) => value + 1)), [store]);

  useEffect(() => {
    if (workspace.tab) setActiveTab(workspace.tab);
  }, [workspace.tab]);

  useEffect(() => {
    if (artifactActivationKey > 0) setActiveTab("artifacts");
  }, [artifactActivationKey]);

  useEffect(() => setArtifactBusy(false), [thread.id]);

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
    setRunListThreadId(null);
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
        setRunListThreadId(thread.id);
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
          setRunListThreadId(thread.id);
        }
      } finally {
        if (!disposed && request === listRequest.current) setListLoaded(true);
      }
    })();
    return () => { disposed = true; };
  }, [store, thread.id, thread.last_run?.id]);

  const selectedRunId = listLoaded && runListThreadId === thread.id
    ? workspace.selectedRunByThread[thread.id] ?? null
    : workspace.selectedRunByThread[thread.id] ?? thread.last_run?.id ?? null;
  const currentRunDetail = detail?.thread_id === thread.id && detail.id === selectedRunId ? detail : null;
  const currentDetailError = detailError?.threadId === thread.id && detailError.runId === selectedRunId
    ? detailError.message
    : null;
  const currentListError = runListThreadId === thread.id ? listError : null;
  const stale = selectedRunId ? workspace.staleRunIds[selectedRunId] === true : false;
  const artifactStateIsCurrent = artifactStateThreadId === thread.id
    && artifactStateInvalidationKey === invalidationKey;
  const currentArtifacts = useMemo(
    () => artifactStateIsCurrent ? artifacts.filter((artifact) => artifact.thread_id === thread.id) : [],
    [artifactStateIsCurrent, artifacts, thread.id],
  );
  const currentArtifactsLoaded = artifactStateIsCurrent && artifactsLoaded;
  const ownedActiveArtifactId = activeArtifactRunId === selectedRunId ? activeArtifactId : null;
  const activeArtifactMetadata = currentArtifacts.find((artifact) => artifact.id === ownedActiveArtifactId) ?? null;
  const currentArtifactDetail = artifactDetail?.thread_id === thread.id
    && artifactDetail.id === activeArtifactMetadata?.id
    ? artifactDetail
    : null;

  useEffect(() => {
    let disposed = false;
    const request = ++artifactListRequest.current;
    artifactDetailRequest.current += 1;
    artifactSelectionRun.current = null;
    setArtifacts([]);
    setArtifactWarnings([]);
    setArtifactsLoaded(false);
    setArtifactStateThreadId(null);
    setArtifactStateInvalidationKey(null);
    setActiveArtifactId(null);
    setActiveArtifactRunId(null);
    setArtifactDetail(null);
    setArtifactError(null);
    setArtifactActionWarning(null);
    void agentApi.listArtifacts(thread.id).then((response) => {
      if (disposed || request !== artifactListRequest.current) return;
      const listed = referencedArtifacts(response.artifacts, response.warnings)
        .filter((artifact) => artifact.thread_id === thread.id);
      setArtifacts(listed);
      setArtifactWarnings(response.warnings);
      setArtifactsLoaded(true);
      setArtifactStateThreadId(thread.id);
      setArtifactStateInvalidationKey(invalidationKey);
    }).catch((error) => {
      if (disposed || request !== artifactListRequest.current) return;
      setArtifactError(error instanceof Error ? error.message : "Artifact 列表加载失败");
      setArtifactsLoaded(true);
      setArtifactStateThreadId(thread.id);
      setArtifactStateInvalidationKey(invalidationKey);
    });
    return () => { disposed = true; };
  }, [invalidationKey, thread.id]);

  useEffect(() => {
    if (!currentArtifactsLoaded) return;
    const activationChanged = artifactActivationKey > 0
      && artifactActivationKey !== lastArtifactActivationKey.current;
    if (activationChanged) lastArtifactActivationKey.current = artifactActivationKey;
    const runChanged = artifactSelectionRun.current !== selectedRunId;
    artifactSelectionRun.current = selectedRunId;
    const explicitlyRequested = activationChanged && selectedArtifactId
      ? currentArtifacts.find((artifact) => artifact.id === selectedArtifactId)?.id ?? null
      : null;
    const currentRunRequested = !activationChanged && selectedArtifactId
      ? currentArtifacts.find((artifact) => (
        artifact.id === selectedArtifactId && artifact.run_id === selectedRunId
      ))?.id ?? null
      : null;
    const retained = !runChanged && ownedActiveArtifactId
      ? currentArtifacts.find((artifact) => artifact.id === ownedActiveArtifactId)?.id ?? null
      : null;
    const primary = currentArtifacts.find((artifact) => artifact.run_id === selectedRunId)?.id ?? null;
    if (!activationChanged && runChanged && selectedArtifactId && !currentRunRequested) {
      onSelectArtifact?.(null);
    }
    setActiveArtifactId(explicitlyRequested ?? currentRunRequested ?? retained ?? primary);
    setActiveArtifactRunId(selectedRunId);
  }, [artifactActivationKey, currentArtifacts, currentArtifactsLoaded, onSelectArtifact, ownedActiveArtifactId, selectedArtifactId, selectedRunId]);

  useEffect(() => {
    if (!ownedActiveArtifactId || !activeArtifactMetadata) {
      artifactDetailRequest.current += 1;
      setArtifactDetail(null);
      return;
    }
    let disposed = false;
    const request = ++artifactDetailRequest.current;
    setArtifactError(null);
    if (artifactDetail?.id !== ownedActiveArtifactId || artifactDetail.thread_id !== thread.id) setArtifactDetail(null);
    void agentApi.getArtifact(thread.id, ownedActiveArtifactId).then((loaded) => {
      if (disposed || request !== artifactDetailRequest.current) return;
      if (loaded.thread_id !== thread.id || loaded.id !== ownedActiveArtifactId) return;
      setArtifactDetail(loaded);
    }).catch((error) => {
      if (disposed || request !== artifactDetailRequest.current) return;
      setArtifactDetail(null);
      setArtifactError(error instanceof Error ? error.message : "Artifact 详情加载失败");
    });
    return () => { disposed = true; };
  }, [activeArtifactMetadata, ownedActiveArtifactId, thread.id]);

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
          setDetailError({
            threadId: thread.id,
            runId,
            message: error instanceof Error ? error.message : "运行详情加载失败",
          });
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
          setRunListThreadId(thread.id);
          setListError(null);
          store.getState().replaceRunList(thread.id, availableRuns);
          const fallback = availableRuns.some((run) => run.id === thread.last_run?.id)
            ? thread.last_run?.id ?? null
            : availableRuns[0]?.id ?? null;
          store.getState().selectRun(thread.id, fallback);
        } catch (refreshError) {
          if (request === detailRequest.current && refresh === listRequest.current) {
            setListError(refreshError instanceof Error ? refreshError.message : "运行历史加载失败");
            setRunListThreadId(thread.id);
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

  const visibleArtifacts = useMemo(() => {
    const primary = selectedRunId
      ? currentArtifacts.filter((artifact) => artifact.run_id === selectedRunId)
      : currentArtifacts;
    const ordered = [...primary];
    const known = new Set(ordered.map((artifact) => artifact.id));
    const focus = [...primary];
    if (activeArtifactMetadata && !focus.some((artifact) => artifact.id === activeArtifactMetadata.id)) {
      focus.push(activeArtifactMetadata);
    }
    for (const artifact of focus) {
      for (const version of artifactChain(currentArtifacts, artifact.id)) {
        if (!known.has(version.id)) {
          known.add(version.id);
          ordered.push(version);
        }
      }
    }
    return ordered;
  }, [activeArtifactMetadata, currentArtifacts, selectedRunId]);

  const versions = useMemo(
    () => ownedActiveArtifactId ? artifactChain(currentArtifacts, ownedActiveArtifactId) : [],
    [currentArtifacts, ownedActiveArtifactId],
  );

  const selectArtifact = (artifactId: string) => {
    setActiveArtifactId(artifactId);
    setActiveArtifactRunId(selectedRunId);
    setArtifactActionWarning(null);
    onSelectArtifact?.(artifactId);
  };

  const discardArtifactState = (expectedThreadId: string) => {
    if (currentThreadId.current !== expectedThreadId) return;
    artifactListRequest.current += 1;
    artifactDetailRequest.current += 1;
    artifactSelectionRun.current = null;
    setArtifacts([]);
    setArtifactWarnings([]);
    setArtifactsLoaded(false);
    setArtifactStateThreadId(null);
    setArtifactStateInvalidationKey(null);
    setActiveArtifactId(null);
    setActiveArtifactRunId(null);
    setArtifactDetail(null);
    setArtifactError(null);
    setArtifactActionWarning(null);
    onSelectArtifact?.(null);
  };

  const reloadArtifacts = async (preferredArtifactId: string | null, expectedThreadId: string): Promise<boolean> => {
    if (currentThreadId.current !== expectedThreadId) return false;
    const request = ++artifactListRequest.current;
    let reloadFailed = false;
    try {
      await onReloadThread?.();
    } catch {
      reloadFailed = true;
    }
    if (request !== artifactListRequest.current || currentThreadId.current !== expectedThreadId) return false;
    artifactDetailRequest.current += 1;
    setArtifactDetail(null);
    try {
      const response = await agentApi.listArtifacts(expectedThreadId);
      if (request !== artifactListRequest.current || currentThreadId.current !== expectedThreadId) return false;
      const listed = referencedArtifacts(response.artifacts, response.warnings)
        .filter((artifact) => artifact.thread_id === expectedThreadId);
      setArtifacts(listed);
      setArtifactWarnings(response.warnings);
      setArtifactsLoaded(true);
      setArtifactStateThreadId(expectedThreadId);
      setArtifactStateInvalidationKey(invalidationKey);
      setArtifactError(null);
      const preferred = preferredArtifactId
        ? listed.find((artifact) => artifact.id === preferredArtifactId)?.id ?? null
        : null;
      const fallback = listed.find((artifact) => artifact.run_id === selectedRunId)?.id ?? null;
      const next = preferred ?? fallback;
      setActiveArtifactId(next);
      setActiveArtifactRunId(selectedRunId);
      onSelectArtifact?.(next);
      if (reloadFailed) setArtifactActionWarning("线程重载失败；Artifact 列表已重新读取，请核对当前状态");
      return !reloadFailed;
    } catch (error) {
      if (request !== artifactListRequest.current || currentThreadId.current !== expectedThreadId) return false;
      setArtifactStateThreadId(expectedThreadId);
      setArtifactStateInvalidationKey(invalidationKey);
      setArtifactError(error instanceof Error ? error.message : "Artifact 列表加载失败");
      return false;
    }
  };

  const downloadArtifact = async () => {
    if (!currentArtifactDetail || artifactBusy) return;
    const expectedThreadId = thread.id;
    const downloadedArtifact = currentArtifactDetail;
    setArtifactBusy(true);
    setArtifactActionWarning(null);
    try {
      const download = await agentApi.downloadArtifact(expectedThreadId, downloadedArtifact.id);
      if (currentThreadId.current !== expectedThreadId) return;
      const objectUrl = URL.createObjectURL(download.blob);
      const anchor = document.createElement("a");
      anchor.href = objectUrl;
      anchor.download = download.filename ?? downloadedArtifact.id;
      anchor.style.display = "none";
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(objectUrl);
    } catch (error) {
      if (currentThreadId.current === expectedThreadId) {
        setArtifactActionWarning(error instanceof Error ? error.message : "Artifact 下载失败");
      }
    } finally {
      if (currentThreadId.current === expectedThreadId) setArtifactBusy(false);
    }
  };

  const deleteArtifact = async () => {
    if (!currentArtifactDetail || !activeArtifactMetadata || activeArtifactMetadata.has_children || artifactBusy) return;
    const expectedThreadId = thread.id;
    const deletedArtifact = currentArtifactDetail;
    setArtifactBusy(true);
    setArtifactActionWarning(null);
    try {
      await agentApi.deleteArtifact(expectedThreadId, deletedArtifact.id, thread.revision);
      if (currentThreadId.current !== expectedThreadId) return;
      await reloadArtifacts(deletedArtifact.parent_artifact_id, expectedThreadId);
    } catch (error) {
      if (currentThreadId.current !== expectedThreadId) return;
      const status = artifactErrorStatus(error);
      if (status === 409) {
        discardArtifactState(expectedThreadId);
        const reloaded = await reloadArtifacts(null, expectedThreadId);
        if (currentThreadId.current === expectedThreadId) {
          const detail = error instanceof Error ? error.message : "Artifact 状态冲突";
          setArtifactActionWarning(`${detail}${reloaded ? "，已重新加载权威状态" : "；权威状态重载未完成"}`);
        }
      } else if (status === 500) {
        discardArtifactState(expectedThreadId);
        const reloaded = await reloadArtifacts(null, expectedThreadId);
        if (currentThreadId.current === expectedThreadId) {
          setArtifactActionWarning(reloaded
            ? "删除结果需要核对，已重新加载权威状态"
            : "删除结果需要核对；权威状态重载未完成");
        }
      } else {
        setArtifactActionWarning(error instanceof Error ? error.message : "Artifact 删除失败");
      }
    } finally {
      if (currentThreadId.current === expectedThreadId) setArtifactBusy(false);
    }
  };

  const artifactMessages = [
    artifactStateIsCurrent ? artifactActionWarning : null,
    artifactStateIsCurrent ? artifactError : null,
    ...(artifactStateIsCurrent ? artifactWarnings : []).map((warning) => (
      warning.code === "ARTIFACT_MISSING_REF"
        ? `缺失引用：${warning.filename}`
        : `恢复警告：${warning.code} · ${warning.filename}`
    )),
    selectedArtifactId && currentArtifactsLoaded && !currentArtifacts.some((artifact) => artifact.id === selectedArtifactId)
      ? `缺失引用：${selectedArtifactId}`
      : null,
  ].filter((message): message is string => Boolean(message));
  const uniqueArtifactMessages = [...new Set(artifactMessages)];

  const selectTab = (tab: InspectorTab) => {
    setActiveTab(tab);
    // approval 也写入 store：移动端视口切换会在列/抽屉间重挂载 Inspector
    store.getState().setTab(tab);
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
  const sourceCount = currentRunDetail?.sources.length ?? null;

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
            {currentListError ? <p role="alert" className="text-xs text-destructive">{currentListError}</p> : null}
            {currentDetailError && currentDetailError !== currentListError
              ? <p role="alert" className="text-xs text-destructive">{currentDetailError}</p>
              : null}
            {currentRunDetail
              ? <RunInspector run={currentRunDetail} />
              : currentDetailError || currentListError
                ? null
                : selectedRunId
                  ? <EmptyState>正在加载运行详情…</EmptyState>
                  : listLoaded && !currentListError
                    ? <EmptyState>没有可查看的运行</EmptyState>
                    : null}
          </div>
        ) : null}
        {activeTab === "approval" ? (
          approvalConnected
            ? <ApprovalPanel disabled={approvalDisabled} actionable={actionableApproval} />
            : <EmptyState>当前没有可操作的审批</EmptyState>
        ) : null}
        {activeTab === "artifacts" ? (
          <div className="space-y-3">
            {uniqueArtifactMessages.length > 0 ? (
              <div role="alert" className="space-y-1 rounded-md border border-border bg-black/10 px-2.5 py-2 text-xs text-muted-foreground">
                {uniqueArtifactMessages.map((message) => <p key={message} className="wrap-break-word">{message}</p>)}
              </div>
            ) : null}
            {visibleArtifacts.length > 1 ? (
              <label className="block text-xs text-muted-foreground">
                当前运行与版本链
                <select
                  aria-label="Artifact"
                  className="mt-1 w-full rounded-md border border-border bg-background px-2 py-1.5 text-xs text-foreground"
                  value={ownedActiveArtifactId ?? ""}
                  onChange={(event) => selectArtifact(event.target.value)}
                >
                  {visibleArtifacts.map((artifact) => (
                    <option key={artifact.id} value={artifact.id}>
                      {artifact.run_id === selectedRunId ? "本次运行" : "版本上下文"} · {artifact.title}
                    </option>
                  ))}
                </select>
              </label>
            ) : null}
            {currentArtifactDetail && activeArtifactMetadata ? (
              <ArtifactViewer
                artifact={currentArtifactDetail}
                versions={versions}
                hasChildren={activeArtifactMetadata.has_children === true
                  || currentArtifacts.some((artifact) => artifact.parent_artifact_id === currentArtifactDetail.id)}
                busy={artifactBusy}
                onSelectVersion={selectArtifact}
                onDownload={downloadArtifact}
                onDelete={deleteArtifact}
              />
            ) : currentArtifactsLoaded && !artifactError && uniqueArtifactMessages.length === 0
              ? <EmptyState>当前运行没有可查看的 Artifact</EmptyState>
              : null}
          </div>
        ) : null}
        {activeTab === "sources" ? (
          currentRunDetail
            ? <SourceInspector sources={currentRunDetail.sources} truncated={currentRunDetail.sources_truncated} />
            : currentDetailError || currentListError
              ? <p role="alert" className="text-xs text-destructive">{currentDetailError ?? currentListError}</p>
              : selectedRunId
                ? <EmptyState>正在加载运行详情…</EmptyState>
                : listLoaded && !currentListError
                  ? <EmptyState>当前运行没有可查看的来源</EmptyState>
                  : null
        ) : null}
      </div>
    </div>
  );
}
