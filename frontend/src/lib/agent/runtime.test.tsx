import { afterEach, expect, it, vi } from "vitest";
import type { RunAgentInput } from "@ag-ui/client";

import { AgentHttpAgent } from "./runtime";
import { loadAgentModelConfig, saveAgentModelConfig } from "./model-config";

const INPUT: RunAgentInput = {
  threadId: "thread-1",
  runId: "run-1",
  state: {},
  messages: [{ id: "user-1", role: "user", content: "hello" }],
  tools: [],
  context: [],
  forwardedProps: {},
};

const CONFIG = {
  provider: "deepseek",
  baseURL: "https://api.deepseek.com/v1",
  model: "deepseek-chat",
  apiKey: "model-secret",
};

function sseStream() {
  return new Response('data: {"type":"RUN_FINISHED","threadId":"thread-1","runId":"run-1"}\n\n', {
    status: 200,
    headers: { "content-type": "text/event-stream" },
  });
}

afterEach(() => {
  localStorage.clear();
  vi.unstubAllGlobals();
});

it("puts the model key only in headers and sends a sanitized model ref", async () => {
  localStorage.setItem("vr-access-key", "backend-access");
  let captured: RequestInit | undefined;
  vi.stubGlobal("fetch", vi.fn(async (_url, init) => {
    captured = init;
    return sseStream();
  }));
  const agent = new AgentHttpAgent(CONFIG, vi.fn());
  await new Promise<void>((resolve, reject) => {
    agent.run(INPUT).subscribe({ complete: resolve, error: reject });
  });
  const headers = new Headers(captured?.headers);
  expect(headers.get("Authorization")).toBe("Bearer backend-access");
  expect(headers.get("X-VR-Agent-Model-Key")).toBe("model-secret");
  const body = JSON.parse(String(captured?.body));
  expect(body.forwardedProps.runtime.model).toEqual({
    provider: "deepseek",
    baseURL: "https://api.deepseek.com/v1",
    model: "deepseek-chat",
  });
  expect(JSON.stringify(body)).not.toContain("model-secret");
  expect(body.forwardedProps.runtime.retryOf).toBeUndefined();
});

it("invokes onConflict exactly once on structured 409", async () => {
  const conflicts: unknown[] = [];
  vi.stubGlobal("fetch", vi.fn(async () => new Response('{"code":"THREAD_BUSY"}', {
    status: 409,
    headers: { "content-type": "application/json" },
  })));
  const agent = new AgentHttpAgent(CONFIG, (value) => conflicts.push(value));
  await new Promise<void>((resolve) => {
    agent.run(INPUT).subscribe({ complete: resolve, error: () => resolve() });
  });
  await new Promise((r) => setTimeout(r, 0));
  expect(conflicts).toEqual([{ code: "THREAD_BUSY" }]);
});

it("stores model config under vr-agent-model and never vr-llm", () => {
  saveAgentModelConfig(CONFIG);
  expect(localStorage.getItem("vr-agent-model")).toContain("deepseek-chat");
  expect(localStorage.getItem("vr-llm")).toBeNull();
  const loaded = loadAgentModelConfig();
  expect(loaded).toEqual(CONFIG);
  saveAgentModelConfig({ provider: "", baseURL: "", model: "", apiKey: "" });
  expect(loadAgentModelConfig()).toBeNull();
});

it("surfaces in-stream RUN_ERROR via onRunError", async () => {
  const runErrors: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async () => new Response(
    'data: {"type":"RUN_STARTED","threadId":"thread-1","runId":"run-1"}\n\ndata: {"type":"RUN_ERROR","message":"上游余额不足","threadId":"thread-1","runId":"run-1"}\n\n',
    { status: 200, headers: { "content-type": "text/event-stream" } },
  )));
  const agent = new AgentHttpAgent(CONFIG, vi.fn(), (message) => runErrors.push(message));
  await new Promise<void>((resolve) => {
    agent.run(INPUT).subscribe({ complete: resolve, error: () => resolve() });
  });
  await new Promise((r) => setTimeout(r, 10));
  expect(runErrors).toContain("上游余额不足");
});

it("armed retry sends retryOf, empty messages and the current revision", async () => {
  let captured: RequestInit | undefined;
  vi.stubGlobal("fetch", vi.fn(async (_url, init) => {
    captured = init;
    return sseStream();
  }));
  const agent = new AgentHttpAgent(CONFIG, vi.fn(), vi.fn(), {
    getRevision: () => 7,
    onRevision: vi.fn(),
  });
  agent.armRetry("run-old");
  await new Promise<void>((resolve, reject) => {
    agent.run(INPUT).subscribe({ complete: resolve, error: reject });
  });
  const body = JSON.parse(String(captured?.body));
  expect(body.messages).toEqual([]);
  expect(body.forwardedProps.runtime.retryOf).toBe("run-old");
  expect(body.forwardedProps.runtime.threadRevision).toBe(7);
});

it("intercepts thread.revision.updated CUSTOM events monotonically", async () => {
  const revisions: Array<[string, number]> = [];
  vi.stubGlobal("fetch", vi.fn(async () => new Response(
    'data: {"type":"CUSTOM","name":"thread.revision.updated","value":"{\\"threadId\\":\\"thread-1\\",\\"revision\\":2}"}\n\n'
      + 'data: {"type":"CUSTOM","name":"thread.revision.updated","value":"{\\"threadId\\":\\"thread-1\\",\\"revision\\":1}"}\n\n'
      + 'data: {"type":"RUN_FINISHED","threadId":"thread-1","runId":"run-1"}\n\n',
    { status: 200, headers: { "content-type": "text/event-stream" } },
  )));
  const agent = new AgentHttpAgent(CONFIG, vi.fn(), vi.fn(), {
    onRevision: (threadId, revision) => revisions.push([threadId, revision]),
  });
  await new Promise<void>((resolve, reject) => {
    agent.run(INPUT).subscribe({ complete: resolve, error: reject });
  });
  await new Promise((r) => setTimeout(r, 10));
  expect(revisions).toEqual([["thread-1", 2], ["thread-1", 1]]);
});

it("scans each persisted custom event without consuming the runtime stream", async () => {
  const events: string[] = [];
  const streamEnd = vi.fn();
  vi.stubGlobal("fetch", vi.fn(async () => new Response(
    'data: {"type":"CUSTOM","name":"thread.revision.updated","value":"{\\"threadId\\":\\"thread-1\\",\\"revision\\":2}"}\n\n'
      + 'data: {"type":"CUSTOM","name":"budget.updated","value":"{\\"threadId\\":\\"thread-1\\",\\"runId\\":\\"run-1\\",\\"controlRevision\\":1,\\"budgetSnapshot\\":{\\"policy_revision\\":0,\\"max_model_calls\\":8,\\"max_tool_calls\\":16,\\"tool_timeout_seconds\\":30,\\"max_active_seconds\\":300,\\"max_context_chars\\":120000},\\"usage\\":{\\"model_calls\\":0,\\"tool_calls\\":0,\\"input_tokens\\":null,\\"output_tokens\\":null,\\"total_tokens\\":null,\\"token_status\\":\\"unavailable\\"},\\"activeElapsedMs\\":0,\\"contextTruncation\\":{\\"occurred\\":false,\\"original_chars\\":null,\\"retained_chars\\":null,\\"removed_turns\\":null}}"}\n\n'
      + 'data: {"type":"CUSTOM","name":"artifact.created","value":"{\\"threadId\\":\\"thread-1\\",\\"runId\\":\\"run-1\\",\\"artifactId\\":\\"artifact-1\\",\\"type\\":\\"markdown\\",\\"title\\":\\"facts\\",\\"threadRevision\\":2}"}\n\n'
      + 'data: {"type":"CUSTOM","name":"sources.updated","value":"{\\"threadId\\":\\"thread-1\\",\\"runId\\":\\"run-1\\",\\"controlRevision\\":1,\\"sourceCount\\":1,\\"sourcesTruncated\\":false}"}\n\n'
      + 'data: {"type":"RUN_FINISHED","threadId":"thread-1","runId":"run-1"}\n\n',
    { status: 200, headers: { "content-type": "text/event-stream" } },
  )));
  const agent = new AgentHttpAgent(CONFIG, vi.fn(), vi.fn(), {
    onEvent: (event) => events.push(event.name),
    onStreamEnd: streamEnd,
  });
  await new Promise<void>((resolve, reject) => {
    agent.run(INPUT).subscribe({ complete: resolve, error: reject });
  });
  await new Promise((resolve) => setTimeout(resolve, 10));

  expect(events).toEqual([
    "thread.revision.updated", "budget.updated", "artifact.created", "sources.updated",
  ]);
  expect(streamEnd).toHaveBeenCalledWith("thread-1", "run-1");
});

it("invalidates REST only for malformed persisted custom events", async () => {
  const invalidated: Array<[string, string]> = [];
  vi.stubGlobal("fetch", vi.fn(async () => new Response(
    'data: {"type":"CUSTOM","name":"sources.updated","value":"{\\"threadId\\":\\"thread-1\\",\\"runId\\":false,\\"controlRevision\\":1}"}\n\n'
      + 'data: {"type":"RUN_FINISHED","threadId":"thread-1","runId":"run-1"}\n\n',
    { status: 200, headers: { "content-type": "text/event-stream" } },
  )));
  const agent = new AgentHttpAgent(CONFIG, vi.fn(), vi.fn(), {
    onInvalidate: (threadId, runId) => invalidated.push([threadId, runId]),
  });
  await new Promise<void>((resolve, reject) => {
    agent.run(INPUT).subscribe({ complete: resolve, error: reject });
  });
  await new Promise((resolve) => setTimeout(resolve, 10));

  expect(invalidated).toEqual([["thread-1", "run-1"]]);
});

it("carries request identity through malformed SSE and stream end invalidation", async () => {
  const invalidated: Array<[string, string]> = [];
  const ended: Array<[string | undefined, string | undefined]> = [];
  vi.stubGlobal("fetch", vi.fn(async () => new Response(
    "data: {malformed top-level SSE JSON}\n\n",
    { status: 200, headers: { "content-type": "text/event-stream" } },
  )));
  const agent = new AgentHttpAgent(CONFIG, vi.fn(), vi.fn(), {
    getThreadId: () => "thread-server",
    onInvalidate: (threadId, runId) => invalidated.push([threadId, runId]),
    onStreamEnd: (threadId, runId) => ended.push([threadId, runId]),
  });
  await new Promise<void>((resolve) => {
    agent.run(INPUT).subscribe({ complete: resolve, error: () => resolve() });
  });
  await new Promise((resolve) => setTimeout(resolve, 10));

  expect(invalidated).toEqual([["thread-server", "run-1"]]);
  expect(ended).toEqual([["thread-server", "run-1"]]);
});

it("invalidates the request run when an otherwise valid stream ends", async () => {
  const invalidated: Array<[string, string]> = [];
  vi.stubGlobal("fetch", vi.fn(async () => sseStream()));
  const agent = new AgentHttpAgent(CONFIG, vi.fn(), vi.fn(), {
    onInvalidate: (threadId, runId) => invalidated.push([threadId, runId]),
  });
  await new Promise<void>((resolve, reject) => {
    agent.run(INPUT).subscribe({ complete: resolve, error: reject });
  });
  await new Promise((resolve) => setTimeout(resolve, 10));

  expect(invalidated).toEqual([["thread-1", "run-1"]]);
});

it("invalidates the same request identity again after a reconnect", async () => {
  const invalidated: Array<[string, string]> = [];
  vi.stubGlobal("fetch", vi.fn(async () => sseStream()));
  const agent = new AgentHttpAgent(CONFIG, vi.fn(), vi.fn(), {
    onInvalidate: (threadId, runId) => invalidated.push([threadId, runId]),
  });
  for (let attempt = 0; attempt < 2; attempt += 1) {
    await new Promise<void>((resolve, reject) => {
      agent.run(INPUT).subscribe({ complete: resolve, error: reject });
    });
  }
  await new Promise((resolve) => setTimeout(resolve, 10));

  expect(invalidated).toEqual([["thread-1", "run-1"], ["thread-1", "run-1"]]);
});

it("overrides the runtime thread id with the server thread id", async () => {
  let captured: RequestInit | undefined;
  vi.stubGlobal("fetch", vi.fn(async (_url, init) => {
    captured = init;
    return sseStream();
  }));
  const agent = new AgentHttpAgent(CONFIG, vi.fn(), vi.fn(), {
    getThreadId: () => "th-server-1",
    getRevision: () => 2,
  });
  await new Promise<void>((resolve, reject) => {
    agent.run({ ...INPUT, threadId: "runtime-internal-id" }).subscribe({ complete: resolve, error: reject });
  });
  const body = JSON.parse(String(captured?.body));
  expect(body.threadId).toBe("th-server-1");
  expect(body.threadId).not.toBe("runtime-internal-id");
});

it("notifies onStreamEnd when Stop aborts before response headers arrive", async () => {
  // Stop 打在响应头之前：fetch 本身抛 AbortError，scanStream 从未启动。
  // 收敛通知不能只挂在 scanStream 的 finally 上，否则 UI 永远停在 running。
  const ended: Array<[string | undefined, string | undefined]> = [];
  vi.stubGlobal("fetch", vi.fn(async () => {
    throw new DOMException("The user aborted a request.", "AbortError");
  }));
  const agent = new AgentHttpAgent(CONFIG, vi.fn(), vi.fn(), {
    onStreamEnd: (threadId, runId) => ended.push([threadId, runId]),
  });
  await new Promise<void>((resolve) => {
    agent.run(INPUT).subscribe({ complete: resolve, error: () => resolve() });
  });
  await new Promise((resolve) => setTimeout(resolve, 10));

  expect(ended).toEqual([["thread-1", "run-1"]]);
});
