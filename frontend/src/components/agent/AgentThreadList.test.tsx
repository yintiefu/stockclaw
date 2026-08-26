/** Task 9：线程列表的 assistant-ui 原生原语契约——新建/切换/重命名/删除。 */
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { ComposerPrimitive } from "@assistant-ui/react";

import { TestAgentRuntimeProvider } from "@/test/agent-runtime";

import { AgentThreadList } from "./AgentThreadList";

afterEach(cleanup);

// 远程线程列表中，新线程只有发出首条消息（initialize）后才会出现在列表里；
// 测试用一个小型 Composer 驱动线程初始化。
function Harness() {
  return (
    <TestAgentRuntimeProvider>
      <AgentThreadList />
      <ComposerPrimitive.Root>
        <ComposerPrimitive.Input aria-label="Agent 消息" />
        <ComposerPrimitive.Send aria-label="发送消息" />
      </ComposerPrimitive.Root>
    </TestAgentRuntimeProvider>
  );
}

async function createThreads(user: ReturnType<typeof userEvent.setup>, count: number) {
  for (let index = 1; index <= count; index += 1) {
    await user.click(screen.getByRole("button", { name: "新建会话" }));
    await user.type(screen.getByRole("textbox", { name: "Agent 消息" }), `会话消息 ${index}`);
    await user.click(screen.getByRole("button", { name: "发送消息" }));
    if (index < count) {
      await waitFor(() => {
        expect(screen.queryAllByTestId(/^agent-thread-/)).toHaveLength(index);
      });
    }
  }
  await waitFor(() => {
    expect(screen.queryAllByTestId(/^agent-thread-/)).toHaveLength(count);
  });
  return screen.getAllByTestId(/^agent-thread-/);
}

/** 按标题文本定位某个线程条目（列表按更新时间倒序，不依赖数组顺序）。 */
function itemTitled(text: string) {
  const item = screen.getAllByTestId(/^agent-thread-/)
    .find((element) => element.textContent?.includes(text));
  if (!item) throw new Error(`找不到标题含 "${text}" 的线程条目`);
  return item;
}

describe("AgentThreadList", () => {
  it("lists initialized threads with the active one marked", async () => {
    const user = userEvent.setup();
    render(<Harness />);

    const items = await createThreads(user, 1);
    expect(items).toHaveLength(1);
    expect(items[0]).toHaveAttribute("data-active", "true");
    expect(within(items[0]).getByText("会话消息 1")).toBeVisible();
  });

  it("switches between threads via thread-list selection", async () => {
    const user = userEvent.setup();
    render(<Harness />);

    await createThreads(user, 2);
    const first = itemTitled("会话消息 1");
    const second = itemTitled("会话消息 2");
    await user.click(within(first).getByRole("button", { name: /会话消息 1/ }));
    await waitFor(() => {
      expect(first).toHaveAttribute("data-active", "true");
      expect(second).not.toHaveAttribute("data-active", "true");
    });
  });

  it("renames the active thread inline without window.prompt", async () => {
    const user = userEvent.setup();
    render(<Harness />);

    const [item] = await createThreads(user, 1);
    await user.click(within(item).getByRole("button", { name: "重命名会话" }), { pointerEventsCheck: 0 });
    const input = within(item).getByRole("textbox", { name: "会话标题" });
    await user.clear(input);
    await user.type(input, "客观核验会话");
    await user.click(within(item).getByRole("button", { name: "确认重命名" }));

    await waitFor(() => {
      expect(within(item).getByText("客观核验会话")).toBeVisible();
    });
  });

  it("cancels renaming with Escape and blocks blank titles", async () => {
    const user = userEvent.setup();
    render(<Harness />);

    const [item] = await createThreads(user, 1);
    await user.click(within(item).getByRole("button", { name: "重命名会话" }), { pointerEventsCheck: 0 });
    const input = within(item).getByRole("textbox", { name: "会话标题" });
    await user.clear(input);
    expect(within(item).getByRole("button", { name: "确认重命名" })).toBeDisabled();
    await user.keyboard("{Escape}");
    expect(within(item).queryByRole("textbox", { name: "会话标题" })).toBeNull();
  });

  it("deletes a thread from the list", async () => {
    const user = userEvent.setup();
    render(<Harness />);

    const [item] = await createThreads(user, 1);
    await user.click(within(item).getByRole("button", { name: "删除会话" }), { pointerEventsCheck: 0 });
    await waitFor(() => {
      expect(screen.queryByTestId(/^agent-thread-/)).toBeNull();
    });
  });

  it("does not render archive controls", async () => {
    const user = userEvent.setup();
    render(<Harness />);

    const [item] = await createThreads(user, 1);
    expect(within(item).queryByRole("button", { name: /归档/ })).toBeNull();
  });
});
