// useWorkflowRun v2 控制器测试：mock useWorkflowStream（消费面）+ workflow-client。
// 覆盖 R3-R5 评审回归：C1 fail-closed、I3 跨线程污染、I4 retry 显式 threadId、
// I5 建线程失败、I6 首屏 pending、I7 restore 竞态、I1/I8 stop 收敛三分支、C2 终态优先。
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  createWorkflowThread,
  deleteWorkflowThread,
  getEffectiveWorkflowDetail,
  getWorkflowState,
} from "@/lib/agent/workflow-client";
import type { WorkflowState } from "@/lib/agent/workflow-types";
import { useWorkflowRun } from "./useWorkflowRun";

const streamMock = vi.hoisted(() => {
  type Snap = {
    values: Record<string, unknown>;
    messages: unknown[];
    threadId: string | null;
    isLoading: boolean;
    error: unknown;
    transient: Record<string, string>;
  };
  const initial = (): Snap => ({ values: {}, messages: [], threadId: null, isLoading: false, error: undefined, transient: {} });
  const listeners = new Set<() => void>();
  const state = initial();
  const bump = () => { for (const l of [...listeners]) l(); };
  const update = (patch: Partial<Snap>) => { Object.assign(state, patch); bump(); };
  const reset = () => { Object.assign(state, initial()); };
  const submit = vi.fn(async () => {});
  const stop = vi.fn(async () => {});
  return { state, listeners, submit, stop, update, reset };
});

vi.mock("@/hooks/useWorkflowStream", async () => {
  const { useEffect, useState } = await import("react");
  return {
    useWorkflowStream: vi.fn(() => {
      const [snap, setSnap] = useState({ ...streamMock.state });
      useEffect(() => {
        const l = () => setSnap({ ...streamMock.state });
        streamMock.listeners.add(l);
        return () => { streamMock.listeners.delete(l); };
      }, []);
      return {
        state: snap.values,
        running: snap.isLoading,
        threadId: snap.threadId,
        error: snap.error,
        transient: snap.transient,
        submit: streamMock.submit,
        stop: streamMock.stop,
      };
    }),
  };
});

vi.mock("@/lib/agent/workflow-client", () => ({
  createWorkflowThread: vi.fn(),
  deleteWorkflowThread: vi.fn(),
  getWorkflowState: vi.fn(),
  getEffectiveWorkflowDetail: vi.fn(),
  searchWorkflowHistory: vi.fn(),
}));

const mkState = (workflow_status: WorkflowState["workflow_status"], extra: Partial<WorkflowState> = {}): WorkflowState => ({
  workflow_status,
  stages: {},
  ...extra,
});

const detail = (threadStatus: "idle" | "busy" | "interrupted" | "error", state: WorkflowState) => ({
  state,
  threadStatus,
  workflowStatus: state.workflow_status,
  status: state.workflow_status,
});

describe("useWorkflowRun (v2)", () => {
  beforeEach(() => {
    streamMock.reset();
    vi.clearAllMocks();
    streamMock.submit.mockImplementation(async () => {});
    vi.mocked(createWorkflowThread).mockResolvedValue("t1");
    vi.mocked(getWorkflowState).mockResolvedValue(mkState("completed"));
    vi.mocked(getEffectiveWorkflowDetail).mockResolvedValue(detail("idle", mkState("completed")));
    vi.mocked(deleteWorkflowThread).mockResolvedValue(undefined);
  });
  afterEach(() => { vi.useRealTimers(); });

  it("① start：建线程（metadata 透传）→ submit({input},{threadId,onError})；failed checkpoint 派生错误且不抛（C1）", async () => {
    vi.mocked(getWorkflowState).mockResolvedValue(mkState("failed", {
      errors: [{ code: "MODEL_ERROR", message: "模型推理执行异常", retryable: true }],
    }));
    const { result } = renderHook(() => useWorkflowRun({ assistantId: "debate" }));

    let outcome: { state: WorkflowState | null; error: string | null } | undefined;
    await act(async () => {
      outcome = await result.current.start({
        input: { code: "600519" },
        variant: "standard",
        metadata: { title: "多空辩论 · 600519", subject: "600519" },
      });
    });

    expect(vi.mocked(createWorkflowThread)).toHaveBeenCalledWith("debate", {
      title: "多空辩论 · 600519", subject: "600519",
    });
    expect(streamMock.submit).toHaveBeenCalledWith(
      { input: { code: "600519" }, variant: "standard" },
      { threadId: "t1", onError: expect.any(Function) },
    );
    expect(outcome!.state?.workflow_status).toBe("failed");
    expect(outcome!.error).toContain("模型推理执行异常");
    expect(result.current.error).toContain("模型推理执行异常");
  });

  it("② fail closed：submit 正常 resolve 但读不到终态 → {state:null,error}，绝不双空（I3）", async () => {
    vi.mocked(getWorkflowState).mockResolvedValue(mkState("running"));
    const { result } = renderHook(() => useWorkflowRun({ assistantId: "debate" }));

    let outcome: { state: WorkflowState | null; error: string | null } | undefined;
    await act(async () => {
      outcome = await result.current.start({ input: { code: "600519" } });
    });
    expect(outcome).toEqual({ state: null, error: "工作流未能完成：未读取到终态，请在历史记录中确认" });

    // getState 持续 reject 同样 fail closed
    vi.mocked(getWorkflowState).mockRejectedValue(new Error("boom"));
    await act(async () => {
      outcome = await result.current.start({ input: { code: "600519" } });
    });
    expect(outcome!.state).toBeNull();
    expect(outcome!.error).toBeTruthy();
  });

  it("③ 跨线程不污染：A 线程 values 在场时 start B 失败，outcome.state 不得是 A 的状态（I3）", async () => {
    act(() => {
      streamMock.update({
        values: { workflow_status: "completed", stages: { bull: { id: "bull", status: "completed", message_id: "m1" } } },
        messages: [{ id: "m1", content: "A 线程正文" }],
        threadId: "tA",
      });
    });
    vi.mocked(getWorkflowState).mockRejectedValue(new Error("unreachable"));
    const { result } = renderHook(() => useWorkflowRun({ assistantId: "debate" }));

    let outcome: { state: WorkflowState | null; error: string | null } | undefined;
    await act(async () => {
      outcome = await result.current.start({ input: { code: "600519" } });
    });
    expect(outcome!.state).toBeNull();
    expect(outcome!.error).toBeTruthy();
  });

  it("④ createWorkflowThread reject → start 返回 {state:null,error} 且不抛（I5）", async () => {
    vi.mocked(createWorkflowThread).mockRejectedValue(new Error("网络不可达"));
    const { result } = renderHook(() => useWorkflowRun({ assistantId: "debate" }));

    let outcome: { state: WorkflowState | null; error: string | null } | undefined;
    await act(async () => {
      outcome = await result.current.start({ input: { code: "600519" } });
    });
    expect(outcome!.state).toBeNull();
    expect(outcome!.error).toBe("网络不可达");
    expect(result.current.error).toBe("网络不可达");
  });

  it("⑤ retry 始终 submit({resume:true},{threadId})：restore 后立即 retry 也提交到新线程；恢复失败有横幅（I4）", async () => {
    vi.mocked(createWorkflowThread).mockResolvedValue("t2");
    let resolveDetail!: (v: unknown) => void;
    vi.mocked(getEffectiveWorkflowDetail).mockImplementation(
      () => new Promise((resolve) => { resolveDetail = resolve; }),  // 详情悬挂：检验同步 threadIdRef
    );
    // dispatch 前是旧终态（无错误消息）；本次 run 的写入带新 completed_at + 错误
    let readCount = 0;
    vi.mocked(getWorkflowState).mockImplementation(async () => {
      readCount += 1;
      return readCount === 1
        ? mkState("failed", { completed_at: "T0" })
        : mkState("failed", {
            completed_at: "T1",
            errors: [{ code: "MODEL_ERROR", message: "模型推理执行异常", retryable: true }],
          });
    });
    const { result } = renderHook(() => useWorkflowRun({ assistantId: "debate" }));

    await act(async () => {
      const restoring = result.current.restore("t2");
      await result.current.retry();  // 详情未决时立即 retry
      resolveDetail(detail("idle", mkState("failed")));
      await restoring;
    });

    expect(streamMock.submit).toHaveBeenCalledWith(
      { resume: true },
      { threadId: "t2", onError: expect.any(Function) },
    );
    // transport 正常完成 + checkpoint failed → 横幅非空
    expect(result.current.error).toContain("模型推理执行异常");
  });

  it("⑨ retry 拒绝收敛：旧终态（篡改 checkpoint）不得短路失败判定——轮询到本次 run 写入的拒绝文案", async () => {
    vi.mocked(createWorkflowThread).mockResolvedValue("t3");
    // 前两次读到旧终态（completed_at 不变、无错误消息——模拟拒绝写入落盘延迟），
    // 第三次起才是本次 run 写入的拒绝终态。
    let readCount = 0;
    vi.mocked(getWorkflowState).mockImplementation(async () => {
      readCount += 1;
      if (readCount <= 2) return mkState("failed", { completed_at: "T0", config_version: 999 });
      return mkState("failed", {
        completed_at: "T1",
        config_version: 999,
        errors: [{ code: "RESUME_CONFIG_VERSION", message: "配置版本不兼容：请查看已有状态或重新发起工作流", retryable: false }],
      });
    });
    const { result } = renderHook(() => useWorkflowRun({ assistantId: "debate" }));

    await act(async () => {
      await result.current.restore("t3");
      await result.current.retry();
    });

    expect(result.current.error).toContain("配置版本不兼容");
  });

  it("⑥ restore 竞态：a 详情晚到不覆盖 b 的 restoredStatus（I7）", async () => {
    let resolveA!: (v: ReturnType<typeof detail>) => void;
    const pendingA = new Promise((resolve) => { resolveA = resolve; });
    vi.mocked(getEffectiveWorkflowDetail)
      .mockImplementationOnce(() => pendingA as never)
      .mockResolvedValueOnce(detail("idle", mkState("pending")));
    const { result } = renderHook(() => useWorkflowRun({ assistantId: "debate" }));

    await act(async () => {
      const pa = result.current.restore("a");
      await result.current.restore("b");
      resolveA(detail("busy", mkState("running")));
      await pa;
    });

    expect(result.current.threadId).toBe("b");
    // b 的 idle + pending → interrupted；若 a 的 busy 晚到覆盖则错误地变成 running
    expect(result.current.status).toBe("interrupted");
  });

  it("⑦ 首屏 pending；成功后 completed；restore busy 线程 values 推到 completed 仍 completed（I6/C2）", async () => {
    const { result } = renderHook(() => useWorkflowRun({ assistantId: "debate" }));
    expect(result.current.status).toBe("pending");

    vi.mocked(getWorkflowState).mockResolvedValue(mkState("completed", {
      stages: { bull: { id: "bull", status: "completed", message_id: "m1" } },
    }));
    await act(async () => {
      await result.current.start({ input: { code: "600519" } });
    });
    act(() => {
      streamMock.update({
        values: { workflow_status: "completed", stages: { bull: { id: "bull", status: "completed", message_id: "m1" } } },
        threadId: "t1",
        isLoading: false,
      });
    });
    expect(result.current.status).toBe("completed");

    // restore 一个 busy 线程（服务端 attach 的 run 已完成）：values 推到 completed 后不得停在 running
    vi.mocked(getEffectiveWorkflowDetail).mockResolvedValue(detail("busy", mkState("running")));
    await act(async () => { await result.current.restore("t2"); });
    act(() => {
      streamMock.update({ values: { workflow_status: "running", stages: {} }, threadId: "t2" });
    });
    expect(result.current.status).toBe("running");  // busy + running，正常
    act(() => {
      streamMock.update({
        values: { workflow_status: "completed", stages: {} },
        threadId: "t2",
      });
    });
    expect(result.current.status).toBe("completed");  // C2：终态压过陈旧 busy
  });

  it("⑧a stop：轮询到非 busy 后以最终线程状态收敛 restoredStatus，无错误（I1/I8）", async () => {
    vi.useFakeTimers();
    vi.mocked(getEffectiveWorkflowDetail)
      .mockResolvedValueOnce(detail("busy", mkState("running")))
      .mockResolvedValueOnce(detail("busy", mkState("running")))
      .mockResolvedValueOnce(detail("interrupted", mkState("running")));
    vi.mocked(createWorkflowThread).mockResolvedValue("t1");
    const { result } = renderHook(() => useWorkflowRun({ assistantId: "debate" }));

    await act(async () => { await result.current.restore("t1"); });
    expect(result.current.status).toBe("running");

    await act(async () => {
      const p = result.current.stop();
      await vi.advanceTimersByTimeAsync(1_600);
      await p;
    });

    expect(streamMock.stop).toHaveBeenCalled();
    expect(result.current.error).toBeNull();
    expect(result.current.status).toBe("interrupted");
  });

  it("⑧b stop：详情持续失败 → 报错，不得静默当成功（I8）", async () => {
    vi.useFakeTimers();
    vi.mocked(getEffectiveWorkflowDetail)
      .mockResolvedValueOnce(detail("busy", mkState("running")))  // restore("t1")
      .mockRejectedValue(new Error("查询失败"));                    // stop 轮询全部失败
    const { result } = renderHook(() => useWorkflowRun({ assistantId: "debate" }));
    await act(async () => { await result.current.restore("t1"); });

    await act(async () => {
      const p = result.current.stop();
      await vi.advanceTimersByTimeAsync(11_000);
      await p;
    });

    expect(result.current.error).toContain("无法确认服务端状态");
  });

  it("⑧c stop：await 期间换线程 → 不写任何状态（轮询旧 id、写入前校验）（I1）", async () => {
    vi.useFakeTimers();
    vi.mocked(getEffectiveWorkflowDetail)
      .mockResolvedValueOnce(detail("busy", mkState("running")))         // restore("t1")
      .mockResolvedValueOnce(detail("interrupted", mkState("running")))  // t1 的 stop 轮询收敛详情（若泄漏会写成 interrupted）
      .mockResolvedValue(detail("idle", mkState("completed")));          // restore("t2")
    const { result } = renderHook(() => useWorkflowRun({ assistantId: "debate" }));
    await act(async () => { await result.current.restore("t1"); });

    await act(async () => {
      const stopping = result.current.stop();
      await result.current.restore("t2");  // stream.stop() 期间换了线程
      await vi.advanceTimersByTimeAsync(600);
      await stopping;
    });
    act(() => {
      streamMock.update({ values: { workflow_status: "completed", stages: {} }, threadId: "t2" });
    });

    expect(result.current.threadId).toBe("t2");
    // t1 的 interrupted 不得写到 t2 头上：t2 是 idle + completed
    expect(result.current.status).toBe("completed");
    expect(result.current.error).toBeNull();
  });
});
