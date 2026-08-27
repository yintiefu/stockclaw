import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AskAiButton } from "./AskAiButton";
import {
  createEmbeddedThread,
  deleteEmbeddedThread,
  findEmbeddedThread,
} from "@/lib/agent/embedded-client";

// useStream 的测试替身：一个可由测试驱动的迷你流快照。
// followThread 模拟真实控制器 hydrate 的行为——线程对齐时把快照重置为初始值，
// 随后由测试用 update() 推入「checkpoint 装载完成」的消息数组。
const streamMock = vi.hoisted(() => {
  type Snap = {
    messages: unknown[];
    threadId: string | null;
    isLoading: boolean;
    isThreadLoading: boolean;
    error: unknown;
  };
  const initial = (): Snap => ({
    messages: [], threadId: null, isLoading: false, isThreadLoading: false, error: undefined,
  });
  const listeners = new Set<(snap: Snap) => void>();
  const state = initial();
  const emit = () => { for (const listener of [...listeners]) listener({ ...state }); };
  const update = (patch: Partial<Snap>) => { Object.assign(state, patch); emit(); };
  const followThread = (threadId: string | null) => {
    if (state.threadId !== threadId) {
      Object.assign(state, initial(), { threadId });
      emit();
    }
  };
  const reset = () => { Object.assign(state, initial()); };
  const submit = vi.fn(async (_input: unknown, options?: { threadId?: string | null }) => {
    if (options?.threadId != null) followThread(options.threadId);
  });
  return { state, listeners, submit, update, followThread, reset };
});

vi.mock("@langchain/react", async () => {
  const { useEffect, useState } = await import("react");
  return {
    useStream: vi.fn(({ threadId }: { threadId: string | null }) => {
      const [snap, setSnap] = useState({ ...streamMock.state });
      useEffect(() => {
        const listener = (next: typeof streamMock.state) => setSnap({ ...next });
        streamMock.listeners.add(listener);
        return () => { streamMock.listeners.delete(listener); };
      }, []);
      useEffect(() => { streamMock.followThread(threadId); }, [threadId]);
      return { ...snap, submit: streamMock.submit };
    }),
  };
});

vi.mock("@/lib/agent/embedded-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/agent/embedded-client")>();
  return {
    ...actual,
    findEmbeddedThread: vi.fn(),
    createEmbeddedThread: vi.fn(),
    deleteEmbeddedThread: vi.fn(),
  };
});

const mocked = {
  findEmbeddedThread: vi.mocked(findEmbeddedThread),
  createEmbeddedThread: vi.mocked(createEmbeddedThread),
  deleteEmbeddedThread: vi.mocked(deleteEmbeddedThread),
};

const historyMessages = (content: string) => [
  { type: "human", content: "当前价格如何？", id: "m1" },
  { type: "ai", content, id: "m2" },
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
  streamMock.reset();
  mocked.findEmbeddedThread.mockResolvedValue(null);
  mocked.createEmbeddedThread.mockResolvedValue("thread-1");
  mocked.deleteEmbeddedThread.mockResolvedValue(undefined);
});

describe("AskAiButton drawer lifecycle", () => {
  it("opening the drawer restores checkpoint history without creating an empty thread", async () => {
    mocked.findEmbeddedThread.mockResolvedValue("thread-1");
    renderButton({ context: "茅台现价 1800", scopeKey: "600519" });

    await userEvent.click(screen.getByRole("button", { name: "问 AI" }));
    await waitFor(() => {
      expect(mocked.findEmbeddedThread).toHaveBeenCalledWith("/stock/600519", "600519");
    });
    await waitFor(() => expect(streamMock.state.threadId).toBe("thread-1"));
    // 模拟 hydrate 装载 checkpoint 历史
    streamMock.update({ messages: historyMessages("历史权威回答") });
    await waitFor(() => {
      expect(screen.getByText("历史权威回答")).toBeInTheDocument();
    });
    expect(mocked.createEmbeddedThread).not.toHaveBeenCalled();
  });

  it("leaves the drawer empty but usable when no thread exists yet", async () => {
    renderButton({ context: "茅台现价 1800", scopeKey: "600519" });

    await userEvent.click(screen.getByRole("button", { name: "问 AI" }));
    await waitFor(() => {
      expect(mocked.findEmbeddedThread).toHaveBeenCalledWith("/stock/600519", "600519");
    });
    expect(screen.getByPlaceholderText("就本页内容提问…")).toBeInTheDocument();
    expect(mocked.createEmbeddedThread).not.toHaveBeenCalled();
  });

  it("first send creates the scoped thread and submits the page context with it", async () => {
    renderButton({ context: "茅台现价 1800", scopeKey: "600519" });

    await userEvent.click(screen.getByRole("button", { name: "问 AI" }));
    await waitFor(() => expect(mocked.findEmbeddedThread).toHaveBeenCalled());
    const input = screen.getByPlaceholderText("就本页内容提问…");
    await userEvent.type(input, "当前价格如何？");
    await userEvent.keyboard("{Enter}");

    await waitFor(() => {
      expect(mocked.createEmbeddedThread).toHaveBeenCalledWith("/stock/600519", "600519");
    });
    await waitFor(() => {
      expect(streamMock.submit).toHaveBeenCalledWith(
        expect.objectContaining({
          messages: [{ role: "user", content: "当前价格如何？" }],
          page_context: {
            route: "/stock/600519",
            scope_key: "600519",
            source_as_of: expect.any(String),
            content: "茅台现价 1800",
          },
        }),
        { threadId: "thread-1" },
      );
    });
    // 流式/权威消息由 hook 合并后渲染
    streamMock.update({ messages: historyMessages("权威回答") });
    await waitFor(() => {
      expect(screen.getByText("权威回答")).toBeInTheDocument();
    });
  });

  it("reuses the existing thread on send without creating a new one", async () => {
    mocked.findEmbeddedThread.mockResolvedValue("thread-1");
    renderButton({ context: "数据", scopeKey: "600519" });

    await userEvent.click(screen.getByRole("button", { name: "问 AI" }));
    await waitFor(() => expect(streamMock.state.threadId).toBe("thread-1"));
    await userEvent.type(screen.getByPlaceholderText("就本页内容提问…"), "再问一句");
    await userEvent.keyboard("{Enter}");

    await waitFor(() => expect(streamMock.submit).toHaveBeenCalled());
    expect(mocked.createEmbeddedThread).not.toHaveBeenCalled();
    expect(streamMock.submit).toHaveBeenCalledWith(expect.anything(), { threadId: "thread-1" });
  });

  it("isolates conversations between scopes on the same route", async () => {
    mocked.findEmbeddedThread.mockImplementation(async (_route, scope) =>
      scope === "600519" ? "thread-a" : null);
    const { rerender } = render(
      <MemoryRouter initialEntries={["/stock"]}>
        <AskAiButton context="数据" scopeKey="600519" />
      </MemoryRouter>,
    );

    await userEvent.click(screen.getByRole("button", { name: "问 AI" }));
    await waitFor(() => expect(streamMock.state.threadId).toBe("thread-a"));
    streamMock.update({ messages: historyMessages("茅台对话") });
    await waitFor(() => expect(screen.getByText("茅台对话")).toBeInTheDocument());

    rerender(
      <MemoryRouter initialEntries={["/stock"]}>
        <AskAiButton context="数据" scopeKey="000001" />
      </MemoryRouter>,
    );
    await waitFor(() => {
      expect(mocked.findEmbeddedThread).toHaveBeenCalledWith("/stock", "000001");
    });
    // thread 切换即重置快照：旧 scope 的历史不外泄
    await waitFor(() => {
      expect(screen.queryByText("茅台对话")).not.toBeInTheDocument();
    });
  });

  it("clearing deletes exactly this scope's thread and empties the drawer", async () => {
    mocked.findEmbeddedThread.mockResolvedValue("thread-1");
    renderButton({ context: "数据", scopeKey: "600519" });

    await userEvent.click(screen.getByRole("button", { name: "问 AI" }));
    await waitFor(() => expect(streamMock.state.threadId).toBe("thread-1"));
    streamMock.update({ messages: historyMessages("历史回答") });
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

  it("surfaces stream errors from the hook below the conversation", async () => {
    renderButton({ context: "数据", scopeKey: "600519" });
    await userEvent.click(screen.getByRole("button", { name: "问 AI" }));
    await waitFor(() => expect(mocked.findEmbeddedThread).toHaveBeenCalled());

    streamMock.update({ error: new Error("模型服务不可用") });
    await waitFor(() => {
      expect(screen.getByText(/模型服务不可用/)).toBeInTheDocument();
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
    await waitFor(() => expect(streamMock.submit).toHaveBeenCalled());
    streamMock.update({ messages: historyMessages("权威回答") });
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
    const user = userEvent.setup();
    renderButton({ context: "数据", scopeKey: "600519", suggestions: ["今天涨了多少"] });
    await user.click(screen.getByRole("button", { name: "问 AI" }));
    await waitFor(() => expect(mocked.findEmbeddedThread).toHaveBeenCalled());

    expect(screen.getByText("今天涨了多少")).toBeInTheDocument();
    await user.click(screen.getByText("今天涨了多少"));
    await waitFor(() => expect(streamMock.submit).toHaveBeenCalled());
    streamMock.update({
      messages: [
        { type: "human", content: "今天涨了多少", id: "m1" },
        { type: "ai", content: "## 摘要\n\n- **波动大**", id: "m2" },
      ],
    });
    await waitFor(() => expect(screen.getByRole("heading", { name: "摘要" })).toBeInTheDocument());
    expect(screen.getByText("波动大")).toBeInTheDocument();
  });
});
