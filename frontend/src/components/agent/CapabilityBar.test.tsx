/** Task 1C-6：CapabilityBar —— 紧凑能力摘要与命令。 */
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CapabilityBar } from "./CapabilityBar";
import type { AgentThread } from "@/lib/agent/types";

afterEach(cleanup);

const thread = (selected: string[]): AgentThread => ({
  schema_version: 1,
  id: "th-1",
  title: "研究",
  created_at: "2026-08-16T00:00:00Z",
  updated_at: "2026-08-16T00:00:00Z",
  revision: 4,
  selected_skills: selected,
  messages: [],
  artifact_ids: [],
  last_run: null,
});

describe("CapabilityBar", () => {
  it("显示已选 Skill 数量并触发打开管理", async () => {
    const onOpen = vi.fn();
    const user = userEvent.setup();
    render(<CapabilityBar thread={thread(["quality"])} onOpenManager={onOpen} disabled={false} />);
    expect(screen.getByText(/已选 1 个 Skill/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /能力管理/ }));
    expect(onOpen).toHaveBeenCalledTimes(1);
  });

  it("未选 Skill 时显示空态", () => {
    render(<CapabilityBar thread={thread([])} onOpenManager={vi.fn()} disabled={false} />);
    expect(screen.getByText(/未选择 Skill/)).toBeInTheDocument();
  });

  it("disabled 时命令按钮不可用", () => {
    render(<CapabilityBar thread={thread([])} onOpenManager={vi.fn()} disabled />);
    expect(screen.getByRole("button", { name: /能力管理/ })).toBeDisabled();
  });
});
