// 页面工作流运行控制器（v2 流版）：
// - start：建线程（带 metadata）→ submit 显式携带 {threadId} + onError（controller
//   未重绑时 SDK 会自铸线程，AskAiButton 已踩过该坑）；submit 从不 reject，失败
//   判定靠 submit resolve 后读目标线程的权威 checkpoint——**只接受明确终态，
//   否则 fail closed**（绝不返回 {state:null, error:null} 让 Intel 把空结果当成功）；
// - retry：提交 {resume: true} 并**始终**显式携带 {threadId}（restore 刚换绑、
//   controller 异步 hydrate 未完成时，SDK 会在 submit 内自行 await hydrate——
//   submit-coordinator.js L204-205）；threadId 取同步 ref，不取 React state 闭包；
//   与 start 同源做错误派生（transport 正常完成 ≠ 恢复成功）；
// - stop：stream.stop() 只发出 fire-and-forget 的 runs.cancel（吞错、立即复位本地
//   loading，controller.js 证实），随后轮询线程状态直到脱离 busy（≤10s）并以最终
//   线程状态收敛 restoredStatus（查询失败/超时报错，绝不静默当成功）；
// - restore：setThreadId 换绑，v2 hydrate 装载历史；restoredStatus 只在此处从
//   服务端读取一次，且写入前校验线程未再切换（过期详情丢弃）；
// - remove：删除线程。
import { useCallback, useRef, useState } from "react";
import { useWorkflowStream } from "@/hooks/useWorkflowStream";
import {
  createWorkflowThread,
  deleteWorkflowThread,
  getEffectiveWorkflowDetail,
  getWorkflowState,
} from "@/lib/agent/workflow-client";
import {
  effectiveWorkflowStatus,
  type DossierProgressEvent,
  type WorkflowState,
  type WorkflowStatus,
} from "@/lib/agent/workflow-types";
import type { WorkflowThreadMetadata } from "@/lib/agent/workflow-client";

type ThreadStatus = "idle" | "busy" | "interrupted" | "error";

export interface UseWorkflowRunOptions {
  assistantId: string;
  onDossierProgress?: (event: DossierProgressEvent) => void;
}

export interface WorkflowStartParams {
  input: Record<string, unknown>;
  variant?: string | null;
  metadata?: WorkflowThreadMetadata;
}

/** 与旧版同形：页面（Intel/DailyReview/Notes）读取 outcome.state / outcome.error。 */
export interface WorkflowRunOutcome {
  state: WorkflowState | null;
  error: string | null;
}

export interface UseWorkflowRunResult {
  threadId: string | null;
  state: WorkflowState | null;
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

const errorText = (error: unknown): string | null =>
  error == null ? null : error instanceof Error ? error.message : String(error);

// values 里可能出现的终态（interrupted/cancelled 是展示层派生值，不会写进 values）。
const TERMINAL_WORKFLOW_STATUSES = new Set(["completed", "partial", "failed", "cancelled"]);
const isTerminal = (state: WorkflowState | null | undefined): boolean =>
  state != null && TERMINAL_WORKFLOW_STATUSES.has(state.workflow_status);

/** submit 在 run 终局后 resolve；读目标线程的权威 checkpoint。
 * 一次重试容忍 dev runtime 的落盘延迟。只认明确终态，不认中间态。 */
async function readTerminalState(threadId: string): Promise<WorkflowState | null> {
  for (let attempt = 0; attempt < 2; attempt++) {
    const state = await getWorkflowState(threadId).catch(() => null);
    if (isTerminal(state)) return state;
    if (attempt === 0) await new Promise((resolve) => setTimeout(resolve, 200));
  }
  return null;
}

function deriveRunError(state: WorkflowState | null, dispatchError: string | null): string | null {
  if (dispatchError) return dispatchError;
  if (state?.workflow_status === "failed") {
    return state.errors?.map((e) => e.message).filter(Boolean).join("；") || "工作流执行失败";
  }
  return null;
}

export function useWorkflowRun(options: UseWorkflowRunOptions): UseWorkflowRunResult {
  const { assistantId } = options;
  const [threadId, setThreadId] = useState<string | null>(null);
  // 同步镜像：restore 后立即 retry 时 React state 闭包还是旧值，submit 必须拿
  // 最新 threadId。start/restore/remove 同步更新 ref。
  const threadIdRef = useRef<string | null>(null);
  // 仅由 restore 写入（服务端视角的线程状态）；start/retry 一律清空——运行期由
  // running 表达，终态由 values.workflow_status 表达，杜绝「busy 永久挂起」。
  const [restoredStatus, setRestoredStatus] = useState<ThreadStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  const stream = useWorkflowStream(assistantId, threadId, options.onDossierProgress);
  const running = stream.running;

  const bindThread = useCallback((id: string | null) => {
    threadIdRef.current = id;
    setThreadId(id);
  }, []);

  const start = useCallback(async (params: WorkflowStartParams): Promise<WorkflowRunOutcome> => {
    setError(null);
    setRestoredStatus(null);
    try {
      const id = await createWorkflowThread(assistantId, params.metadata ?? {});
      bindThread(id);
      let dispatchError: string | null = null;
      await stream.submit(
        {
          input: params.input,
          ...(params.variant ? { variant: params.variant } : {}),
        },
        {
          threadId: id,
          onError: (e) => { dispatchError = errorText(e) ?? "工作流启动失败"; },
        },
      );
      // submit() 失败也不 reject（错误只进 onError/stream.error）；且节点内捕获的
      // 模型异常以 failed checkpoint 正常终局（run 本身 completed、stream.error 为空）
      // ——失败判定唯一可靠来源是目标线程的权威 checkpoint。
      // 注意不做任何「换线程前的旧 values」兜底（跨线程污染）；读不到终态即 fail closed。
      const finalState = await readTerminalState(id);
      let runError = deriveRunError(finalState, dispatchError);
      if (!runError && !isTerminal(finalState)) {
        runError = "工作流未能完成：未读取到终态，请在历史记录中确认";
      }
      if (runError) setError(runError);
      return { state: finalState, error: runError };
    } catch (e) {
      // 建线程/网络层失败：同样不抛，落回 {state:null, error}。
      const message = errorText(e) ?? "工作流启动失败";
      setError(message);
      return { state: null, error: message };
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [assistantId, stream.submit, bindThread]);

  const stop = useCallback(async () => {
    // 提前捕获：await stream.stop() 期间用户可能 restore 了别的线程，
    // 事后取 ref 会轮询/收敛到错误的线程。
    const id = threadIdRef.current;
    try {
      await stream.stop();  // 发出 runs.cancel（fire-and-forget、吞错）并复位本地 loading
      if (id == null) return;
      // 服务端取消是异步的：轮询直到脱离 busy（500ms×20 ≤10s）。查询失败不当作
      // 完成——只有拿到非 busy 详情才收敛；持续失败与超时都必须报错。
      let lastStatus: ThreadStatus | null = null;
      for (let attempt = 0; attempt < 20; attempt++) {
        const detail = await getEffectiveWorkflowDetail(id).catch(() => null);
        if (detail) {
          lastStatus = detail.threadStatus;
          if (detail.threadStatus !== "busy") break;
        }
        await new Promise((resolve) => setTimeout(resolve, 500));
      }
      if (threadIdRef.current !== id) return;  // 期间换线程：不写状态
      if (lastStatus == null) {
        setError("已请求取消，但无法确认服务端状态：请稍后在历史记录中查看");
      } else if (lastStatus === "busy") {
        setError("取消超时：服务端仍在收尾，请稍后刷新历史");
      } else {
        // 用最终线程状态收敛 restoredStatus——restore 写入的 busy 若不更新，
        // 残留 running 的 checkpoint 会让 status 永久钉在 running（中止按钮不消失）。
        setRestoredStatus(lastStatus);
        setError(null);
      }
    } catch (e) {
      setError(errorText(e) ?? "取消失败");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stream.stop]);

  const retry = useCallback(async () => {
    const id = threadIdRef.current;
    if (!id) return;
    setError(null);
    setRestoredStatus(null);
    let dispatchError: string | null = null;
    try {
      // 输入驱动 resume：版本门控与目标阶段路由全部在后端 auto_resume。
      // 始终显式携带 threadId——restore 后 controller 的异步 hydrate 可能未完成，
      // 显式传参时 SDK 会在 submit 内自行 await hydrate。
      await stream.submit({ resume: true }, {
        threadId: id,
        onError: (e) => { dispatchError = errorText(e) ?? "恢复失败"; },
      });
    } catch (e) {
      setError(errorText(e) ?? "恢复失败");
      return;
    }
    // 与 start 同源：submit 正常 resolve ≠ 恢复成功，失败终态只在 checkpoint 里。
    const state = await readTerminalState(id);
    const runError = deriveRunError(state, dispatchError)
      ?? (isTerminal(state) ? null : "恢复未完成：请稍后重试");
    if (runError) setError(runError);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stream.submit]);

  const restore = useCallback(async (id: string) => {
    setError(null);
    bindThread(id);  // v2 hydrate 装载历史
    try {
      const detail = await getEffectiveWorkflowDetail(id);
      if (threadIdRef.current !== id) return;  // 已切到别的线程：丢弃过期详情
      setRestoredStatus(detail.threadStatus === "busy" ? "busy"
        : detail.threadStatus === "interrupted" ? "interrupted"
        : detail.threadStatus === "error" ? "error" : "idle");
    } catch {
      if (threadIdRef.current !== id) return;
      setRestoredStatus("idle");
    }
  }, [bindThread]);

  const remove = useCallback(async () => {
    const id = threadIdRef.current;
    if (!id) return;
    await deleteWorkflowThread(id);
    bindThread(null);
    setRestoredStatus(null);
    setError(null);
  }, [bindThread]);

  const boundThreadId = stream.threadId ?? threadId;
  const workflowStatus: WorkflowStatus = stream.state.workflow_status ?? "pending";
  const status: WorkflowStatus = running
    ? "running"
    // 首屏（无线程）必须是 pending——不进 effectiveWorkflowStatus（idle+pending 会被
    // 派生成 interrupted）。
    : boundThreadId == null
      ? "pending"
      : effectiveWorkflowStatus(restoredStatus ?? "idle", workflowStatus);

  return {
    threadId: boundThreadId,
    state: Object.keys(stream.state).length ? stream.state : null,
    transient: stream.transient,
    running,
    status,
    error: error ?? errorText(stream.error),
    start, stop, retry, restore, remove,
  };
}
