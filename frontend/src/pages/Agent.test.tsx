/** Task 9：Agent 页面组合根——native runtime 边界内的两栏工作台契约。 */
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

// 只模拟 native runtime 边界：用真实 assistant-ui 运行时 + 内存线程列表适配器
vi.mock("@/lib/agent/runtime", async () => {
  const { TestAgentRuntimeProvider } = await import("@/test/agent-runtime");
  return {
    AgentRuntimeProvider: TestAgentRuntimeProvider,
  };
});

import { Agent } from "./Agent";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("Agent page", () => {
  it("renders the compact workspace shell without management affordances", () => {
    window.matchMedia = vi.fn().mockReturnValue({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() });
    render(<Agent />);

    expect(screen.getByTestId("agent-workspace")).toBeVisible();
    expect(screen.getByTestId("agent-threads-column")).toBeVisible();
    expect(screen.getByTestId("agent-chat-column")).toBeVisible();
    expect(screen.getByLabelText("Agent 消息")).toBeEnabled();

    expect(screen.queryByText("Inspector")).not.toBeInTheDocument();
    expect(screen.queryByText("管理 MCP")).not.toBeInTheDocument();
    expect(screen.queryByText("管理 Skills")).not.toBeInTheDocument();
    expect(screen.queryByText("预算")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "模型设置" })).not.toBeInTheDocument();
  });

  it("keeps the approval region stable while chatting", async () => {
    window.matchMedia = vi.fn().mockReturnValue({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() });
    render(<Agent />);

    const region = screen.getByRole("region", { name: "MCP 工具审批" });
    expect(region).toHaveTextContent("暂无待审批工具调用");

    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Agent 消息"), "查询客观数据");
    await user.click(screen.getByRole("button", { name: "发送", hidden: true }));
    await waitFor(() => {
      expect(screen.getByText("回复：查询客观数据")).toBeVisible();
    });
    expect(screen.getByRole("region", { name: "MCP 工具审批" })).toHaveTextContent("暂无待审批工具调用");
  });
});
