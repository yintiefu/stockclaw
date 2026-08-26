/** Task 8：ApprovalPanel 原生 HITL 契约——聚合中断、一次提交、禁用态与空态。 */
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const hooks = vi.hoisted(() => ({
  interrupts: [] as Array<{ id?: string; value?: unknown }>,
  respond: vi.fn(),
}));

vi.mock("@assistant-ui/react-langchain", () => ({
  useLangChainInterrupts: () => hooks.interrupts,
  useLangChainRespond: () => hooks.respond,
}));

import { ApprovalPanel } from "./ApprovalPanel";

const interrupt = () => ({
  id: "interrupt-1",
  value: {
    action_requests: [
      { name: "fixture_echo", args: { value: "a" }, description: "审批 A" },
      { name: "fixture_echo", args: { value: "b" }, description: "审批 B" },
    ],
    review_configs: [
      { action_name: "fixture_echo", allowed_decisions: ["approve", "reject"] },
      { action_name: "fixture_echo", allowed_decisions: ["approve", "reject"] },
    ],
  },
});

beforeEach(() => {
  vi.clearAllMocks();
  hooks.interrupts = [];
});
afterEach(cleanup);

describe("ApprovalPanel", () => {
  it("renders a stable empty region when no interrupt is pending", () => {
    render(<ApprovalPanel disabled={false} />);
    const region = screen.getByRole("region", { name: "MCP 工具审批" });
    expect(region).toHaveTextContent("暂无待审批工具调用");
    expect(screen.queryByRole("radio")).toBeNull();
    expect(screen.queryByRole("button", { name: "提交全部决定" })).toBeNull();
  });

  it("renders the empty region for a malformed interrupt payload", () => {
    hooks.interrupts = [{ id: "i-1", value: { action_requests: [], review_configs: [{}] } }];
    render(<ApprovalPanel disabled={false} />);
    expect(screen.getByRole("region", { name: "MCP 工具审批" }))
      .toHaveTextContent("暂无待审批工具调用");
  });

  it("submits one aggregate respond with ordered decisions", async () => {
    hooks.interrupts = [interrupt()];
    const user = userEvent.setup();
    render(<ApprovalPanel disabled={false} />);

    await screen.findAllByRole("radio", { name: /批准/ });
    const approveButtons = screen.getAllByRole("radio", { name: /批准/ });
    const rejectButtons = screen.getAllByRole("radio", { name: /拒绝/ });
    expect(approveButtons).toHaveLength(2);
    await user.click(approveButtons[0]);
    await user.click(rejectButtons[1]);
    await user.click(screen.getByRole("button", { name: "提交全部决定" }));

    expect(hooks.respond).toHaveBeenCalledTimes(1);
    expect(hooks.respond).toHaveBeenCalledWith({ decisions: [
      { type: "approve" },
      { type: "reject", message: "用户拒绝该工具调用。" },
    ] });
  });

  it("does not respond until every action has a choice", async () => {
    hooks.interrupts = [interrupt()];
    const user = userEvent.setup();
    render(<ApprovalPanel disabled={false} />);

    await screen.findAllByRole("radio", { name: /批准/ });
    const approveButtons = screen.getAllByRole("radio", { name: /批准/ });
    await user.click(approveButtons[0]);
    const submit = screen.getByRole("button", { name: "提交全部决定" });
    expect(submit).toBeDisabled();
    await user.click(submit);
    expect(hooks.respond).not.toHaveBeenCalled();
  });

  it("disables every decision control while disabled", async () => {
    hooks.interrupts = [interrupt()];
    const user = userEvent.setup();
    render(<ApprovalPanel disabled />);

    await screen.findAllByRole("radio", { name: /批准/ });
    for (const radio of screen.getAllByRole("radio")) {
      expect(radio).toBeDisabled();
    }
    const submit = screen.getByRole("button", { name: "提交全部决定" });
    expect(submit).toBeDisabled();
    await user.click(submit).catch(() => undefined);
    expect(hooks.respond).not.toHaveBeenCalled();
  });
});
