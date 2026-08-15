import { useMemo, type ReactNode } from "react";
import { AssistantRuntimeProvider } from "@assistant-ui/react";
import { useAgUiRuntime } from "@assistant-ui/react-ag-ui";
import { HttpAgent, type RunAgentInput } from "@ag-ui/client";

import { authHeaders } from "@/lib/api";
import type { AgentModelConfig } from "./model-config";

type Conflict = { code?: string; detail?: string };

async function scanRunErrors(stream: ReadableStream<Uint8Array>, onRunError: (message: string) => void) {
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
        if (!line.startsWith("data: ")) continue;
        try {
          const event = JSON.parse(line.slice(6));
          if (event?.type === "RUN_ERROR") {
            onRunError(event.message || "Agent 运行出错");
          }
        } catch {
          // 非完整 JSON 行，忽略
        }
      }
    }
  } catch {
    // 扫描失败不影响主流程
  }
}

export class AgentHttpAgent extends HttpAgent {
  private readonly modelConfig: AgentModelConfig;

  constructor(
    config: AgentModelConfig,
    onConflict: (value: Conflict) => void,
    onRunError: (message: string) => void = () => {},
  ) {
    const transportFetch = async (url: string, init: RequestInit) => {
      const response = await fetch(url, init);
      if (response.status === 409) {
        const payload = await response.clone().json().catch(() => ({})) as Conflict;
        onConflict(payload);
        return response;
      }
      // 流内 RUN_ERROR（如上游余额不足/模型异常）不会走 runtime 的 onError，
      // 在传输层 tee 流扫描并上报给页面（订阅钩子在当前 @ag-ui/client 版本不触发）。
      if (response.body && (response.headers.get("content-type") ?? "").includes("text/event-stream")) {
        const [forCaller, forScan] = response.body.tee();
        void scanRunErrors(forScan, onRunError);
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
  }

  protected requestInit(input: RunAgentInput): RequestInit {
    const forwardedProps = input.forwardedProps ?? {};
    const runtime = typeof forwardedProps.runtime === "object" && forwardedProps.runtime
      ? forwardedProps.runtime as Record<string, unknown>
      : {};
    return super.requestInit({
      ...input,
      forwardedProps: {
        ...forwardedProps,
        runtime: {
          ...runtime,
          model: {
            provider: this.modelConfig.provider,
            baseURL: this.modelConfig.baseURL,
            model: this.modelConfig.model,
          },
        },
      },
    });
  }
}

export function AgentRuntimeProvider({
  config,
  onConflict,
  onError,
  children,
}: {
  config: AgentModelConfig;
  onConflict: (value: Conflict) => void;
  onError: (error: Error) => void;
  children: ReactNode;
}) {
  const agent = useMemo(
    () => new AgentHttpAgent(config, onConflict, (message) => onError(new Error(message))),
    [config, onConflict, onError],
  );
  const runtime = useAgUiRuntime({
    agent,
    autoCancelPendingToolCalls: false,
    unstable_enableMessageQueue: false,
    onError,
  });
  return <AssistantRuntimeProvider runtime={runtime}>{children}</AssistantRuntimeProvider>;
}
