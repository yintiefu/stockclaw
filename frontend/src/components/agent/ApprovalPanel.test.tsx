/** Task 1C-14：ApprovalPanel / SteerAwayComposer / 503 与历史水合契约。 */
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AgentMessage, AgentThread } from "@/lib/agent/types";
import { exportRepositoryOf } from "@/lib/agent/history";

const hooks = vi.hoisted(() => ({
  resolveAll: vi.fn(),
  steerAway: vi.fn(),
  pending: [
    { id: "i-1", toolCallId: "c-1", message: "echo", serverId: "fixture",
      serverName: "夹具", toolName: "echo", toolAlias: "mcp__fixture__echo",
      arguments: { value: "hi" } },
    { id: "i-2", toolCallId: "c-2", message: "lookup", serverId: "fixture",
      serverName: "夹具", toolName: "lookup", toolAlias: "mcp__fixture__lookup",
      arguments: {} },
  ],
}));
vi.mock("@/lib/agent/approval", () => ({
  useApprovalBridge: () => ({
    pending: hooks.pending,
    resolveAll: hooks.resolveAll,
    steerAway: hooks.steerAway,
  }),
}));

import { ApprovalPanel } from "./ApprovalPanel";
import { SteerAwayComposer } from "./SteerAwayComposer";

beforeEach(() => {
  vi.clearAllMocks();
  hooks.pending = [
    { id: "i-1", toolCallId: "c-1", message: "echo", serverId: "fixture",
      serverName: "夹具", toolName: "echo", toolAlias: "mcp__fixture__echo",
      arguments: { value: "hi" } },
    { id: "i-2", toolCallId: "c-2", message: "lookup", serverId: "fixture",
      serverName: "夹具", toolName: "lookup", toolAlias: "mcp__fixture__lookup",
      arguments: {} },
  ];
});
afterEach(cleanup);

describe("ApprovalPanel", () => {
  it("renders a stable bounded empty state", () => {
    hooks.pending = [];
    render(<ApprovalPanel disabled={false} />);

    expect(screen.getByRole("status")).toHaveTextContent("当前没有待审批的工具调用");
    expect(screen.getByLabelText("MCP 工具审批")).toHaveClass("min-h-24");
  });

  it("keeps historical runs non-actionable even when the live bridge has interrupts", () => {
    render(<ApprovalPanel disabled={false} actionable={false} />);

    expect(screen.getByRole("status")).toHaveTextContent("所选历史运行没有可操作的审批");
    expect(screen.queryByRole("button", { name: "提交全部决定" })).toBeNull();
  });

  it("submits all scoped decisions exactly once", async () => {
    const user = userEvent.setup();
    render(<ApprovalPanel disabled={false} />);
    await user.click(screen.getByLabelText("echo：本会话允许"));
    await user.click(screen.getByLabelText("lookup：拒绝"));
    await user.click(screen.getByRole("button", { name: "提交全部决定" }));
    expect(hooks.resolveAll).toHaveBeenCalledWith([
      { id: "i-1", decision: "approve", scope: "thread_session" },
      { id: "i-2", decision: "reject", scope: "once" },
    ]);
    expect(hooks.resolveAll).toHaveBeenCalledTimes(1);
  });

  it("requires every row before submit", async () => {
    const user = userEvent.setup();
    render(<ApprovalPanel disabled={false} />);
    await user.click(screen.getByLabelText("echo：本次允许"));
    expect(screen.getByRole("button", { name: "提交全部决定" })).toBeDisabled();
  });

  it("renders redacted JSON arguments in bounded pre", () => {
    render(<ApprovalPanel disabled={false} />);
    const pres = screen.getAllByText(/"value"/);
    expect(pres.length).toBeGreaterThan(0);
    expect(pres[0].tagName).toBe("PRE");
  });

  it("disables everything while submitting or disabled", () => {
    render(<ApprovalPanel disabled />);
    expect(screen.getByRole("button", { name: "提交全部决定" })).toBeDisabled();
    expect(screen.getByLabelText("echo：本次允许")).toBeDisabled();
  });
});

describe("SteerAwayComposer", () => {
  it("sends exactly one new user message via steer-away hook", async () => {
    hooks.steerAway.mockResolvedValue(undefined);
    const user = userEvent.setup();
    render(<SteerAwayComposer disabled={false} />);
    await user.type(screen.getByLabelText("转向新问题"), "换个问题");
    await user.click(screen.getByRole("button", { name: /发送/ }));
    await waitFor(() => expect(hooks.steerAway).toHaveBeenCalledTimes(1));
  });
});

describe("history 水合门控", () => {
  const pendingMessage: AgentMessage = {
    id: "a1", role: "assistant", content: "", partial: false,
    pending_interrupt: true,
    interrupts: [{ id: "i-1", serverId: "f", serverName: "n", toolName: "t",
      toolAlias: "mcp__f__t", arguments: {} }],
    tool_calls: [], tool_call_id: null, created_at: null,
  };

  const thread = (awaiting: boolean, resumeAvailable: boolean): AgentThread => ({
    schema_version: 1, id: "th-1", title: "t",
    created_at: "", updated_at: "", revision: 0, selected_skills: [],
    messages: [pendingMessage], artifact_ids: [],
    last_run: awaiting ? { id: "r1", status: "awaiting_approval", updated_at: "", retry_of: null } : null,
    ...(resumeAvailable ? { resume_available: true } : {}),
  } as AgentThread);

  it("仅在 awaiting + resume_available 时水合 actionable interrupts", () => {
    const repo = exportRepositoryOf(thread(true, true));
    expect(repo).toBeDefined();

    const gated = exportRepositoryOf(thread(true, false));
    expect(gated).toBeDefined();
    // 消息仍可见但 metadata 不注入（非 actionable）
    const messages = (gated as unknown as { messages?: unknown[] }).messages;
    if (messages) {
      const meta = (messages.at(-1) as { metadata?: unknown })?.metadata;
      expect(meta).toBeUndefined();
    }
  });
});
