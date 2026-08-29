/** 设置中心布局:分区子导航、激活高亮与 index 重定向(mock api)。 */
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Navigate, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";
import { Settings } from "./Settings";
import { SettingsLayout } from "./SettingsLayout";
import { Skills } from "./Skills";

vi.mock("@/lib/api", () => ({
  api: {
    agentStatus: vi.fn(),
    skills: vi.fn(),
    setSkillEnabled: vi.fn(),
    deleteSkill: vi.fn(),
    importSkill: vi.fn(),
  },
  loadAccessKey: vi.fn(() => ""),
  saveAccessKey: vi.fn(),
}));

// vitest 配置 restoreMocks:true,实现必须在每个用例前重设
beforeEach(() => {
  vi.mocked(api.agentStatus).mockResolvedValue({
    configured: true,
    settings_path: "~/.vibe-research/agent/settings.json",
    model_name: "test-model",
    base_url_host: "example.invalid",
    builtin_skill_count: 0,
    mcp_server_count: 0,
    restart_required: false,
    config_template: "",
  });
  vi.mocked(api.skills).mockResolvedValue({ builtin: [], user: [], user_available: true });
  localStorage.clear();
  localStorage.setItem("vr-sidebar", "expanded");
  vi.stubGlobal("matchMedia", vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => true,
  })));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("SettingsLayout 设置中心", () => {
  function renderAt(path: string) {
    // 用 MemoryRouter 子树复刻 settings 路由形状:data router 的 Navigate 在 jsdom
    // 里会触发 undici 跨 realm AbortSignal 校验错误(环境限制,配置由浏览器验证)。
    return render(
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/settings" element={<SettingsLayout />}>
            <Route index element={<Navigate to="/settings/model" replace />} />
            <Route path="model" element={<Settings />} />
            <Route path="skills" element={<Skills />} />
            <Route path="skills/:source/:name" element={<div>技能详情内容</div>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );
  }

  it("子导航展示模型设置与技能管理两个分区", async () => {
    renderAt("/settings/model");
    const nav = await screen.findByRole("navigation", { name: "设置分区" });
    expect(nav.textContent).toContain("模型设置");
    expect(nav.textContent).toContain("技能管理");
  });

  it("/settings/skills 时技能管理高亮、模型设置不高亮", async () => {
    renderAt("/settings/skills");
    await screen.findByRole("navigation", { name: "设置分区" });
    const skills = screen.getByRole("link", { name: /技能管理/ });
    const model = screen.getByRole("link", { name: /模型设置/ });
    expect(skills).toHaveAttribute("aria-current", "page");
    expect(model).not.toHaveAttribute("aria-current", "page");
  });

  it("/settings index 重定向到模型设置分区", async () => {
    renderAt("/settings");
    await waitFor(() => expect(screen.getByRole("heading", { level: 1, name: "模型设置" })).toBeInTheDocument());
    expect(vi.mocked(api.agentStatus).mock.calls.length).toBeGreaterThan(0);
  });
});
