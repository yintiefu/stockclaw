import { useMemo, type ReactNode } from "react";
import { AssistantRuntimeProvider } from "@assistant-ui/react";
import { useAgUiRuntime } from "@assistant-ui/react-ag-ui";
import { HttpAgent, type RunAgentInput } from "@ag-ui/client";

import { authHeaders } from "@/lib/api";
import type { AgentModelConfig } from "./model-config";

type Conflict = { code?: string; detail?: string };

export class AgentHttpAgent extends HttpAgent {
  private readonly modelConfig: AgentModelConfig;

  constructor(config: AgentModelConfig, onConflict: (value: Conflict) => void) {
    const transportFetch = async (url: string, init: RequestInit) => {
      const response = await fetch(url, init);
      if (response.status === 409) {
        const payload = await response.clone().json().catch(() => ({})) as Conflict;
        onConflict(payload);
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
    () => new AgentHttpAgent(config, onConflict),
    [config, onConflict],
  );
  const runtime = useAgUiRuntime({
    agent,
    autoCancelPendingToolCalls: false,
    unstable_enableMessageQueue: false,
    onError,
  });
  return <AssistantRuntimeProvider runtime={runtime}>{children}</AssistantRuntimeProvider>;
}
