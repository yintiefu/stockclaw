// 页面工作流运行控制器：只持有 thread / run / 权威 state / 阶段临时文本与错误，
// 协议行为（序号校验、断线重连、checkpoint 对账、取消回写）全部委托给
// lib/agent/workflow-client 与 workflow-stream，不在此重复 SDK 调用。
//
// 关键语义：
// - start 每次 new thread（重新发起 = 新 thread，旧结果保留在历史里）；
// - retry 复用当前 thread，从 checkpoint 恢复而不重放已完成阶段；
// - stop 走 cancel(wait=true) + 取消回写；
// - restore 优先按本地 cursor 重连仍在跑的 run，否则一次 effective detail 读取；
// - remove 只删除用户明确选择的当前 thread。
import { useCallback, useRef, useState } from "react";
import {
  cancelWorkflowRun,
  createWorkflowThread,
  deleteWorkflowThread,
  getEffectiveWorkflowDetail,
  getWorkflowState,
  reconnectWorkflowRun,
  retryWorkflowRun,
  startWorkflowRun,
  type WorkflowClientEvent,
  type WorkflowThreadMetadata,
} from "@/lib/agent/workflow-client";
import type { WorkflowStreamState } from "@/lib/agent/workflow-stream";
import {
  effectiveWorkflowStatus,
  WORKFLOW_CONFIG_VERSIONS,
  type WorkflowState,
  type WorkflowStatus,
} from "@/lib/agent/workflow-types";
import { loadWorkflowStreamCursor } from "@/lib/storage";

export interface UseWorkflowRunOptions {
  assistantId: string;
  /** 页面专属事件回调（底稿进度、阶段标签等 UI 只在此处理）。 */
  onEvent?: (event: WorkflowClientEvent) => void;
}

export interface WorkflowStartParams {
  input: Record<string, unknown>;
  variant?: string | null;
  metadata?: WorkflowThreadMetadata;
}

export interface WorkflowRunOutcome {
  /** 流结束后的权威 checkpoint（失败时可能是刷新到的失败终态或 null）。 */
  state: WorkflowState | null;
  error: string | null;
}

export interface UseWorkflowRunResult {
  threadId: string | null;
  runId: string | null;
  /** 权威 checkpoint 状态；流式 delta 只进 transient。 */
  state: WorkflowState | null;
  /** 阶段临时文本（stage_id → 已拼接 delta），完成后由 checkpoint 内容取代。 */
  transient: Record<string, string>;
  running: boolean;
  status: WorkflowStatus;
  error: string | null;
  start: (params: WorkflowStartParams) => Promise<WorkflowRunOutcome>;
  stop: () => Promise<void>;
  retry: () => Promise<void>;
  restore: (threadId: string) => Promise<void>;
  remove: () => Promise<void>;
}

export function useWorkflowRun(options: UseWorkflowRunOptions): UseWorkflowRunResult {
  const { assistantId } = options;
  const eventRef = useRef(options.onEvent);
  eventRef.current = options.onEvent;

  const [threadId, setThreadId] = useState<string | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const [state, setState] = useState<WorkflowState | null>(null);
  const [transient, setTransient] = useState<Record<string, string>>({});
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [threadStatus, setThreadStatus] = useState<"idle" | "busy" | "interrupted" | "error">("idle");

  const applyStream = useCallback((stream: WorkflowStreamState) => {
    if (stream.runId) setRunId(stream.runId);
    setTransient({ ...stream.transient });
    if (stream.checkpoint) setState(stream.checkpoint);
  }, []);

  const refreshState = useCallback(async (id: string) => {
    const next = await getWorkflowState(id);
    setState(next);
    return next;
  }, []);

  const start = useCallback(async (params: WorkflowStartParams): Promise<WorkflowRunOutcome> => {
    setError(null);
    setTransient({});
    setState(null);
    setRunning(true);
    setThreadStatus("busy");
    const id = await createWorkflowThread(assistantId, {
      ...params.metadata,
      config_version: WORKFLOW_CONFIG_VERSIONS[assistantId] ?? params.metadata?.config_version,
    });
    setThreadId(id);
    try {
      const result = await startWorkflowRun({
        threadId: id,
        assistantId,
        input: {
          input: params.input,
          ...(params.variant ? { variant: params.variant } : {}),
        },
        // 绑定 run_id 要趁早：长任务一开始就可能被用户中止。
        onRunCreated: (created) => setRunId(created),
        onEvent: (event) => eventRef.current?.(event),
        onState: applyStream,
      });
      applyStream(result.stream);
      setRunning(false);
      setThreadStatus("idle");
      return { state: result.stream.checkpoint, error: null };
    } catch (e) {
      setRunning(false);
      setThreadStatus("idle");
      // 失败也要以 checkpoint 为准展示阶段终态，避免「生成中」永久挂着。
      let failedState: WorkflowState | null = null;
      try {
        failedState = await refreshState(id);
      } catch {
        /* 状态读取失败时保留本地 error 展示 */
      }
      const message = e instanceof Error ? e.message : String(e);
      setError(message);
      return { state: failedState, error: message };
    }
  }, [assistantId, applyStream, refreshState]);

  const stop = useCallback(async () => {
    if (!threadId || !runId) return;
    try {
      await cancelWorkflowRun(threadId, runId);
      await refreshState(threadId);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRunning(false);
      setThreadStatus("idle");
    }
  }, [threadId, runId, refreshState]);

  const retry = useCallback(async () => {
    if (!threadId) return;
    setError(null);
    setTransient({});
    setRunning(true);
    setThreadStatus("busy");
    try {
      const result = await retryWorkflowRun(threadId, assistantId, {
        onRunCreated: (created) => setRunId(created),
        onEvent: (event) => eventRef.current?.(event),
        onState: applyStream,
      });
      applyStream(result.stream);
      setRunning(false);
      setThreadStatus("idle");
    } catch (e) {
      setRunning(false);
      setThreadStatus("idle");
      try {
        await refreshState(threadId);
      } catch {
        /* 同 start：优先展示 checkpoint 终态 */
      }
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [threadId, assistantId, applyStream, refreshState]);

  const restore = useCallback(async (id: string) => {
    setError(null);
    setTransient({});
    const cursor = loadWorkflowStreamCursor(id);
    if (cursor?.runId) {
      try {
        const result = await reconnectWorkflowRun(id, {
          runId: cursor.runId,
          onEvent: (event) => eventRef.current?.(event),
          onState: applyStream,
        });
        setThreadId(id);
        applyStream(result.stream);
        const terminal = result.stream.checkpoint?.workflow_status;
        setRunning(result.stream.checkpoint != null
          && (terminal === "running" || terminal === "pending"));
        setThreadStatus("idle");
        return;
      } catch {
        // 流缓冲不存在 / Server 已重启：回落到一次性权威读取。
      }
    }
    const detail = await getEffectiveWorkflowDetail(id);
    setThreadId(id);
    setState(detail.state);
    setRunId(cursor?.runId ?? null);
    setThreadStatus(detail.threadStatus);
    setRunning(detail.status === "running");
  }, [applyStream]);

  const remove = useCallback(async () => {
    if (!threadId) return;
    await deleteWorkflowThread(threadId);
    setThreadId(null);
    setRunId(null);
    setState(null);
    setTransient({});
    setError(null);
    setRunning(false);
  }, [threadId]);

  const status: WorkflowStatus = running
    ? "running"
    : effectiveWorkflowStatus(threadStatus, state?.workflow_status ?? "pending");

  return {
    threadId, runId, state, transient, running, status, error,
    start, stop, retry, restore, remove,
  };
}
