import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ThreadState } from "@langchain/langgraph-sdk";
import {
  createEmbeddedClient,
  type EmbeddedClientSubset,
} from "./embedded-client";

const now = "2026-08-25T12:00:00Z";

const thread = (overrides: Record<string, unknown> = {}) => ({
  thread_id: "thread-1",
  created_at: now,
  updated_at: now,
  state_updated_at: now,
  metadata: { channel: "embedded", route: "/stock/600519", scope_key: "600519" },
  status: "idle",
  values: {},
  interrupts: {},
  ...overrides,
});

const stateResponse = (messages: unknown[]): ThreadState<Record<string, unknown>> => ({
  values: { messages },
  next: [],
  checkpoint: {
    thread_id: "thread-1",
    checkpoint_ns: "",
    checkpoint_id: "checkpoint-1",
    checkpoint_map: null,
  },
  metadata: null,
  created_at: now,
  parent_checkpoint: null,
  tasks: [],
});

function asyncChunks(
  chunks: Array<{ event: string; data: unknown }>,
  before?: () => void,
) {
  return (async function* () {
    before?.();
    for (const chunk of chunks) yield chunk;
  })();
}

function makeClient() {
  const client = {
    threads: {
      search: vi.fn(),
      create: vi.fn(),
      get: vi.fn(),
      getState: vi.fn(),
      delete: vi.fn(),
    },
    runs: {
      stream: vi.fn(),
      cancel: vi.fn(),
    },
  };
  return client as unknown as EmbeddedClientSubset & {
    threads: Record<string, ReturnType<typeof vi.fn>>;
    runs: Record<string, ReturnType<typeof vi.fn>>;
  };
}

const sendOptions = {
  route: "/stock/600519",
  scopeKey: "600519",
  pageContext: { sourceAsOf: "15:00", content: "茅台现价 1800" },
  message: "当前价格如何？",
};

describe("embedded thread lookup", () => {
  it("searches with the exact embedded metadata, newest first, and never creates", async () => {
    const client = makeClient();
    client.threads.search.mockResolvedValue([
      thread({ thread_id: "newest", updated_at: now }),
      thread({ thread_id: "old", updated_at: "2026-08-24T10:00:00Z" }),
    ]);

    await expect(createEmbeddedClient(client).findThread("/stock/600519", "600519"))
      .resolves.toBe("newest");

    expect(client.threads.search).toHaveBeenCalledWith({
      metadata: { channel: "embedded", route: "/stock/600519", scope_key: "600519" },
      limit: 1,
      sortBy: "updated_at",
      sortOrder: "desc",
    });
    expect(client.threads.create).not.toHaveBeenCalled();
  });

  it("returns null without creating when no thread matches the scope", async () => {
    const client = makeClient();
    client.threads.search.mockResolvedValue([]);

    await expect(createEmbeddedClient(client).findThread("/daily-review", "")).resolves.toBeNull();
    expect(client.threads.create).not.toHaveBeenCalled();
  });

  it("ignores threads whose metadata does not match the exact scope", async () => {
    const client = makeClient();
    client.threads.search.mockResolvedValue([
      thread({ metadata: { channel: "embedded", route: "/stock/600519", scope_key: "000001" } }),
    ]);

    await expect(createEmbeddedClient(client).findThread("/stock/600519", "600519"))
      .resolves.toBeNull();
  });

  it("restores checkpoint messages with roles, tool chips, and ids", async () => {
    const client = makeClient();
    client.threads.getState.mockResolvedValue(stateResponse([
      { type: "human", content: "当前价格如何？", id: "m1" },
      {
        type: "ai",
        content: "现价 1800。",
        id: "m2",
        tool_calls: [{ name: "query_quote", args: { codes: ["600519"] }, id: "c1" }],
      },
    ]));

    const messages = await createEmbeddedClient(client).loadMessages("thread-1");

    expect(client.threads.getState).toHaveBeenCalledOnce();
    expect(client.threads.getState).toHaveBeenCalledWith("thread-1");
    expect(messages).toEqual([
      { id: "m1", role: "user", content: "当前价格如何？" },
      { id: "m2", role: "assistant", content: "现价 1800。", tools: [{ name: "query_quote", arg: "600519" }] },
    ]);
  });

  it("merges tool-call intermediates into the final answer bubble on restore", async () => {
    const client = makeClient();
    client.threads.getState.mockResolvedValue(stateResponse([
      { type: "human", content: "查一下", id: "m1" },
      {
        type: "ai",
        content: "",
        id: "m2",
        tool_calls: [{ name: "query_quote", args: { codes: ["600519"] }, id: "c1" }],
      },
      { type: "tool", content: "行情结果", id: "m3" },
      { type: "ai", content: "最终回答", id: "m4" },
    ]));

    const messages = await createEmbeddedClient(client).loadMessages("thread-1");

    expect(messages).toEqual([
      { id: "m1", role: "user", content: "查一下" },
      { id: "m2", role: "assistant", content: "最终回答", tools: [{ name: "query_quote", arg: "600519" }] },
    ]);
  });
});

describe("embedded send", () => {
  beforeEach(() => localStorage.clear());

  it("creates a thread with exact metadata and deterministic title only on the first missing send", async () => {
    const client = makeClient();
    client.threads.search.mockResolvedValue([]);
    client.threads.create.mockResolvedValue(thread());
    client.threads.getState.mockResolvedValue(stateResponse([
      { type: "human", content: "当前价格如何？", id: "m1" },
      { type: "ai", content: "权威回答", id: "m2" },
    ]));
    client.runs.stream.mockImplementation((_threadId, _assistantId, options) => asyncChunks([
      { event: "messages", data: [{ type: "AIMessageChunk", content: "权威回答" }, {}] },
    ]));

    const result = await createEmbeddedClient(client).send(sendOptions);

    expect(client.threads.create).toHaveBeenCalledWith({ metadata: {
      channel: "embedded",
      route: "/stock/600519",
      scope_key: "600519",
      title: "问 AI · 600519",
    } });
    expect(result.threadId).toBe("thread-1");
    expect(result.messages.at(-1)).toMatchObject({ role: "assistant", content: "权威回答" });
  });

  it("reuses the existing thread on repeated sends and reopen without creating", async () => {
    const client = makeClient();
    client.threads.search.mockResolvedValue([thread()]);
    client.threads.getState.mockResolvedValue(stateResponse([
      { type: "human", content: "当前价格如何？", id: "m1" },
      { type: "ai", content: "权威回答", id: "m2" },
    ]));
    client.runs.stream.mockImplementation(() => asyncChunks([]));

    const first = await createEmbeddedClient(client).send(sendOptions);
    const second = await createEmbeddedClient(client).send(sendOptions);

    expect(client.threads.create).not.toHaveBeenCalled();
    expect(first.threadId).toBe("thread-1");
    expect(second.threadId).toBe("thread-1");
    expect(client.runs.stream).toHaveBeenCalledTimes(2);
  });

  it("skips the lookup when the caller already holds the thread id", async () => {
    const client = makeClient();
    client.threads.getState.mockResolvedValue(stateResponse([]));
    client.runs.stream.mockImplementation(() => asyncChunks([]));

    await createEmbeddedClient(client).send({ ...sendOptions, threadId: "thread-1" });

    expect(client.threads.search).not.toHaveBeenCalled();
    expect(client.threads.create).not.toHaveBeenCalled();
    expect(client.runs.stream).toHaveBeenCalledWith("thread-1", "embedded_agent",
      expect.objectContaining({ input: expect.any(Object) }));
  });

  it("runs embedded_agent with a user message plus a complete page context and resumable options", async () => {
    const client = makeClient();
    client.threads.search.mockResolvedValue([thread()]);
    client.threads.getState.mockResolvedValue(stateResponse([]));
    client.runs.stream.mockImplementation(() => asyncChunks([]));

    await createEmbeddedClient(client).send(sendOptions);

    expect(client.runs.stream).toHaveBeenCalledWith("thread-1", "embedded_agent", {
      input: {
        messages: [{ role: "user", content: "当前价格如何？" }],
        page_context: {
          route: "/stock/600519",
          scope_key: "600519",
          source_as_of: "15:00",
          content: "茅台现价 1800",
        },
      },
      streamMode: ["messages", "updates"],
      streamResumable: true,
      onDisconnect: "continue",
      durability: "sync",
    });
  });

  it.each([
    ["route", { ...sendOptions, route: " " }],
    ["message", { ...sendOptions, message: "  " }],
    ["sourceAsOf", { ...sendOptions, pageContext: { sourceAsOf: "", content: "数据" } }],
    ["content", { ...sendOptions, pageContext: { sourceAsOf: "15:00", content: " " } }],
  ])("rejects an empty %s before any SDK call", async (_label, options) => {
    const client = makeClient();
    await expect(createEmbeddedClient(client).send(options)).rejects.toThrow();
    expect(client.threads.search).not.toHaveBeenCalled();
    expect(client.threads.create).not.toHaveBeenCalled();
    expect(client.runs.stream).not.toHaveBeenCalled();
  });

  it("normalizes a missing scope key to the route so scope-less pages stay isolated", async () => {
    const client = makeClient();
    client.threads.search.mockResolvedValue([]);
    client.threads.create.mockResolvedValue(thread({
      metadata: { channel: "embedded", route: "/daily-review", scope_key: "/daily-review" },
    }));
    client.threads.getState.mockResolvedValue(stateResponse([]));
    client.runs.stream.mockImplementation(() => asyncChunks([]));

    const result = await createEmbeddedClient(client).send({
      route: "/daily-review",
      pageContext: { sourceAsOf: "15:00", content: "今日大盘数据" },
      message: "今天大盘怎么走",
    });

    expect(client.threads.search).toHaveBeenCalledWith({
      metadata: { channel: "embedded", route: "/daily-review", scope_key: "/daily-review" },
      limit: 1,
      sortBy: "updated_at",
      sortOrder: "desc",
    });
    expect(client.threads.create).toHaveBeenCalledWith({ metadata: {
      channel: "embedded",
      route: "/daily-review",
      scope_key: "/daily-review",
      title: "问 AI · /daily-review",
    } });
    expect(result.threadId).toBe("thread-1");
    expect(client.runs.stream).toHaveBeenCalledWith("thread-1", "embedded_agent",
      expect.objectContaining({
        input: expect.objectContaining({
          page_context: {
            route: "/daily-review",
            scope_key: "/daily-review",
            source_as_of: "15:00",
            content: "今日大盘数据",
          },
        }),
      }));
  });

  it("streams text deltas and tool calls, then replaces them with the authoritative checkpoint", async () => {
    const client = makeClient();
    client.threads.search.mockResolvedValue([thread()]);
    client.threads.getState.mockResolvedValue(stateResponse([
      { type: "human", content: "当前价格如何？", id: "m1" },
      {
        type: "ai",
        content: "权威完整回答",
        id: "m2",
        tool_calls: [{ name: "query_quote", args: { codes: ["600519"] }, id: "c1" }],
      },
    ]));
    client.runs.stream.mockImplementation(() => asyncChunks([
      { event: "messages", data: [{ type: "AIMessageChunk", content: "临时" }, {}] },
      { event: "messages", data: [{ type: "AIMessageChunk", content: "片段" }, {}] },
      {
        event: "messages",
        data: [{
          type: "AIMessageChunk",
          content: "",
          tool_calls: [{ name: "query_quote", args: { codes: ["600519"] }, id: "c1" }],
        }, {}],
      },
      { event: "updates", data: { embedded_agent: { messages: [] } } },
    ]));
    const deltas: string[] = [];
    const tools: Array<{ name: string; arg: string }> = [];

    const result = await createEmbeddedClient(client).send({
      ...sendOptions,
      onDelta: (delta) => deltas.push(delta),
      onTool: (name, arg) => tools.push({ name, arg }),
    });

    expect(deltas).toEqual(["临时", "片段"]);
    expect(tools).toEqual([{ name: "query_quote", arg: "600519" }]);
    expect(result.messages.at(-1)).toMatchObject({
      role: "assistant",
      content: "权威完整回答",
      tools: [{ name: "query_quote", arg: "600519" }],
    });
  });

  it("never cancels the server run when local consumption aborts", async () => {
    const client = makeClient();
    const controller = new AbortController();
    client.threads.search.mockResolvedValue([thread()]);
    client.threads.getState.mockResolvedValue(stateResponse([]));
    client.runs.stream.mockImplementation((_threadId, _assistantId, options) => (async function* () {
      yield { event: "messages", data: [{ type: "AIMessageChunk", content: "部分" }, {}] };
      controller.abort();
      if (options.signal?.aborted) return;
      yield { event: "messages", data: [{ type: "AIMessageChunk", content: "不再消费" }, {}] };
    })());

    await expect(createEmbeddedClient(client).send({
      ...sendOptions,
      signal: controller.signal,
    })).rejects.toMatchObject({ name: "AbortError" });

    expect(client.runs.cancel).not.toHaveBeenCalled();
    expect(client.runs.stream).toHaveBeenCalledWith("thread-1", "embedded_agent",
      expect.objectContaining({ signal: controller.signal, onDisconnect: "continue" }));
  });

  it("ignores non-assistant stream parts and tolerates array content blocks", async () => {
    const client = makeClient();
    client.threads.search.mockResolvedValue([thread()]);
    client.threads.getState.mockResolvedValue(stateResponse([
      { type: "ai", content: "最终权威", id: "m2" },
    ]));
    client.runs.stream.mockImplementation(() => asyncChunks([
      { event: "messages", data: [{ type: "HumanMessage", content: "回声" }, {}] },
      { event: "messages", data: [{ type: "ai", content: [{ type: "text", text: "块文本" }] }, {}] },
    ]));
    const deltas: string[] = [];

    const result = await createEmbeddedClient(client).send({
      ...sendOptions,
      onDelta: (delta) => deltas.push(delta),
    });

    expect(deltas).toEqual(["块文本"]);
    expect(result.messages.at(-1)).toMatchObject({ content: "最终权威" });
  });
});

describe("embedded clear", () => {
  it("deletes every thread matching the exact scope and never other channels", async () => {
    const client = makeClient();
    client.threads.search.mockResolvedValue([
      thread({ thread_id: "embedded-a" }),
      thread({ thread_id: "embedded-b" }),
    ]);

    await createEmbeddedClient(client).deleteThread("/stock/600519", "600519");

    expect(client.threads.search).toHaveBeenCalledWith({
      metadata: { channel: "embedded", route: "/stock/600519", scope_key: "600519" },
      sortBy: "updated_at",
      sortOrder: "desc",
    });
    expect(client.threads.delete).toHaveBeenCalledTimes(2);
    expect(client.threads.delete).toHaveBeenCalledWith("embedded-a");
    expect(client.threads.delete).toHaveBeenCalledWith("embedded-b");
  });

  it("is a no-op when no thread matches", async () => {
    const client = makeClient();
    client.threads.search.mockResolvedValue([]);

    await expect(createEmbeddedClient(client).deleteThread("/daily-review", "")).resolves.toBeUndefined();
    expect(client.threads.delete).not.toHaveBeenCalled();
  });
});

describe("embedded secrets boundary", () => {
  it("never persists model credentials into thread metadata, input, or storage", async () => {
    const client = makeClient();
    client.threads.search.mockResolvedValue([]);
    client.threads.create.mockResolvedValue(thread());
    client.threads.getState.mockResolvedValue(stateResponse([]));
    client.runs.stream.mockImplementation(() => asyncChunks([]));
    localStorage.setItem("vr-llm", JSON.stringify({ apiKey: "sk-secret" }));

    await createEmbeddedClient(client).send(sendOptions);

    const createCall = client.threads.create.mock.calls[0]?.[0];
    const streamCall = client.runs.stream.mock.calls[0]?.[2];
    expect(JSON.stringify(createCall)).not.toContain("sk-secret");
    expect(JSON.stringify(streamCall)).not.toContain("sk-secret");
    expect(JSON.stringify(streamCall)).not.toContain("Authorization");
    const written = Object.entries(localStorage)
      .filter(([key]) => key.startsWith("vr-askai"));
    expect(written).toEqual([]);
  });
});
