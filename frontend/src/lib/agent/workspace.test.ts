import { afterEach, describe, expect, it, vi } from "vitest";

import { agentApi } from "./api";
import { createAgentWorkspaceStore } from "./workspace";
import type { AgentRecoveryWarning, AgentRunDetail, AgentRunListItem, AgentThread } from "./types";

const run = (id: string, threadId = "thread-1", controlRevision = 0): AgentRunDetail => ({
  schema_version: 1,
  id,
  thread_id: threadId,
  protocol_run_ids: [],
  trigger_message_id: "message-1",
  retry_of: null,
  status: "completed",
  started_at: "2026-08-18T00:00:00Z",
  updated_at: "2026-08-18T00:00:00Z",
  ended_at: null,
  elapsed_ms: 0,
  active_elapsed_ms: 0,
  approval_wait_ms: 0,
  budget_snapshot: {},
  control_revision: controlRevision,
  context_truncation: { occurred: false, original_chars: null, retained_chars: null, removed_turns: null },
  model_ref: { provider: "test", baseURL: "https://example.test/v1", model: "test" },
  history_head_id: null,
  usage: { model_calls: 0, tool_calls: 0, input_tokens: null, output_tokens: null, total_tokens: null, token_status: "unavailable" },
  tool_summaries: [],
  sources: [],
  sources_truncated: false,
  error_code: null,
  error_message: null,
});

const thread = (lastRunId: string | null): AgentThread => ({
  schema_version: 1,
  id: "thread-1",
  title: "Thread",
  created_at: "2026-08-18T00:00:00Z",
  updated_at: "2026-08-18T00:00:00Z",
  revision: 0,
  selected_skills: [],
  messages: [],
  artifact_ids: [],
  last_run: lastRunId ? { id: lastRunId, status: "completed", updated_at: "2026-08-18T00:00:00Z", retry_of: null } : null,
});

const runItem = (id: string): AgentRunListItem => ({
  id,
  status: "completed",
  started_at: "2026-08-18T00:00:00Z",
  updated_at: "2026-08-18T00:00:00Z",
  ended_at: null,
  retry_of: null,
  error_code: null,
});

describe("Agent workspace", () => {
  it("keeps a historical selected run per thread and falls back to last_run", () => {
    const store = createAgentWorkspaceStore();
    store.getState().selectRun("thread-1", "run-old");
    store.getState().selectRun("thread-2", "run-other");

    expect(store.getState().selectedRunId(thread("run-new"))).toBe("run-old");
    expect(store.getState().selectedRunId({ ...thread("run-new"), id: "thread-3" })).toBe("run-new");
    store.getState().selectRun("thread-1", null);
    expect(store.getState().selectedRunId(thread(null))).toBeNull();
  });

  it("drops a missing historical selection when the run list reloads", () => {
    const store = createAgentWorkspaceStore();
    store.getState().selectRun("thread-1", "run-old");
    store.getState().replaceRunList("thread-1", [runItem("run-new")]);

    expect(store.getState().selectedRunId(thread("run-new"))).toBe("run-new");
  });

  it("uses mutually exclusive drawers and keeps tab state in memory", () => {
    const store = createAgentWorkspaceStore();
    store.getState().openDrawer("threads");
    store.getState().openDrawer("settings");
    store.getState().setTab("artifacts");

    expect(store.getState().drawer).toBe("settings");
    expect(store.getState().tab).toBe("artifacts");
  });

  it("never writes workspace UI state to localStorage", () => {
    const store = createAgentWorkspaceStore();
    localStorage.setItem("workspace-sentinel", "keep");
    store.getState().selectRun("thread-1", "run-1");
    store.getState().openDrawer("inspector");

    expect(localStorage.getItem("workspace-sentinel")).toBe("keep");
    expect(localStorage.length).toBe(1);
  });

  it("REST detail replacement advances only matching run control watermarks", () => {
    const store = createAgentWorkspaceStore();
    store.getState().replaceRunDetail(run("run-1", "thread-1", 4));

    expect(store.getState().watermark("budget.updated", "thread-1", "run-1")).toBe(4);
    expect(store.getState().watermark("sources.updated", "thread-1", "run-1")).toBe(4);
    expect(store.getState().watermark("budget.updated", "thread-1", "run-2")).toBe(0);
  });

  it("invalidates only the relevant run when an event control revision has a gap", () => {
    const store = createAgentWorkspaceStore();
    store.getState().replaceRunDetail(run("run-1", "thread-1", 1));
    store.getState().replaceRunDetail(run("run-2", "thread-1", 1));
    store.getState().applyEvent({
      name: "sources.updated",
      value: { threadId: "thread-1", runId: "run-1", controlRevision: 3, sourceCount: 2, sourcesTruncated: false },
    });

    expect(store.getState().isRunStale("run-1")).toBe(true);
    expect(store.getState().isRunStale("run-2")).toBe(false);
    expect(store.getState().watermark("budget.updated", "thread-1", "run-1")).toBe(1);
  });

  it("keeps event kinds independent for the same run", () => {
    const store = createAgentWorkspaceStore();
    store.getState().replaceRunDetail(run("run-1", "thread-1", 1));
    store.getState().applyEvent({
      name: "budget.updated",
      value: {
        threadId: "thread-1", runId: "run-1", controlRevision: 2,
        budgetSnapshot: { policy_revision: 1, max_model_calls: 8, max_tool_calls: 16, tool_timeout_seconds: 30, max_active_seconds: 300, max_context_chars: 120000 },
        usage: { model_calls: 1, tool_calls: 0, input_tokens: null, output_tokens: null, total_tokens: null, token_status: "unavailable" },
        activeElapsedMs: 1,
        contextTruncation: { occurred: false, original_chars: null, retained_chars: null, removed_turns: null },
      },
    });

    expect(store.getState().watermark("budget.updated", "thread-1", "run-1")).toBe(2);
    expect(store.getState().watermark("sources.updated", "thread-1", "run-1")).toBe(1);
    expect(store.getState().isRunStale("run-1")).toBe(false);
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

it("uses the exact run REST shape and only derives artifact download names from Content-Disposition", async () => {
  const requests: Array<{ url: string; init?: RequestInit }> = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    requests.push({ url, init });
    if (url.includes("/download")) {
      return new Response("artifact", {
        headers: {
          "Content-Type": "application/json; charset=utf-8",
          "Content-Disposition": 'attachment; filename="artifact-1.json"',
        },
      });
    }
    return new Response(JSON.stringify({ runs: [], next_before: null, warnings: [] }), {
      headers: { "content-type": "application/json" },
    });
  }));

  await agentApi.listRuns("thread /1", 25, "run /1");
  const download = await agentApi.downloadArtifact("thread /1", "artifact /1");

  expect(requests[0]?.url).toBe("/api/agent/threads/thread%20%2F1/runs?limit=25&before=run%20%2F1");
  expect(requests[0]?.init?.method).toBe("GET");
  expect(download.filename).toBe("artifact-1.json");
  expect(download.filename).not.toContain("title");
});

it("rejects artifact downloads without the fixed media type and attachment disposition", async () => {
  vi.stubGlobal("fetch", vi.fn()
    .mockResolvedValueOnce(new Response("<html>", {
      headers: { "Content-Type": "text/html", "Content-Disposition": 'attachment; filename="report.html"' },
    }))
    .mockResolvedValueOnce(new Response("artifact", {
      headers: { "Content-Type": "application/json; charset=utf-8", "Content-Disposition": 'inline; filename="report.json"' },
    })));

  await expect(agentApi.downloadArtifact("thread-1", "artifact-1")).rejects.toMatchObject({
    code: "ARTIFACT_DOWNLOAD_INVALID",
  });
  await expect(agentApi.downloadArtifact("thread-1", "artifact-1")).rejects.toMatchObject({
    code: "ARTIFACT_DOWNLOAD_INVALID",
  });
});

it("supports backend artifact recovery warnings", () => {
  const warning: AgentRecoveryWarning = {
    code: "ARTIFACT_ORPHAN",
    document_type: "artifact",
    filename: "thread-1/artifact-1.json.corrupt-1",
  };

  expect(warning.document_type).toBe("artifact");
});
