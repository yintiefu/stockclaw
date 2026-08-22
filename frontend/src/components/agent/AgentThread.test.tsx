import { cleanup, render, screen } from "@testing-library/react";
import {
  AssistantRuntimeProvider,
  useExternalStoreRuntime,
} from "@assistant-ui/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("./SteerAwayComposer", () => ({
  SteerAwayComposer: () => <textarea aria-label="转向新问题" />,
}));

import { AgentThread } from "./AgentThread";

const ARTIFACT_RESULT = JSON.stringify({
  ok: true,
  artifact: {
    id: "artifact-1",
    title: "证据表",
    type: "table",
    run_id: "run-1",
    parent_artifact_id: null,
  },
  thread_revision: 2,
});

function Harness({
  isRunning,
  pendingApproval = false,
  statusNote = null,
  toolResult,
  onOpenArtifact,
}: {
  isRunning: boolean;
  pendingApproval?: boolean;
  statusNote?: string | null;
  toolResult?: unknown;
  onOpenArtifact?: (id: string) => void;
}) {
  const runtime = useExternalStoreRuntime({
    isRunning,
    messages: [
      { role: "user", content: [{ type: "text", text: "生成证据表" }] },
      {
        role: "assistant",
        content: [
          {
            type: "tool-call",
            toolCallId: "tc-1",
            toolName: "create_artifact",
            args: {},
            result: toolResult,
          },
          { type: "text", text: "完成" },
        ],
      },
    ],
    // 裸消息缺 id，经 convertMessage 由 runtime 自动补全（idx 作为 id）
    convertMessage: (m) => m as never,
    onNew: () => {},
    onCancel: () => {},
    onAddMessage: () => Promise.resolve(),
  });
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <AgentThread
        pendingApproval={pendingApproval}
        statusNote={statusNote}
        onOpenArtifact={onOpenArtifact}
      />
    </AssistantRuntimeProvider>
  );
}

afterEach(() => cleanup());

describe("AgentThread 运行态", () => {
  it("空闲时：输入可用、发送可见、无停止、无附件入口（无 adapter 自隐藏）", () => {
    render(<Harness isRunning={false} />);
    expect(screen.getByLabelText("Agent 消息")).not.toBeDisabled();
    expect(screen.getByTitle("发送")).toBeInTheDocument();
    expect(screen.queryByTitle("停止")).toBeNull();
    // spec §7.1 验证点：无 attachment adapter 时 AddAttachment 应自隐藏；
    // 若本断言失败，按 spec 从 thread.tsx 的 Composer 移除附件三件套引用后复跑
    expect(screen.queryByTitle("添加附件")).toBeNull();
  });

  it("运行中：输入禁用、只显示停止命令", () => {
    render(<Harness isRunning />);
    expect(screen.getByLabelText("Agent 消息")).toBeDisabled();
    expect(screen.queryByTitle("发送")).toBeNull();
    expect(screen.getByTitle("停止")).toBeInTheDocument();
  });

  it("终态错误区域高度稳定", () => {
    const { rerender } = render(<Harness isRunning={false} statusNote={null} />);
    const area = screen.getByTestId("agent-status-area");
    expect(area).toHaveClass("min-h-8");
    rerender(<Harness isRunning={false} statusNote="后端重启导致上次运行中断" />);
    expect(screen.getByTestId("agent-status-area")).toHaveClass("min-h-8");
    expect(screen.getByText("后端重启导致上次运行中断")).toBeInTheDocument();
  });

  it("等待审批时普通 Composer 让位给 steer composer", () => {
    render(<Harness isRunning={false} pendingApproval />);
    expect(screen.getByLabelText("转向新问题")).toBeInTheDocument();
    expect(screen.queryByLabelText("Agent 消息")).toBeNull();
  });

  it("create_artifact 结果的 Inspector 按钮在折叠面板外直接可见", () => {
    const opened: string[] = [];
    render(
      <Harness
        isRunning={false}
        toolResult={ARTIFACT_RESULT}
        onOpenArtifact={(id) => opened.push(id)}
      />,
    );
    // 关键契约：无需展开工具折叠条即可点击
    const button = screen.getByRole("button", { name: "在 Inspector 打开" });
    button.click();
    expect(opened).toEqual(["artifact-1"]);
  });

  it("无效 artifact 结果不渲染 Inspector 按钮", () => {
    render(
      <Harness
        isRunning={false}
        toolResult={{ ok: true, artifact: { id: "artifact-2" } }}
        onOpenArtifact={() => {}}
      />,
    );
    expect(screen.queryByRole("button", { name: "在 Inspector 打开" })).toBeNull();
  });
});
