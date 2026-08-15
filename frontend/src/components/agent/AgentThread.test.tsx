import { cleanup, render, screen } from "@testing-library/react";
import {
  AssistantRuntimeProvider,
  useExternalStoreRuntime,
} from "@assistant-ui/react";
import { afterEach, describe, expect, it } from "vitest";

import { AgentThread } from "./AgentThread";

function Harness({ isRunning }: { isRunning: boolean }) {
  const runtime = useExternalStoreRuntime({
    isRunning,
    messages: [],
    onNew: () => {},
    onCancel: () => {},
    onAddMessage: () => Promise.resolve(),
  });
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <AgentThread />
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

  it("运行中：输入禁用、发送隐藏、停止可用", () => {
    render(<Harness isRunning />);
    expect(screen.getByLabelText("Agent 消息")).toBeDisabled();
    expect(screen.queryByTitle("发送")).toBeNull();
    expect(screen.getByTitle("停止")).toBeInTheDocument();
  });
});
