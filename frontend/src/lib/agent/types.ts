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
  /** 计算字段：仅 thread detail 响应携带，不持久化。 */
  resume_available?: boolean;
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
  total_tokens: number | null;
  token_status: "available" | "partial" | "unavailable";
};

export type AgentPolicySnapshot = {
  policy_revision: number;
  max_model_calls: number;
  max_tool_calls: number;
  tool_timeout_seconds: number;
  max_active_seconds: number;
  max_context_chars: number;
};

export type AgentPolicy = {
  schema_version: 1;
  revision: number;
  updated_at: string | null;
  persisted: boolean;
  max_model_calls: number;
  max_tool_calls: number;
  tool_timeout_seconds: number;
  max_active_seconds: number;
  max_context_chars: number;
};

export type AgentPolicyPatch = Pick<AgentPolicy, "revision"> & Partial<
  Pick<AgentPolicy, "max_model_calls" | "max_tool_calls" | "tool_timeout_seconds" | "max_active_seconds" | "max_context_chars">
>;

export type AgentPolicyReset = { revision: number } | { confirm_corrupt: true };

export type ContextTruncation = {
  occurred: boolean;
  original_chars: number | null;
  retained_chars: number | null;
  removed_turns: number | null;
};

export type ToolExecutionSource = {
  id: string;
  kind: "tool_execution";
  tool_call_id: string;
  tool_name: string;
  origin: "builtin" | "skill" | "mcp" | "artifact";
  completed_at: string;
  arguments_summary: string;
  result_summary: string;
  verification: "executed_record";
};

export type ModelUrlSource = {
  id: string;
  kind: "model_url";
  url: string;
  label: string | null;
  created_at: string;
  verification: "model_provided_unverified";
};

export type Source = ToolExecutionSource | ModelUrlSource;
export type AgentSource = Source;

export type AgentRunDetail = {
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
  budget_snapshot: AgentPolicySnapshot | Record<string, never>;
  control_revision: number;
  context_truncation: ContextTruncation;
  model_ref: { provider: string; baseURL: string; model: string };
  history_head_id: string | null;
  usage: AgentUsage;
  tool_summaries: Record<string, unknown>[];
  sources: AgentSource[];
  sources_truncated: boolean;
  error_code: string | null;
  error_message: string | null;
};

/** 兼容既有调用；REST /runs/{id} 的完整线格式。 */
export type AgentRun = AgentRunDetail;

export type AgentRunListItem = {
  id: string;
  status: AgentRunStatus;
  started_at: string;
  updated_at: string;
  ended_at: string | null;
  retry_of: string | null;
  error_code: string | null;
};

export type AgentRunListCursors = {
  next_before: string | null;
};

export type AgentRunListResponse = AgentRunListCursors & {
  runs: AgentRunListItem[];
  warnings: AgentRecoveryWarning[];
};

export type ArtifactType = "markdown" | "table" | "json" | "sources";

export type ArtifactMetadata = {
  id: string;
  thread_id: string;
  run_id: string;
  type: ArtifactType;
  title: string;
  created_at: string;
  parent_artifact_id: string | null;
  source_count: number;
  has_children?: boolean;
};

export type ArtifactMarkdownContent = { markdown: string };
export type ArtifactTableContent = {
  columns: Array<{ key: string; label: string }>;
  rows: Array<Record<string, string | number | boolean | null>>;
};
export type ArtifactJsonContent = { value: unknown };
export type ArtifactSourcesContent = { items: Array<{ source_id: string; note: string | null }> };
export type ArtifactContent = ArtifactMarkdownContent | ArtifactTableContent | ArtifactJsonContent | ArtifactSourcesContent;

type ArtifactDetailBase = Omit<ArtifactMetadata, "source_count" | "has_children" | "type"> & {
  schema_version: 1;
  source_ids: string[];
};

export type ArtifactDetail =
  | (ArtifactDetailBase & { type: "markdown"; content: ArtifactMarkdownContent })
  | (ArtifactDetailBase & { type: "table"; content: ArtifactTableContent })
  | (ArtifactDetailBase & { type: "json"; content: ArtifactJsonContent })
  | (ArtifactDetailBase & { type: "sources"; content: ArtifactSourcesContent });

export type ArtifactListResponse = {
  artifacts: ArtifactMetadata[];
  warnings: AgentRecoveryWarning[];
};

export type ArtifactDownload = { blob: Blob; filename: string | null };

export type AgentManagementError = AgentConflict & {
  code?: "POLICY_INVALID" | "POLICY_CORRUPT" | "POLICY_REVISION_CONFLICT" | "ARTIFACT_INVALID" | "ARTIFACT_NOT_FOUND" | "ARTIFACT_NOT_IN_THREAD" | "ARTIFACT_HAS_CHILD" | "ARTIFACT_DELETE_FAILED" | string;
  current_revision?: number;
};

export type ThreadRevisionUpdatedEvent = {
  name: "thread.revision.updated";
  value: { threadId: string; revision: number; persistedAt?: string };
};

export type BudgetUpdatedEvent = {
  name: "budget.updated";
  value: {
    threadId: string;
    runId: string;
    controlRevision: number;
    budgetSnapshot: AgentPolicySnapshot;
    usage: AgentUsage;
    activeElapsedMs: number;
    contextTruncation: ContextTruncation;
  };
};

export type ArtifactCreatedEvent = {
  name: "artifact.created";
  value: { threadId: string; runId: string; artifactId: string; type: ArtifactType; title: string; threadRevision: number };
};

export type SourcesUpdatedEvent = {
  name: "sources.updated";
  value: { threadId: string; runId: string; controlRevision: number; sourceCount: number; sourcesTruncated: boolean };
};

export type AgentStreamEvent = ThreadRevisionUpdatedEvent | BudgetUpdatedEvent | ArtifactCreatedEvent | SourcesUpdatedEvent;

/** 结构化 409 冲突体（snake_case 线格式；展示名只在组件边界映射）。 */
export type AgentConflict = {
  code?: string;
  detail?: string;
  thread_id?: string | null;
  product_run_id?: string | null;
  status?: string | null;
};

/** Task 1C：Skill 管理类型（与后端 /api/agent/skills 线格式一致）。 */
export type SkillFile = {
  relative_path: string;
  category: "skill" | "reference" | "asset" | "script" | "other";
  size: number;
  mtime_ns: number;
  sha256: string;
  mime: string | null;
  downloadable: boolean;
};

export type SkillSummary = {
  directory: string;
  name: string | null;
  description: string | null;
  digest: string | null;
  valid: boolean;
  error_code: string | null;
  error_detail: string | null;
};

export type SkillDetail = SkillSummary & {
  instructions: string | null;
  files: SkillFile[];
};

export type SkillListResponse = {
  generation: number;
  skills: SkillSummary[];
};

export type SkillImportResult = {
  record: SkillDetail;
  created: boolean;
};

/** Task 1C 切片 2：MCP 管理类型。 */
export type McpEnvReference = { from_env: string };

export type McpStdioTransport = {
  type: "stdio";
  executable: string;
  args: string[];
  env: Record<string, McpEnvReference>;
};

export type McpHttpTransport = {
  type: "streamable_http";
  url: string;
  headers: Record<string, McpEnvReference>;
};

export type McpToolEntry = {
  original_name: string;
  alias: string;
  description: string;
  input_schema: Record<string, unknown>;
  enabled: boolean;
  discovered_at: string;
};

export type McpServer = {
  id: string;
  display_name: string;
  enabled: boolean;
  transport: McpStdioTransport | McpHttpTransport;
  trust_fingerprint: string | null;
  trusted_at: string | null;
  tools: McpToolEntry[];
  health: { state: "unknown" | "ok" | "unreachable" | "error"; detail: string; checked_at: string };
};

export type McpDocument = {
  schema_version: 1;
  revision: number;
  servers: McpServer[];
  recovery_warnings?: string[];
};

export type StdioTrustPreview = {
  executable: string;
  resolved_executable: string;
  args: string[];
  fingerprint: string;
};

/** Task 1C：审批交互类型。 */
export type McpInterruptMetadata = {
  source: "mcp";
  serverId: string;
  serverName: string;
  toolName: string;
  toolAlias: string;
  arguments: Record<string, unknown>;
};

export type AgentInterruptPayload = {
  id: string;
  reason?: string;
  message?: string;
  toolCallId?: string;
  responseSchema?: Record<string, unknown>;
} & Partial<McpInterruptMetadata>;

export type ApprovalDecision = {
  id: string;
  decision: "approve" | "reject";
  scope: "once" | "thread_session";
};
