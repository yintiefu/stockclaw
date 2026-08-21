/** Task 18：AgentSettingsDrawer —— 模型 / Skill / MCP / Policy 四页签与抽屉关闭确认。 */
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  getPolicy: vi.fn(),
  patchPolicy: vi.fn(),
  resetPolicy: vi.fn(),
  patchThread: vi.fn(),
  getMcp: vi.fn(),
  testMcp: vi.fn(),
}));
vi.mock("@/lib/agent/api", () => ({ agentApi: api }));

import { AgentSettingsDrawer } from "./AgentSettingsDrawer";
import { loadAgentModelConfig } from "@/lib/agent/model-config";
import type { AgentPolicy, AgentThread, SkillSummary } from "@/lib/agent/types";

const policy = (over: Partial<AgentPolicy> = {}): AgentPolicy => ({
  schema_version: 1,
  revision: 3,
  updated_at: "2026-08-20T00:00:00Z",
  persisted: true,
  max_model_calls: 8,
  max_tool_calls: 16,
  tool_timeout_seconds: 30,
  max_active_seconds: 300,
  max_context_chars: 120_000,
  ...over,
});

const thread: AgentThread = {
  schema_version: 1,
  id: "th-1",
  title: "研究",
  created_at: "2026-08-16T00:00:00Z",
  updated_at: "2026-08-16T00:00:00Z",
  revision: 4,
  selected_skills: [],
  messages: [],
  artifact_ids: [],
  last_run: null,
};

const skills: SkillSummary[] = [
  { directory: "quality", name: "quality", description: "质检", digest: "d1", valid: true, error_code: null, error_detail: null },
];

const mcpDoc = {
  schema_version: 1 as const,
  revision: 2,
  servers: [{
    id: "demo",
    display_name: "演示",
    enabled: false,
    trust_fingerprint: "fp",
    transport: { type: "streamable_http" as const, url: "https://mcp.example/mcp", headers: {} },
    health: { state: "ok" as const, detail: "ok" },
    tools: [],
  }],
};

type SettingsProps = Partial<Parameters<typeof AgentSettingsDrawer>[0]>;

function renderSettings(props: SettingsProps = {}) {
  const onModelSaved = vi.fn();
  const onThreadReloaded = vi.fn();
  const onSkillsChanged = vi.fn();
  const onClose = vi.fn();
  const defaults = {
    open: true,
    onClose,
    thread,
    skills,
    modelBusy: false,
    selectionDisabled: false,
    onModelSaved,
    onThreadReloaded,
    onSkillsChanged,
  };
  const view = render(<AgentSettingsDrawer {...defaults} {...props} />);
  return { ...view, onModelSaved, onThreadReloaded, onSkillsChanged, onClose };
}

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  api.getPolicy.mockResolvedValue(policy());
  api.patchPolicy.mockImplementation(async (patch: { revision: number } & Record<string, number>) =>
    policy({ revision: patch.revision + 1 }));
  api.resetPolicy.mockResolvedValue(policy({ revision: 1 }));
  api.patchThread.mockResolvedValue({ ...thread, selected_skills: ["quality"], revision: 5 });
  api.getMcp.mockResolvedValue(mcpDoc);
  vi.stubGlobal("confirm", vi.fn(() => true));
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("AgentSettingsDrawer 页签", () => {
  it("提供四个可访问页签并支持左右方向键切换", async () => {
    const user = userEvent.setup();
    renderSettings();
    const tabs = ["模型", "Skill", "MCP", "Policy"];
    tabs.forEach((label) => expect(screen.getByRole("tab", { name: new RegExp(label) })).toBeInTheDocument());
    expect(screen.getByRole("tab", { name: /模型/ })).toHaveAttribute("aria-selected", "true");

    await user.click(screen.getByRole("tab", { name: /Policy/ }));
    expect(screen.getByRole("tab", { name: /Policy/ })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: /模型/ })).toHaveAttribute("aria-selected", "false");

    const policyTab = screen.getByRole("tab", { name: /Policy/ });
    policyTab.focus();
    await user.keyboard("{ArrowRight}");
    // 循环回到第一个页签「模型」
    expect(screen.getByRole("tab", { name: /模型/ })).toHaveFocus();
    expect(screen.getByRole("tab", { name: /模型/ })).toHaveAttribute("aria-selected", "true");
  });

  it("未访问过的页签不提前挂载，访问后保持挂载", async () => {
    const user = userEvent.setup();
    renderSettings();
    expect(api.getPolicy).not.toHaveBeenCalled();

    await user.click(screen.getByRole("tab", { name: /Policy/ }));
    await waitFor(() => expect(api.getPolicy).toHaveBeenCalledTimes(1));
    await user.click(screen.getByRole("tab", { name: /模型/ }));
    // 已访问页签保持挂载（隐藏不卸载），draft 不丢失
    expect(screen.getByLabelText(/上下文字符上限/)).toBeInTheDocument();
  });
});

describe("模型页签", () => {
  it("保存只写本机 vr-agent-model 并通知页面", async () => {
    const user = userEvent.setup();
    const { onModelSaved } = renderSettings();
    await user.type(screen.getByLabelText("Provider"), "deepseek");
    await user.type(screen.getByLabelText("Base URL"), "https://api.deepseek.com/v1");
    await user.type(screen.getByLabelText("模型名称"), "deepseek-chat");
    await user.type(screen.getByLabelText("API Key"), "sk-drawer");
    await user.click(screen.getByRole("button", { name: "保存模型配置" }));

    expect(loadAgentModelConfig()).toEqual({
      provider: "deepseek",
      baseURL: "https://api.deepseek.com/v1",
      model: "deepseek-chat",
      apiKey: "sk-drawer",
    });
    expect(Object.keys(localStorage)).toEqual(["vr-agent-model"]);
    expect(onModelSaved).toHaveBeenCalledTimes(1);
  });

  it("任一线程运行或待审批时模型身份禁用", () => {
    renderSettings({ modelBusy: true });
    ["Provider", "Base URL", "模型名称", "API Key"].forEach((label) => {
      expect(screen.getByLabelText(label)).toBeDisabled();
    });
    expect(screen.getByRole("button", { name: "保存模型配置" })).toBeDisabled();
  });
});

describe("Policy 页签", () => {
  const openPolicy = async (user: userEvent.UserEvent, loaded: AgentPolicy = policy()) => {
    api.getPolicy.mockResolvedValue(loaded);
    renderSettings();
    await user.click(screen.getByRole("tab", { name: /Policy/ }));
    await waitFor(() => expect(screen.getByLabelText(/上下文字符上限/)).toHaveValue(loaded.max_context_chars));
  };

  it("展示服务端默认值与 revision，未修改时保存禁用", async () => {
    const user = userEvent.setup();
    await openPolicy(user, policy({ revision: 0, persisted: false, updated_at: null }));
    expect(screen.getByText(/revision 0/)).toBeInTheDocument();
    expect(screen.getByLabelText(/单次运行模型调用上限/)).toHaveValue(8);
    expect(screen.getByLabelText(/单次运行工具调用上限/)).toHaveValue(16);
    expect(screen.getByLabelText(/工具超时（秒）/)).toHaveValue(30);
    expect(screen.getByRole("button", { name: "保存 Policy" })).toBeDisabled();
  });

  it("仅提交变更字段并带当前 revision", async () => {
    const user = userEvent.setup();
    await openPolicy(user);
    const field = screen.getByLabelText(/上下文字符上限/);
    await user.clear(field);
    await user.type(field, "90000");
    const save = screen.getByRole("button", { name: "保存 Policy" });
    expect(save).toBeEnabled();
    await user.click(save);
    expect(api.patchPolicy).toHaveBeenCalledTimes(1);
    expect(api.patchPolicy).toHaveBeenCalledWith({ revision: 3, max_context_chars: 90_000 });
  });

  it("小的上下文预算只给非阻断警告，不改值", async () => {
    const user = userEvent.setup();
    await openPolicy(user);
    expect(screen.queryByText(/Skill 指令可能占满上下文/)).toBeNull();
    const field = screen.getByLabelText(/上下文字符上限/);
    await user.clear(field);
    await user.type(field, "60000");
    expect(screen.getByText(/Skill 指令可能占满上下文/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "保存 Policy" }));
    expect(api.patchPolicy).toHaveBeenCalledWith(expect.objectContaining({ max_context_chars: 60_000 }));
  });

  it("409 冲突用 GET 替换草稿并提示", async () => {
    const user = userEvent.setup();
    await openPolicy(user);
    api.patchPolicy.mockRejectedValueOnce(
      Object.assign(new Error("Policy revision 冲突"), { status: 409, code: "POLICY_REVISION_CONFLICT" }),
    );
    api.getPolicy.mockResolvedValue(policy({ revision: 6, max_tool_calls: 24 }));
    const field = screen.getByLabelText(/单次运行工具调用上限/);
    await user.clear(field);
    await user.type(field, "20");
    await user.click(screen.getByRole("button", { name: "保存 Policy" }));

    await waitFor(() => expect(api.getPolicy).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.getByLabelText(/单次运行工具调用上限/)).toHaveValue(24));
    expect(screen.getByText(/已加载最新版本（revision 6）/)).toBeInTheDocument();
  });

  it("损坏时禁用普通保存、显示非密原因并要求二次确认后重置", async () => {
    const user = userEvent.setup();
    api.getPolicy.mockRejectedValue(
      Object.assign(new Error("policy.json 校验失败：max_model_calls 输入应为数字"), { status: 503, code: "POLICY_CORRUPT" }),
    );
    renderSettings();
    await user.click(screen.getByRole("tab", { name: /Policy/ }));
    await waitFor(() => expect(screen.getByText(/校验失败/)).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "保存 Policy" })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "重置损坏的 Policy" }));
    expect(api.resetPolicy).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "确认重置" }));

    await waitFor(() => expect(api.resetPolicy).toHaveBeenCalledWith({ confirm_corrupt: true }));
    await waitFor(() => expect(screen.getByText(/已重置为默认 Policy/)).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "保存 Policy" })).toBeDisabled();
  });

  it("运行期间 Policy 仍可编辑", async () => {
    const user = userEvent.setup();
    await openPolicy(user);
    expect(screen.getByLabelText(/上下文字符上限/)).toBeEnabled();
  });

  it("未保存的 Policy 修改在关闭前需要确认", async () => {
    const user = userEvent.setup();
    api.getPolicy.mockResolvedValue(policy());
    const { onClose } = renderSettings();
    await user.click(screen.getByRole("tab", { name: /Policy/ }));
    await waitFor(() => expect(screen.getByLabelText(/上下文字符上限/)).toHaveValue(120_000));
    const confirm = vi.mocked(window.confirm).mockReturnValueOnce(false);
    const field = screen.getByLabelText(/上下文字符上限/);
    await user.clear(field);
    await user.type(field, "90000");
    await user.click(screen.getByRole("button", { name: "关闭" }));
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(onClose).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "关闭" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("没有修改时关闭无需确认", async () => {
    const user = userEvent.setup();
    api.getPolicy.mockResolvedValue(policy());
    const view = renderSettings();
    await user.click(view.getByRole("tab", { name: /Policy/ }));
    await waitFor(() => expect(screen.getByLabelText(/上下文字符上限/)).toHaveValue(120_000));
    await user.click(screen.getByRole("button", { name: "关闭" }));
    expect(window.confirm).not.toHaveBeenCalled();
    expect(view.onClose).toHaveBeenCalledTimes(1);
  });
});

describe("Skill 页签", () => {
  it("一次 PATCH 应用当前线程选择", async () => {
    const user = userEvent.setup();
    const { onThreadReloaded } = renderSettings({ focusTab: "skills" });
    await user.click(screen.getByRole("checkbox", { name: "quality" }));
    await user.click(screen.getByRole("button", { name: "应用到本会话" }));
    await waitFor(() => expect(api.patchThread).toHaveBeenCalledTimes(1));
    expect(api.patchThread).toHaveBeenCalledWith("th-1", 4, { selected_skills: ["quality"] });
    await waitFor(() => expect(onThreadReloaded).toHaveBeenCalled());
  });

  it("409 冲突丢弃草稿并刷新权威线程", async () => {
    const user = userEvent.setup();
    api.patchThread.mockRejectedValueOnce(Object.assign(new Error("线程 revision 冲突"), { status: 409 }));
    const { onThreadReloaded } = renderSettings({ focusTab: "skills" });
    await user.click(screen.getByRole("checkbox", { name: "quality" }));
    await user.click(screen.getByRole("button", { name: "应用到本会话" }));
    await waitFor(() => expect(onThreadReloaded).toHaveBeenCalled());
    expect(screen.getByRole("checkbox", { name: "quality" })).not.toBeChecked();
    expect(api.patchThread).toHaveBeenCalledTimes(1);
  });

  it("线程忙时禁用应用到本会话，但 Skill 管理仍可用", () => {
    renderSettings({ focusTab: "skills", selectionDisabled: true });
    expect(screen.getByRole("button", { name: "应用到本会话" })).toBeDisabled();
    expect(screen.getByRole("button", { name: /详情 quality/ })).toBeEnabled();
  });
});

describe("MCP 页签", () => {
  it("进入时加载，测试连接后重新读取 REST 状态", async () => {
    const user = userEvent.setup();
    renderSettings({ focusTab: "mcp" });
    await waitFor(() => expect(api.getMcp).toHaveBeenCalledTimes(1));
    expect(screen.getByText("演示")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "测试连接" }));
    await waitFor(() => expect(api.testMcp).toHaveBeenCalledWith("demo", 2));
    await waitFor(() => expect(api.getMcp).toHaveBeenCalledTimes(2));
  });
});
