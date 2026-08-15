import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi, beforeEach } from "vitest";

// AgentRuntimeProvider / AgentThread 以轻量桩替代，专注页面自身状态契约
const captured = vi.hoisted(() => ({ onError: null as ((e: Error) => void) | null, onConflict: null as ((v: Record<string, unknown>) => void) | null }));
vi.mock("@/lib/agent/runtime", () => ({
  AgentRuntimeProvider: ({ children, onError, onConflict }: {
    children: React.ReactNode;
    onError: (e: Error) => void;
    onConflict: (v: Record<string, unknown>) => void;
  }) => {
    captured.onError = onError;
    captured.onConflict = onConflict;
    return <div data-testid="agent-runtime-stub">{children}</div>;
  },
}));
vi.mock("@/components/agent/AgentThread", () => ({
  AgentThread: () => <div data-testid="agent-thread-stub" />,
}));

const api = vi.hoisted(() => ({
  listThreads: vi.fn(),
  createThread: vi.fn(),
  getThread: vi.fn(),
  patchThread: vi.fn(),
  deleteThread: vi.fn(),
}));
vi.mock("@/lib/agent/api", () => ({ agentApi: api }));

import { Agent } from "./Agent";
import { loadAgentModelConfig } from "@/lib/agent/model-config";
import type { AgentThread } from "@/lib/agent/types";

const threadDoc = (id: string, revision: number, extra: Partial<AgentThread> = {}): AgentThread => ({
  schema_version: 1,
  id,
  title: id,
  created_at: "2026-08-15T00:00:00Z",
  updated_at: "2026-08-15T12:00:00Z",
  revision,
  selected_skills: [],
  messages: [],
  artifact_ids: [],
  last_run: null,
  ...extra,
});

describe("Agent 工作台页面", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.clearAllMocks();
    api.listThreads.mockResolvedValue({ threads: [], warnings: [] });
    api.createThread.mockResolvedValue(threadDoc("th-new", 0));
    api.getThread.mockImplementation(async (id: string) => threadDoc(id, 1));
    api.patchThread.mockResolvedValue(threadDoc("th-1", 2));
    api.deleteThread.mockResolvedValue(undefined);
  });

  afterEach(() => {
    cleanup();
  });

  const completeForm = async (user: userEvent.UserEvent) => {
    await user.type(screen.getByLabelText("Provider"), "openai");
    await user.type(screen.getByLabelText("Base URL"), "https://api.openai.com/v1");
    await user.type(screen.getByLabelText("模型"), "gpt-5-mini");
    await user.type(screen.getByLabelText("API Key"), "sk-page-key");
    await user.click(screen.getByRole("button", { name: /保存/ }));
  };

  it("渲染模型表单与会话输入区", () => {
    render(<MemoryRouter><Agent /></MemoryRouter>);
    expect(screen.getByLabelText("Provider")).toBeInTheDocument();
    expect(screen.getByLabelText("Base URL")).toBeInTheDocument();
    expect(screen.getByLabelText("模型")).toBeInTheDocument();
    expect(screen.getByText("开始前请先完成模型配置")).toBeInTheDocument();
  });

  it("保存只写入 vr-agent-model，不碰 vr-llm", async () => {
    const user = userEvent.setup();
    render(<MemoryRouter><Agent /></MemoryRouter>);
    await user.type(screen.getByLabelText("Provider"), "deepseek");
    await user.type(screen.getByLabelText("Base URL"), "https://api.deepseek.com/v1");
    await user.type(screen.getByLabelText("模型"), "deepseek-chat");
    await user.type(screen.getByLabelText("API Key"), "page-secret");
    await user.click(screen.getByRole("button", { name: /保存/ }));
    expect(localStorage.getItem("vr-llm")).toBeNull();
    expect(loadAgentModelConfig()).toEqual({
      provider: "deepseek",
      baseURL: "https://api.deepseek.com/v1",
      model: "deepseek-chat",
      apiKey: "page-secret",
    });
  });

  it("表单不完整时不挂载 runtime", () => {
    render(<MemoryRouter><Agent /></MemoryRouter>);
    expect(screen.queryByTestId("agent-runtime-stub")).toBeNull();
  });

  it("首次加载选中最新线程；无线程时先创建新会话", async () => {
    api.listThreads.mockResolvedValue({
      threads: [
        { id: "th-new", title: "新", updated_at: "2026-08-15T12:00:00Z", revision: 2, last_run: null },
        { id: "th-old", title: "旧", updated_at: "2026-08-14T00:00:00Z", revision: 1, last_run: null },
      ],
      warnings: [],
    });
    const user = userEvent.setup();
    render(<MemoryRouter><Agent /></MemoryRouter>);
    await completeForm(user);
    await waitFor(() => expect(screen.getByTestId("agent-runtime-stub")).toBeInTheDocument());
    // 选中最新线程（列表第一项），且没有创建新线程
    expect(api.getThread).toHaveBeenCalledWith("th-new");
    expect(api.createThread).not.toHaveBeenCalled();
  });

  it("刷新恢复消息与选中线程，localStorage 只含允许键", async () => {
    api.getThread.mockImplementation(async (id: string) => threadDoc(id, 1, {
      messages: [{ id: "u1", role: "user", content: "刷新前的问题", partial: false, pending_interrupt: false, interrupts: [], tool_calls: [], tool_call_id: null, created_at: null }],
    }));
    const user = userEvent.setup();
    const { unmount } = render(<MemoryRouter><Agent /></MemoryRouter>);
    await completeForm(user);
    await waitFor(() => expect(screen.getByTestId("agent-runtime-stub")).toBeInTheDocument());
    unmount();

    api.listThreads.mockResolvedValue({
      threads: [{ id: "th-1", title: "恢复", updated_at: "2026-08-15T12:00:00Z", revision: 1, last_run: null }],
      warnings: [],
    });
    render(<MemoryRouter><Agent /></MemoryRouter>);
    await waitFor(() => expect(api.getThread).toHaveBeenCalled());
    expect([...Object.keys(localStorage)].every((key) => key === "vr-agent-model" || key === "vr-access-key")).toBe(true);
  });

  it("rename/delete 走 REST 且带 revision", async () => {
    api.listThreads.mockResolvedValue({
      threads: [{ id: "th-1", title: "现金流", updated_at: "2026-08-15T12:00:00Z", revision: 3, last_run: null }],
      warnings: [],
    });
    const user = userEvent.setup();
    render(<MemoryRouter><Agent /></MemoryRouter>);
    await completeForm(user);
    await waitFor(() => expect(screen.getByLabelText("重命名会话")).toBeInTheDocument());
    await user.click(screen.getByLabelText("重命名会话"));
    await user.clear(screen.getByLabelText("新会话标题"));
    await user.type(screen.getByLabelText("新会话标题"), "改名后的会话");
    await user.click(screen.getByRole("button", { name: "确认" }));
    await waitFor(() => expect(api.patchThread).toHaveBeenCalledWith("th-1", 3, "改名后的会话"));
    await user.click(screen.getByLabelText("删除会话"));
    await waitFor(() => expect(api.deleteThread).toHaveBeenCalledWith("th-1"));
  });

  it("结构化 409 触发一次权威重载并显示中文 detail", async () => {
    api.listThreads.mockResolvedValue({
      threads: [{ id: "th-1", title: "冲突线程", updated_at: "2026-08-15T12:00:00Z", revision: 3, last_run: null }],
      warnings: [],
    });
    const user = userEvent.setup();
    render(<MemoryRouter><Agent /></MemoryRouter>);
    await completeForm(user);
    await waitFor(() => expect(captured.onConflict).not.toBeNull());
    captured.onConflict?.({ code: "THREAD_REVISION_CONFLICT", detail: "线程 revision 已变化，已恢复服务器历史" });
    await waitFor(() => expect(screen.getByText(/已恢复服务器历史/)).toBeInTheDocument());
    await waitFor(() => expect(api.getThread).toHaveBeenCalledWith("th-1"));
  });

  it("runtime onError 触发后错误可见且不暴露密钥", async () => {
    const user = userEvent.setup();
    render(<MemoryRouter><Agent /></MemoryRouter>);
    await completeForm(user);
    await waitFor(() => expect(screen.getByTestId("agent-runtime-stub")).toBeInTheDocument());
    captured.onError?.(new Error("上游余额不足"));
    await waitFor(() => expect(screen.getByText(/上游余额不足/)).toBeInTheDocument());
    expect(document.body.textContent ?? "").not.toContain("sk-page-key");
  });

  it("恢复警告只显示隔离文件名", async () => {
    api.listThreads.mockResolvedValue({
      threads: [{ id: "th-1", title: "健康", updated_at: "2026-08-15T12:00:00Z", revision: 1, last_run: null }],
      warnings: [{ code: "DOCUMENT_CORRUPT", document_type: "run", filename: "run-9.json.corrupt-20260815" }],
    });
    const user = userEvent.setup();
    render(<MemoryRouter><Agent /></MemoryRouter>);
    await completeForm(user);
    await waitFor(() => expect(screen.getByText(/run-9\.json\.corrupt-20260815/)).toBeInTheDocument());
    expect(document.body.textContent ?? "").not.toContain("/agent/runs/");
  });
});
