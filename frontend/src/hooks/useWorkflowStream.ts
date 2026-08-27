// 工作流 v2 流控制器：useStream 的薄封装。
// - values = 权威状态（每 superstep 一份），messages = 阶段正文流（token 增量 +
//   权威按 id 归并都由 SDK 处理），custom = 底稿进度（唯一保留的可丢弃事件）。
// - transient 派生：最后一条尚未被任何 StageResult.message_id 认领的 AI 消息，
//   归属 values.current_stage（变体是线性流水线，归属确定）。
import { useEffect, useMemo, useRef } from "react";
import { STREAM_CONTROLLER, useStream } from "@langchain/react";
import { acquireChannelEffect } from "@langchain/langgraph-sdk/stream";
import { resolveAgentApiUrl } from "@/lib/agent/thread-adapter";
import {
  messageText,
  parseDossierProgress,
  type DossierProgressEvent,
  type WorkflowState,
} from "@/lib/agent/workflow-types";

const isAi = (m: unknown): boolean => {
  const t = (m as { getType?: unknown })?.getType;
  if (typeof t === "function") { try { return (t as () => unknown)() === "ai"; } catch { /* fallthrough */ } }
  return (m as { type?: unknown })?.type === "ai";
};
const idOf = (m: unknown): unknown => (m as { id?: unknown })?.id;

export interface WorkflowSubmitOptions {
  /** 首次提交必须显式携带：controller 尚未重绑时会自铸线程（AskAiButton 同款坑）。 */
  threadId?: string | null;
  /** per-submit 错误回调：submit() 从不 reject（错误只进 rootStore.error 与此处，
   * 见 submit-coordinator.js L318-331），调用方靠它捕获 dispatch/lifecycle 失败。 */
  onError?: (error: unknown) => void;
}

export function useWorkflowStream(
  assistantId: string,
  threadId: string | null,
  onDossierProgress?: (event: DossierProgressEvent) => void,
) {
  const stream = useStream<Record<string, unknown>>({
    assistantId,
    apiUrl: resolveAgentApiUrl(),
    threadId,
  });
  const progressRef = useRef(onDossierProgress);
  progressRef.current = onDossierProgress;

  const controller = (stream as Record<symbol, unknown>)[STREAM_CONTROLLER] as
    | { registry: Parameters<typeof acquireChannelEffect>[0] }
    | undefined;
  useEffect(() => {
    if (!controller) return;
    return acquireChannelEffect(controller.registry, ["custom"], [], {
      onEvent: (event) => {
        const payload = (event as { params?: { data?: { payload?: unknown } } })?.params?.data?.payload;
        const progress = parseDossierProgress(payload);
        if (progress) progressRef.current?.(progress);
      },
    });
  }, [controller]);

  const state = (stream.values ?? {}) as WorkflowState;
  const transient = useMemo(() => {
    const claimed = new Set(
      Object.values(state.stages ?? {})
        .map((s) => s.message_id)
        .filter((v): v is string => typeof v === "string"),
    );
    const msgs = stream.messages ?? [];
    for (let i = msgs.length - 1; i >= 0; i -= 1) {
      const m = msgs[i];
      if (!isAi(m)) continue;
      if (claimed.has(idOf(m) as string)) return {};
      const sid = state.current_stage;
      return sid ? { [sid]: messageText(m) } : {};
    }
    return {};
  }, [stream.messages, state.stages, state.current_stage]);

  return {
    state,
    running: stream.isLoading,
    threadId: stream.threadId,
    error: stream.error,
    transient,
    submit: (input: Record<string, unknown>, options?: WorkflowSubmitOptions) =>
      stream.submit(input as never, options as never),
    stop: () => stream.stop(),
  };
}
