import { useCallback, useEffect, useRef, useState } from "react";

import { AgentThread } from "@/components/agent/AgentThread";
import { AgentThreadList } from "@/components/agent/AgentThreadList";
import {
  loadAgentModelConfig,
  saveAgentModelConfig,
  type AgentModelConfig,
} from "@/lib/agent/model-config";
import { AgentHistoryController } from "@/lib/agent/history";
import { AgentRuntimeProvider, type AgentHttpAgent } from "@/lib/agent/runtime";
import type { AgentThreadSummary, AgentThread as AgentThreadDoc } from "@/lib/agent/types";
import { agentApi } from "@/lib/agent/api";
import { ApiError } from "@/lib/api";

const INPUT_CLASS =
  "w-full rounded-lg border border-border bg-black/20 px-3 py-2 text-sm outline-none focus:border-primary/50";

/** Agent 工作台（1B）：服务端权威线程历史 + 最小线程管理控件。 */
export function Agent() {
  const [saved, setSaved] = useState<AgentModelConfig | null>(() => loadAgentModelConfig());
  const [draft, setDraft] = useState<AgentModelConfig>(
    () => loadAgentModelConfig() ?? { provider: "", baseURL: "", model: "", apiKey: "" },
  );
  const [conflict, setConflict] = useState<string | null>(null);
  const [runtimeError, setRuntimeError] = useState<string | null>(null);
  const [statusNote, setStatusNote] = useState<string | null>(null);

  const controllerRef = useRef<AgentHistoryController | null>(null);
  if (controllerRef.current === null) {
    controllerRef.current = new AgentHistoryController();
  }
  const controller = controllerRef.current;
  const [, forceRefresh] = useState(0);
  const bump = useCallback(() => forceRefresh((n) => n + 1), []);

  const [threads, setThreads] = useState<AgentThreadSummary[]>([]);
  const [warnings, setWarnings] = useState<Awaited<ReturnType<typeof agentApi.listThreads>>["warnings"]>([]);
  const [activeThread, setActiveThread] = useState<AgentThreadDoc | null>(null);
  const [loadingThread, setLoadingThread] = useState(true);

  const syncFromController = useCallback(async () => {
    setThreads(controller.getThreads());
    setWarnings(controller.getWarnings());
    const active = controller.getActiveThread();
    if (active) {
      setActiveThread(active);
    }
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
    }
  }, [controller, syncFromController]);
  const onError = useCallback((error: Error) => {
    setRuntimeError(error.message || "Agent 运行出错");
  }, []);

  const complete = Boolean(
    draft.provider && draft.baseURL && draft.model && draft.apiKey,
  ) || Boolean(saved?.provider && saved?.baseURL && saved?.model && saved?.apiKey);

  const handleSelect = async (threadId: string) => {
    try {
      await controller.switchTo(threadId);
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

  const handleDelete = async (threadId: string) => {
    try {
      await controller.remove(threadId);
      const remaining = controller.getThreads();
      if (remaining.length > 0) {
        await controller.switchTo(remaining[0].id);
      } else {
        await controller.selectInitialThread();
      }
      await syncFromController();
    } catch (error) {
      setConflict(error instanceof Error ? error.message : "删除会话失败");
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

  const activeBusy = activeThread?.last_run?.status === "running"
    || activeThread?.last_run?.status === "awaiting_approval";

  return (
    <div className="mx-auto max-w-6xl space-y-4 p-4">
      <header>
        <h1 className="text-lg font-semibold">Agent 工作台</h1>
        <p className="mt-1 text-xs text-muted-foreground">
          客观数据 + 分析框架。不给出买卖建议。模型密钥只保存在本地浏览器，仅经请求头传输。
        </p>
      </header>

      <section className="glass-card rounded-xl p-4">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <div>
            <label htmlFor="agent-provider" className="mb-1.5 block text-xs font-medium text-muted-foreground">Provider</label>
            <input id="agent-provider" value={draft.provider}
              onChange={(e) => setDraft({ ...draft, provider: e.target.value })}
              placeholder="deepseek / openai / …"
              className={INPUT_CLASS} />
          </div>
          <div>
            <label htmlFor="agent-base-url" className="mb-1.5 block text-xs font-medium text-muted-foreground">Base URL</label>
            <input id="agent-base-url" value={draft.baseURL}
              onChange={(e) => setDraft({ ...draft, baseURL: e.target.value })}
              placeholder="https://api.deepseek.com/v1"
              className={INPUT_CLASS} />
          </div>
          <div>
            <label htmlFor="agent-model" className="mb-1.5 block text-xs font-medium text-muted-foreground">模型</label>
            <input id="agent-model" value={draft.model}
              onChange={(e) => setDraft({ ...draft, model: e.target.value })}
              placeholder="模型名称"
              className={INPUT_CLASS} />
          </div>
          <div>
            <label htmlFor="agent-api-key" className="mb-1.5 block text-xs font-medium text-muted-foreground">API Key</label>
            <input id="agent-api-key" type="password" value={draft.apiKey}
              onChange={(e) => setDraft({ ...draft, apiKey: e.target.value })}
              placeholder="sk-…"
              className={INPUT_CLASS} />
          </div>
        </div>
        <div className="mt-3 flex items-center gap-3">
          <button onClick={save}
            className="inline-flex items-center gap-1.5 rounded-lg bg-primary/15 px-4 py-2 text-sm font-medium text-primary shadow-glow hover:bg-primary/25">
            保存（存本地）
          </button>
          {!complete && (
            <span className="text-xs text-muted-foreground">填写完整配置后即可开始会话</span>
          )}
        </div>
      </section>

      {conflict && (
        <div className="rounded-lg border border-border bg-black/20 px-3 py-2 text-sm text-muted-foreground">
          {conflict}
        </div>
      )}
      {runtimeError && (
        <div className="rounded-lg border border-border bg-black/20 px-3 py-2 text-sm text-muted-foreground">
          {runtimeError}
        </div>
      )}

      <section className="glass-card space-y-3 rounded-xl p-4">
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
        {complete && !loadingThread && activeThread ? (
          <AgentRuntimeProvider
            key={activeThread.id}
            config={saved ?? draft}
            onConflict={onConflict}
            onError={onError}
            controller={controller}
            onRuntime={(refs) => {
              runtimeRefs.current = refs;
            }}
          >
            <AgentThread
              activeThread={activeThread}
              onRetry={handleRetry}
              statusNote={statusNote ?? (activeThread.last_run?.status === "interrupted"
                ? "后端重启导致上次运行中断，可重试本轮"
                : null)}
            />
          </AgentRuntimeProvider>
        ) : (
          <div className="flex min-h-[560px] items-center justify-center text-sm text-muted-foreground">
            {complete ? "正在加载会话历史…" : "开始前请先完成模型配置"}
          </div>
        )}
      </section>
    </div>
  );
}
