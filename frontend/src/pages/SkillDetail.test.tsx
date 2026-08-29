/** Task 9：技能详情页——Markdown 渲染、虚拟路径、启停与删除确认（全部 mock api）。 */
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api, type SkillDetail as SkillDetailType } from "@/lib/api";
import { SkillDetail } from "./SkillDetail";

vi.mock("@/lib/api", () => ({
  api: {
    skillDetail: vi.fn(),
    setSkillEnabled: vi.fn(),
    deleteSkill: vi.fn(),
  },
}));

const mocked = vi.mocked(api);

function userDetail(overrides: Partial<SkillDetailType> = {}): SkillDetailType {
  return {
    name: "research",
    description: "研究技能。",
    source: "user",
    enabled: true,
    valid: true,
    effective: true,
    error: null,
    path: "/user/research/SKILL.md",
    location: "/home/admin/.vibe-research/agent/skills/research/SKILL.md",
    instructions: "---\nname: research\ndescription: 研究技能。\n---\n\n# 指令\n\n- 项目一\n- 项目二\n\n| 列 A | 列 B |\n| --- | --- |\n| 1 | 2 |\n",
    ...overrides,
  };
}

function builtinDetail(): SkillDetailType {
  return {
    name: "debate",
    description: "多空辩论。",
    source: "builtin",
    enabled: true,
    valid: true,
    effective: true,
    error: null,
    path: "/builtin/debate/SKILL.md",
    location: "/opt/vibe-research/backend/builtin-skills/debate/SKILL.md",
    instructions: "# 内置指令\n",
  };
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/settings/skills/:source/:name" element={<SkillDetail />} />
        <Route path="/settings/skills" element={<div>技能列表页</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(() => cleanup());

describe("SkillDetail", () => {
  it("用户技能渲染 Markdown/GFM 与虚拟路径", async () => {
    mocked.skillDetail.mockResolvedValue(userDetail());
    renderAt("/settings/skills/user/research");
    await waitFor(() => expect(screen.getByRole("heading", { level: 1, name: "research" })).toBeInTheDocument());
    expect(screen.getByRole("heading", { level: 1, name: "指令" })).toBeInTheDocument();
    expect(screen.getByRole("table")).toBeInTheDocument();
    // 头部展示真实磁盘路径，而非虚拟路径
    expect(screen.getByText("/home/admin/.vibe-research/agent/skills/research/SKILL.md")).toBeInTheDocument();
    // 内容区剥离 frontmatter：不渲染元信息行
    expect(screen.queryByText(/name: research/)).toBeNull();
  });

  it("内置技能只读：无开关无删除，仅展示始终启用", async () => {
    mocked.skillDetail.mockResolvedValue(builtinDetail());
    renderAt("/settings/skills/builtin/debate");
    await waitFor(() => expect(screen.getByRole("heading", { level: 1, name: "debate" })).toBeInTheDocument());
    expect(screen.getByText(/内置 · 始终启用/)).toBeInTheDocument();
    expect(screen.queryByRole("switch")).toBeNull();
    expect(screen.queryByRole("button", { name: "删除技能" })).toBeNull();
  });

  it("名称/描述/技能内容三分区各自独立展示", async () => {
    mocked.skillDetail.mockResolvedValue(userDetail());
    renderAt("/settings/skills/user/research");
    await waitFor(() => expect(screen.getByRole("heading", { level: 1, name: "research" })).toBeInTheDocument());
    expect(screen.getByRole("region", { name: "名称" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "描述" })).toHaveTextContent("研究技能。");
    expect(screen.getByRole("region", { name: /技能内容/ })).toBeInTheDocument();
    // 用户技能显示「全局」徽章
    expect(screen.getByText("全局")).toBeInTheDocument();
  });

  it("无效技能显示安全错误且无 Markdown 正文", async () => {
    mocked.skillDetail.mockResolvedValue(userDetail({
      valid: false, enabled: true, effective: false,
      error: "技能 name 格式无效或与目录名不一致", instructions: null, description: null,
    }));
    renderAt("/settings/skills/user/broken");
    await waitFor(() => expect(screen.getByText(/name 格式无效/)).toBeInTheDocument());
    expect(screen.queryByRole("table")).toBeNull();
    // 活动无效：有停用命令、无启用开关
    expect(screen.getByRole("button", { name: "停用" })).toBeInTheDocument();
    expect(screen.queryByRole("switch")).toBeNull();
  });

  it("合法用户技能提供开关与删除", async () => {
    mocked.skillDetail.mockResolvedValue(userDetail());
    renderAt("/settings/skills/user/research");
    await waitFor(() => expect(screen.getByRole("switch")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "删除技能" })).toBeInTheDocument();
  });

  it("404 时显示不存在并提供返回", async () => {
    mocked.skillDetail.mockRejectedValue(Object.assign(new Error("用户技能不存在"), { status: 404 }));
    renderAt("/settings/skills/user/absent");
    await waitFor(() => expect(screen.getByText(/不存在/)).toBeInTheDocument());
    expect(screen.getByRole("link", { name: /返回技能列表/ })).toBeInTheDocument();
  });

  it("启停请求期间控件禁用", async () => {
    mocked.skillDetail.mockResolvedValue(userDetail());
    mocked.setSkillEnabled.mockReturnValue(new Promise(() => {}));
    const user = userEvent.setup();
    renderAt("/settings/skills/user/research");
    const toggle = await screen.findByRole("switch");
    await user.click(toggle);
    await waitFor(() => expect(toggle).toBeDisabled());
  });

  it("删除需要二次确认，成功后返回列表", async () => {
    mocked.skillDetail.mockResolvedValue(userDetail({ enabled: false, effective: false }));
    mocked.deleteSkill.mockResolvedValue({ ok: true });
    const user = userEvent.setup();
    renderAt("/settings/skills/user/research");
    await user.click(await screen.findByRole("button", { name: "删除技能" }));
    expect(await screen.findByText(/永久删除/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /确认删除/ }));
    await waitFor(() => expect(mocked.deleteSkill).toHaveBeenCalledWith("research"));
    await waitFor(() => expect(screen.getByText("技能列表页")).toBeInTheDocument());
  });

  it("失败的删除保留当前页面", async () => {
    mocked.skillDetail.mockResolvedValue(userDetail({ enabled: false, effective: false }));
    mocked.deleteSkill.mockRejectedValue(new Error("技能目录不可写"));
    const user = userEvent.setup();
    renderAt("/settings/skills/user/research");
    await user.click(await screen.findByRole("button", { name: "删除技能" }));
    await user.click(await screen.findByRole("button", { name: /确认删除/ }));
    await waitFor(() => expect(screen.getByText(/技能目录不可写/)).toBeInTheDocument());
    expect(screen.getByRole("heading", { level: 1, name: "research" })).toBeInTheDocument();
  });

  it("非法 source 直接拒绝", () => {
    renderAt("/settings/skills/other/research");
    expect(screen.getByText(/来源无效/)).toBeInTheDocument();
    expect(mocked.skillDetail).not.toHaveBeenCalled();
  });
});
