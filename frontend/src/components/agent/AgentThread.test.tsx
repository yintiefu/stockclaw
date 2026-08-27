/** Task 9：聊天线程——标准 Composer、脚本化回复回显、无自定义重试/转向控件。 */
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { TestAgentRuntimeProvider } from "@/test/agent-runtime";

import { AgentThread } from "./AgentThread";

afterEach(cleanup);

function renderThread() {
  return render(
    <TestAgentRuntimeProvider>
      <AgentThread />
    </TestAgentRuntimeProvider>,
  );
}

describe("AgentThread", () => {
  it("renders the standard composer with the Chinese placeholder", () => {
    renderThread();
    const input = screen.getByRole("textbox", { name: "Agent 消息" });
    expect(input).toBeEnabled();
    expect(input).toHaveAttribute("placeholder", "输入投研问题");
    expect(screen.getByRole("button", { name: "发送", hidden: true })).toBeInTheDocument();
  });

  it("echoes a scripted reply after sending", async () => {
    const user = userEvent.setup();
    renderThread();

    await user.type(screen.getByRole("textbox", { name: "Agent 消息" }), "查询 600519 客观数据");
    await user.click(screen.getByRole("button", { name: "发送", hidden: true }));
    await waitFor(() => {
      expect(screen.getByText("回复：查询 600519 客观数据")).toBeVisible();
    });
  });

  it("exposes no retry or steer-away controls", () => {
    renderThread();
    expect(screen.queryByRole("button", { name: /重试本轮/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /转向/ })).toBeNull();
    expect(screen.queryByText(/Inspector/)).toBeNull();
  });
});

describe("AgentThread 待审批锁定", () => {
  it("approvalPending 时输入与发送禁用并显示提示", () => {
    render(
      <TestAgentRuntimeProvider>
        <AgentThread approvalPending />
      </TestAgentRuntimeProvider>,
    );
    const input = screen.getByRole("textbox", { name: "Agent 消息" });
    expect(input).toBeDisabled();
    expect(screen.getByRole("button", { name: "发送", hidden: true })).toBeDisabled();
    expect(screen.getByText("请先处理待审批工具调用")).toBeInTheDocument();
  });

  it("无待审批时保持可用且无提示", () => {
    render(
      <TestAgentRuntimeProvider>
        <AgentThread approvalPending={false} />
      </TestAgentRuntimeProvider>,
    );
    expect(screen.getByRole("textbox", { name: "Agent 消息" })).toBeEnabled();
    expect(screen.queryByText("请先处理待审批工具调用")).toBeNull();
  });
});
