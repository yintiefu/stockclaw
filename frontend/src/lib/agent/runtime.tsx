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
  /** 当前权威 revision（发送时读取）。 */
  getRevision?: () => number;
  /** 流内 thread.revision.updated 事件（提交后到达）。 */
  onRevision?: (threadId: string, revision: number) => void;
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
      // 流内 RUN_ERROR / thread.revision.updated 不会走 runtime 回调，
      // 在传输层 tee 流扫描并上报给页面（订阅钩子在当前 @ag-ui/client 版本不触发）。
      if (response.body && (response.headers.get("content-type") ?? "").includes("text/event-stream")) {
        const [forCaller, forScan] = response.body.tee();
        void scanStream(forScan, { onRunError, onRevision: transportOptions.onRevision });
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
    const forwardedProps = input.forwardedProps ?? {};
    const runtime = typeof forwardedProps.runtime === "object" && forwardedProps.runtime
      ? forwardedProps.runtime as Record<string, unknown>
      : {};
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
    return super.requestInit({
      ...input,
      // retry 不携带新消息（messages=[]）；其余形状保持 runtime 生成的历史前缀
      messages: retryOf ? [] : input.messages,
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
  children,
}: {
  config: AgentModelConfig;
  onConflict: (value: Conflict) => void;
  onError: (error: Error) => void;
  controller?: AgentHistoryController;
  onRuntime?: (refs: { agent: AgentHttpAgent; startRun: (parentId: string | null) => void }) => void;
  children: ReactNode;
}) {
  const threadId = controller?.getActiveThreadId() ?? null;
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
        getRevision,
        onRevision: (id, revision) => controller?.applyRevision(id, revision),
      },
    ),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [config, onConflict, onError, getRevision],
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
