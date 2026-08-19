import { useCallback, useEffect, useRef, useState } from "react";

import { AgentThread } from "@/components/agent/AgentThread";
import { AgentThreadList } from "@/components/agent/AgentThreadList";
import { AgentInspector } from "@/components/agent/AgentInspector";
import { AgentWorkspace } from "@/components/agent/AgentWorkspace";
import { ApprovalPanel } from "@/components/agent/ApprovalPanel";
import { CapabilityBar } from "@/components/agent/CapabilityBar";
import { CapabilityManagerDialog } from "@/components/agent/CapabilityManagerDialog";
import {
  loadAgentModelConfig,
  saveAgentModelConfig,
  type AgentModelConfig,
} from "@/lib/agent/model-config";
import { AgentHistoryController } from "@/lib/agent/history";
import { AgentRuntimeProvider, type AgentHttpAgent } from "@/lib/agent/runtime";
import { createAgentWorkspaceStore } from "@/lib/agent/workspace";
import type { AgentStreamEvent, AgentThreadSummary, AgentThread as AgentThreadDoc, SkillSummary } from "@/lib/agent/types";
import { agentApi } from "@/lib/agent/api";
import { ApiError } from "@/lib/api";

/** Agent 工作台：服务端权威线程历史 + 三栏操作壳。 */
export function Agent() {
  const [saved, setSaved] = useState<AgentModelConfig | null>(() => loadAgentModelConfig());
  const [draft, setDraft] = useState<AgentModelConfig>(
    () => loadAgentModelConfig() ?? { provider: "", baseURL: "", model: "", apiKey: "" },
  );
  const [conflict, setConflict] = useState<string | null>(null);
  // 409 权威重载后递增，强制 runtime 重建并从服务端重新水合消息
  const [sessionEpoch, setSessionEpoch] = useState(0);
  // 流结束→权威 reload 完成期间禁用输入，防止携带旧历史发送
  const [converging, setConverging] = useState(false);
  const [runtimeError, setRuntimeError] = useState<string | null>(null);
  const [unavailable, setUnavailable] = useState<string | null>(null);
  const [statusNote, setStatusNote] = useState<string | null>(null);
  const [selectedArtifactId, setSelectedArtifactId] = useState<string | null>(null);
  const [artifactActivationKey, setArtifactActivationKey] = useState(0);
  const [inspectorInvalidation, setInspectorInvalidation] = useState(0);
  const [desktopViewport, setDesktopViewport] = useState(() =>
    typeof window !== "undefined" && window.matchMedia("(min-width: 1280px)").matches);

  const controllerRef = useRef<AgentHistoryController | null>(null);
  if (controllerRef.current === null) {
    controllerRef.current = new AgentHistoryController();
  }
  const controller = controllerRef.current;
  const workspaceRef = useRef<ReturnType<typeof createAgentWorkspaceStore> | null>(null);
  if (workspaceRef.current === null) {
    workspaceRef.current = createAgentWorkspaceStore();
  }
  const workspace = workspaceRef.current;
  const [, forceRefresh] = useState(0);
  const bump = useCallback(() => forceRefresh((n) => n + 1), []);

  useEffect(() => {
    const media = window.matchMedia("(min-width: 1280px)");
    const update = (event: MediaQueryListEvent) => setDesktopViewport(event.matches);
    setDesktopViewport(media.matches);
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);

  const [threads, setThreads] = useState<AgentThreadSummary[]>([]);
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const [managerOpen, setManagerOpen] = useState(false);
  const [warnings, setWarnings] = useState<Awaited<ReturnType<typeof agentApi.listThreads>>["warnings"]>([]);
  const [activeThread, setActiveThread] = useState<AgentThreadDoc | null>(null);
  const [loadingThread, setLoadingThread] = useState(true);

  const syncFromController = useCallback(async () => {
    setThreads(controller.getThreads());
    setWarnings(controller.getWarnings());
    setActiveThread(controller.getActiveThread());
  }, [controller]);

  useEffect(() => {
    let disposed = false;
    (async () => {
      try {
        await controller.selectInitialThread();
      } catch (error) {
        setStatusNote(error instanceof Error ? error.message : "线程初始化失败");
      }
      if (!disposed) {
        await syncFromController();
        setLoadingThread(false);
      }
    })();
    return () => {
      disposed = true;
    };
  }, [controller, syncFromController]);

  const save = () => {
    saveAgentModelConfig(draft);
    setSaved(loadAgentModelConfig());
  };

  // 结构化 409：展示中文 detail + 恰好一次权威重载（不自动重放被拒变更）
  const onConflict = useCallback(async (value: { code?: string; detail?: string }) => {
    setConflict(value.detail ?? value.code ?? "线程正忙，请稍候");
    const threadId = controller.getActiveThreadId();
    if (threadId) {
      try {
        await controller.reload(threadId);
      } catch {
        // 重载失败保持提示可见
      }
      await syncFromController();
      setSessionEpoch((epoch) => epoch + 1);
    }
  }, [controller, syncFromController]);
  const onError = useCallback((error: Error) => {
    setRuntimeError(error.message || "Agent 运行出错");
  }, []);
  // 流开始前的 503（MCP_UNAVAILABLE）：保留消息、不重试、不 409 reload
  const onUnavailable = useCallback((detail: string) => {
    setUnavailable(detail);
  }, []);

  const runtimeReady = Boolean(
    saved?.provider && saved.baseURL && saved.model && saved.apiKey,
  );

  const handleSelect = async (threadId: string) => {
    try {
      await controller.switchTo(threadId);
      setSelectedArtifactId(null);
      await syncFromController();
    } catch (error) {
      setConflict(error instanceof Error ? error.message : "切换线程失败");
    }
  };

  const handleCreate = async () => {
    try {
      await agentApi.createThread();
      await controller.refreshList();
      const newest = controller.getThreads()[0];
      if (newest) {
        await controller.switchTo(newest.id);
      }
      setSelectedArtifactId(null);
      await syncFromController();
    } catch (error) {
      setConflict(error instanceof Error ? error.message : "新建会话失败");
    }
  };

  const handleRename = async (threadId: string, title: string) => {
    try {
      await controller.rename(threadId, title);
      await syncFromController();
    } catch (error) {
      if (error instanceof ApiError || error instanceof Error) {
        setConflict(error.message);
      }
      const activeId = controller.getActiveThreadId();
      if (activeId) {
        await controller.reload(activeId).catch(() => undefined);
        await syncFromController();
      }
    }
  };

  const loadSkills = useCallback(async () => {
    try {
      const listed = await agentApi.listSkills();
      setSkills(listed.skills);
    } catch {
      setSkills([]); // 只读降级
    }
  }, []);

  useEffect(() => { void loadSkills(); }, [loadSkills]);

  const applySkills = useCallback(async (updated: AgentThreadDoc) => {
    try {
      await controller.reload(updated.id);
      await syncFromController();
      setManagerOpen(false);
    } catch {
      setManagerOpen(false);
    }
  }, [controller, syncFromController]);

  const handleSkillConflict = useCallback(async () => {
    // 409：丢弃草稿并刷新一次（Skill 列表 + 权威线程）
    await loadSkills();
    const threadId = controller.getActiveThreadId();
    if (threadId) {
      await controller.reload(threadId).catch(() => undefined);
      await syncFromController();
    }
  }, [controller, syncFromController, loadSkills]);

  const handleDelete = async (threadId: string) => {
    try {
      await controller.remove(threadId, controller.getRevision(threadId));
      const remaining = controller.getThreads();
      if (remaining.length > 0) {
        await controller.switchTo(remaining[0].id);
      } else {
        await controller.selectInitialThread();
      }
      setSelectedArtifactId(null);
      await syncFromController();
    } catch (error) {
      setConflict(error instanceof Error ? error.message : "删除会话失败");
      const selectedId = controller.getActiveThreadId();
      await controller.refreshList().catch(() => undefined);
      if (selectedId && controller.getThreads().some((thread) => thread.id === selectedId)) {
        await controller.reload(selectedId).catch(() => undefined);
      } else {
        const remaining = controller.getThreads();
        if (remaining.length > 0) {
          await controller.switchTo(remaining[0].id);
        } else {
          await controller.selectInitialThread();
        }
        setSelectedArtifactId(null);
      }
      await syncFromController();
    }
  };

  const runtimeRefs = useRef<{ agent: AgentHttpAgent; startRun: (parentId: string | null) => void } | null>(null);
  const handleRetry = useCallback((runId: string) => {
    runtimeRefs.current?.agent.armRetry(runId);
    const lastUser = [...(activeThread?.messages ?? [])].reverse().find((m) => m.role === "user");
    runtimeRefs.current?.startRun(lastUser?.id ?? null);
  }, [activeThread]);

  // revision 事件驱动轻量刷新（线程列表排序/状态）
  useEffect(() => controller.subscribe(bump), [controller, bump]);

  // 终局 / Stop / 断连后收敛到服务端权威状态（last_run、Retry 可用性、最新 revision）。
  // 停止时后端还在落盘 partial，先等一拍再重载；若仍在运行则再补一次。
  const onInvalidate = useCallback((threadId: string, _runId: string) => {
    workspace.getState().markRunStale(threadId, _runId);
    setInspectorInvalidation((value) => value + 1);
    void controller.reload(threadId).then(syncFromController).catch(() => undefined);
  }, [controller, syncFromController, workspace]);

  const onEvent = useCallback((event: AgentStreamEvent) => {
    workspace.getState().applyEvent(event);
    if (event.name !== "thread.revision.updated") {
      workspace.getState().markRunStale(event.value.threadId, event.value.runId);
      setInspectorInvalidation((value) => value + 1);
    }
  }, [workspace]);

  const onStreamEnd = useCallback((streamThreadId?: string, _runId?: string) => {
    const threadId = streamThreadId ?? controller.getActiveThreadId();
    if (!threadId) return;
    if (_runId) {
      workspace.getState().markRunStale(threadId, _runId);
      setInspectorInvalidation((value) => value + 1);
    }
    const isCurrentThread = () => controller.getActiveThreadId() === threadId;
    if (isCurrentThread()) setConverging(true); // Stop/终态后先禁用输入，等待取消持久化 + 权威 reload
    const reloadOnce = async (bumpEpoch: boolean) => {
      try {
        const thread = await controller.reload(threadId);
        await syncFromController();
        if (bumpEpoch && isCurrentThread()) {
          // 用服务端权威历史替换 runtime 本地消息
          setSessionEpoch((epoch) => epoch + 1);
        }
        if (thread.last_run?.status === "running" || thread.last_run?.status === "awaiting_approval") {
          setTimeout(() => { void controller.reload(threadId).then(syncFromController).catch(() => undefined); }, 4000);
        }
      } catch {
        // 重载失败保持现状
      } finally {
        if (isCurrentThread()) setConverging(false);
      }
    };
    // 后端 Stop 后仍在落盘 partial：先等一拍再重载
    setTimeout(() => { void reloadOnce(true); }, 1200);
  }, [controller, syncFromController, workspace]);

  const activeBusy = activeThread?.last_run?.status === "running"
    || activeThread?.last_run?.status === "awaiting_approval";

  const openArtifact = useCallback((artifactId: string) => {
    setSelectedArtifactId(artifactId);
    setArtifactActivationKey((value) => value + 1);
    workspace.getState().setTab("artifacts");
    workspace.getState().openDrawer("inspector");
  }, [workspace]);

  const alerts = conflict || runtimeError || unavailable ? (
    <div className="space-y-1 text-xs text-muted-foreground">
      {conflict ? <p className="truncate">{conflict}</p> : null}
      {runtimeError ? <p className="truncate">{runtimeError}</p> : null}
      {unavailable ? (
        <div className="flex min-w-0 items-center justify-between gap-2">
          <span className="truncate">MCP 服务不可用：{unavailable}（本次提问未发出）</span>
          <button type="button" onClick={() => setManagerOpen(true)} className="shrink-0 rounded-md px-2 py-1 text-primary hover:bg-primary/10">
            管理 MCP
          </button>
        </div>
      ) : null}
    </div>
  ) : null;

  const threadList = (
    <AgentThreadList
      threads={threads}
      activeThreadId={activeThread?.id ?? null}
      warnings={warnings}
      canDeleteActive={!activeBusy}
      onSelect={handleSelect}
      onCreate={handleCreate}
      onRename={handleRename}
      onDelete={handleDelete}
    />
  );

  const chat = runtimeReady && !loadingThread && activeThread ? (
    <div data-testid="agent-runtime-content" className="flex h-full min-h-0 flex-col">
      {!desktopViewport && activeThread.last_run?.status === "awaiting_approval" && activeThread.resume_available ? (
        <div className="max-h-[40%] shrink-0 overflow-y-auto py-2"><ApprovalPanel disabled={converging} /></div>
      ) : null}
      <div data-testid="agent-thread-region" className="min-h-0 flex-1 overflow-hidden">
        <AgentThread
          activeThread={activeThread}
          composerDisabled={converging}
          pendingApproval={activeThread.last_run?.status === "awaiting_approval"
            && activeThread.resume_available === true}
          onRetry={handleRetry}
          onOpenArtifact={openArtifact}
          statusNote={statusNote ?? (activeThread.last_run?.status === "interrupted"
            ? "后端重启导致上次运行中断，可重试本轮"
            : null)}
        />
      </div>
    </div>
  ) : (
    <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
      {runtimeReady ? "正在加载会话历史…" : "开始前请先完成模型配置"}
    </div>
  );

  const workspaceContent = (
    <AgentWorkspace
        threadTitle={activeThread?.title ?? "新会话"}
        modelConfig={draft}
        modelLabel={(saved ?? draft).model}
        configured={runtimeReady}
        capabilityLabel={activeThread?.selected_skills.length
          ? `${activeThread.selected_skills.length} 个 Skill`
          : "未选择 Skill"}
        onModelConfigChange={setDraft}
        onSaveModel={save}
        onOpenThreads={() => workspace.getState().openDrawer("threads")}
        onOpenInspector={() => workspace.getState().openDrawer("inspector")}
        onOpenSettings={() => workspace.getState().openDrawer("settings")}
        threads={threadList}
        chat={chat}
        alerts={alerts}
        selectedArtifactId={selectedArtifactId}
        inspector={!loadingThread && activeThread ? (
          <div className="flex min-h-full flex-col">
            <div className="border-b border-border/70 px-3 py-1">
              <CapabilityBar
                thread={activeThread}
                onOpenManager={() => setManagerOpen(true)}
                disabled={activeBusy || converging}
              />
            </div>
            <AgentInspector
              thread={activeThread}
              store={workspace}
              invalidationKey={inspectorInvalidation}
              approvalDisabled={converging}
              approvalConnected={runtimeReady && desktopViewport}
              selectedArtifactId={selectedArtifactId}
              artifactActivationKey={artifactActivationKey}
            />
          </div>
        ) : null}
    />
  );

  return (
    <div className="h-full min-h-0">
      {runtimeReady && !loadingThread && activeThread ? (
        <AgentRuntimeProvider
          key={`${activeThread.id}-${sessionEpoch}`}
          config={saved ?? draft}
          onConflict={onConflict}
          onError={onError}
          onUnavailable={onUnavailable}
          onInvalidate={onInvalidate}
          onEvent={onEvent}
          controller={controller}
          onRuntime={(refs) => { runtimeRefs.current = refs; }}
          onStreamEnd={onStreamEnd}
        >
          {workspaceContent}
        </AgentRuntimeProvider>
      ) : workspaceContent}
      {!loadingThread && activeThread && (
        <CapabilityManagerDialog
          open={managerOpen}
          thread={activeThread}
          skills={skills}
          onApplied={applySkills}
          onConflict={handleSkillConflict}
          onClose={() => { setManagerOpen(false); setUnavailable(null); }}
          disabled={activeBusy || converging}
        />
      )}
    </div>
  );
}
