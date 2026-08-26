/** Task 18：WorkspaceDrawer —— 可访问抽屉（命名 dialog / 焦点进出与陷阱 / Esc / 背板 / 宽度）。 */
import { useState } from "react";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { WorkspaceDrawer } from "./WorkspaceDrawer";

afterEach(cleanup);

/** 受控抽屉骨架：打开按钮既是触发器也是焦点归还目标。 */
function Harness(props: Omit<React.ComponentProps<typeof WorkspaceDrawer>, "open" | "onClose">) {
  const [open, setOpen] = useState(false);
  const onClose = vi.fn(() => setOpen(false));
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>打开抽屉</button>
      <WorkspaceDrawer open={open} onClose={onClose} {...props} />
    </>
  );
}

function closeSpy(props: Partial<React.ComponentProps<typeof WorkspaceDrawer>>) {
  const onClose = vi.fn();
  return { onClose, element: <WorkspaceDrawer open onClose={onClose} title="会话线程" side="left" {...props} /> };
}

describe("WorkspaceDrawer", () => {
  it("关闭时保持懒挂载，打开后呈现命名 modal dialog 与关闭按钮", async () => {
    const user = userEvent.setup();
    render(<Harness title="会话线程" side="left"><p>线程内容</p></Harness>);
    expect(screen.queryByRole("dialog")).toBeNull();

    await user.click(screen.getByRole("button", { name: "打开抽屉" }));

    const dialog = screen.getByRole("dialog", { name: "会话线程" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog).toBeVisible();
    expect(screen.getByRole("button", { name: "关闭" })).toBeInTheDocument();
    expect(screen.getByText("线程内容")).toBeVisible();
  });

  it("打开后焦点移入抽屉，关闭时归还触发器焦点", async () => {
    const user = userEvent.setup();
    render(<Harness title="Inspector" side="right"><button type="button">面板动作</button></Harness>);
    const trigger = screen.getByRole("button", { name: "打开抽屉" });
    await user.click(trigger);
    const dialog = screen.getByRole("dialog", { name: "Inspector" });
    await waitFor(() => expect(dialog).toContainElement(document.activeElement));

    await user.click(screen.getByRole("button", { name: "关闭" }));

    await waitFor(() => expect(trigger).toHaveFocus());
    expect(screen.queryByRole("dialog", { name: "Inspector" })).toBeNull();
  });

  it("Tab / Shift+Tab 在抽屉内循环", async () => {
    const user = userEvent.setup();
    render(
      <Harness title="会话线程" side="left">
        <button type="button">第一个内容按钮</button>
        <button type="button">最后一个内容按钮</button>
      </Harness>,
    );
    await user.click(screen.getByRole("button", { name: "打开抽屉" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "关闭" })).toHaveFocus());

    await user.tab();
    expect(screen.getByRole("button", { name: "第一个内容按钮" })).toHaveFocus();
    await user.tab();
    expect(screen.getByRole("button", { name: "最后一个内容按钮" })).toHaveFocus();
    await user.tab();
    // 从最后一个回到抽屉内第一个可聚焦元素（关闭按钮）
    expect(screen.getByRole("button", { name: "关闭" })).toHaveFocus();
    await user.tab({ shift: true });
    expect(screen.getByRole("button", { name: "最后一个内容按钮" })).toHaveFocus();
  });

  it("Escape 与背板点击请求关闭", async () => {
    const user = userEvent.setup();
    const { onClose, element } = closeSpy({ children: <p>内容</p> });
    render(element);
    const dialog = screen.getByRole("dialog", { name: "会话线程" });
    await waitFor(() => expect(dialog).toContainElement(document.activeElement));

    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(1);

    onClose.mockClear();
    await user.click(screen.getByTestId("workspace-drawer-backdrop"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("首次打开后关闭再重开保留面板状态与滚动容器", async () => {
    const user = userEvent.setup();
    function Counter() {
      const [count, setCount] = useState(0);
      return (
        <div className="h-full overflow-y-auto">
          <button type="button" onClick={() => setCount((value) => value + 1)}>计数 {count}</button>
        </div>
      );
    }
    render(<Harness title="会话线程" side="left"><Counter /></Harness>);
    await user.click(screen.getByRole("button", { name: "打开抽屉" }));
    await user.click(screen.getByRole("button", { name: "计数 0" }));
    expect(screen.getByRole("button", { name: "计数 1" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "关闭" }));
    expect(screen.queryByRole("button", { name: /计数/ })).toBeNull();

    await user.click(screen.getByRole("button", { name: "打开抽屉" }));
    expect(screen.getByRole("button", { name: "计数 1" })).toBeInTheDocument();
  });

  it("宽度：普通抽屉 min(88vw,360px)，设置抽屉手机全宽 / 桌面固定宽", () => {
    const { onClose: onClosePanel, element: panel } = closeSpy({ variant: "panel", children: null });
    render(panel);
    expect(screen.getByRole("dialog", { name: "会话线程" })).toHaveClass("w-[min(88vw,360px)]");
    cleanup();

    const { onClose, element } = closeSpy({ title: "设置", variant: "settings", side: "right", children: null });
    render(element);
    expect(screen.getByRole("dialog", { name: "设置" })).toHaveClass("w-full", "xl:w-[480px]");
    void onClosePanel;
    void onClose;
  });
});
