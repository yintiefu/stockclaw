/** Agent REST 文档类型 —— 字段名与后端线格式严格一致（snake_case）。 */

export type AgentRunStatus =
  | "running"
  | "awaiting_approval"
  | "completed"
  | "failed"
  | "cancelled"
  | "interrupted";

export type AgentRunSummary = {
  id: string;
  status: AgentRunStatus;
  updated_at: string;
  retry_of: string | null;
};

export type AgentMessage = {
  id: string;
  role: "user" | "assistant" | "tool";
  content: unknown;
  partial: boolean;
  pending_interrupt: boolean;
  interrupts: Record<string, unknown>[];
  tool_calls: Record<string, unknown>[];
  tool_call_id: string | null;
  created_at: string | null;
};

export type AgentThread = {
  schema_version: 1;
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  revision: number;
  selected_skills: string[];
  messages: AgentMessage[];
  artifact_ids: string[];
  last_run: AgentRunSummary | null;
};

export type AgentThreadSummary = {
  id: string;
  title: string;
  updated_at: string;
  revision: number;
  last_run: AgentRunSummary | null;
};

export type AgentRecoveryWarning = {
  code: "DOCUMENT_CORRUPT";
  document_type: "thread" | "run";
  filename: string;
};

export type AgentThreadListResponse = {
  threads: AgentThreadSummary[];
  warnings: AgentRecoveryWarning[];
};

export type AgentUsage = {
  model_calls: number;
  tool_calls: number;
  input_tokens: number | null;
  output_tokens: number | null;
};

export type AgentRun = {
  schema_version: 1;
  id: string;
  thread_id: string;
  protocol_run_ids: string[];
  trigger_message_id: string;
  retry_of: string | null;
  status: AgentRunStatus;
  started_at: string;
  updated_at: string;
  ended_at: string | null;
  elapsed_ms: number;
  active_elapsed_ms: number;
  approval_wait_ms: number;
  budget_snapshot: Record<string, unknown>;
  model_ref: { provider: string; baseURL?: string; base_url?: string; model: string };
  history_head_id: string | null;
  usage: AgentUsage;
  tool_summaries: Record<string, unknown>[];
  error_code: string | null;
  error_message: string | null;
};

/** 结构化 409 冲突体（snake_case 线格式；展示名只在组件边界映射）。 */
export type AgentConflict = {
  code?: string;
  detail?: string;
  thread_id?: string | null;
  product_run_id?: string | null;
  status?: string | null;
};
