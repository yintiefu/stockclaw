// 统一工作流流式客户端与自定义事件契约定义。
// 严格对齐 docs/contracts/workflow-custom-events.json 与后端 workflow_events.py

import { authHeaders, ApiError } from "./api.ts";

export type WorkflowEventType =
  | "workflow_started"
  | "dossier_progress"
  | "dossier_completed"
  | "stage_started"
  | "stage_delta"
  | "stage_completed"
  | "stage_failed"
  | "workflow_completed"
  | "workflow_failed";

export interface WorkflowError {
  code: string;
  message: string;
  stage_id?: string | null;
}

export interface BaseWorkflowEvent {
  type: WorkflowEventType;
  workflow_id: string;
  run_id: string;
  seq: number;
  emitted_at: string;
}

export interface WorkflowStartedEvent extends BaseWorkflowEvent {
  type: "workflow_started";
  workflow_type: string;
  input: Record<string, unknown>;
  variant?: string | null;
}

export interface DossierProgressEvent extends BaseWorkflowEvent {
  type: "dossier_progress";
  title: string;
  section_id: string;
  tool: string;
  status: "ok" | "no_record" | "gap";
  loaded: number;
  total: number;
}

export interface DossierCompletedEvent extends BaseWorkflowEvent {
  type: "dossier_completed";
  section_count: number;
  missing_count: number;
}

export interface StageStartedEvent extends BaseWorkflowEvent {
  type: "stage_started";
  stage_id: string;
}

export interface StageDeltaEvent extends BaseWorkflowEvent {
  type: "stage_delta";
  stage_id: string;
  delta: string;
}

export interface StageCompletedEvent extends BaseWorkflowEvent {
  type: "stage_completed";
  stage_id: string;
  truncated?: boolean;
}

export interface StageFailedEvent extends BaseWorkflowEvent {
  type: "stage_failed";
  stage_id: string;
  error: WorkflowError;
}

export interface WorkflowCompletedEvent extends BaseWorkflowEvent {
  type: "workflow_completed";
  workflow_type: string;
  result_summary: string;
}

export interface WorkflowFailedEvent extends BaseWorkflowEvent {
  type: "workflow_failed";
  workflow_type: string;
  error: WorkflowError;
}

export type WorkflowEvent =
  | WorkflowStartedEvent
  | DossierProgressEvent
  | DossierCompletedEvent
  | StageStartedEvent
  | StageDeltaEvent
  | StageCompletedEvent
  | StageFailedEvent
  | WorkflowCompletedEvent
  | WorkflowFailedEvent;

export function normalizeWorkflowEvent(raw: any): WorkflowEvent {
  if (!raw || typeof raw !== "object") {
    return {
      type: "stage_delta",
      workflow_id: "unknown",
      run_id: "unknown",
      seq: 0,
      emitted_at: new Date().toISOString(),
      stage_id: "unknown",
      delta: String(raw ?? ""),
    };
  }

  const eventType = raw.type || raw.event;
  if (typeof eventType === "string") {
    const workflowId = String(raw.workflow_id || raw.workflow_type || "workflow");
    const runId = String(raw.run_id || "local-run");
    const seq = typeof raw.seq === "number" ? raw.seq : 0;
    const emittedAt = String(raw.emitted_at || raw.created_at || new Date().toISOString());

    switch (eventType) {
      case "workflow_started":
        return {
          type: "workflow_started",
          workflow_id: workflowId,
          workflow_type: String(raw.workflow_type || workflowId),
          input: (raw.input && typeof raw.input === "object") ? raw.input : {},
          variant: raw.variant ?? null,
          run_id: runId,
          seq,
          emitted_at: emittedAt,
        };
      case "dossier_progress":
        return {
          type: "dossier_progress",
          workflow_id: workflowId,
          run_id: runId,
          seq,
          emitted_at: emittedAt,
          title: String(raw.title || raw.section || ""),
          section_id: String(raw.section_id || raw.section || ""),
          tool: String(raw.tool || ""),
          status: raw.status === "no_record" || raw.status === "gap" || raw.status === "ok"
            ? raw.status
            : (raw.missing ? "gap" : "ok"),
          loaded: typeof raw.loaded === "number" ? raw.loaded : 0,
          total: typeof raw.total === "number" ? raw.total : 0,
        };
      case "dossier_completed":
      case "dossier":
        return {
          type: "dossier_completed",
          workflow_id: workflowId,
          run_id: runId,
          seq,
          emitted_at: emittedAt,
          section_count: typeof raw.section_count === "number"
            ? raw.section_count
            : (Array.isArray(raw.sections) ? raw.sections.length : 0),
          missing_count: typeof raw.missing_count === "number"
            ? raw.missing_count
            : (Array.isArray(raw.missing) ? raw.missing.length : 0),
        };
      case "stage_started":
      case "round_start":
        return {
          type: "stage_started",
          workflow_id: workflowId,
          run_id: runId,
          seq,
          emitted_at: emittedAt,
          stage_id: String(raw.stage_id || raw.role || raw.stage || "main"),
        };
      case "stage_delta":
      case "delta":
        return {
          type: "stage_delta",
          workflow_id: workflowId,
          run_id: runId,
          seq,
          emitted_at: emittedAt,
          stage_id: String(raw.stage_id || raw.role || raw.stage || "main"),
          delta: String(raw.delta || raw.content || raw.text || ""),
        };
      case "stage_completed":
      case "round_end":
        return {
          type: "stage_completed",
          workflow_id: workflowId,
          run_id: runId,
          seq,
          emitted_at: emittedAt,
          stage_id: String(raw.stage_id || raw.role || raw.stage || "main"),
          truncated: Boolean(raw.truncated),
        };
      case "stage_failed":
        return {
          type: "stage_failed",
          workflow_id: workflowId,
          run_id: runId,
          seq,
          emitted_at: emittedAt,
          stage_id: String(raw.stage_id || raw.role || "main"),
          error: {
            code: String(raw.error?.code || "STAGE_ERROR"),
            message: String(raw.error?.message || raw.message || "阶段执行异常"),
            stage_id: raw.stage_id || raw.role || "main",
          },
        };
      case "workflow_completed":
      case "done":
        return {
          type: "workflow_completed",
          workflow_id: workflowId,
          run_id: runId,
          seq,
          emitted_at: emittedAt,
          workflow_type: String(raw.workflow_type || workflowId),
          result_summary: String(raw.result_summary || "执行完成"),
        };
      case "workflow_failed":
      case "error":
        return {
          type: "workflow_failed",
          workflow_id: workflowId,
          run_id: runId,
          seq,
          emitted_at: emittedAt,
          workflow_type: String(raw.workflow_type || workflowId),
          error: {
            code: String(raw.error?.code || "WORKFLOW_FAILED"),
            message: String(raw.error?.message || raw.message || "工作流执行失败"),
            stage_id: raw.error?.stage_id || null,
          },
        };
    }
  }

  return {
    type: "stage_delta",
    workflow_id: "unknown",
    run_id: "local-run",
    seq: 0,
    emitted_at: new Date().toISOString(),
    stage_id: "main",
    delta: raw.content || JSON.stringify(raw),
  };
}

export function parseSSEEvents(raw: string): WorkflowEvent[] {
  const events: WorkflowEvent[] = [];
  const blocks = raw.split(/\n\n|\r\n\r\n/);

  for (const block of blocks) {
    if (!block.trim()) continue;
    let eventName = "message";
    const dataLines: string[] = [];

    for (const line of block.split("\n")) {
      const trimmed = line.trimEnd();
      if (!trimmed || trimmed.startsWith(":")) continue;
      if (trimmed.startsWith("event:")) {
        eventName = trimmed.slice(6).trim();
      } else if (trimmed.startsWith("data:")) {
        dataLines.push(trimmed.slice(5).trim());
      }
    }

    if (dataLines.length > 0) {
      const dataStr = dataLines.join("\n");
      try {
        const parsed = JSON.parse(dataStr);
        if (eventName === "custom" || parsed.type || parsed.event) {
          events.push(normalizeWorkflowEvent(parsed));
        }
      } catch {
        // 忽略非 JSON 数据行
      }
    }
  }

  return events;
}

export async function* streamWorkflowEvents(
  url: string,
  body: unknown,
  signal?: AbortSignal,
  headers?: Record<string, string>,
): AsyncGenerator<WorkflowEvent> {
  const isDirectLangGraph = url.includes(":2024") || url.startsWith("/agent-api");
  const resp = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      // LangGraph Server 不接收 VR_API_KEY，仅在调用 FastAPI 路由时注入 Bearer
      ...(isDirectLangGraph ? {} : authHeaders()),
      ...(headers || {}),
    },
    body: JSON.stringify(body),
    signal,
  });

  if (!resp.ok) {
    let errBody: any = null;
    try {
      errBody = await resp.json();
    } catch {
      /* ignore */
    }
    throw new ApiError(errBody?.detail || `HTTP ${resp.status}`, resp.status);
  }

  if (!resp.body) throw new ApiError("后端无响应流", 502);

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // 按行拆分处理（兼容 NDJSON 与 SSE）
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";

    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith("event:") || trimmed.startsWith(":")) continue;

      let jsonStr = trimmed;
      if (trimmed.startsWith("data:")) {
        jsonStr = trimmed.slice(5).trim();
      }

      try {
        const parsed = JSON.parse(jsonStr);
        yield normalizeWorkflowEvent(parsed);
      } catch {
        // 忽略非合法 JSON 行
      }
    }
  }

  if (buffer.trim()) {
    try {
      const parsed = JSON.parse(buffer.trim().replace(/^data:\s*/, ""));
      yield normalizeWorkflowEvent(parsed);
    } catch {
      // 忽略
    }
  }
}
