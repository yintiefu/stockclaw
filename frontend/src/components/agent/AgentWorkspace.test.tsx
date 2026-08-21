import { useEffect, useState } from "react";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AgentWorkspace } from "./AgentWorkspace";
import { createAgentWorkspaceStore } from "@/lib/agent/workspace";

vi.mock("./AgentSettingsDrawer", () => ({
  AgentSettingsDrawer: ({ open, onClose }: { open: boolean; onClose: () => void }) =>
    open ? (
      <div role="dialog" aria-modal="true" aria-label="设置">
        <button type="button" aria-label="关闭" onClick={onClose}>关闭</button>
        设置内容
      </div>
    ) : null,
}));

import { AgentSettingsDrawer } from "./AgentSettingsDrawer";

afterEach(() => cleanup());

function StatefulThreads() {
  const [count, setCount] = useState(0);
  return (
    <div data-testid="threads-panel">
      <button type="button" onClick={() => setCount((value) => value + 1)}>线程动作 {count}</button>
    </div>
  );
}

type WorkspaceProps = Partial<Parameters<typeof AgentWorkspace>[0]>;

function renderWorkspace(props: WorkspaceProps = {}) {
  const store = props.store ?? createAgentWorkspaceStore();
  const view = render(
    <AgentWorkspace
      threadTitle="现金流核验"
      modelLabel="gpt-5"
      capabilityLabel="2 个 Skill"
      desktop
      store={store}
      threads={<StatefulThreads />}
      chat={<div data-testid="chat-panel">聊天内容</div>}
      inspector={<div data-testid="inspector-panel">检查器内容</div>}
      settings={<div data-testid="settings-panel">设置内容</div>}
      {...props}
      store={store}
    />,
  );
  return { ...view, store };
}

/** 用（桩化的）真实设置抽屉替换默认 settings 节点，验证互斥。 */
function SubscribedSettings({ store }: { store: ReturnType<typeof createAgentWorkspaceStore> }) {
  const [, force] = useState(0);
  useEffect(() => store.subscribe(() => force((value) => value + 1)), [store]);
  return (
    <AgentSettingsDrawer
      open={store.getState().drawer === "settings"}
      onClose={() => store.getState().openDrawer(null)}
    />
  );
}

function renderWorkspaceWithSettingsDrawer(props: WorkspaceProps = {}) {
  const store = props.store ?? createAgentWorkspaceStore();
  const view = render(
    <AgentWorkspace
      threadTitle="现金流核验"
      modelLabel="gpt-5"
      capabilityLabel="未选择 Skill"
      desktop={false}
      store={store}
      threads={<StatefulThreads />}
      chat={<div data-testid="chat-panel">聊天内容</div>}
      inspector={<div data-testid="inspector-panel">检查器内容</div>}
      settings={<SubscribedSettings store={store} />}
      {...props}
      store={store}
    />,
  );
  return { ...view, store };
}

describe("AgentWorkspace", () => {
  it("桌面三栏使用固定轨道并各自持有滚动区", () => {
    renderWorkspace();
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

  it("桌面常驻三栏，不渲染线程/Inspector 抽屉，设置由右覆盖抽屉承载", () => {
    renderWorkspace();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByTestId("settings-panel")).toBeInTheDocument();
    // 桌面只有设置命令；线程/Inspector 已是常驻列
    expect(screen.queryByRole("button", { name: "打开线程" })).toBeNull();
    expect(screen.queryByRole("button", { name: "打开 Inspector" })).toBeNull();
    expect(screen.getByRole("button", { name: "模型设置" })).toBeInTheDocument();
  });

  it("紧凑头部展示会话、模型、能力并暴露移动端三个图标命令", async () => {
    const user = userEvent.setup();
    const { store } = renderWorkspace({ desktop: false });
    expect(screen.getByRole("heading", { name: "现金流核验" })).toBeInTheDocument();
    expect(screen.getByText("gpt-5")).toBeInTheDocument();
    expect(screen.getByText("2 个 Skill")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "打开线程" }));
    expect(store.getState().drawer).toBe("threads");
    await user.click(screen.getByRole("button", { name: "打开 Inspector" }));
    expect(store.getState().drawer).toBe("inspector");
    await user.click(screen.getByRole("button", { name: "模型设置" }));
    expect(store.getState().drawer).toBe("settings");
  });

  it("窄屏工作台轨道只渲染聊天列，线程/Inspector 挂进抽屉", () => {
    renderWorkspace({ desktop: false });
    expect(screen.getByTestId("agent-chat-column")).toBeInTheDocument();
    expect(screen.queryByTestId("agent-threads-column")).toBeNull();
    expect(screen.queryByTestId("agent-inspector-column")).toBeNull();
    expect(screen.queryByTestId("threads-panel")).toBeNull(); // 抽屉未打开前懒挂载
    expect(screen.queryByTestId("inspector-panel")).toBeNull();
  });

  it("移动端线程/Inspector/设置抽屉互斥且关闭后保留面板状态", async () => {
    const user = userEvent.setup();
    const { store } = renderWorkspaceWithSettingsDrawer();

    await user.click(screen.getByRole("button", { name: "打开线程" }));
    expect(screen.getByRole("dialog", { name: "会话线程" })).toBeVisible();
    await user.click(screen.getByRole("button", { name: "线程动作 0" }));
    expect(screen.getByRole("button", { name: "线程动作 1" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "打开 Inspector" }));
    expect(screen.queryByRole("dialog", { name: "会话线程" })).toBeNull();
    expect(screen.getByRole("dialog", { name: "Inspector" })).toBeVisible();
    expect(store.getState().drawer).toBe("inspector");

    await user.click(screen.getByRole("button", { name: "模型设置" }));
    expect(screen.queryByRole("dialog", { name: "Inspector" })).toBeNull();
    expect(screen.getByRole("dialog", { name: "设置" })).toBeVisible();
    expect(store.getState().drawer).toBe("settings");

    // 关闭设置后重开线程：面板状态在抽屉开合间保留
    await user.click(screen.getByRole("button", { name: "关闭" }));
    expect(store.getState().drawer).toBeNull();
    await user.click(screen.getByRole("button", { name: "打开线程" }));
    expect(screen.getByRole("button", { name: "线程动作 1" })).toBeInTheDocument();
  });
});
