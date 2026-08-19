import { cleanup, render, screen } from "@testing-library/react";
import {
  AssistantRuntimeProvider,
  useExternalStoreRuntime,
} from "@assistant-ui/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("./SteerAwayComposer", () => ({
  SteerAwayComposer: () => <textarea aria-label="转向新问题" />,
}));

import { AgentThread, ToolFallback } from "./AgentThread";

function Harness({ isRunning, pendingApproval = false }: { isRunning: boolean; pendingApproval?: boolean }) {
  const runtime = useExternalStoreRuntime({
    isRunning,
    messages: [],
    onNew: () => {},
    onCancel: () => {},
    onAddMessage: () => Promise.resolve(),
  });
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <AgentThread pendingApproval={pendingApproval} />
    </AssistantRuntimeProvider>
  );
}

function StatusHarness({ statusNote }: { statusNote: string | null }) {
  const runtime = useExternalStoreRuntime({
    isRunning: false,
    messages: [],
    onNew: () => {},
    onCancel: () => {},
    onAddMessage: () => Promise.resolve(),
  });
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <AgentThread statusNote={statusNote} />
    </AssistantRuntimeProvider>
  );
}

afterEach(() => cleanup());

describe("AgentThread 运行态", () => {
  it("空闲时：输入可用、发送可见、无停止", () => {
    render(<Harness isRunning={false} />);
    expect(screen.getByLabelText("Agent 消息")).not.toBeDisabled();
    expect(screen.getByTitle("发送")).toBeInTheDocument();
    expect(screen.queryByTitle("停止")).toBeNull();
  });

  it("运行中：只显示稳定的停止命令", () => {
    render(<Harness isRunning />);
    expect(screen.queryByLabelText("Agent 消息")).toBeNull();
    expect(screen.queryByTitle("发送")).toBeNull();
    expect(screen.getByTitle("停止")).toBeInTheDocument();
    expect(screen.getAllByRole("button")).toHaveLength(1);
  });

  it("终态错误区域高度稳定", () => {
    const { rerender } = render(<StatusHarness statusNote={null} />);
    const area = screen.getByTestId("agent-status-area");
    expect(area).toHaveClass("min-h-8");
    rerender(<StatusHarness statusNote="后端重启导致运行中断" />);
    expect(screen.getByTestId("agent-status-area")).toHaveClass("min-h-8");
    expect(screen.getByText("后端重启导致运行中断")).toBeInTheDocument();
  });

  it("等待审批时普通 Composer 让位给 steer composer", () => {
    render(<Harness isRunning={false} pendingApproval />);
    expect(screen.getByLabelText("转向新问题")).toBeInTheDocument();
    expect(screen.queryByLabelText("Agent 消息")).toBeNull();
  });

  it("成功 create_artifact 结果提供 Inspector 动作，其他结果安全降级", () => {
    const opened: string[] = [];
    const { rerender } = render(
      <ToolFallback
        toolName="create_artifact"
        result={JSON.stringify({
          ok: true,
          artifact: {
            id: "artifact-1",
            title: "证据表",
            type: "table",
            run_id: "run-1",
            parent_artifact_id: null,
          },
          thread_revision: 2,
        })}
        onOpenArtifact={(id) => opened.push(id)}
      />,
    );
    screen.getByRole("button", { name: "在 Inspector 打开" }).click();
    expect(opened).toEqual(["artifact-1"]);

    rerender(<ToolFallback toolName="create_artifact" result={{ ok: true, artifact: { id: "artifact-2" } }} onOpenArtifact={() => {}} />);
    expect(screen.queryByRole("button", { name: "在 Inspector 打开" })).toBeNull();

    rerender(<ToolFallback toolName="other_tool" result={{ artifact: { id: "artifact-2" } }} onOpenArtifact={() => {}} />);
    expect(screen.queryByRole("button", { name: "在 Inspector 打开" })).toBeNull();
  });
});
