import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { AgentRunDetail } from "@/lib/agent/types";
import { RunInspector } from "./RunInspector";

const runDetail = (patch: Partial<AgentRunDetail> = {}): AgentRunDetail => ({
  schema_version: 1,
  id: "run-1",
  thread_id: "th-1",
  protocol_run_ids: [],
  trigger_message_id: "message-1",
  retry_of: null,
  status: "failed",
  started_at: "2026-08-19T00:00:00Z",
  updated_at: "2026-08-19T00:00:12Z",
  ended_at: "2026-08-19T00:00:12Z",
  elapsed_ms: 12_000,
  active_elapsed_ms: 8_000,
  approval_wait_ms: 4_000,
  budget_snapshot: {
    policy_revision: 3,
    max_model_calls: 8,
    max_tool_calls: 16,
    tool_timeout_seconds: 30,
    max_active_seconds: 300,
    max_context_chars: 120_000,
  },
  control_revision: 7,
  context_truncation: {
    occurred: true,
    original_chars: 140_000,
    retained_chars: 118_000,
    removed_turns: 3,
  },
  model_ref: { provider: "openai-compatible", baseURL: "https://private.invalid/v1", model: "model-factual" },
  history_head_id: "message-9",
  usage: {
    model_calls: 3,
    tool_calls: 5,
    input_tokens: 1200,
    output_tokens: 300,
    total_tokens: 1500,
    token_status: "partial",
  },
  tool_summaries: [],
  sources: [],
  sources_truncated: false,
  error_code: "MODEL_TIMEOUT",
  error_message: "Provider 请求超时",
  ...patch,
});

afterEach(cleanup);

describe("RunInspector", () => {
  it("shows factual status, timing, reservations, provider tokens, truncation and terminal error", () => {
    render(<RunInspector run={runDetail()} />);

    expect(screen.getByText(/failed/)).toBeInTheDocument();
    expect(screen.getByText("8 秒")).toBeInTheDocument();
    expect(screen.getByText("4 秒")).toBeInTheDocument();
    expect(screen.getByText("12 秒")).toBeInTheDocument();
    expect(screen.getByText("3 / 8")).toBeInTheDocument();
    expect(screen.getByText("5 / 16")).toBeInTheDocument();
    expect(screen.getByText(/partial/)).toBeInTheDocument();
    expect(screen.getByText("1,200")).toBeInTheDocument();
    expect(screen.getByText("300")).toBeInTheDocument();
    expect(screen.getByText("1,500")).toBeInTheDocument();
    expect(screen.getByText("118,000")).toBeInTheDocument();
    expect(screen.getByText("3 轮")).toBeInTheDocument();
    expect(screen.getByText("MODEL_TIMEOUT")).toBeInTheDocument();
    expect(screen.getByText("Provider 请求超时")).toBeInTheDocument();
    expect(screen.getByText("openai-compatible · model-factual")).toBeInTheDocument();
  });

  it("marks legacy telemetry and unavailable provider usage without inventing values", () => {
    render(<RunInspector run={runDetail({
      status: "completed",
      budget_snapshot: {},
      context_truncation: { occurred: false, original_chars: null, retained_chars: null, removed_turns: null },
      usage: {
        model_calls: 2,
        tool_calls: 1,
        input_tokens: null,
        output_tokens: null,
        total_tokens: null,
        token_status: "unavailable",
      },
      error_code: null,
      error_message: null,
    })} />);

    expect(screen.getByText("旧版运行未记录预算快照")).toBeInTheDocument();
    expect(screen.getByText("2 / 未记录")).toBeInTheDocument();
    expect(screen.getByText("1 / 未记录")).toBeInTheDocument();
    expect(screen.getByText(/unavailable/)).toBeInTheDocument();
    expect(screen.getAllByText("未提供")).toHaveLength(3);
    expect(screen.queryByText("MODEL_TIMEOUT")).toBeNull();
  });

  it("does not render monetary estimates, model endpoint, or removed private text", () => {
    const { container } = render(<RunInspector run={runDetail()} />);
    const text = container.textContent ?? "";

    expect(text).not.toMatch(/费用|成本|cost|price/i);
    expect(text).not.toContain("https://private.invalid/v1");
    expect(text).not.toMatch(/被省略.*原文|omitted text/i);
  });
});
