import { cleanup, render, screen, within } from "@testing-library/react";
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
  onSelect: vi.fn(),
  onCreate: vi.fn(),
  onRename: vi.fn(),
  onDelete: vi.fn(),
});

function rowOf(title: RegExp) {
  const select = screen.getByRole("button", { name: title });
  const row = select.closest("[data-thread-row]");
  if (!row) throw new Error(`未找到标题为 ${title} 的会话行`);
  return row as HTMLElement;
}

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

  it("按更新时间分组显示今天/昨天/近 7 天/更早", () => {
    const now = new Date();
    const iso = (offsetDays: number) => {
      const date = new Date(now.getFullYear(), now.getMonth(), now.getDate() - offsetDays, 12);
      return date.toISOString();
    };
    render(
      <AgentThreadList
        {...props()}
        warnings={[]}
        threads={[
          { id: "t-today", title: "今天会话", updated_at: iso(0), revision: 1, last_run: null },
          { id: "t-yesterday", title: "昨天会话", updated_at: iso(1), revision: 1, last_run: null },
          { id: "t-week", title: "本周会话", updated_at: iso(3), revision: 1, last_run: null },
          { id: "t-earlier", title: "更早会话", updated_at: iso(30), revision: 1, last_run: null },
        ]}
      />,
    );
    expect(screen.getByText("今天")).toBeInTheDocument();
    expect(screen.getByText("昨天")).toBeInTheDocument();
    expect(screen.getByText("近 7 天")).toBeInTheDocument();
    expect(screen.getByText("更早")).toBeInTheDocument();
  });

  it("行内三点菜单支持重命名，Escape 关闭菜单", async () => {
    const user = userEvent.setup();
    const handlers = props();
    render(<AgentThreadList {...handlers} />);

    await user.click(screen.getByRole("button", { name: "新建会话" }));
    expect(handlers.onCreate).toHaveBeenCalledOnce();

    await user.click(within(rowOf(/一段很长的现金流核验会话标题/)).getByLabelText("会话操作"));
    await user.click(screen.getByRole("menuitem", { name: "重命名" }));
    await user.clear(screen.getByLabelText("新会话标题"));
    await user.type(screen.getByLabelText("新会话标题"), "新标题");
    await user.click(screen.getByRole("button", { name: "确认重命名" }));

    expect(handlers.onRename).toHaveBeenCalledWith("thread-running", "新标题");

    // Escape 关闭菜单
    await user.click(within(rowOf(/一段很长的现金流核验会话标题/)).getByLabelText("会话操作"));
    expect(screen.getByRole("menu")).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("行内重命名可取消且空标题不可确认", async () => {
    const user = userEvent.setup();
    const handlers = props();
    render(<AgentThreadList {...handlers} />);

    await user.click(within(rowOf(/行业供需/)).getByLabelText("会话操作"));
    await user.click(screen.getByRole("menuitem", { name: "重命名" }));
    await user.clear(screen.getByLabelText("新会话标题"));
    expect(screen.getByRole("button", { name: "确认重命名" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "取消重命名" }));

    expect(handlers.onRename).not.toHaveBeenCalled();
    expect(screen.getByText("行业供需")).toBeInTheDocument();
  });

  it("运行中的会话禁止删除，空闲会话删除需确认", async () => {
    const user = userEvent.setup();
    const handlers = props();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<AgentThreadList {...handlers} />);

    // 运行中：删除禁用
    await user.click(within(rowOf(/一段很长的现金流核验会话标题/)).getByLabelText("会话操作"));
    expect(screen.getByRole("menuitem", { name: "删除" })).toBeDisabled();
    await user.keyboard("{Escape}");

    // 空闲：确认后删除
    await user.click(within(rowOf(/行业供需/)).getByLabelText("会话操作"));
    await user.click(screen.getByRole("menuitem", { name: "删除" }));
    expect(confirm).toHaveBeenCalledOnce();
    expect(handlers.onDelete).toHaveBeenCalledWith("thread-failed");
    confirm.mockRestore();
  });
});
