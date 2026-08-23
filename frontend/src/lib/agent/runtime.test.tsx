import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const useStreamRuntime = vi.hoisted(() => vi.fn(() => ({ __testRuntime: true })));

vi.mock("@assistant-ui/react-langchain", () => ({ useStreamRuntime }));
vi.mock("@assistant-ui/react", () => ({
  AssistantRuntimeProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

import { AgentRuntimeProvider } from "./runtime";
import { langGraphThreadAdapter } from "./thread-adapter";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("AgentRuntimeProvider", () => {
  it("builds the runtime from the fixed native options without any model secret", () => {
    const onThreadIdChange = vi.fn();
    render(
      <AgentRuntimeProvider onThreadIdChange={onThreadIdChange}>
        <div>content</div>
      </AgentRuntimeProvider>,
    );

    expect(useStreamRuntime).toHaveBeenCalledTimes(1);
    const options = useStreamRuntime.mock.calls[0][0];
    expect(options).toEqual({
      assistantId: "agent",
      apiUrl: "/agent-api",
      onThreadIdChange,
      unstable_threadListAdapter: langGraphThreadAdapter,
    });
    expect(JSON.stringify(options)).not.toContain("apiKey");
    expect(JSON.stringify(options)).not.toContain("vr-agent-model");
    expect(Object.keys(options)).not.toContain("threadId");
    expect(Object.keys(options)).not.toContain("create");
    expect(Object.keys(options)).not.toContain("delete");
  });

  it("forwards the settled canonical thread id to the observer", () => {
    const observed: Array<string | undefined> = [];
    render(
      <AgentRuntimeProvider onThreadIdChange={(threadId) => observed.push(threadId)}>
        <div>content</div>
      </AgentRuntimeProvider>,
    );
    const options = useStreamRuntime.mock.calls[0][0];
    options.onThreadIdChange("018f4f4e-7b2d-7f2a-8000-123456789abc");
    expect(observed).toEqual(["018f4f4e-7b2d-7f2a-8000-123456789abc"]);
  });

  it("omits the observer entirely when not provided", () => {
    render(
      <AgentRuntimeProvider>
        <div>content</div>
      </AgentRuntimeProvider>,
    );
    const options = useStreamRuntime.mock.calls[0][0];
    expect(options.onThreadIdChange).toBeUndefined();
  });
});
