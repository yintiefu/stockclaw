/** Task 9：两栏工作台壳——桌面双栏/移动抽屉、审批位于聊天列内。 */
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { AgentWorkspace } from "./AgentWorkspace";

afterEach(cleanup);

const THREADS = <div data-testid="threads-content">线程列内容</div>;
const CHAT = <div data-testid="chat-content">聊天列内容</div>;
const APPROVAL = <section aria-label="MCP 工具审批">审批面板</section>;

describe("AgentWorkspace desktop", () => {
  it("renders the compact two-column shell", () => {
    render(<AgentWorkspace desktop threads={THREADS} chat={CHAT} approval={APPROVAL} />);
    expect(screen.getByTestId("agent-workspace")).toBeVisible();
    expect(screen.getByTestId("agent-threads-column")).toBeVisible();
    expect(screen.getByTestId("agent-chat-column")).toBeVisible();
    expect(screen.queryByRole("button", { name: "打开会话列表" })).toBeNull();
  });

  it("renders approval above the chat column content", () => {
    render(<AgentWorkspace desktop threads={THREADS} chat={CHAT} approval={APPROVAL} />);
    const column = screen.getByTestId("agent-chat-column");
    const approval = screen.getByRole("region", { name: "MCP 工具审批" });
    const chat = screen.getByTestId("chat-content");
    expect(column).toContainElement(approval);
    expect(column).toContainElement(chat);
    expect(approval.compareDocumentPosition(chat) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("renders without approval", () => {
    render(<AgentWorkspace desktop threads={THREADS} chat={CHAT} />);
    expect(screen.queryByRole("region", { name: "MCP 工具审批" })).toBeNull();
    expect(screen.getByTestId("chat-content")).toBeVisible();
  });
});

describe("AgentWorkspace mobile", () => {
  it("moves threads into an accessible drawer", async () => {
    const user = userEvent.setup();
    render(<AgentWorkspace desktop={false} threads={THREADS} chat={CHAT} approval={APPROVAL} />);

    expect(screen.getByTestId("agent-chat-column")).toBeVisible();
    expect(screen.queryByTestId("agent-threads-column")).toBeNull();

    await user.click(screen.getByRole("button", { name: "打开会话列表" }));
    const dialog = await screen.findByRole("dialog", { name: "会话" });
    expect(dialog).toContainElement(screen.getByTestId("threads-content"));

    await user.click(screen.getByRole("button", { name: "关闭" }));
    expect(screen.queryByRole("dialog", { name: "会话" })).toBeNull();
  });

  it("keeps approval inline above the composer area, never in the drawer", () => {
    render(<AgentWorkspace desktop={false} threads={THREADS} chat={CHAT} approval={APPROVAL} />);
    const column = screen.getByTestId("agent-chat-column");
    expect(column).toContainElement(screen.getByRole("region", { name: "MCP 工具审批" }));
  });
});
