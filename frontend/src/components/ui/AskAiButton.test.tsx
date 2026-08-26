import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AskAiButton } from "./AskAiButton";
import {
  deleteEmbeddedThread,
  findEmbeddedThread,
  loadEmbeddedMessages,
  sendEmbeddedMessage,
  type EmbeddedMessage,
} from "@/lib/agent/embedded-client";

vi.mock("@/lib/agent/embedded-client", () => ({
  findEmbeddedThread: vi.fn(),
  loadEmbeddedMessages: vi.fn(),
  sendEmbeddedMessage: vi.fn(),
  deleteEmbeddedThread: vi.fn(),
}));

const mocked = {
  findEmbeddedThread: vi.mocked(findEmbeddedThread),
  loadEmbeddedMessages: vi.mocked(loadEmbeddedMessages),
  sendEmbeddedMessage: vi.mocked(sendEmbeddedMessage),
  deleteEmbeddedThread: vi.mocked(deleteEmbeddedThread),
};

const assistantMessages = (content: string): EmbeddedMessage[] => [
  { id: "m1", role: "user", content: "当前价格如何？" },
  { id: "m2", role: "assistant", content },
];

function renderButton(props: Parameters<typeof AskAiButton>[0]) {
  return render(
    <MemoryRouter initialEntries={["/stock/600519"]}>
      <AskAiButton {...props} />
    </MemoryRouter>,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

beforeEach(() => {
  localStorage.clear();
  mocked.findEmbeddedThread.mockResolvedValue(null);
  mocked.loadEmbeddedMessages.mockResolvedValue([]);
  mocked.sendEmbeddedMessage.mockResolvedValue({
    threadId: "thread-1",
    messages: assistantMessages("权威回答"),
  });
});

describe("AskAiButton drawer lifecycle", () => {
  it("opening the drawer restores checkpoint history without creating an empty thread", async () => {
    mocked.findEmbeddedThread.mockResolvedValue("thread-1");
    mocked.loadEmbeddedMessages.mockResolvedValue(assistantMessages("历史权威回答"));
    renderButton({ context: "茅台现价 1800", scopeKey: "600519" });

    await userEvent.click(screen.getByRole("button", { name: "问 AI" }));
    await waitFor(() => {
      expect(mocked.findEmbeddedThread).toHaveBeenCalledWith("/stock/600519", "600519");
    });
    await waitFor(() => {
      expect(mocked.loadEmbeddedMessages).toHaveBeenCalledWith("thread-1");
    });
    await waitFor(() => {
      expect(screen.getByText("历史权威回答")).toBeInTheDocument();
    });
    expect(mocked.sendEmbeddedMessage).not.toHaveBeenCalled();
  });

  it("leaves the drawer empty but usable when no thread exists yet", async () => {
    renderButton({ context: "茅台现价 1800", scopeKey: "600519" });

    await userEvent.click(screen.getByRole("button", { name: "问 AI" }));
    await waitFor(() => {
      expect(mocked.findEmbeddedThread).toHaveBeenCalledWith("/stock/600519", "600519");
    });
    expect(mocked.loadEmbeddedMessages).not.toHaveBeenCalled();
    expect(screen.getByPlaceholderText("就本页内容提问…")).toBeInTheDocument();
    expect(mocked.sendEmbeddedMessage).not.toHaveBeenCalled();
  });

  it("first send passes the page context and reuses the checkpoint as the answer", async () => {
    renderButton({ context: "茅台现价 1800", scopeKey: "600519" });

    await userEvent.click(screen.getByRole("button", { name: "问 AI" }));
    await waitFor(() => {
      expect(mocked.findEmbeddedThread).toHaveBeenCalled();
    });
    const input = screen.getByPlaceholderText("就本页内容提问…");
    await userEvent.type(input, "当前价格如何？");
    await userEvent.keyboard("{Enter}");

    await waitFor(() => {
      expect(mocked.sendEmbeddedMessage).toHaveBeenCalledWith(expect.objectContaining({
        route: "/stock/600519",
        scopeKey: "600519",
        message: "当前价格如何？",
        pageContext: expect.objectContaining({ content: "茅台现价 1800" }),
      }));
    });
    await waitFor(() => {
      expect(screen.getByText("权威回答")).toBeInTheDocument();
    });
  });

  it("streams transient deltas before the authoritative checkpoint replaces them", async () => {
    const user = userEvent.setup();
    renderButton({ context: "茅台现价 1800", scopeKey: "600519" });
    await user.click(screen.getByRole("button", { name: "问 AI" }));
    await waitFor(() => expect(mocked.findEmbeddedThread).toHaveBeenCalled());

    mocked.sendEmbeddedMessage.mockImplementation(async (options) => {
      options?.onDelta?.("临时片段");
      return { threadId: "thread-1", messages: assistantMessages("权威完整回答") };
    });

    await user.type(screen.getByPlaceholderText("就本页内容提问…"), "当前价格如何？");
    await user.keyboard("{Enter}");

    await waitFor(() => {
      expect(screen.getByText("权威完整回答")).toBeInTheDocument();
    });
    expect(screen.queryByText("临时片段")).not.toBeInTheDocument();
  });

  it("isolates conversations between scopes on the same route", async () => {
    mocked.findEmbeddedThread.mockImplementation(async (_route, scope) =>
      scope === "600519" ? "thread-a" : null);
    mocked.loadEmbeddedMessages.mockResolvedValue(assistantMessages("茅台对话"));
    const { rerender } = render(
      <MemoryRouter initialEntries={["/stock"]}>
        <AskAiButton context="数据" scopeKey="600519" />
      </MemoryRouter>,
    );

    await userEvent.click(screen.getByRole("button", { name: "问 AI" }));
    await waitFor(() => expect(screen.getByText("茅台对话")).toBeInTheDocument());

    rerender(
      <MemoryRouter initialEntries={["/stock"]}>
        <AskAiButton context="数据" scopeKey="000001" />
      </MemoryRouter>,
    );
    await waitFor(() => {
      expect(mocked.findEmbeddedThread).toHaveBeenCalledWith("/stock", "000001");
    });
    expect(screen.queryByText("茅台对话")).not.toBeInTheDocument();
  });

  it("clearing deletes exactly this scope's thread and empties the drawer", async () => {
    mocked.deleteEmbeddedThread.mockResolvedValue(undefined);
    mocked.findEmbeddedThread.mockResolvedValue("thread-1");
    mocked.loadEmbeddedMessages.mockResolvedValue(assistantMessages("历史回答"));
    renderButton({ context: "数据", scopeKey: "600519" });

    await userEvent.click(screen.getByRole("button", { name: "问 AI" }));
    await waitFor(() => expect(screen.getByText("历史回答")).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: "清空本页对话" }));

    await waitFor(() => {
      expect(mocked.deleteEmbeddedThread).toHaveBeenCalledWith("/stock/600519", "600519");
    });
    await waitFor(() => {
      expect(screen.queryByText("历史回答")).not.toBeInTheDocument();
    });
  });

  it("shows a readiness error instead of an empty answer when the agent server is unreachable", async () => {
    mocked.findEmbeddedThread.mockRejectedValue(new Error("连接不到 Agent 服务"));
    renderButton({ context: "数据", scopeKey: "600519" });

    await userEvent.click(screen.getByRole("button", { name: "问 AI" }));
    await waitFor(() => {
      expect(screen.getByText(/连接不到 Agent 服务/)).toBeInTheDocument();
    });
  });
});

describe("AskAiButton legacy browser data boundary", () => {
  const LEGACY_CHAT_KEY = "vr-askai-chat:/stock/600519#600519";

  beforeEach(() => {
    localStorage.setItem(LEGACY_CHAT_KEY, JSON.stringify([
      { role: "user", content: "旧提问" },
      { role: "assistant", content: "旧回答" },
    ]));
    localStorage.setItem("vr-llm", JSON.stringify({ apiKey: "sk-legacy" }));
  });

  it("never reads, migrates, or deletes legacy chat or model keys during a full send cycle", async () => {
    const user = userEvent.setup();
    renderButton({ context: "茅台现价 1800", scopeKey: "600519" });
    await user.click(screen.getByRole("button", { name: "问 AI" }));
    await waitFor(() => expect(mocked.findEmbeddedThread).toHaveBeenCalled());

    await user.type(screen.getByPlaceholderText("就本页内容提问…"), "当前价格如何？");
    await user.keyboard("{Enter}");
    await waitFor(() => expect(screen.getByText("权威回答")).toBeInTheDocument());

    expect(screen.queryByText("旧提问")).not.toBeInTheDocument();
    expect(screen.queryByText("旧回答")).not.toBeInTheDocument();
    expect(localStorage.getItem(LEGACY_CHAT_KEY)).toBe(JSON.stringify([
      { role: "user", content: "旧提问" },
      { role: "assistant", content: "旧回答" },
    ]));
    expect(localStorage.getItem("vr-llm")).toBe(JSON.stringify({ apiKey: "sk-legacy" }));
    const askaiKeys = Object.keys(localStorage).filter((key) => key.startsWith("vr-askai"));
    expect(askaiKeys).toEqual([LEGACY_CHAT_KEY]);
  });

  it("renders markdown answers, suggestions, save-note, and the clear affordance", async () => {
    mocked.sendEmbeddedMessage.mockResolvedValue({
      threadId: "thread-1",
      messages: [
        { id: "m1", role: "user", content: "当前价格如何？" },
        { id: "m2", role: "assistant", content: "## 摘要\n\n- **波动大**" },
      ],
    });
    const user = userEvent.setup();
    renderButton({ context: "数据", scopeKey: "600519", suggestions: ["今天涨了多少"] });
    await user.click(screen.getByRole("button", { name: "问 AI" }));
    await waitFor(() => expect(mocked.findEmbeddedThread).toHaveBeenCalled());

    expect(screen.getByText("今天涨了多少")).toBeInTheDocument();
    await user.click(screen.getByText("今天涨了多少"));
    await waitFor(() => expect(screen.getByRole("heading", { name: "摘要" })).toBeInTheDocument());
    expect(screen.getByText("波动大")).toBeInTheDocument();
  });
});
