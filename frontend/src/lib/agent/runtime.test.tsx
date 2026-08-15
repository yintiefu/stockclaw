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
