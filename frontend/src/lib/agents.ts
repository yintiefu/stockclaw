// 多 agent 与工作流能力的前端客户端：多空辩论 + 反思审计 + 每日复盘 + 资讯摘要。
// 统一接入本地 LangGraph 工作流图，模型配置统一由服务端 agent/settings.json 管理。

import { runWorkflowStream } from "./agent/workflow-client.ts";
import type { WorkflowEvent } from "./workflow-stream.ts";

export type DebateStage = "bull" | "bear" | "bull_rebut" | "bear_rebut" | "referee";

export interface DebateHandlers {
  onStatus?: (message: string) => void;
  onDossierProgress?: (title: string, ok: boolean, loaded: number, total: number) => void;
  onDossierReady?: (sections: { title: string; tool: string }[], missing: string[]) => void;
  onStageStart?: (stage: DebateStage, label: string) => void;
  onDelta?: (stage: DebateStage, text: string) => void;
  onStageDone?: (stage: DebateStage, label: string, content: string) => void;
  onError?: (message: string, stage?: DebateStage) => void;
}

function dispatchDebateEvent(ev: WorkflowEvent, h: DebateHandlers) {
  switch (ev.type) {
    case "workflow_started":
      h.onStatus?.("多空辩论已启动，准备拉取客观事实底稿…");
      break;
    case "dossier_progress":
      h.onDossierProgress?.(ev.title, ev.status === "ok", ev.loaded, ev.total);
      break;
    case "dossier_completed":
      h.onDossierReady?.([], []);
      h.onStatus?.("底稿就绪，辩论开始");
      break;
    case "stage_started":
      h.onStageStart?.(ev.stage_id as DebateStage, ev.stage_id);
      break;
    case "stage_delta":
      h.onDelta?.(ev.stage_id as DebateStage, ev.delta);
      break;
    case "stage_completed":
      h.onStageDone?.(ev.stage_id as DebateStage, ev.stage_id, "");
      break;
    case "stage_failed":
      h.onError?.(ev.error.message, ev.stage_id as DebateStage);
      break;
    case "workflow_failed":
      h.onError?.(ev.error.message);
      break;
    case "workflow_completed":
      h.onStatus?.(ev.result_summary || "辩论完成");
      break;
  }
}

/** 跑一场多空辩论。rounds=2 时走 cross_exam 变体。 */
export async function debateStream(
  code: string,
  rounds: number,
  handlers: DebateHandlers = {},
  signal?: AbortSignal,
): Promise<void> {
  const variant = rounds > 1 ? "cross_exam" : "standard";
  await runWorkflowStream({
    assistantId: "debate",
    input: { code },
    variant,
    onEvent: (ev) => dispatchDebateEvent(ev, handlers),
    signal,
  });
}

export interface ReflectHandlers {
  onStatus?: (message: string) => void;
  onDelta?: (text: string) => void;
  onDone?: (content: string, truncated: boolean) => void;
  onError?: (message: string) => void;
}

/** 对一段已写好的分析做推理审计。 */
export async function reflectStream(
  source: string,
  title: string,
  handlers: ReflectHandlers = {},
  signal?: AbortSignal,
): Promise<void> {
  let acc = "";
  await runWorkflowStream({
    assistantId: "reflection",
    input: { source, title },
    onEvent: (ev) => {
      if (ev.type === "workflow_started") handlers.onStatus?.("反思审计开始…");
      else if (ev.type === "stage_delta") {
        acc += ev.delta;
        handlers.onDelta?.(ev.delta);
      } else if (ev.type === "stage_completed") handlers.onDone?.(acc, Boolean(ev.truncated));
      else if (ev.type === "stage_failed" || ev.type === "workflow_failed") handlers.onError?.(ev.error.message);
    },
    signal,
  });
}

export interface ReviewHandlers {
  onStatus?: (message: string) => void;
  onDelta?: (text: string) => void;
  onDone?: (content: string, truncated: boolean) => void;
  onError?: (message: string) => void;
}

/** 执行每日复盘工作流。 */
export async function dailyReviewStream(
  summary: string,
  date: string = "",
  handlers: ReviewHandlers = {},
  signal?: AbortSignal,
): Promise<void> {
  let acc = "";
  await runWorkflowStream({
    assistantId: "daily_review",
    input: { market_snapshot: summary, date },
    onEvent: (ev) => {
      if (ev.type === "workflow_started") handlers.onStatus?.("每日复盘开始…");
      else if (ev.type === "stage_delta") {
        acc += ev.delta;
        handlers.onDelta?.(ev.delta);
      } else if (ev.type === "stage_completed") handlers.onDone?.(acc, Boolean(ev.truncated));
      else if (ev.type === "stage_failed" || ev.type === "workflow_failed") handlers.onError?.(ev.error.message);
    },
    signal,
  });
}

/** 执行新闻摘要工作流。 */
export async function newsDigestStream(
  newsText: string,
  handlers: ReviewHandlers = {},
  signal?: AbortSignal,
): Promise<void> {
  let acc = "";
  await runWorkflowStream({
    assistantId: "news_digest",
    input: { news_snapshot: newsText },
    onEvent: (ev) => {
      if (ev.type === "workflow_started") handlers.onStatus?.("新闻摘要开始…");
      else if (ev.type === "stage_delta") {
        acc += ev.delta;
        handlers.onDelta?.(ev.delta);
      } else if (ev.type === "stage_completed") handlers.onDone?.(acc, Boolean(ev.truncated));
      else if (ev.type === "stage_failed" || ev.type === "workflow_failed") handlers.onError?.(ev.error.message);
    },
    signal,
  });
}
