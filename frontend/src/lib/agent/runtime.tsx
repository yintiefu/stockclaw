import { useCallback, useEffect, useMemo, type ReactNode } from "react";
import { AssistantRuntimeProvider } from "@assistant-ui/react";
import { useAgUiRuntime } from "@assistant-ui/react-ag-ui";
import { HttpAgent, type RunAgentInput } from "@ag-ui/client";

import { authHeaders } from "@/lib/api";
import type { AgentHistoryController } from "./history";
import type { AgentModelConfig } from "./model-config";
import type { AgentConflict, AgentStreamEvent } from "./types";

type Conflict = AgentConflict;

export type AgentTransportOptions = {
  /** 当前服务端线程 ID（发送时读取；覆盖 runtime 内部的临时线程 ID）。 */
  getThreadId?: () => string | null;
  /** 当前权威 revision（发送时读取）。 */
  getRevision?: () => number;
  /** 流内 thread.revision.updated 事件（提交后到达）。 */
  onRevision?: (threadId: string, revision: number) => void;
  /** 已通过基础身份/修订号校验的持久化领域事件。 */
  onEvent?: (event: AgentStreamEvent) => void;
  /** 流中事件无法解析时，仅标记对应 REST 数据失效。 */
  onInvalidate?: (threadId: string, runId: string) => void;
  /** 流结束（终局 / 停止 / 断连）后触发一次，用于收敛到服务端权威状态。 */
  onStreamEnd?: (threadId?: string, runId?: string) => void;
  /** 流开始前的 503（MCP_UNAVAILABLE）：不重试、不 reload。 */
  onUnavailable?: (detail: string) => void;
};

function parseSsePayload(line: string): Record<string, unknown> | null | undefined {
  if (!line.startsWith("data: ")) return undefined;
  try {
    return JSON.parse(line.slice(6)) as Record<string, unknown>;
  } catch {
    return null;
  }
}

function requestIdentity(init: RequestInit): { threadId?: string; runId?: string } {
  if (typeof init.body !== "string") return {};
  try {
    const body = JSON.parse(init.body) as Record<string, unknown>;
    return {
      threadId: typeof body.threadId === "string" && body.threadId ? body.threadId : undefined,
      runId: typeof body.runId === "string" && body.runId ? body.runId : undefined,
    };
  } catch {
    return {};
  }
}

async function scanStream(
  stream: ReadableStream<Uint8Array>,
  handlers: {
    onRunError: (message: string) => void;
    onRevision?: (threadId: string, revision: number) => void;
    onEvent?: (event: AgentStreamEvent) => void;
    onInvalidate?: (threadId: string, runId: string) => void;
    onStreamEnd?: (threadId?: string, runId?: string) => void;
    threadId?: string;
    runId?: string;
  },
) {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let streamThreadId = handlers.threadId;
  let streamRunId = handlers.runId;
  let invalidated = false;
  const invalidate = () => {
    if (invalidated || !streamThreadId || !streamRunId) return;
    invalidated = true;
    handlers.onInvalidate?.(streamThreadId, streamRunId);
  };
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";
      for (const line of lines) {
        const event = parseSsePayload(line);
        if (event === undefined) continue;
        if (event === null) {
          invalidate();
          continue;
        }
        if (typeof event.threadId === "string" && event.threadId) streamThreadId = event.threadId;
        if (typeof event.runId === "string" && event.runId) streamRunId = event.runId;
        if (event.type === "RUN_ERROR") {
          handlers.onRunError(String(event.message || "Agent 运行出错"));
        }
        if (event.type === "CUSTOM" && typeof event.name === "string") {
          let value: unknown = event.value;
          if (typeof value === "string") {
            try {
              value = JSON.parse(value);
            } catch {
              invalidate();
              continue;
            }
          }
          const custom = parsePersistedEvent(event.name, value);
          if (!custom) {
            invalidate();
            continue;
          }
          streamThreadId = custom.value.threadId;
          if ("runId" in custom.value) streamRunId = custom.value.runId;
          if (custom.name === "thread.revision.updated") {
            handlers.onRevision?.(custom.value.threadId, custom.value.revision);
          }
          handlers.onEvent?.(custom);
        }
      }
    }
  } catch {
    // 扫描失败不影响主流程
  } finally {
    invalidate();
    handlers.onStreamEnd?.(streamThreadId, streamRunId);
  }
}

function isIdentity(value: Record<string, unknown>, revision: string, run = false): boolean {
  return typeof value.threadId === "string" && value.threadId.length > 0
    && (!run || (typeof value.runId === "string" && value.runId.length > 0))
    && typeof value[revision] === "number" && Number.isInteger(value[revision]) && value[revision] >= 0;
}

/** 只在这里接受持久化事件；复杂字段留给 REST 权威详情，避免破坏 assistant-ui 流。 */
function parsePersistedEvent(name: string, raw: unknown): AgentStreamEvent | null {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const value = raw as Record<string, unknown>;
  if (name === "thread.revision.updated" && isIdentity(value, "revision")) {
    return { name, value: value as AgentStreamEvent["value"] & { threadId: string; revision: number } } as AgentStreamEvent;
  }
  if (name === "budget.updated" && isIdentity(value, "controlRevision", true)) {
    return { name, value: value as AgentStreamEvent["value"] & { threadId: string; runId: string; controlRevision: number } } as AgentStreamEvent;
  }
  if (name === "artifact.created" && isIdentity(value, "threadRevision", true)
    && typeof value.artifactId === "string" && value.artifactId.length > 0) {
    return { name, value: value as AgentStreamEvent["value"] & { threadId: string; runId: string; threadRevision: number } } as AgentStreamEvent;
  }
  if (name === "sources.updated" && isIdentity(value, "controlRevision", true)) {
    return { name, value: value as AgentStreamEvent["value"] & { threadId: string; runId: string; controlRevision: number } } as AgentStreamEvent;
  }
  return null;
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
      const identity = requestIdentity(init);
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
          onEvent: transportOptions.onEvent,
          onInvalidate: transportOptions.onInvalidate,
          onStreamEnd: transportOptions.onStreamEnd,
          threadId: identity.threadId ?? transportOptions.getThreadId?.() ?? undefined,
          runId: identity.runId,
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
  onInvalidate,
  onEvent,
  onUnavailable,
  children,
}: {
  config: AgentModelConfig;
  onConflict: (value: Conflict) => void;
  onError: (error: Error) => void;
  controller?: AgentHistoryController;
  onRuntime?: (refs: { agent: AgentHttpAgent; startRun: (parentId: string | null) => void }) => void;
  onStreamEnd?: (threadId?: string, runId?: string) => void;
  onInvalidate?: (threadId: string, runId: string) => void;
  onEvent?: (event: AgentStreamEvent) => void;
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
        onInvalidate,
        onEvent,
        onUnavailable,
      },
    ),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [config, onConflict, onError, getThreadId, getRevision, onStreamEnd, onInvalidate, onEvent, onUnavailable],
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
