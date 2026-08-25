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

export interface WorkflowStartedEvent {
  event: "workflow_started";
  workflow_type: string;
  input: Record<string, unknown>;
  variant?: string | null;
  run_id?: string;
  seq?: number;
  created_at?: string;
}

export interface DossierProgressEvent {
  event: "dossier_progress";
  code: string;
  section: string;
  status: "ok" | "empty" | "error";
  missing: boolean;
  run_id?: string;
  seq?: number;
  created_at?: string;
}

export interface DossierCompletedEvent {
  event: "dossier_completed";
  code: string;
  sections: { key: string; title: string; body: string; missing: boolean }[];
  missing: string[];
  summary: string;
  run_id?: string;
  seq?: number;
  created_at?: string;
}

export interface StageStartedEvent {
  event: "stage_started";
  stage_id: string;
  run_id?: string;
  seq?: number;
  created_at?: string;
}

export interface StageDeltaEvent {
  event: "stage_delta";
  stage_id: string;
  delta: string;
  run_id?: string;
  seq?: number;
  created_at?: string;
}

export interface StageCompletedEvent {
  event: "stage_completed";
  stage_id: string;
  truncated: boolean;
  run_id?: string;
  seq?: number;
  created_at?: string;
}

export interface StageFailedEvent {
  event: "stage_failed";
  stage_id: string;
  error: { code: string; message: string; details?: unknown };
  run_id?: string;
  seq?: number;
  created_at?: string;
}

export interface WorkflowCompletedEvent {
  event: "workflow_completed";
  workflow_type: string;
  result_summary: string;
  run_id?: string;
  seq?: number;
  created_at?: string;
}

export interface WorkflowFailedEvent {
  event: "workflow_failed";
  workflow_type: string;
  error: { code: string; message: string; details?: unknown };
  run_id?: string;
  seq?: number;
  created_at?: string;
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
      event: "stage_delta",
      stage_id: "unknown",
      delta: String(raw ?? ""),
    };
  }

  // 原生自定义事件直接返回
  if (raw.event && typeof raw.event === "string") {
    return raw as WorkflowEvent;
  }

  // 兼容传统 NDJSON 格式
  const type = raw.type;
  if (type === "dossier_progress") {
    return {
      event: "dossier_progress",
      code: raw.code || "",
      section: raw.section || "",
      status: raw.status || (raw.missing ? "empty" : "ok"),
      missing: Boolean(raw.missing),
    };
  }
  if (type === "dossier") {
    return {
      event: "dossier_completed",
      code: raw.code || "",
      sections: raw.sections || [],
      missing: raw.missing || [],
      summary: raw.summary || "",
    };
  }
  if (type === "round_start" || type === "start") {
    return {
      event: "stage_started",
      stage_id: raw.role || raw.stage || "main",
    };
  }
  if (type === "delta") {
    return {
      event: "stage_delta",
      stage_id: raw.role || raw.stage || "main",
      delta: raw.content || raw.delta || "",
    };
  }
  if (type === "round_end") {
    return {
      event: "stage_completed",
      stage_id: raw.role || raw.stage || "main",
      truncated: Boolean(raw.truncated),
    };
  }
  if (type === "done") {
    return {
      event: "workflow_completed",
      workflow_type: raw.workflow_type || "workflow",
      result_summary: raw.result_summary || "完成",
    };
  }
  if (type === "error") {
    return {
      event: "stage_failed",
      stage_id: raw.role || "main",
      error: { code: "ERROR", message: raw.message || "执行错误" },
    };
  }

  return {
    event: "stage_delta",
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
        if (eventName === "custom" || parsed.event) {
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
): AsyncGenerator<WorkflowEvent> {
  const resp = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...authHeaders(),
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

    // 按行或双换行拆分处理（兼容 NDJSON 与 SSE）
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
