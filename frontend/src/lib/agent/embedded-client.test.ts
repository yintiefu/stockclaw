import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  buildEmbeddedSubmitInput,
  createEmbeddedClient,
  fromBaseMessages,
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

function makeClient() {
  const client = {
    threads: {
      search: vi.fn(),
      create: vi.fn(),
      delete: vi.fn(),
    },
  };
  return client as unknown as EmbeddedClientSubset & {
    threads: Record<string, ReturnType<typeof vi.fn>>;
  };
}

const submitParams = {
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
});

describe("embedded thread creation", () => {
  it("creates the thread with exact metadata and a deterministic title on first send", async () => {
    const client = makeClient();
    client.threads.create.mockResolvedValue(thread());

    await expect(createEmbeddedClient(client).createThread("/stock/600519", "600519"))
      .resolves.toBe("thread-1");

    expect(client.threads.create).toHaveBeenCalledWith({ metadata: {
      channel: "embedded",
      route: "/stock/600519",
      scope_key: "600519",
      title: "问 AI · 600519",
    } });
  });

  it("normalizes a missing scope key to the route so scope-less pages stay isolated", async () => {
    const client = makeClient();
    client.threads.create.mockResolvedValue(thread({
      metadata: { channel: "embedded", route: "/daily-review", scope_key: "/daily-review" },
    }));

    await expect(createEmbeddedClient(client).createThread("/daily-review")).resolves.toBe("thread-1");

    expect(client.threads.create).toHaveBeenCalledWith({ metadata: {
      channel: "embedded",
      route: "/daily-review",
      scope_key: "/daily-review",
      title: "问 AI · /daily-review",
    } });
  });

  it("rejects an empty route before any SDK call", async () => {
    const client = makeClient();
    await expect(createEmbeddedClient(client).createThread(" ")).rejects.toThrow("route");
    expect(client.threads.create).not.toHaveBeenCalled();
  });
});

describe("embedded submit input", () => {
  it("builds a user message plus a complete page context with a normalized scope", () => {
    expect(buildEmbeddedSubmitInput(submitParams)).toEqual({
      messages: [{ role: "user", content: "当前价格如何？" }],
      page_context: {
        route: "/stock/600519",
        scope_key: "600519",
        source_as_of: "15:00",
        content: "茅台现价 1800",
      },
    });
  });

  it("falls back to the route as scope key when none is given", () => {
    const input = buildEmbeddedSubmitInput({
      route: "/daily-review",
      pageContext: { sourceAsOf: "15:00", content: "今日大盘数据" },
      message: "今天大盘怎么走",
    });
    expect(input.page_context.scope_key).toBe("/daily-review");
  });

  it.each([
    ["route", { ...submitParams, route: " " }],
    ["message", { ...submitParams, message: "  " }],
    ["sourceAsOf", { ...submitParams, pageContext: { sourceAsOf: "", content: "数据" } }],
    ["content", { ...submitParams, pageContext: { sourceAsOf: "15:00", content: " " } }],
  ])("rejects an empty %s before any SDK call", (_label, params) => {
    expect(() => buildEmbeddedSubmitInput(params)).toThrow();
  });
});

describe("embedded message mapping", () => {
  it("maps serialized checkpoint messages with roles, tool chips, and ids", () => {
    expect(fromBaseMessages([
      { type: "human", content: "当前价格如何？", id: "m1" },
      {
        type: "ai",
        content: "现价 1800。",
        id: "m2",
        tool_calls: [{ name: "query_quote", args: { codes: ["600519"] }, id: "c1" }],
      },
    ])).toEqual([
      { id: "m1", role: "user", content: "当前价格如何？" },
      { id: "m2", role: "assistant", content: "现价 1800。", tools: [{ name: "query_quote", arg: "600519" }] },
    ]);
  });

  it("maps live BaseMessage instances via getType()", () => {
    expect(fromBaseMessages([
      { getType: () => "human", id: "m1", content: "查一下" },
      { getType: () => "ai", id: "m2", content: "", tool_calls: [
        { name: "query_quote", args: { codes: ["600519"] }, id: "c1" },
      ] },
      { getType: () => "tool", id: "m3", content: "行情结果" },
      { getType: () => "ai", id: "m4", content: "最终回答" },
    ])).toEqual([
      { id: "m1", role: "user", content: "查一下" },
      { id: "m2", role: "assistant", content: "最终回答", tools: [{ name: "query_quote", arg: "600519" }] },
    ]);
  });

  it("merges tool-call intermediates into the final answer bubble", () => {
    expect(fromBaseMessages([
      { type: "human", content: "查一下", id: "m1" },
      { type: "ai", content: "", id: "m2", tool_calls: [
        { name: "query_quote", args: { codes: ["600519"] }, id: "c1" },
      ] },
      { type: "tool", content: "行情结果", id: "m3" },
      { type: "ai", content: "最终回答", id: "m4" },
    ])).toEqual([
      { id: "m1", role: "user", content: "查一下" },
      { id: "m2", role: "assistant", content: "最终回答", tools: [{ name: "query_quote", arg: "600519" }] },
    ]);
  });

  it("skips reasoning blocks and joins text blocks of array content", () => {
    expect(fromBaseMessages([
      { getType: () => "ai", id: "m1", content: [
        { type: "reasoning", reasoning: "思考过程不进正文" },
        { type: "text", text: "块文本" },
        { type: "text", text: "续" },
      ] },
    ])).toEqual([
      { id: "m1", role: "assistant", content: "块文本续" },
    ]);
  });

  it("tolerates non-array input without throwing", () => {
    expect(fromBaseMessages(undefined)).toEqual([]);
    expect(fromBaseMessages({ messages: "nope" })).toEqual([]);
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
  beforeEach(() => localStorage.clear());

  it("never persists model credentials into thread metadata or submit input", async () => {
    const client = makeClient();
    client.threads.create.mockResolvedValue(thread());
    localStorage.setItem("vr-llm", JSON.stringify({ apiKey: "sk-secret" }));

    await createEmbeddedClient(client).createThread("/stock/600519", "600519");
    const input = buildEmbeddedSubmitInput(submitParams);

    const createCall = client.threads.create.mock.calls[0]?.[0];
    expect(JSON.stringify(createCall)).not.toContain("sk-secret");
    expect(JSON.stringify(input)).not.toContain("sk-secret");
    expect(JSON.stringify(input)).not.toContain("Authorization");
    const written = Object.entries(localStorage)
      .filter(([key]) => key.startsWith("vr-askai"));
    expect(written).toEqual([]);
  });
});
