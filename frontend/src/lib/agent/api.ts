/** Agent 专用 REST 客户端 —— 不改动共享 lib/api.ts 的 request 签名。 */

import { authHeaders } from "@/lib/api";
import type {
  AgentConflict,
  AgentRun,
  AgentThread,
  AgentThreadListResponse,
} from "./types";

export class AgentApiError extends Error {
  readonly status: number;
  readonly code?: string;
  readonly threadId?: string | null;
  readonly productRunId?: string | null;
  readonly runStatus?: string | null;

  constructor(status: number, payload: AgentConflict) {
    super(payload.detail ?? payload.code ?? `Agent API 错误（${status}）`);
    this.name = "AgentApiError";
    this.status = status;
    this.code = payload.code;
    this.threadId = payload.thread_id ?? null;
    this.productRunId = payload.product_run_id ?? null;
    this.runStatus = payload.status ?? null;
  }
}

async function agentRequest<T>(
  url: string,
  method: "GET" | "POST" | "PATCH" | "DELETE" = "GET",
  body?: unknown,
): Promise<T> {
  const response = await fetch(url, {
    method,
    headers: {
      ...authHeaders(),
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (response.status === 204) {
    return undefined as T;
  }
  if (!response.ok) {
    const payload = await response.json().catch(() => ({})) as AgentConflict;
    throw new AgentApiError(response.status, payload);
  }
  return await response.json() as T;
}

export const agentApi = {
  listThreads: () => agentRequest<AgentThreadListResponse>("/api/agent/threads"),
  createThread: (title = "新会话") =>
    agentRequest<AgentThread>("/api/agent/threads", "POST", { title }),
  getThread: (id: string) => agentRequest<AgentThread>(`/api/agent/threads/${encodeURIComponent(id)}`),
  patchThread: (id: string, revision: number, title: string) =>
    agentRequest<AgentThread>(`/api/agent/threads/${encodeURIComponent(id)}`, "PATCH", { revision, title }),
  deleteThread: (id: string, revision: number) =>
    agentRequest<void>(`/api/agent/threads/${encodeURIComponent(id)}`, "DELETE", { revision }),
  cancelRun: (id: string) =>
    agentRequest<AgentRun>(`/api/agent/runs/${encodeURIComponent(id)}/cancel`, "POST"),
};
