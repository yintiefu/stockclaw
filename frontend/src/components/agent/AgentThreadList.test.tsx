import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AgentThreadList } from "./AgentThreadList";
import type { AgentThreadSummary } from "@/lib/agent/types";

const threads: AgentThreadSummary[] = [
  {
    id: "thread-running",
    title: "一段很长的现金流核验会话标题",
    updated_at: "2026-08-19T09:30:00Z",
    revision: 3,
    last_run: { id: "run-1", status: "running", updated_at: "2026-08-19T09:30:00Z", retry_of: null },
  },
  {
    id: "thread-failed",
    title: "行业供需",
    updated_at: "2026-08-18T08:00:00Z",
    revision: 2,
    last_run: { id: "run-2", status: "failed", updated_at: "2026-08-18T08:00:00Z", retry_of: null },
  },
];

const props = () => ({
  threads,
  activeThreadId: "thread-running",
  warnings: [{ code: "DOCUMENT_CORRUPT" as const, document_type: "run" as const, filename: "run-bad.json.corrupt" }],
  canDeleteActive: true,
  onSelect: vi.fn(),
  onCreate: vi.fn(),
  onRename: vi.fn(),
  onDelete: vi.fn(),
});

afterEach(() => cleanup());

describe("AgentThreadList", () => {
  it("按标题本地搜索并保留选中态与完整标题 tooltip", async () => {
    const user = userEvent.setup();
    render(<AgentThreadList {...props()} />);

    const selected = screen.getByRole("button", { name: /一段很长的现金流核验会话标题/ });
    expect(selected).toHaveAttribute("aria-current", "true");
    expect(screen.getByText("一段很长的现金流核验会话标题")).toHaveAttribute(
      "title",
      "一段很长的现金流核验会话标题",
    );
    await user.type(screen.getByRole("searchbox", { name: "搜索会话" }), "供需");
    expect(screen.queryByText("一段很长的现金流核验会话标题")).toBeNull();
    expect(screen.getByText("行业供需")).toBeInTheDocument();
  });

  it("线程行展示更新时间、最近运行状态和恢复警告", () => {
    render(<AgentThreadList {...props()} />);
    expect(screen.getByText("运行中")).toBeInTheDocument();
    expect(screen.getByText("失败")).toBeInTheDocument();
    expect(screen.getAllByText(/08\/19|8月19日/).length).toBeGreaterThan(0);
    expect(screen.getByText(/run-bad\.json\.corrupt/)).toBeInTheDocument();
  });

  it("使用具名图标命令创建、重命名并确认删除", async () => {
    const user = userEvent.setup();
    const handlers = props();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<AgentThreadList {...handlers} />);

    await user.click(screen.getByRole("button", { name: "新建会话" }));
    await user.click(screen.getByRole("button", { name: "重命名会话" }));
    await user.clear(screen.getByLabelText("新会话标题"));
    await user.type(screen.getByLabelText("新会话标题"), "新标题");
    await user.click(screen.getByRole("button", { name: "确认重命名" }));
    await user.click(screen.getByRole("button", { name: "删除会话" }));

    expect(handlers.onCreate).toHaveBeenCalledOnce();
    expect(handlers.onRename).toHaveBeenCalledWith("thread-running", "新标题");
    expect(confirm).toHaveBeenCalledOnce();
    expect(handlers.onDelete).toHaveBeenCalledWith("thread-running");
    confirm.mockRestore();
  });
});
