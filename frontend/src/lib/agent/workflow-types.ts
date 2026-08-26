export type WorkflowStatus =
  | "pending"
  | "running"
  | "completed"
  | "partial"
  | "failed"
  | "cancelled"
  | "interrupted";

export type StageStatus =
  | "pending"
  | "running"
  | "completed"
  | "failed"
  | "skipped"
  | "cancelled"
  | "interrupted";

export interface WorkflowError {
  code: string;
  message: string;
  retryable: boolean;
  stage_id?: string | null;
}

export interface StageResult {
  id: string;
  status: StageStatus;
  content?: string | null;
  truncated?: boolean;
  context_truncated?: boolean;
  started_at?: string | null;
  completed_at?: string | null;
  error?: WorkflowError | null;
}

export interface DossierSection {
  id: string;
  tool: string;
  title: string;
  empty_policy: "gap_if_empty" | "allow_no_record";
  status: "completed" | "no_record" | "gap" | "failed";
  summary: string;
  body: string;
  error?: string | null;
}

export interface WorkflowState {
  workflow_id?: string;
  workflow_status: WorkflowStatus;
  started_at?: string | null;
  completed_at?: string | null;
  config_version?: number;
  input?: Record<string, unknown>;
  variant?: string | null;
  dossier?: {
    sections: DossierSection[];
    summary: string;
    missing: string[];
    has_substantive_data: boolean;
  } | null;
  stages: Record<string, StageResult>;
  current_stage?: string | null;
  result?: string | null;
  result_summary?: string | null;
  errors?: WorkflowError[];
  event_seq?: number;
  event_run_id?: string;
}

interface EventBase {
  workflow_id: string;
  run_id: string;
  seq: number;
  emitted_at: string;
}

export interface WorkflowStatusEvent extends EventBase {
  type: "workflow.status";
  status: WorkflowStatus;
  message: string;
}

export interface DossierProgressEvent extends EventBase {
  type: "dossier.progress";
  section_id: string;
  section_status: "completed" | "no_record" | "gap" | "failed";
  completed: number;
  total: number;
}

export interface DossierReadyEvent extends EventBase {
  type: "dossier.ready";
  completed: number;
  missing: string[];
  has_substantive_data: boolean;
}

export interface StageStartedEvent extends EventBase {
  type: "stage.started";
  stage_id: string;
  label: string;
}

export interface StageDeltaEvent extends EventBase {
  type: "stage.delta";
  stage_id: string;
  delta: string;
}

export interface StageCompletedEvent extends EventBase {
  type: "stage.completed";
  stage_id: string;
  truncated: boolean;
}

export interface StageFailedEvent extends EventBase {
  type: "stage.failed";
  stage_id: string;
  error_code: string;
  message: string;
  retryable: boolean;
}

export interface WorkflowCompletedEvent extends EventBase {
  type: "workflow.completed";
  status: "completed" | "partial";
}

export interface WorkflowFailedEvent extends EventBase {
  type: "workflow.failed";
  error_code: string;
  message: string;
  retryable: boolean;
}

export type WorkflowEvent =
  | WorkflowStatusEvent
  | DossierProgressEvent
  | DossierReadyEvent
  | StageStartedEvent
  | StageDeltaEvent
  | StageCompletedEvent
  | StageFailedEvent
  | WorkflowCompletedEvent
  | WorkflowFailedEvent;

export type WorkflowEventParseResult =
  | { kind: "event"; event: WorkflowEvent }
  | { kind: "ignored"; type: string }
  | { kind: "error"; error: WorkflowError };

const EVENT_TYPES = new Set<WorkflowEvent["type"]>([
  "workflow.status", "dossier.progress", "dossier.ready", "stage.started",
  "stage.delta", "stage.completed", "stage.failed", "workflow.completed", "workflow.failed",
]);
const WORKFLOW_STATUSES = new Set<WorkflowStatus>([
  "pending", "running", "completed", "partial", "failed", "cancelled", "interrupted",
]);
const DOSSIER_STATUSES = new Set(["completed", "no_record", "gap", "failed"]);
const ISO_UTC = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/;
const COMMON_FIELDS = ["type", "workflow_id", "run_id", "seq", "emitted_at"];
const EVENT_FIELDS: Record<WorkflowEvent["type"], string[]> = {
  "workflow.status": ["status", "message"],
  "dossier.progress": ["section_id", "section_status", "completed", "total"],
  "dossier.ready": ["completed", "missing", "has_substantive_data"],
  "stage.started": ["stage_id", "label"],
  "stage.delta": ["stage_id", "delta"],
  "stage.completed": ["stage_id", "truncated"],
  "stage.failed": ["stage_id", "error_code", "message", "retryable"],
  "workflow.completed": ["status"],
  "workflow.failed": ["error_code", "message", "retryable"],
};

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);
const isString = (value: unknown): value is string => typeof value === "string";
const isNonEmptyString = (value: unknown): value is string => isString(value) && value.length > 0;
const isCount = (value: unknown): value is number => Number.isInteger(value) && Number(value) >= 0;

function malformed(message: string): WorkflowEventParseResult {
  return {
    kind: "error",
    error: { code: "MALFORMED_WORKFLOW_EVENT", message, retryable: true },
  };
}

export function parseWorkflowEvent(raw: unknown): WorkflowEventParseResult {
  if (!isRecord(raw) || !isString(raw.type)) return malformed("工作流事件格式无效");
  if (!EVENT_TYPES.has(raw.type as WorkflowEvent["type"])) {
    console.warn(`忽略未知工作流事件: ${raw.type}`);
    return { kind: "ignored", type: raw.type };
  }
  const allowed = new Set([...COMMON_FIELDS, ...EVENT_FIELDS[raw.type as WorkflowEvent["type"]]]);
  if (Object.keys(raw).some((field) => !allowed.has(field))) {
    return malformed(`工作流事件 ${raw.type} 包含未允许字段`);
  }
  if (!isNonEmptyString(raw.workflow_id) || !isNonEmptyString(raw.run_id)
    || !Number.isInteger(raw.seq) || Number(raw.seq) < 1
    || !isString(raw.emitted_at) || !ISO_UTC.test(raw.emitted_at)) {
    return malformed(`工作流事件 ${raw.type} 缺少有效公共字段`);
  }

  let valid = false;
  switch (raw.type) {
    case "workflow.status":
      valid = WORKFLOW_STATUSES.has(raw.status as WorkflowStatus) && isString(raw.message);
      break;
    case "dossier.progress":
      valid = isNonEmptyString(raw.section_id) && DOSSIER_STATUSES.has(String(raw.section_status))
        && isCount(raw.completed) && isCount(raw.total);
      break;
    case "dossier.ready":
      valid = isCount(raw.completed) && Array.isArray(raw.missing)
        && raw.missing.every(isString) && typeof raw.has_substantive_data === "boolean";
      break;
    case "stage.started":
      valid = isNonEmptyString(raw.stage_id) && isNonEmptyString(raw.label);
      break;
    case "stage.delta":
      valid = isNonEmptyString(raw.stage_id) && isString(raw.delta);
      break;
    case "stage.completed":
      valid = isNonEmptyString(raw.stage_id) && typeof raw.truncated === "boolean";
      break;
    case "stage.failed":
      valid = isNonEmptyString(raw.stage_id) && isNonEmptyString(raw.error_code)
        && isNonEmptyString(raw.message) && typeof raw.retryable === "boolean";
      break;
    case "workflow.completed":
      valid = raw.status === "completed" || raw.status === "partial";
      break;
    case "workflow.failed":
      valid = isNonEmptyString(raw.error_code) && isNonEmptyString(raw.message)
        && typeof raw.retryable === "boolean";
      break;
  }
  if (!valid) return malformed(`工作流事件 ${raw.type} 缺少有效必填字段`);
  return { kind: "event", event: raw as unknown as WorkflowEvent };
}

/** 与 backend/agent/workflows/*.yaml 的 config_version 一一对应；升级配置时同步更新。 */
export const WORKFLOW_CONFIG_VERSIONS: Record<string, number> = {
  debate: 1,
  reflection: 1,
  daily_review: 1,
  news_digest: 1,
};

export function effectiveWorkflowStatus(
  threadStatus: "idle" | "busy" | "interrupted" | "error",
  workflowStatus: WorkflowStatus,
): WorkflowStatus {
  if (threadStatus === "busy") return "running";
  if (workflowStatus !== "pending" && workflowStatus !== "running") return workflowStatus;
  if (threadStatus === "error") return "failed";
  if (threadStatus === "interrupted") return "interrupted";
  if (workflowStatus === "running" || workflowStatus === "pending") return "interrupted";
  return workflowStatus;
}
