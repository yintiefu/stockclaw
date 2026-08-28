// 工作流 v2 状态契约：阶段权威正文只住在 state.messages（add_messages 按 id 归并），
// StageResult 是状态机 + message_id 指针；custom 通道只剩底稿进度（可丢弃提示）。
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
  /** 权威正文在 state.messages 里的指针。 */
  message_id?: string | null;
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
  resume?: boolean;
  variant?: string | null;
  dossier?: {
    sections: DossierSection[];
    summary: string;
    missing: string[];
    has_substantive_data: boolean;
  } | null;
  stages: Record<string, StageResult>;
  /** 阶段正文唯一载体（add_messages 按 id 归并）。 */
  messages?: unknown[];
  current_stage?: string | null;
  result_summary?: string | null;
  errors?: WorkflowError[];
}

export function effectiveWorkflowStatus(
  threadStatus: "idle" | "busy" | "interrupted" | "error",
  workflowStatus: WorkflowStatus,
): WorkflowStatus {
  // 终态优先：restoredStatus 只在 restore 时读一次服务端，之后不再更新——
  // 陈旧 busy 不得遮蔽 values 里的权威终态。
  if (workflowStatus !== "pending" && workflowStatus !== "running") return workflowStatus;
  if (threadStatus === "busy") return "running";
  if (threadStatus === "error") return "failed";
  if (threadStatus === "interrupted") return "interrupted";
  // 到这里 workflowStatus 只能是 pending/running 且服务端视角已停（idle）。
  return "interrupted";
}

const isRecord = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

export function messageText(message: unknown): string {
  if (!isRecord(message)) return "";
  const content = message.content;
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .map((b) => (isRecord(b) && typeof b.text === "string" ? b.text : ""))
      .join("");
  }
  return "";
}

export function stageContent(state: WorkflowState, stageId: string): string | null {
  const stage = state.stages?.[stageId];
  if (!stage?.message_id) return null;
  for (const m of state.messages ?? []) {
    if (isRecord(m) && m.id === stage.message_id) return messageText(m);
  }
  return null;
}

export interface DossierProgressEvent {
  type: "dossier.progress";
  section_id: string;
  section_status: "completed" | "no_record" | "gap" | "failed";
  completed: number;
  total: number;
}

const DOSSIER_STATUSES = new Set(["completed", "no_record", "gap", "failed"]);

export function parseDossierProgress(payload: unknown): DossierProgressEvent | null {
  if (!isRecord(payload) || payload.type !== "dossier.progress") return null;
  const { section_id, section_status, completed, total } = payload;
  if (typeof section_id !== "string" || !section_id
    || typeof section_status !== "string" || !DOSSIER_STATUSES.has(section_status)
    || !Number.isInteger(completed) || !Number.isInteger(total)) return null;
  return {
    type: "dossier.progress",
    section_id,
    section_status: section_status as DossierProgressEvent["section_status"],
    completed: completed as number,
    total: total as number,
  };
}
