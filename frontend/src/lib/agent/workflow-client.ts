// LangGraph 工作流客户端：管理工作流会话创建、流式执行、取消与状态查询。
// 严格按 channel 隔离，直连本地 LangGraph Server（通过 /agent-api 代理）。

import { langGraphClient, resolveAgentApiUrl } from "./thread-adapter.ts";
import { normalizeWorkflowEvent, type WorkflowEvent } from "../workflow-stream.ts";

export interface WorkflowRunOptions {
  assistantId: "debate" | "reflection" | "daily_review" | "news_digest" | string;
  input: Record<string, unknown>;
  variant?: string | null;
  threadId?: string;
  onEvent?: (ev: WorkflowEvent) => void;
  signal?: AbortSignal;
}

export interface WorkflowRunResult {
  threadId: string;
  runId: string;
  events: WorkflowEvent[];
  finalEvent?: WorkflowEvent;
}

/** 创建带有 channel 隔离元数据的工作流 Thread。 */
export async function createWorkflowThread(
  workflowType: string,
  metadata: Record<string, unknown> = {},
): Promise<string> {
  const thread = await langGraphClient.threads.create({
    metadata: {
      channel: workflowType,
      ...metadata,
    },
  });
  return thread.thread_id;
}

/** 按工作流类型检索历史线程。 */
export async function searchWorkflowThreads(workflowType: string) {
  const threads = await langGraphClient.threads.search({
    limit: 50,
    sortBy: "updated_at",
    sortOrder: "desc",
  });
  return threads.filter((t) => (t.metadata as Record<string, unknown>)?.channel === workflowType);
}

/** 流式执行指定工作流图。 */
export async function runWorkflowStream(
  options: WorkflowRunOptions,
): Promise<WorkflowRunResult> {
  const { assistantId, input, variant, onEvent, signal } = options;

  let threadId = options.threadId;
  if (!threadId) {
    threadId = await createWorkflowThread(assistantId, {
      title: `${assistantId} - ${input.code || input.title || "run"}`,
    });
  }

  const apiUrl = resolveAgentApiUrl();
  const streamUrl = `${apiUrl.replace(/\/$/, "")}/threads/${threadId}/runs/stream`;

  const payload: Record<string, unknown> = {
    assistant_id: assistantId,
    input: {
      input,
      ...(variant ? { variant } : {}),
    },
    stream_mode: ["custom", "updates"],
  };

  const resp = await fetch(streamUrl, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
    signal,
  });

  if (!resp.ok) {
    let errDetail = `HTTP ${resp.status}`;
    try {
      const errJson = await resp.json();
      errDetail = errJson.detail || errJson.message || errDetail;
    } catch {
      /* ignore */
    }
    throw new Error(`启动工作流失败: ${errDetail}`);
  }

  if (!resp.body) {
    throw new Error("后端无响应流");
  }

  const events: WorkflowEvent[] = [];
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let runId = "unknown";

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";

      let currentEventName = "message";
      for (const line of lines) {
        const trimmed = line.trimEnd();
        if (!trimmed || trimmed.startsWith(":")) continue;

        if (trimmed.startsWith("event:")) {
          currentEventName = trimmed.slice(6).trim();
          continue;
        }

        if (trimmed.startsWith("data:")) {
          const dataStr = trimmed.slice(5).trim();
          if (!dataStr) continue;

          try {
            const parsed = JSON.parse(dataStr);
            if (currentEventName === "custom" || parsed.type || parsed.event) {
              const ev = normalizeWorkflowEvent(parsed);
              if (ev.run_id) runId = ev.run_id;
              events.push(ev);
              onEvent?.(ev);
            }
          } catch {
            // 忽略非 JSON 数据行
          }
        }
      }
    }
  } catch (err: any) {
    if (signal?.aborted) {
      // 被主动中止，尝试发送取消请求
      try {
        if (runId && runId !== "unknown") {
          await cancelWorkflowRun(threadId, runId);
        }
      } catch {
        /* ignore */
      }
    }
    throw err;
  }

  return {
    threadId,
    runId,
    events,
    finalEvent: events[events.length - 1],
  };
}

/** 取消正在运行的工作流 Run。 */
export async function cancelWorkflowRun(threadId: string, runId: string): Promise<void> {
  const apiUrl = resolveAgentApiUrl();
  const cancelUrl = `${apiUrl.replace(/\/$/, "")}/threads/${threadId}/runs/${runId}/cancel`;
  try {
    await fetch(cancelUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    });
  } catch {
    /* 忽略取消失败错误 */
  }
}

/** 获取工作流 Thread 的最新 Checkpoint State。 */
export async function getWorkflowState(threadId: string) {
  return await langGraphClient.threads.getState(threadId);
}
