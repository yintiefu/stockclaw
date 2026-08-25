import { describe, expect, it, vi } from "vitest";
import type { AgentThreadClient } from "./thread-adapter";
import { createLangGraphThreadAdapter } from "./thread-adapter";

const thread = (overrides: Record<string, unknown> = {}) => ({
  thread_id: "th-1",
  updated_at: "2026-08-23T00:00:00Z",
  metadata: {},
  ...overrides,
});

function makeClient() {
  return {
    threads: {
      search: vi.fn(),
      get: vi.fn(),
      create: vi.fn(),
      update: vi.fn(),
      delete: vi.fn(),
    },
  } as unknown as AgentThreadClient & {
    threads: Record<string, ReturnType<typeof vi.fn>>;
  };
}

async function collectText(stream: AsyncIterable<{ type: string; textDelta?: string }>) {
  let text = "";
  for await (const part of stream) {
    if (part.type === "text-delta" && typeof part.textDelta === "string") {
      text += part.textDelta;
    }
  }
  return text;
}

describe("createLangGraphThreadAdapter", () => {
  const client = makeClient();
  const adapter = createLangGraphThreadAdapter(client);

  it("implements the complete unstable adapter contract", async () => {
    expect(Object.keys(adapter).sort()).toEqual([
      "archive", "delete", "fetch", "generateTitle", "initialize", "list", "rename", "unarchive",
    ]);
  });

  it("lists and fetches threads mapped onto remote metadata", async () => {
    client.threads.search.mockResolvedValue([
      thread({ metadata: { title: "标题" } }),
      thread({ thread_id: "th-2", metadata: { archived: true } }),
    ]);
    const listed = await adapter.list();
    expect(client.threads.search).toHaveBeenCalledWith({
      limit: 100, sortBy: "updated_at", sortOrder: "desc",
    });
    expect(listed.threads[0]).toMatchObject({ remoteId: "th-1", externalId: "th-1", status: "regular", title: "标题" });
    expect(listed.threads[1]).toMatchObject({ remoteId: "th-2", status: "archived" });

    client.threads.get.mockResolvedValue(thread({ metadata: { title: "标题" } }));
    await expect(adapter.fetch("th-1")).resolves.toMatchObject({
      remoteId: "th-1", externalId: "th-1", status: "regular", title: "标题",
    });
    expect(client.threads.get).toHaveBeenCalledWith("th-1");
  });

  it("merges metadata and maps archived status", async () => {
    client.threads.get.mockResolvedValue(thread({ metadata: { title: "标题", source: "studio" } }));
    await adapter.archive("th-1");
    expect(client.threads.update).toHaveBeenCalledWith("th-1", {
      metadata: { title: "标题", source: "studio", archived: true },
    });
    client.threads.search.mockResolvedValue([thread({ metadata: { title: "标题", archived: true } })]);
    expect((await adapter.list()).threads[0]).toMatchObject({ remoteId: "th-1", externalId: "th-1", status: "archived", title: "标题" });
  });

  it("renames and deletes through the SDK", async () => {
    client.threads.get.mockResolvedValue(thread());
    await adapter.rename("th-1", "新标题");
    expect(client.threads.update).toHaveBeenCalledWith("th-1", { metadata: { title: "新标题" } });
    await adapter.delete("th-1");
    expect(client.threads.delete).toHaveBeenCalledWith("th-1");
  });

  it("unarchive clears the archived flag", async () => {
    client.threads.get.mockResolvedValue(thread({ metadata: { title: "标题", archived: true } }));
    await adapter.unarchive("th-1");
    expect(client.threads.update).toHaveBeenCalledWith("th-1", {
      metadata: { title: "标题", archived: false },
    });
  });

  it("writes and streams a deterministic first-user-message title", async () => {
    client.threads.get.mockResolvedValue(thread());
    const stream = await adapter.generateTitle("th-1", [{
      id: "m1", role: "user", createdAt: new Date(), content: [{ type: "text", text: "  查询 600519 的客观数据  " }],
    }]);
    expect(client.threads.update).toHaveBeenCalledWith("th-1", { metadata: { title: "查询 600519 的客观数据" } });
    expect(await collectText(stream)).toBe("查询 600519 的客观数据");
  });

  it("falls back to a default title when no user text exists", async () => {
    client.threads.get.mockResolvedValue(thread());
    const stream = await adapter.generateTitle("th-1", [{
      id: "m1", role: "assistant", createdAt: new Date(),
      content: [{ type: "text", text: "客观回复" }],
    } as never]);
    expect(client.threads.update).toHaveBeenCalledWith("th-1", { metadata: { title: "新会话" } });
    expect(await collectText(stream)).toBe("新会话");
  });

  it("lets LangGraph allocate the canonical UUID for a local assistant-ui thread", async () => {
    client.threads.create.mockResolvedValue(thread({ thread_id: "018f4f4e-7b2d-7f2a-8000-123456789abc" }));
    await expect(adapter.initialize("__LOCALID_Ab3xY9z")).resolves.toEqual({
      remoteId: "018f4f4e-7b2d-7f2a-8000-123456789abc",
      externalId: "018f4f4e-7b2d-7f2a-8000-123456789abc",
    });
    expect(client.threads.create).toHaveBeenCalledWith({ metadata: { channel: "workspace" } });
  });
});
