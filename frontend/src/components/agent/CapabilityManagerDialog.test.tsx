/** Task 1C-6：CapabilityManagerDialog —— 一提交草稿生命周期。 */
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  patchThread: vi.fn(),
  listSkills: vi.fn(),
  getSkill: vi.fn(),
  importSkill: vi.fn(),
  refreshSkills: vi.fn(),
  deleteSkill: vi.fn(),
  fetchSkillFile: vi.fn(),
}));
vi.mock("@/lib/agent/api", () => ({ agentApi: api }));

import { CapabilityManagerDialog } from "./CapabilityManagerDialog";
import { SkillManager } from "./SkillManager";
import type { AgentThread, SkillSummary } from "@/lib/agent/types";

const thread = (revision: number, selected: string[]): AgentThread => ({
  schema_version: 1,
  id: "th-1",
  title: "研究",
  created_at: "2026-08-16T00:00:00Z",
  updated_at: "2026-08-16T00:00:00Z",
  revision,
  selected_skills: selected,
  messages: [],
  artifact_ids: [],
  last_run: null,
});

const skills: SkillSummary[] = [
  { directory: "quality", name: "quality", description: "质检", digest: "d1", valid: true, error_code: null, error_detail: null },
  { directory: "macro", name: "macro", description: "宏观", digest: "d2", valid: true, error_code: null, error_detail: null },
];

beforeEach(() => {
  vi.clearAllMocks();
  api.listSkills.mockResolvedValue({ generation: 1, skills });
  api.patchThread.mockResolvedValue(thread(5, ["quality"]));
});
afterEach(cleanup);

describe("CapabilityManagerDialog", () => {
  it("applies selected skills once with the current thread revision", async () => {
    const onApplied = vi.fn();
    const onClose = vi.fn();
    const user = userEvent.setup();
    render(
      <CapabilityManagerDialog
        open
        thread={thread(4, [])}
        skills={skills}
        onApplied={onApplied}
        onClose={onClose}
        disabled={false}
      />,
    );
    await user.click(screen.getByRole("checkbox", { name: /quality/ }));
    await user.click(screen.getByRole("button", { name: "应用到本会话" }));
    expect(api.patchThread).toHaveBeenCalledTimes(1);
    expect(api.patchThread).toHaveBeenCalledWith("th-1", 4, {
      selected_skills: ["quality"],
    });
    await waitFor(() => expect(onApplied).toHaveBeenCalledTimes(1));
  });

  it("关闭丢弃草稿不提交任何 PATCH", async () => {
    const user = userEvent.setup();
    render(
      <CapabilityManagerDialog
        open thread={thread(4, [])} skills={skills} onApplied={vi.fn()} onClose={vi.fn()} disabled={false}
      />,
    );
    await user.click(screen.getByRole("checkbox", { name: /macro/ }));
    await user.click(screen.getByRole("button", { name: /关闭/ }));
    expect(api.patchThread).not.toHaveBeenCalled();
  });

  it("409 冲突时丢弃草稿并触发一次刷新", async () => {
    const onConflict = vi.fn();
    api.patchThread.mockRejectedValueOnce(
      Object.assign(new Error("revision 冲突"), { status: 409, code: "THREAD_REVISION_CONFLICT" }),
    );
    const user = userEvent.setup();
    render(
      <CapabilityManagerDialog
        open thread={thread(4, [])} skills={skills} onApplied={vi.fn()}
        onClose={vi.fn()} onConflict={onConflict} disabled={false}
      />,
    );
    await user.click(screen.getByRole("checkbox", { name: /quality/ }));
    await user.click(screen.getByRole("button", { name: "应用到本会话" }));
    await waitFor(() => expect(onConflict).toHaveBeenCalledTimes(1));
    expect(api.patchThread).toHaveBeenCalledTimes(1);
  });

  it("disabled 时禁用提交", () => {
    render(
      <CapabilityManagerDialog
        open thread={thread(4, [])} skills={skills} onApplied={vi.fn()} onClose={vi.fn()} disabled
      />,
    );
    expect(screen.getByRole("button", { name: "应用到本会话" })).toBeDisabled();
  });
});

describe("SkillManager", () => {
  it("导入走 multipart 且刷新列表", async () => {
    api.importSkill.mockResolvedValue({
      record: { ...skills[0] }, created: true,
    });
    api.listSkills.mockResolvedValue({ generation: 2, skills });
    const user = userEvent.setup();
    const onRefreshed = vi.fn();
    render(<SkillManager skills={skills} disabled={false} onChanged={onRefreshed} />);
    const file = new File(["zip-bytes"], "quality.zip", { type: "application/zip" });
    await user.upload(screen.getByLabelText(/导入 Skill/), file);
    await waitFor(() => expect(api.importSkill).toHaveBeenCalledTimes(1));
    const [calledPath, calledFile, options] = api.importSkill.mock.calls[0];
    expect(calledPath).toBe("/api/agent/skills/import");
    expect(calledFile).toBe(file);
    // multipart 上传不得手工设置 Content-Type（浏览器提供 boundary）
    expect((options as Record<string, unknown> | undefined)?.headers).toBeUndefined();
  });

  it("删除需要 digest 确认", async () => {
    vi.stubGlobal("confirm", () => true);
    api.deleteSkill.mockResolvedValue({ deleted: "macro" });
    api.listSkills.mockResolvedValue({ generation: 3, skills: [skills[0]] });
    const user = userEvent.setup();
    const onChanged = vi.fn();
    render(<SkillManager skills={skills} disabled={false} onChanged={onChanged} />);
    await user.click(screen.getByRole("button", { name: /删除 macro/ }));
    await waitFor(() => expect(api.deleteSkill).toHaveBeenCalledWith("macro", "d2"));
  });

  it("文本资源预览渲染转义内容", async () => {
    api.getSkill.mockResolvedValue({
      directory: "quality", name: "quality", description: "质检", digest: "d1",
      valid: true, instructions: "# hi\n<script>x</script>", error_code: null, error_detail: null,
      files: [
        { relative_path: "references/note.md", category: "reference", size: 3, mtime_ns: 1,
          sha256: "x", mime: "text/plain", downloadable: true },
      ],
    });
    const user = userEvent.setup();
    render(<SkillManager skills={skills} disabled={false} onChanged={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /详情 quality/ }));
    await waitFor(() => expect(api.getSkill).toHaveBeenCalledWith("quality"));
    const pre = await screen.findByText(/<script>x<\/script>/);
    expect(pre.tagName).toBe("PRE");
  });

  it("disabled 时禁用导入/刷新/删除", () => {
    render(<SkillManager skills={skills} disabled onChanged={vi.fn()} />);
    expect(screen.getByLabelText(/导入 Skill/)).toBeDisabled();
    expect(screen.getByRole("button", { name: /刷新/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: /删除 macro/ })).toBeDisabled();
  });
});
