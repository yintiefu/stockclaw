import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useWorkflowStream } from "./useWorkflowStream";

const streamMock = vi.hoisted(() => {
  type Snap = {
    values: Record<string, unknown>;
    messages: unknown[];
    threadId: string | null;
    isLoading: boolean;
    error: unknown;
  };
  const initial = (): Snap => ({ values: {}, messages: [], threadId: null, isLoading: false, error: undefined });
  const listeners = new Set<(s: Snap) => void>();
  const state = initial();
  const emit = () => { for (const l of [...listeners]) l({ ...state }); };
  const update = (patch: Partial<Snap>) => { Object.assign(state, patch); emit(); };
  const reset = () => { Object.assign(state, initial()); };
  const submit = vi.fn();
  const stop = vi.fn();
  return { state, listeners, submit, stop, update, reset };
});

const CONTROLLER = vi.hoisted(() => Symbol.for("test-controller"));

vi.mock("@langchain/react", async () => {
  const { useEffect, useState } = await import("react");
  return {
    STREAM_CONTROLLER: CONTROLLER,
    useStream: vi.fn(() => {
      const [snap, setSnap] = useState({ ...streamMock.state });
      useEffect(() => {
        const l = (v: typeof streamMock.state) => setSnap({ ...v });
        streamMock.listeners.add(l);
        return () => { streamMock.listeners.delete(l); };
      }, []);
      return { ...snap, submit: streamMock.submit, stop: streamMock.stop, [CONTROLLER]: { registry: {} } };
    }),
  };
});

const effectCallbacks: Array<(e: unknown) => void> = [];
vi.mock("@langchain/langgraph-sdk/stream", () => ({
  acquireChannelEffect: vi.fn((_registry, _channels, _ns, options) => {
    effectCallbacks.push(options.onEvent);
    return () => {};
  }),
}));

describe("useWorkflowStream", () => {
  beforeEach(() => { streamMock.reset(); vi.clearAllMocks(); effectCallbacks.length = 0; });

  it("derives transient text for the current stage until its pointer lands", () => {
    const { result } = renderHook(() => useWorkflowStream("debate", "t1"));
    act(() => {
      streamMock.update({
        values: { workflow_status: "running", current_stage: "bull", stages: {} },
        messages: [{ type: "ai", id: "s1", content: "多方流式中" }],
        isLoading: true, threadId: "t1",
      });
    });
    expect(result.current.transient).toEqual({ bull: "多方流式中" });
    act(() => {
      streamMock.update({
        values: {
          workflow_status: "running", current_stage: "bear",
          stages: { bull: { id: "bull", status: "completed", message_id: "s1" } },
        },
        isLoading: true,
      });
    });
    expect(result.current.transient).toEqual({});
  });

  it("forwards parsed dossier progress from the custom channel", () => {
    const seen: unknown[] = [];
    renderHook(() => useWorkflowStream("debate", "t1", (e) => seen.push(e)));
    act(() => {
      for (const cb of [...effectCallbacks]) {
        cb({ params: { data: { payload: { type: "dossier.progress", section_id: "q", section_status: "completed", completed: 1, total: 13 } } } });
        cb({ params: { data: { payload: { type: "stage.delta" } } } });
      }
    });
    expect(seen).toHaveLength(1);
  });

  it("submits state partials (with threadId option) and stops server-side", async () => {
    const { result } = renderHook(() => useWorkflowStream("debate", "t1"));
    await result.current.submit({ resume: true }, { threadId: "t1" });
    expect(streamMock.submit).toHaveBeenCalledWith({ resume: true }, { threadId: "t1" });
    await result.current.stop();
    expect(streamMock.stop).toHaveBeenCalled();
  });
});
