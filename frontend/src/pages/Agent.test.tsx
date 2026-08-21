import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi, beforeEach } from "vitest";

// AgentRuntimeProvider / AgentThread 以轻量桩替代，专注页面自身状态契约
const captured = vi.hoisted(() => ({
  onError: null as ((e: Error) => void) | null,
  onConflict: null as ((v: Record<string, unknown>) => void) | null,
  onStreamEnd: null as ((threadId?: string, runId?: string) => void) | null,
  onInvalidate: null as ((threadId: string, runId: string) => void) | null,
  onEvent: null as ((event: Record<string, unknown>) => void) | null,
  onOpenArtifact: null as ((artifactId: string) => void) | null,
  onUnavailable: null as ((detail: string) => void) | null,
}));
const workspaceListeners = vi.hoisted(() => new Set<() => void>());
const workspace = vi.hoisted(() => {
  const state = {
    tab: "runs" as string,
    drawer: null as string | null,
    selectedRunByThread: {} as Record<string, string>,
    staleRunIds: {} as Record<string, true>,
  };
  const emit = () => workspaceListeners.forEach((listener) => listener());
  const openDrawer = vi.fn((drawer: string | null) => {
    state.drawer = drawer;
    emit();
  });
  const setTab = vi.fn((tab: string) => {
    state.tab = tab;
    emit();
  });
  const applyEvent = vi.fn();
  const markRunStale = vi.fn();
  const selectRun = vi.fn();
  const replaceRunList = vi.fn();
  const replaceRunDetail = vi.fn();
  const subscribe = vi.fn((listener: () => void) => {
    workspaceListeners.add(listener);
    return () => { workspaceListeners.delete(listener); };
  });
  // 与真实 zustand vanilla store 一致：getState() 返回含动作方法的完整状态
  const stateWithActions = () => ({
    ...state,
    applyEvent,
    markRunStale,
    openDrawer,
    setTab,
    selectRun,
    replaceRunList,
    replaceRunDetail,
  });
  return {
    state,
    stateWithActions,
    applyEvent,
    markRunStale,
    openDrawer,
    setTab,
    selectRun,
    replaceRunList,
    replaceRunDetail,
    subscribe,
  };
});
vi.mock("@/lib/agent/runtime", () => ({
  AgentRuntimeProvider: ({ children, onError, onConflict, onStreamEnd, onInvalidate, onEvent, onUnavailable }: {
    children: React.ReactNode;
    onError: (e: Error) => void;
    onConflict: (v: Record<string, unknown>) => void;
    onStreamEnd?: (threadId?: string, runId?: string) => void;
    onInvalidate?: (threadId: string, runId: string) => void;
    onEvent?: (event: Record<string, unknown>) => void;
    onUnavailable?: (detail: string) => void;
  }) => {
    captured.onError = onError;
    captured.onConflict = onConflict;
    captured.onStreamEnd = onStreamEnd ?? null;
    captured.onInvalidate = onInvalidate ?? null;
    captured.onEvent = onEvent ?? null;
    captured.onUnavailable = onUnavailable ?? null;
    return <div data-testid="agent-runtime-stub">{children}</div>;
  },
}));
vi.mock("@/components/agent/ApprovalPanel", () => ({
  ApprovalPanel: ({ actionable = true }: { actionable?: boolean }) => (
    <form data-testid="approval-panel-stub">
      <button type="submit" disabled={!actionable}>提交审批</button>
    </form>
  ),
}));
vi.mock("@/lib/agent/workspace", () => ({
  createAgentWorkspaceStore: () => ({
    getState: () => workspace.stateWithActions(),
    subscribe: workspace.subscribe,
  }),
}));
vi.mock("@/components/agent/AgentThread", () => ({
  AgentThread: ({ onOpenArtifact }: { onOpenArtifact?: (artifactId: string) => void }) => {
    captured.onOpenArtifact = onOpenArtifact ?? null;
    return <div data-testid="agent-thread-stub" />;
  },
}));

const api = vi.hoisted(() => ({
  listThreads: vi.fn(),
  createThread: vi.fn(),
  getThread: vi.fn(),
  patchThread: vi.fn(),
  deleteThread: vi.fn(),
  listSkills: vi.fn(),
  listRuns: vi.fn(),
  getRun: vi.fn(),
  listArtifacts: vi.fn(),
  getArtifact: vi.fn(),
  downloadArtifact: vi.fn(),
  deleteArtifact: vi.fn(),
  getPolicy: vi.fn(),
}));
vi.mock("@/lib/agent/api", () => ({ agentApi: api }));

import { Agent } from "./Agent";
import { loadAgentModelConfig } from "@/lib/agent/model-config";
import type { AgentThread } from "@/lib/agent/types";

let desktopViewport = true;
const viewportListeners = new Set<(event: MediaQueryListEvent) => void>();

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
    vi.spyOn(window, "confirm").mockReturnValue(true);
    desktopViewport = true;
    viewportListeners.clear();
    vi.stubGlobal("matchMedia", vi.fn().mockImplementation((query: string) => ({
      matches: query === "(min-width: 1280px)" && desktopViewport,
      media: query,
      onchange: null,
      addEventListener: (_: string, listener: (event: MediaQueryListEvent) => void) => viewportListeners.add(listener),
      removeEventListener: (_: string, listener: (event: MediaQueryListEvent) => void) => viewportListeners.delete(listener),
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => true,
    })));
    captured.onStreamEnd = null;
    captured.onInvalidate = null;
    captured.onEvent = null;
    captured.onOpenArtifact = null;
    captured.onUnavailable = null;
    workspace.state.tab = "runs";
    workspace.state.drawer = null;
    workspace.state.selectedRunByThread = {};
    workspace.state.staleRunIds = {};
    workspaceListeners.clear();
    api.listRuns.mockReset();
    api.getRun.mockReset();
    api.listArtifacts.mockReset().mockResolvedValue({ artifacts: [], warnings: [] });
    api.getArtifact.mockReset();
    api.downloadArtifact.mockReset();
    api.deleteArtifact.mockReset();
    api.listThreads.mockResolvedValue({ threads: [], warnings: [] });
    api.createThread.mockResolvedValue(threadDoc("th-new", 0));
    api.getThread.mockImplementation(async (id: string) => threadDoc(id, 1));
    api.patchThread.mockResolvedValue(threadDoc("th-1", 2));
    api.deleteThread.mockResolvedValue(undefined);
    api.listSkills.mockResolvedValue({
      generation: 1,
      skills: [
        { directory: "quality", name: "quality", description: "质检", digest: "d1", valid: true,
          error_code: null, error_detail: null },
      ],
    });
    api.getPolicy.mockResolvedValue({
      schema_version: 1,
      revision: 1,
      updated_at: "2026-08-20T00:00:00Z",
      persisted: true,
      max_model_calls: 8,
      max_tool_calls: 16,
      tool_timeout_seconds: 30,
      max_active_seconds: 300,
      max_context_chars: 120_000,
    });
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  const completeForm = async (user: userEvent.UserEvent) => {
    await user.click(screen.getByRole("button", { name: "模型设置" }));
    await user.type(screen.getByLabelText("Provider"), "openai");
    await user.type(screen.getByLabelText("Base URL"), "https://api.openai.com/v1");
    await user.type(screen.getByLabelText("模型名称"), "gpt-5-mini");
    await user.type(screen.getByLabelText("API Key"), "sk-page-key");
    await user.click(screen.getByRole("button", { name: "保存模型配置" }));
    await user.click(screen.getByRole("button", { name: "关闭" }));
  };

  it("渲染紧凑工作台设置与会话输入区", async () => {
    const user = userEvent.setup();
    render(<MemoryRouter><Agent /></MemoryRouter>);
    await user.click(screen.getByRole("button", { name: "模型设置" }));
    expect(screen.getByLabelText("Provider")).toBeInTheDocument();
    expect(screen.getByLabelText("Base URL")).toBeInTheDocument();
    expect(screen.getByText("开始前请先完成模型配置")).toBeInTheDocument();
    expect(screen.getByTestId("agent-workspace")).toHaveClass("h-full", "min-h-0");
    expect(screen.queryByText("Agent 工作台")).toBeNull();
  });

  it("保存只写入 vr-agent-model，不碰 vr-llm", async () => {
    const user = userEvent.setup();
    render(<MemoryRouter><Agent /></MemoryRouter>);
    await user.click(screen.getByRole("button", { name: "模型设置" }));
    await user.type(screen.getByLabelText("Provider"), "deepseek");
    await user.type(screen.getByLabelText("Base URL"), "https://api.deepseek.com/v1");
    await user.type(screen.getByLabelText("模型名称"), "deepseek-chat");
    await user.type(screen.getByLabelText("API Key"), "page-secret");
    await user.click(screen.getByRole("button", { name: "保存模型配置" }));
    expect(localStorage.getItem("vr-llm")).toBeNull();
    expect(loadAgentModelConfig()).toEqual({
      provider: "deepseek",
      baseURL: "https://api.deepseek.com/v1",
      model: "deepseek-chat",
      apiKey: "page-secret",
    });
  });

  it("未保存模型草稿不改变当前 runtime 的头部模型", async () => {
    localStorage.setItem("vr-agent-model", JSON.stringify({
      provider: "openai",
      baseURL: "https://api.openai.com/v1",
      model: "gpt-saved",
      apiKey: "sk-saved-secret",
    }));
    const user = userEvent.setup();
    render(<MemoryRouter><Agent /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText("gpt-saved")).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "模型设置" }));
    await user.clear(screen.getByLabelText("模型名称"));
    await user.type(screen.getByLabelText("模型名称"), "gpt-draft");

    expect(screen.getByLabelText("模型名称")).toHaveValue("gpt-draft");
    expect(screen.getByText("gpt-saved")).toBeInTheDocument();
    expect(document.body.textContent ?? "").not.toContain("sk-saved-secret");
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
    await waitFor(() => expect(screen.getByLabelText("会话操作")).toBeInTheDocument());
    await user.click(screen.getByLabelText("会话操作"));
    await user.click(screen.getByRole("menuitem", { name: "重命名" }));
    await user.clear(screen.getByLabelText("新会话标题"));
    await user.type(screen.getByLabelText("新会话标题"), "改名后的会话");
    await user.click(screen.getByRole("button", { name: "确认重命名" }));
    await waitFor(() => expect(api.patchThread).toHaveBeenCalledWith("th-1", 3, { title: "改名后的会话" }));
    await user.click(screen.getByLabelText("会话操作"));
    await user.click(screen.getByRole("menuitem", { name: "删除" }));
    await waitFor(() => expect(api.deleteThread).toHaveBeenCalledWith("th-1", expect.any(Number)));
  });

  it("删除失败且原线程已消失时切换到权威首项并清除 artifact", async () => {
    const initial = {
      threads: [
        { id: "th-1", title: "第一线程", updated_at: "2026-08-19T12:00:00Z", revision: 1, last_run: null },
        { id: "th-2", title: "第二线程", updated_at: "2026-08-18T12:00:00Z", revision: 1, last_run: null },
      ],
      warnings: [],
    };
    api.listThreads
      .mockResolvedValueOnce(initial)
      .mockResolvedValueOnce(initial)
      .mockResolvedValue({
        threads: [{ id: "th-2", title: "第二线程", updated_at: "2026-08-18T12:00:00Z", revision: 1, last_run: null }],
        warnings: [],
      });
    api.getThread.mockImplementation(async (id: string) => threadDoc(id, 1, {
      title: id === "th-1" ? "第一线程" : "第二线程",
    }));
    api.deleteThread.mockRejectedValueOnce(new Error("线程已不存在"));
    const user = userEvent.setup();
    render(<MemoryRouter><Agent /></MemoryRouter>);
    await completeForm(user);
    await waitFor(() => expect(captured.onOpenArtifact).not.toBeNull());
    captured.onOpenArtifact?.("artifact-1");
    await waitFor(() => expect(screen.getByText("Artifact · artifact-1")).toBeInTheDocument());
    api.getThread.mockClear();

    // 删除第一线程（列表首项，当前激活）：经行内三点菜单触发
    const firstRow = screen.getByRole("button", { name: /第一线程/ }).closest("[data-thread-row]");
    expect(firstRow).not.toBeNull();
    await user.click(within(firstRow as HTMLElement).getByLabelText("会话操作"));
    await user.click(screen.getByRole("menuitem", { name: "删除" }));

    await waitFor(() => expect(api.getThread).toHaveBeenCalledWith("th-2"));
    expect(screen.getByRole("button", { name: /第二线程/ })).toHaveAttribute("aria-current", "true");
    expect(screen.queryByText("Artifact · artifact-1")).toBeNull();
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

  it("线程重命名 revision 冲突时丢弃本地动作并权威重载", async () => {
    api.listThreads.mockResolvedValue({
      threads: [{ id: "th-1", title: "服务器标题", updated_at: "2026-08-15T12:00:00Z", revision: 4, last_run: null }],
      warnings: [],
    });
    api.patchThread.mockRejectedValueOnce(Object.assign(new Error("revision 已变化"), { status: 409 }));
    const user = userEvent.setup();
    render(<MemoryRouter><Agent /></MemoryRouter>);
    await completeForm(user);
    await waitFor(() => expect(screen.getByLabelText("会话操作")).toBeInTheDocument());
    api.getThread.mockClear();
    await user.click(screen.getByLabelText("会话操作"));
    await user.click(screen.getByRole("menuitem", { name: "重命名" }));
    await user.clear(screen.getByLabelText("新会话标题"));
    await user.type(screen.getByLabelText("新会话标题"), "本地草稿");
    await user.click(screen.getByRole("button", { name: "确认重命名" }));
    await waitFor(() => expect(api.getThread).toHaveBeenCalledWith("th-1"));
    expect(screen.queryByText("本地草稿")).toBeNull();
    expect(screen.getByRole("button", { name: /服务器标题/ })).toHaveAttribute("aria-current", "true");
  });

  it("重复 artifact 动作会从本地 Approval 重新激活 Inspector Artifact", async () => {
    const user = userEvent.setup();
    render(<MemoryRouter><Agent /></MemoryRouter>);
    await completeForm(user);
    await waitFor(() => expect(captured.onOpenArtifact).not.toBeNull());

    captured.onOpenArtifact?.("artifact-1");

    expect(workspace.openDrawer).toHaveBeenCalledWith("inspector");
    expect(workspace.setTab).toHaveBeenCalledWith("artifacts");
    await waitFor(() => expect(screen.getByText("Artifact · artifact-1")).toBeInTheDocument());
    await waitFor(() => expect(screen.getByRole("tab", { name: /Artifact/ })).toHaveAttribute("aria-selected", "true"));

    await user.click(screen.getByRole("tab", { name: /Approval/ }));
    expect(screen.getByRole("tab", { name: /Approval/ })).toHaveAttribute("aria-selected", "true");
    captured.onOpenArtifact?.("artifact-1");

    await waitFor(() => expect(screen.getByRole("tab", { name: /Artifact/ })).toHaveAttribute("aria-selected", "true"));
    // 两次 artifact 激活各恰一次 setTab("artifacts")；点 Approval 也会同步 tab
    expect(workspace.setTab.mock.calls.filter((call) => call[0] === "artifacts")).toHaveLength(2);
    // completeForm 打开/关闭设置抽屉不计入 Inspector 激活
    expect(workspace.openDrawer.mock.calls.filter((call) => call[0] === "inspector")).toHaveLength(2);
  });

  it("切换线程时清除上一线程的 artifact 选择", async () => {
    api.listThreads.mockResolvedValue({
      threads: [
        { id: "th-1", title: "第一线程", updated_at: "2026-08-19T12:00:00Z", revision: 1, last_run: null },
        { id: "th-2", title: "第二线程", updated_at: "2026-08-18T12:00:00Z", revision: 1, last_run: null },
      ],
      warnings: [],
    });
    api.getThread.mockImplementation(async (id: string) => threadDoc(id, 1, { title: id === "th-1" ? "第一线程" : "第二线程" }));
    const user = userEvent.setup();
    render(<MemoryRouter><Agent /></MemoryRouter>);
    await completeForm(user);
    await waitFor(() => expect(captured.onOpenArtifact).not.toBeNull());
    captured.onOpenArtifact?.("artifact-1");
    await waitFor(() => expect(screen.getByText("Artifact · artifact-1")).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: /第二线程/ }));

    await waitFor(() => expect(screen.queryByText("Artifact · artifact-1")).toBeNull());
  });

  it("待审批运行只有 Inspector 中一个审批面板", async () => {
    api.listThreads.mockResolvedValue({
      threads: [{ id: "th-await", title: "待审批", updated_at: "2026-08-19T12:00:00Z", revision: 1,
        last_run: { id: "run-1", status: "awaiting_approval", updated_at: "t", retry_of: null } }],
      warnings: [],
    });
    api.getThread.mockResolvedValue(threadDoc("th-await", 1, {
      resume_available: true,
      last_run: { id: "run-1", status: "awaiting_approval", updated_at: "t", retry_of: null },
    }));
    workspace.state.selectedRunByThread["th-await"] = "run-1";
    api.listRuns.mockResolvedValue({
      runs: [{
        id: "run-1",
        status: "awaiting_approval",
        started_at: "t",
        updated_at: "t",
        ended_at: null,
        retry_of: null,
        error_code: null,
      }],
      next_before: null,
      warnings: [],
    });
    api.getRun.mockImplementation(() => new Promise(() => {}));
    // 待审批线程会锁定模型身份，直接预置已保存配置
    localStorage.setItem("vr-agent-model", JSON.stringify({
      provider: "openai", baseURL: "https://api.openai.com/v1", model: "gpt-5", apiKey: "sk-approval",
    }));
    const user = userEvent.setup();
    render(<MemoryRouter><Agent /></MemoryRouter>);
    await user.click(await screen.findByRole("tab", { name: /Approval/ }));
    await waitFor(() => expect(screen.getAllByTestId("approval-panel-stub")).toHaveLength(1));
    expect(screen.getByRole("button", { name: "提交审批" })).toBeEnabled();
    expect(screen.getByTestId("agent-runtime-content")).toHaveClass("flex", "h-full", "min-h-0", "flex-col");
    expect(screen.getByTestId("agent-thread-region")).toHaveClass("min-h-0", "flex-1", "overflow-hidden");
  });

  it("窄屏将唯一可操作审批面板交给聊天区并随视口切回 Inspector", async () => {
    api.listThreads.mockResolvedValue({
      threads: [{ id: "th-await", title: "待审批", updated_at: "2026-08-19T12:00:00Z", revision: 1,
        last_run: { id: "run-1", status: "awaiting_approval", updated_at: "t", retry_of: null } }],
      warnings: [],
    });
    api.getThread.mockResolvedValue(threadDoc("th-await", 1, {
      resume_available: true,
      last_run: { id: "run-1", status: "awaiting_approval", updated_at: "t", retry_of: null },
    }));
    workspace.state.selectedRunByThread["th-await"] = "run-1";
    api.listRuns.mockResolvedValue({
      runs: [{
        id: "run-1", status: "awaiting_approval", started_at: "t", updated_at: "t", ended_at: null,
        retry_of: null, error_code: null,
      }],
      next_before: null,
      warnings: [],
    });
    api.getRun.mockImplementation(() => new Promise(() => {}));
    // 待审批线程会锁定模型身份，直接预置已保存配置
    localStorage.setItem("vr-agent-model", JSON.stringify({
      provider: "openai", baseURL: "https://api.openai.com/v1", model: "gpt-5", apiKey: "sk-narrow",
    }));
    const user = userEvent.setup();
    render(<MemoryRouter><Agent /></MemoryRouter>);
    await user.click(await screen.findByRole("tab", { name: /Approval/ }));
    expect(screen.getAllByRole("button", { name: "提交审批" }).filter((button) => !button.hasAttribute("disabled"))).toHaveLength(1);
    expect(within(screen.getByTestId("agent-runtime-content")).queryByTestId("approval-panel-stub")).toBeNull();

    desktopViewport = false;
    act(() => viewportListeners.forEach((listener) => listener({ matches: false } as MediaQueryListEvent)));
    await waitFor(() => expect(screen.getAllByRole("button", { name: "提交审批" }).filter((button) => !button.hasAttribute("disabled"))).toHaveLength(1));
    expect(within(screen.getByTestId("agent-runtime-content")).getByTestId("approval-panel-stub")).toBeInTheDocument();

    desktopViewport = true;
    act(() => viewportListeners.forEach((listener) => listener({ matches: true } as MediaQueryListEvent)));
    await waitFor(() => expect(screen.getAllByRole("button", { name: "提交审批" }).filter((button) => !button.hasAttribute("disabled"))).toHaveLength(1));
    expect(within(screen.getByTestId("agent-runtime-content")).queryByTestId("approval-panel-stub")).toBeNull();
  });

  it("后续运行与 MCP 错误不会被旧冲突提示遮挡", async () => {
    const user = userEvent.setup();
    render(<MemoryRouter><Agent /></MemoryRouter>);
    await completeForm(user);
    await waitFor(() => expect(captured.onConflict).not.toBeNull());
    captured.onConflict?.({ detail: "旧 revision 冲突" });
    captured.onError?.(new Error("后续运行错误"));
    captured.onUnavailable?.("连接已断开");

    await waitFor(() => expect(screen.getByText("旧 revision 冲突")).toBeInTheDocument());
    expect(screen.getByText("后续运行错误")).toBeInTheDocument();
    expect(screen.getByText(/MCP 服务不可用：连接已断开/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "管理 MCP" })).toBeInTheDocument();
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

  it("stream invalidation reloads the originating inactive thread", async () => {
    api.listThreads.mockResolvedValue({
      threads: [{ id: "th-active", title: "当前", updated_at: "2026-08-15T12:00:00Z", revision: 1, last_run: null }],
      warnings: [],
    });
    const user = userEvent.setup();
    render(<MemoryRouter><Agent /></MemoryRouter>);
    await completeForm(user);
    await waitFor(() => expect(captured.onInvalidate).not.toBeNull());

    captured.onInvalidate?.("th-origin", "run-origin");

    await waitFor(() => expect(api.getThread).toHaveBeenCalledWith("th-origin"));
    expect(workspace.markRunStale).toHaveBeenCalledWith("th-origin", "run-origin");
  });

  it("forwards persisted scanner events to the mounted workspace", async () => {
    const user = userEvent.setup();
    render(<MemoryRouter><Agent /></MemoryRouter>);
    await completeForm(user);
    await waitFor(() => expect(captured.onEvent).not.toBeNull());
    const event = {
      name: "sources.updated",
      value: { threadId: "th-new", runId: "run-1", controlRevision: 1, sourceCount: 1, sourcesTruncated: false },
    };

    captured.onEvent?.(event);

    expect(workspace.applyEvent).toHaveBeenCalledWith(event);
  });

  it("stream end reloads the originating inactive thread", async () => {
    api.listThreads.mockResolvedValue({
      threads: [{ id: "th-active", title: "当前", updated_at: "2026-08-15T12:00:00Z", revision: 1, last_run: null }],
      warnings: [],
    });
    const user = userEvent.setup();
    render(<MemoryRouter><Agent /></MemoryRouter>);
    await completeForm(user);
    await waitFor(() => expect(captured.onStreamEnd).not.toBeNull());

    captured.onStreamEnd?.("th-origin", "run-origin");

    await waitFor(() => expect(api.getThread).toHaveBeenCalledWith("th-origin"), { timeout: 2000 });
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

  it("能力管理：应用 selected_skills 恰好一次 PATCH 并刷新", async () => {
    const user = userEvent.setup();
    render(<MemoryRouter><Agent /></MemoryRouter>);
    await completeForm(user);
    await waitFor(() => expect(screen.getByTestId("agent-runtime-stub")).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: /能力管理/ }));
    await user.click(screen.getByRole("checkbox", { name: "quality" }));
    await user.click(screen.getByRole("button", { name: "应用到本会话" }));
    await waitFor(() =>
      expect(api.patchThread).toHaveBeenCalledWith("th-new", 0, { selected_skills: ["quality"] }));
    expect(api.patchThread).toHaveBeenCalledTimes(1);
  });

  it("运行中禁用能力管理命令", async () => {
    api.listThreads.mockResolvedValue({
      threads: [
        { id: "th-busy", title: "忙", updated_at: "2026-08-16T00:00:00Z", revision: 1,
          last_run: { id: "r1", status: "running", updated_at: "t", retry_of: null } },
      ],
      warnings: [],
    });
    api.getThread.mockImplementation(async (id: string) => threadDoc(id, 1, {
      last_run: { id: "r1", status: "running", updated_at: "t", retry_of: null },
    }));
    const user = userEvent.setup();
    render(<MemoryRouter><Agent /></MemoryRouter>);
    await completeForm(user);
    await waitFor(() => expect(screen.getByRole("button", { name: /能力管理/ })).toBeDisabled());
  });
});
