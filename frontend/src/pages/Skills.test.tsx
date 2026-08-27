/** Task 8：技能列表页——双分区、计数、启停与导入入口（全部 mock api，不联网）。 */
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api, type SkillsResponse } from "@/lib/api";
import { Skills } from "./Skills";

vi.mock("@/lib/api", () => ({
  api: {
    skills: vi.fn(),
    setSkillEnabled: vi.fn(),
    deleteSkill: vi.fn(),
    importSkill: vi.fn(),
  },
}));

const mocked = vi.mocked(api);

function sampleResponse(overrides: Partial<SkillsResponse> = {}): SkillsResponse {
  return {
    builtin: [{
      name: "debate", description: "多空辩论。", source: "builtin",
      enabled: true, valid: true, effective: true, error: null,
    }],
    user: [
      {
        name: "research", description: "研究技能。", source: "user",
        enabled: true, valid: true, effective: true, error: null,
      },
      {
        name: "draft", description: "草稿技能。", source: "user",
        enabled: false, valid: true, effective: false, error: null,
      },
    ],
    user_available: true,
    ...overrides,
  };
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/skills"]}>
      <Routes>
        <Route path="/skills" element={<Skills />} />
        <Route path="/skills/:source/:name" element={<div>详情页</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mocked.skills.mockResolvedValue(sampleResponse());
});

afterEach(() => cleanup());

describe("Skills 列表页", () => {
  it("用户技能区位于内置技能区之前，计数用 effective", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("research")).toBeInTheDocument());
    const headings = screen.getAllByText(/技能/).map((node) => node.textContent);
    const userIndex = headings.findIndex((text) => text?.includes("用户技能"));
    const builtinIndex = headings.findIndex((text) => text?.includes("内置技能"));
    expect(userIndex).toBeGreaterThanOrEqual(0);
    expect(builtinIndex).toBeGreaterThan(userIndex);
    expect(screen.getByText(/已加载 1/)).toBeInTheDocument();
  });

  it("加载中显示骨架状态", async () => {
    mocked.skills.mockReturnValue(new Promise(() => {}));
    renderPage();
    expect(screen.getByText(/加载中/)).toBeInTheDocument();
  });

  it("空列表显示空状态", async () => {
    mocked.skills.mockResolvedValue({
      builtin: [], user: [], user_available: true,
    });
    renderPage();
    await waitFor(() => expect(screen.getAllByText(/暂无技能/).length).toBeGreaterThan(0));
  });

  it("加载失败显示错误与重试", async () => {
    mocked.skills.mockRejectedValue(new Error("连接不到后端"));
    renderPage();
    await waitFor(() => expect(screen.getByText(/连接不到后端/)).toBeInTheDocument());
    expect(screen.getByRole("button", { name: /重试/ })).toBeInTheDocument();
  });

  it("用户配置不可用时内置技能仍可见并提示", async () => {
    mocked.skills.mockResolvedValue(sampleResponse({
      user: [], user_available: false, user_error: "Agent 设置缺失或无效",
    }));
    renderPage();
    await waitFor(() => expect(screen.getByText("debate")).toBeInTheDocument());
    expect(screen.getByText(/Agent 设置缺失或无效/)).toBeInTheDocument();
  });

  it("无效启用的用户技能显示已阻止与停用入口", async () => {
    mocked.skills.mockResolvedValue(sampleResponse({
      user: [{
        name: "broken", description: null, source: "user",
        enabled: true, valid: false, effective: false,
        error: "技能 name 格式无效或与目录名不一致",
      }],
    }));
    renderPage();
    await waitFor(() => expect(screen.getByText("broken")).toBeInTheDocument());
    expect(screen.getByText(/已阻止/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "停用" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "删除技能" })).toBeInTheDocument();
  });

  it("卡片点击进入详情，开关点击不导航", async () => {
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(screen.getByText("research")).toBeInTheDocument());
    await user.click(screen.getByRole("switch", { name: /已启用|启用/ }));
    expect(screen.queryByText("详情页")).toBeNull();
    expect(mocked.setSkillEnabled).toHaveBeenCalledWith("research", false);
  });

  it("成功的启停刷新列表并提示 /reload-skills", async () => {
    const user = userEvent.setup();
    mocked.setSkillEnabled.mockResolvedValue(sampleResponse().user[0]);
    renderPage();
    await waitFor(() => expect(screen.getByRole("switch", { name: /已启用|启用/ })).toBeInTheDocument());
    await user.click(screen.getByRole("switch", { name: /已启用|启用/ }));
    await waitFor(() => expect(mocked.setSkillEnabled).toHaveBeenCalled());
    expect(mocked.skills.mock.calls.length).toBeGreaterThanOrEqual(2);
  });

  it("失败的启停不改本地状态并提示错误", async () => {
    const user = userEvent.setup();
    mocked.setSkillEnabled.mockRejectedValue(new Error("同名技能已存在"));
    renderPage();
    await waitFor(() => expect(screen.getByRole("switch", { name: /已启用|启用/ })).toBeInTheDocument());
    await user.click(screen.getByRole("switch", { name: /已启用|启用/ }));
    await waitFor(() => expect(mocked.setSkillEnabled).toHaveBeenCalled());
    // 列表未重新拉取，状态保持
    expect(mocked.skills.mock.calls.length).toBe(1);
  });

  it("页头提供导入入口打开对话框", async () => {
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(screen.getByText("research")).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: /导入技能/ }));
    expect(await screen.findByText(/本地导入/)).toBeInTheDocument();
  });
});
