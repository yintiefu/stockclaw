// NDJSON 事件类型（spec §7）
export type AgentEventType =
  | "text_delta"
  | "tool_trace"
  | "decision_artifact"
  | "chart_artifact"
  | "table_artifact"
  | "citations"
  | "done"
  | "error";

export interface TextDeltaEvent {
  type: "text_delta";
  text: string;
}

export interface ToolTraceEvent {
  type: "tool_trace";
  tool: string;
  status: "running" | "ok" | "error";
  args: Record<string, unknown>;
  summary?: string;
}

// 决策卡 basis_type 4 档色标（spec §6 约束 3）
export type BasisType = "model" | "model_fallback" | "llm_reasoning" | "hybrid";

export interface CadenceBatch {
  batch: number;
  pct: number;
  trigger: string;
  price?: number;
  amount?: number;
  ref_price?: number;
}

export interface DecisionCardData {
  code: string;
  name: string;
  current_price: number;
  target_price: number;
  entry_low: number;
  entry_high: number;
  stop_loss: number;
  take_profit: number;
  cadence: CadenceBatch[];
  basis_type: BasisType;
  model_versions_json: Record<string, string>;
  assumptions: string[];
  citations: { source: string; code?: string; range?: string; note?: string }[];
  explanation: string;
}

export interface DecisionArtifactEvent {
  type: "decision_artifact";
  decision_id: string;
  data: DecisionCardData;
}

export interface CitationsEvent {
  type: "citations";
  items: { source: string; code?: string; range?: string }[];
}

export interface DoneEvent {
  type: "done";
  summary: { thread_id?: string; rounds?: number; failed?: boolean };
}

export interface ErrorEvent {
  type: "error";
  message: string;
  code?: string;
}

export type AgentEvent =
  | TextDeltaEvent
  | ToolTraceEvent
  | DecisionArtifactEvent
  | CitationsEvent
  | DoneEvent
  | ErrorEvent;

// API 请求体
export interface AgentChatReq {
  thread_id: string | null;
  messages: { role: string; content: string }[];
  context_codes: string[];
  llm: { provider: string; baseURL: string; apiKey: string; model: string };
  style: "conservative" | "balanced" | "aggressive";
}

// 会话
export interface AgentThread {
  id: string;
  title: string;
  model: string;
  created_at: number;
  updated_at: number;
}

// 渲染用消息
export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  toolTraces: ToolTraceEvent[];
  decisionCard?: DecisionCardData;
  citations?: { source: string; code?: string }[];
  streaming?: boolean;
}
