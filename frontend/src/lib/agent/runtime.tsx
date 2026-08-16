import { useCallback, useEffect, useMemo, type ReactNode } from "react";
import { AssistantRuntimeProvider } from "@assistant-ui/react";
import { useAgUiRuntime } from "@assistant-ui/react-ag-ui";
import { HttpAgent, type RunAgentInput } from "@ag-ui/client";

import { authHeaders } from "@/lib/api";
import type { AgentHistoryController } from "./history";
import type { AgentModelConfig } from "./model-config";
import type { AgentConflict } from "./types";

type Conflict = AgentConflict;

export type AgentTransportOptions = {
  /** 当前服务端线程 ID（发送时读取；覆盖 runtime 内部的临时线程 ID）。 */
  getThreadId?: () => string | null;
  /** 当前权威 revision（发送时读取）。 */
  getRevision?: () => number;
  /** 流内 thread.revision.updated 事件（提交后到达）。 */
  onRevision?: (threadId: string, revision: number) => void;
  /** 流结束（终局 / 停止 / 断连）后触发一次，用于收敛到服务端权威状态。 */
  onStreamEnd?: () => void;
  /** 流开始前的 503（MCP_UNAVAILABLE）：不重试、不 reload。 */
  onUnavailable?: (detail: string) => void;
};

function parseSsePayload(line: string): Record<string, unknown> | null {
  if (!line.startsWith("data: ")) return null;
  try {
    return JSON.parse(line.slice(6)) as Record<string, unknown>;
  } catch {
    return null;
  }
}

async function scanStream(
  stream: ReadableStream<Uint8Array>,
  handlers: {
    onRunError: (message: string) => void;
    onRevision?: (threadId: string, revision: number) => void;
    onStreamEnd?: () => void;
  },
) {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";
      for (const line of lines) {
        const event = parseSsePayload(line);
        if (!event) continue;
        if (event.type === "RUN_ERROR") {
          handlers.onRunError(String(event.message || "Agent 运行出错"));
        }
        if (event.type === "CUSTOM" && event.name === "thread.revision.updated") {
          const raw = event.value;
          const value = typeof raw === "string" ? JSON.parse(raw) : raw;
          if (value && typeof value.revision === "number") {
            handlers.onRevision?.(String(value.threadId ?? ""), value.revision);
          }
        }
      }
    }
  } catch {
    // 扫描失败不影响主流程
  } finally {
    handlers.onStreamEnd?.();
  }
}

export class AgentHttpAgent extends HttpAgent {
  private readonly modelConfig: AgentModelConfig;
  private armedRetryOf: string | null = null;
  private readonly transportOptions: AgentTransportOptions;

  constructor(
    config: AgentModelConfig,
    onConflict: (value: Conflict) => void,
    onRunError: (message: string) => void = () => {},
    transportOptions: AgentTransportOptions = {},
  ) {
    const transportFetch = async (url: string, init: RequestInit) => {
      const response = await fetch(url, init);
      if (response.status === 409) {
        const payload = await response.clone().json().catch(() => ({})) as Conflict;
        onConflict(payload);
        return response;
      }
      if (response.status === 503) {
        // fail-closed MCP 准入失败：流从未开始。只上报脱敏 detail；
        // 不触发 409 权威 reload，也不自动重试。
        const payload = await response.clone().json().catch(() => ({})) as Conflict;
        transportOptions.onUnavailable?.(payload.detail ?? payload.code ?? "MCP 服务不可用");
        return response;
      }
      // 流内 RUN_ERROR / thread.revision.updated 不会走 runtime 回调，
      // 在传输层 tee 流扫描并上报给页面（订阅钩子在当前 @ag-ui/client 版本不触发）。
      if (response.body && (response.headers.get("content-type") ?? "").includes("text/event-stream")) {
        const [forCaller, forScan] = response.body.tee();
        void scanStream(forScan, {
          onRunError,
          onRevision: transportOptions.onRevision,
          onStreamEnd: transportOptions.onStreamEnd,
        });
        return new Response(forCaller, {
          status: response.status,
          statusText: response.statusText,
          headers: response.headers,
        });
      }
      return response;
    };
    super({
      url: "/api/agent/run",
      headers: {
        ...authHeaders(),
        "X-VR-Agent-Model-Key": config.apiKey,
      },
      fetch: transportFetch,
    });
    this.modelConfig = config;
    this.transportOptions = transportOptions;
  }

  /** 装填一次性重试目标；下一次请求消费后自动清空。 */
  armRetry(runId: string) {
    this.armedRetryOf = runId;
  }

  protected requestInit(input: RunAgentInput): RequestInit {
    let forwardedProps = input.forwardedProps ?? {};
    const runtime = typeof forwardedProps.runtime === "object" && forwardedProps.runtime
      ? forwardedProps.runtime as Record<string, unknown>
      : {};
    // @ag-ui/client 把 resume 放在顶层字段；后端合同是 forwardedProps.command.resume
    // （纯 resume 还要求 messages 为空）。这里做协议翻译，不改后端形状。
    const topLevelResume = (input as { resume?: unknown[] }).resume;
    let messages = input.messages;
    if (Array.isArray(topLevelResume) && topLevelResume.length > 0) {
      const allCancelled = topLevelResume.every(
        (entry) => typeof entry === "object" && entry !== null
          && (entry as { status?: string }).status === "cancelled");
      forwardedProps = {
        ...forwardedProps,
        command: { ...(forwardedProps as { command?: object }).command, resume: topLevelResume },
      };
      if (!allCancelled) {
        messages = []; // 纯 resume：不得携带新消息
      }
      input = { ...input, resume: undefined };
    }
    const retryOf = this.armedRetryOf;
    this.armedRetryOf = null;
    const nextRuntime: Record<string, unknown> = {
      ...runtime,
      model: {
        provider: this.modelConfig.provider,
        baseURL: this.modelConfig.baseURL,
        model: this.modelConfig.model,
      },
      threadRevision: this.transportOptions.getRevision?.() ?? 0,
    };
    if (retryOf) {
      nextRuntime.retryOf = retryOf;
    }
    const serverThreadId = this.transportOptions.getThreadId?.();
    return super.requestInit({
      ...input,
      // assistant-ui 内部线程 ID 与服务端线程 ID 不同：一律以服务端为准
      threadId: serverThreadId ?? input.threadId,
      // retry 不携带新消息（messages=[]）；其余形状保持 runtime 生成的历史前缀
      messages: retryOf ? [] : messages,
      forwardedProps: {
        ...forwardedProps,
        runtime: nextRuntime,
      },
    });
  }
}

export function AgentRuntimeProvider({
  config,
  onConflict,
  onError,
  controller,
  onRuntime,
  onStreamEnd,
  onUnavailable,
  children,
}: {
  config: AgentModelConfig;
  onConflict: (value: Conflict) => void;
  onError: (error: Error) => void;
  controller?: AgentHistoryController;
  onRuntime?: (refs: { agent: AgentHttpAgent; startRun: (parentId: string | null) => void }) => void;
  onStreamEnd?: () => void;
  onUnavailable?: (detail: string) => void;
  children: ReactNode;
}) {
  const threadId = controller?.getActiveThreadId() ?? null;
  const getThreadId = useCallback(() => controller?.getActiveThreadId() ?? null, [controller]);
  const getRevision = useCallback(
    () => (threadId ? controller?.getRevision(threadId) ?? 0 : 0),
    [controller, threadId],
  );
  const agent = useMemo(
    () => new AgentHttpAgent(
      config,
      onConflict,
      (message) => onError(new Error(message)),
      {
        getThreadId,
        getRevision,
        onRevision: (id, revision) => controller?.applyRevision(id, revision),
        onStreamEnd,
        onUnavailable,
      },
    ),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [config, onConflict, onError, getThreadId, getRevision, onStreamEnd, onUnavailable],
  );
  const historyAdapter = useMemo(() => controller?.historyAdapter(), [controller, threadId]);
  const runtime = useAgUiRuntime({
    agent,
    autoCancelPendingToolCalls: false,
    unstable_enableMessageQueue: false,
    onError,
    ...(historyAdapter ? { adapters: { history: historyAdapter } } : {}),
  });
  useEffect(() => {
    if (!onRuntime) return;
    onRuntime({
      agent,
      startRun: (parentId: string | null) => {
        runtime.thread.startRun({ parentId, sourceId: null });
      },
    });
  }, [agent, runtime, onRuntime]);
  return <AssistantRuntimeProvider runtime={runtime}>{children}</AssistantRuntimeProvider>;
}
