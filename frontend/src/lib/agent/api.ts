/** Agent 专用 REST 客户端 —— 不改动共享 lib/api.ts 的 request 签名。 */

import { authHeaders } from "@/lib/api";
import type {
  AgentConflict,
  AgentManagementError,
  AgentPolicy,
  AgentPolicyPatch,
  AgentPolicyReset,
  AgentRun,
  AgentRunDetail,
  AgentRunListResponse,
  AgentThread,
  AgentThreadListResponse,
  ArtifactDetail,
  ArtifactDownload,
  ArtifactListResponse,
  SkillDetail,
  SkillImportResult,
  SkillListResponse,
  McpDocument,
} from "./types";

export class AgentApiError extends Error {
  readonly status: number;
  readonly code?: string;
  readonly threadId?: string | null;
  readonly productRunId?: string | null;
  readonly runStatus?: string | null;
  readonly preview?: {
    executable: string;
    resolved_executable: string;
    args: string[];
    fingerprint: string;
  };

  constructor(status: number, payload: AgentManagementError & { preview?: {
    executable: string;
    resolved_executable: string;
    args: string[];
    fingerprint: string;
  } }) {
    super(payload.detail ?? payload.code ?? `Agent API 错误（${status}）`);
    this.name = "AgentApiError";
    this.status = status;
    this.code = payload.code;
    this.threadId = payload.thread_id ?? null;
    this.productRunId = payload.product_run_id ?? null;
    this.runStatus = payload.status ?? null;
    this.preview = payload.preview;
  }
}

function downloadFilename(header: string | null): string | null {
  if (!header) return null;
  const encoded = /filename\*=UTF-8''([^;]+)/i.exec(header)?.[1];
  if (encoded) {
    try {
      return decodeURIComponent(encoded);
    } catch {
      return null;
    }
  }
  return /filename="([^"]+)"/i.exec(header)?.[1] ?? null;
}

function validArtifactDownload(response: Response): string | null {
  const contentType = response.headers.get("Content-Type")?.toLowerCase();
  const disposition = response.headers.get("Content-Disposition");
  if (
    (contentType !== "text/markdown; charset=utf-8" && contentType !== "application/json; charset=utf-8")
    || !disposition?.startsWith("attachment;")
  ) return null;
  return downloadFilename(disposition);
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
  patchThread: (
    id: string,
    revision: number,
    patch: { title?: string; selected_skills?: string[] },
  ) =>
    agentRequest<AgentThread>(`/api/agent/threads/${encodeURIComponent(id)}`, "PATCH", {
      revision,
      ...patch,
    }),
  deleteThread: (id: string, revision: number) =>
    agentRequest<void>(`/api/agent/threads/${encodeURIComponent(id)}`, "DELETE", { revision }),
  cancelRun: (id: string) =>
    agentRequest<AgentRun>(`/api/agent/runs/${encodeURIComponent(id)}/cancel`, "POST"),
  getPolicy: () => agentRequest<AgentPolicy>("/api/agent/policy"),
  patchPolicy: (patch: AgentPolicyPatch) =>
    agentRequest<AgentPolicy>("/api/agent/policy", "PATCH", patch),
  resetPolicy: (reset: AgentPolicyReset) =>
    agentRequest<AgentPolicy>("/api/agent/policy/reset", "POST", reset),
  listRuns: (threadId: string, limit = 50, before?: string) => {
    const query = `limit=${limit}${before ? `&before=${encodeURIComponent(before)}` : ""}`;
    return agentRequest<AgentRunListResponse>(
      `/api/agent/threads/${encodeURIComponent(threadId)}/runs?${query}`,
    );
  },
  getRun: (runId: string) => agentRequest<AgentRunDetail>(`/api/agent/runs/${encodeURIComponent(runId)}`),
  listArtifacts: (threadId: string) => agentRequest<ArtifactListResponse>(
    `/api/agent/threads/${encodeURIComponent(threadId)}/artifacts`,
  ),
  getArtifact: (threadId: string, artifactId: string) => agentRequest<ArtifactDetail>(
    `/api/agent/threads/${encodeURIComponent(threadId)}/artifacts/${encodeURIComponent(artifactId)}`,
  ),
  downloadArtifact: async (threadId: string, artifactId: string): Promise<ArtifactDownload> => {
    const response = await fetch(
      `/api/agent/threads/${encodeURIComponent(threadId)}/artifacts/${encodeURIComponent(artifactId)}/download`,
      { headers: authHeaders() },
    );
    if (!response.ok) {
      const payload = await response.json().catch(() => ({})) as AgentManagementError;
      throw new AgentApiError(response.status, payload);
    }
    const filename = validArtifactDownload(response);
    if (!filename) {
      throw new AgentApiError(response.status, {
        code: "ARTIFACT_DOWNLOAD_INVALID",
        detail: "Artifact 下载响应头不符合预期",
      });
    }
    return { blob: await response.blob(), filename };
  },
  deleteArtifact: (threadId: string, artifactId: string, threadRevision: number) => agentRequest<{ thread_revision: number }>(
    `/api/agent/threads/${encodeURIComponent(threadId)}/artifacts/${encodeURIComponent(artifactId)}`,
    "DELETE",
    { thread_revision: threadRevision },
  ),
  listSkills: () => agentRequest<SkillListResponse>("/api/agent/skills"),
  getSkill: (name: string) =>
    agentRequest<SkillDetail>(`/api/agent/skills/${encodeURIComponent(name)}`),
  // multipart 上传：不手工设置 Content-Type，让浏览器提供 boundary
  importSkill: (url: string, archive: File, options?: {
    overwrite?: boolean;
    expectedDigest?: string;
  }) => {
    const form = new FormData();
    form.append("archive", archive);
    if (options?.overwrite) form.append("overwrite", "true");
    if (options?.expectedDigest) form.append("expected_digest", options.expectedDigest);
    return fetch(url, { method: "POST", headers: authHeaders(), body: form })
      .then(async (response) => {
        const payload = await response.json().catch(() => ({})) as AgentConflict;
        if (!response.ok) {
          const error = new AgentApiError(response.status, payload);
          throw error;
        }
        return await response.json() as SkillImportResult;
      });
  },
  refreshSkills: () => agentRequest<{ generation: number }>("/api/agent/skills/refresh", "POST"),
  deleteSkill: (name: string, expectedDigest: string) =>
    fetch(`/api/agent/skills/${encodeURIComponent(name)}?expected_digest=${encodeURIComponent(expectedDigest)}`, {
      method: "DELETE",
      headers: authHeaders(),
    }).then(async (response) => {
      if (!response.ok) {
        const payload = await response.json().catch(() => ({})) as AgentConflict;
        throw new AgentApiError(response.status, payload);
      }
      return await response.json() as { deleted: string };
    }),
  getMcp: () => agentRequest<McpDocument>("/api/agent/mcp"),
  addMcp: (revision: number, server: Record<string, unknown>) =>
    agentRequest<McpDocument>("/api/agent/mcp", "POST", { revision, server }),
  patchMcp: (
    serverId: string,
    revision: number,
    patch: { server?: Record<string, unknown>; tool_enabled?: Record<string, boolean> },
  ) =>
    agentRequest<McpDocument>(
      `/api/agent/mcp/${encodeURIComponent(serverId)}`, "PATCH",
      { revision, ...patch },
    ),
  deleteMcp: (serverId: string, revision: number) =>
    agentRequest<McpDocument & { recovery_warnings: string[] }>(
      `/api/agent/mcp/${encodeURIComponent(serverId)}?revision=${revision}`, "DELETE"),
  trustMcp: (serverId: string, revision: number, fingerprint: string) =>
    agentRequest<McpDocument>(`/api/agent/mcp/${encodeURIComponent(serverId)}/trust`, "POST",
      { revision, fingerprint }),
  testMcp: (serverId: string, revision: number) =>
    agentRequest<McpDocument & { health: McpDocument["servers"][number]["health"] }>(
      `/api/agent/mcp/${encodeURIComponent(serverId)}/test`, "POST", { revision }),
  refreshMcp: (serverId: string, revision: number) =>
    agentRequest<McpDocument>(`/api/agent/mcp/${encodeURIComponent(serverId)}/refresh`, "POST",
      { revision }),
  fetchSkillFile: async (name: string, relativePath: string): Promise<Blob> => {
    const response = await fetch(
      `/api/agent/skills/${encodeURIComponent(name)}/files/${relativePath
        .split("/")
        .map(encodeURIComponent)
        .join("/")}`,
      { headers: authHeaders() },
    );
    if (!response.ok) {
      const payload = await response.json().catch(() => ({})) as AgentConflict;
      throw new AgentApiError(response.status, payload);
    }
    return await response.blob();
  },
};
