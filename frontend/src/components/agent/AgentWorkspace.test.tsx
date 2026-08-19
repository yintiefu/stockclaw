import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AgentWorkspace } from "./AgentWorkspace";

const model = { provider: "openai", baseURL: "https://api.openai.com/v1", model: "gpt-5", apiKey: "secret" };

afterEach(() => cleanup());

describe("AgentWorkspace", () => {
  it("桌面三栏使用固定轨道并各自持有滚动区", () => {
    render(
      <AgentWorkspace
        threadTitle="现金流核验"
        modelConfig={model}
        modelLabel={model.model}
        configured
        capabilityLabel="2 个 Skill"
        onModelConfigChange={() => {}}
        onSaveModel={() => {}}
        threads={<div>线程内容</div>}
        chat={<div>聊天内容</div>}
        inspector={<div>检查器内容</div>}
      />,
    );

    expect(screen.getByTestId("agent-workspace")).toHaveClass(
      "h-full",
      "min-h-0",
      "xl:grid-cols-[240px_minmax(480px,1fr)_320px]",
    );
    expect(screen.getByTestId("agent-threads-column")).toHaveClass("overflow-y-auto");
    expect(screen.getByTestId("agent-chat-column")).toHaveClass("overflow-hidden");
    expect(screen.getByTestId("agent-inspector-column")).toHaveClass("overflow-y-auto");
    expect(screen.getByTestId("agent-alert-area")).toHaveClass("min-h-10");
  });

  it("紧凑头部展示会话、模型、能力并暴露三个图标命令", async () => {
    const user = userEvent.setup();
    const onOpenThreads = vi.fn();
    const onOpenInspector = vi.fn();
    const onOpenSettings = vi.fn();
    render(
      <AgentWorkspace
        threadTitle="现金流核验"
        modelConfig={model}
        modelLabel={model.model}
        configured
        capabilityLabel="2 个 Skill"
        onModelConfigChange={() => {}}
        onSaveModel={() => {}}
        onOpenThreads={onOpenThreads}
        onOpenInspector={onOpenInspector}
        onOpenSettings={onOpenSettings}
        threads={null}
        chat={null}
        inspector={null}
      />,
    );

    expect(screen.getByRole("heading", { name: "现金流核验" })).toBeInTheDocument();
    expect(screen.getByText("gpt-5")).toBeInTheDocument();
    expect(screen.getByText("2 个 Skill")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "打开线程" }));
    await user.click(screen.getByRole("button", { name: "打开 Inspector" }));
    await user.click(screen.getByRole("button", { name: "模型设置" }));
    expect(onOpenThreads).toHaveBeenCalledOnce();
    expect(onOpenInspector).toHaveBeenCalledOnce();
    expect(onOpenSettings).toHaveBeenCalledOnce();
  });

  it("模型设置位于工作台右栏而不是页面大卡片", async () => {
    const user = userEvent.setup();
    const onSaveModel = vi.fn();
    render(
      <AgentWorkspace
        threadTitle="新会话"
        modelConfig={{ provider: "", baseURL: "", model: "", apiKey: "" }}
        modelLabel=""
        configured={false}
        capabilityLabel="未选择 Skill"
        onModelConfigChange={() => {}}
        onSaveModel={onSaveModel}
        threads={null}
        chat={<div>开始前请先完成模型配置</div>}
        inspector={null}
      />,
    );

    expect(screen.getByLabelText("Provider")).toBeInTheDocument();
    expect(screen.getByTestId("agent-settings")).not.toHaveClass("glass-card");
    await user.click(screen.getByRole("button", { name: "保存模型配置" }));
    expect(onSaveModel).toHaveBeenCalledOnce();
  });
});
