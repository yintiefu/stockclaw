import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi, beforeEach } from "vitest";

// AgentRuntimeProvider / AgentThread 以轻量桩替代，专注页面自身状态契约
vi.mock("@/lib/agent/runtime", () => ({
  AgentRuntimeProvider: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="agent-runtime-stub">{children}</div>
  ),
}));
vi.mock("@/components/agent/AgentThread", () => ({
  AgentThread: () => <div data-testid="agent-thread-stub" />,
}));

import { Agent } from "./Agent";
import { loadAgentModelConfig } from "@/lib/agent/model-config";

describe("Agent 工作台页面", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    cleanup();
  });

  it("渲染模型表单与会话输入区", () => {
    render(<MemoryRouter><Agent /></MemoryRouter>);
    expect(screen.getByLabelText("Provider")).toBeInTheDocument();
    expect(screen.getByLabelText("Base URL")).toBeInTheDocument();
    expect(screen.getByLabelText("模型")).toBeInTheDocument();
    expect(screen.getByText("开始前请先完成模型配置")).toBeInTheDocument();
  });

  it("保存只写入 vr-agent-model，不碰 vr-llm", async () => {
    const user = userEvent.setup();
    render(<MemoryRouter><Agent /></MemoryRouter>);
    await user.type(screen.getByLabelText("Provider"), "deepseek");
    await user.type(screen.getByLabelText("Base URL"), "https://api.deepseek.com/v1");
    await user.type(screen.getByLabelText("模型"), "deepseek-chat");
    await user.type(screen.getByLabelText("API Key"), "page-secret");
    await user.click(screen.getByRole("button", { name: /保存/ }));
    expect(localStorage.getItem("vr-llm")).toBeNull();
    expect(loadAgentModelConfig()).toEqual({
      provider: "deepseek",
      baseURL: "https://api.deepseek.com/v1",
      model: "deepseek-chat",
      apiKey: "page-secret",
    });
  });

  it("表单不完整时不挂载 runtime", () => {
    render(<MemoryRouter><Agent /></MemoryRouter>);
    expect(screen.queryByTestId("agent-runtime-stub")).toBeNull();
  });

  it("表单完整时挂载 runtime 且错误提示不暴露密钥", async () => {
    const user = userEvent.setup();
    render(<MemoryRouter><Agent /></MemoryRouter>);
    await user.type(screen.getByLabelText("Provider"), "openai");
    await user.type(screen.getByLabelText("Base URL"), "https://api.openai.com/v1");
    await user.type(screen.getByLabelText("模型"), "gpt-5-mini");
    await user.type(screen.getByLabelText("API Key"), "sk-sentinel-key");
    await user.click(screen.getByRole("button", { name: /保存/ }));
    await waitFor(() => {
      expect(screen.getByTestId("agent-runtime-stub")).toBeInTheDocument();
    });
    // 页面可见文本里不应出现密钥
    expect(document.body.textContent ?? "").not.toContain("sk-sentinel-key");
  });
});
