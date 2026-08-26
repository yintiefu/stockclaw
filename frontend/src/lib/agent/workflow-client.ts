import type { Client, Command, Thread, ThreadState, ThreadStatus } from "@langchain/langgraph-sdk";
import {
  clearWorkflowStreamCursor,
  loadWorkflowStreamCursor,
  saveWorkflowStreamCursor,
} from "../storage.ts";
import { langGraphClient } from "./thread-adapter.ts";
import {
  effectiveWorkflowStatus,
  parseWorkflowEvent,
  WORKFLOW_CONFIG_VERSIONS,
  type StageCompletedEvent,
  type StageResult,
  type WorkflowEvent,
  type WorkflowState,
  type WorkflowStatus,
} from "./workflow-types.ts";
import {
  applyWorkflowCheckpoint,
  initialWorkflowStreamState,
  reduceWorkflowStream,
  type WorkflowStreamState,
} from "./workflow-stream.ts";

export type WorkflowClientSubset = {
  threads: Pick<Client["threads"], "create" | "delete" | "get" | "getState" | "search" | "updateState">;
  runs: Pick<Client["runs"], "cancel" | "get" | "joinStream" | "stream">;
};

export interface WorkflowThreadProjection {
  threadId: string;
  title?: string;
  subject?: string;
  workflowType: string;
  createdAt: string;
  updatedAt: string;
  threadStatus: ThreadStatus;
  workflowStatus: WorkflowStatus;
  status: WorkflowStatus;
  resultSummary?: string;
}

export interface WorkflowThreadMetadata {
  title?: string;
  subject?: string;
  config_version?: number;
}

export interface WorkflowStartOptions {
  threadId: string;
  assistantId: string;
  input: Record<string, unknown> | null;
  signal?: AbortSignal;
  onRunCreated?: (runId: string) => void;
  onEvent?: (event: WorkflowClientEvent) => void;
  onState?: (state: WorkflowStreamState) => void;
}

export interface WorkflowReconnectOptions {
  runId?: string;
  signal?: AbortSignal;
  onEvent?: (event: WorkflowClientEvent) => void;
  onState?: (state: WorkflowStreamState) => void;
}

export interface WorkflowStreamResult {
  threadId: string;
  runId: string;
  stream: WorkflowStreamState;
}

export type WorkflowClientEvent = Exclude<WorkflowEvent, StageCompletedEvent>
  | (StageCompletedEvent & { content?: string | null });

export interface WorkflowEffectiveDetail {
  state: WorkflowState;
  threadStatus: ThreadStatus;
  workflowStatus: WorkflowStatus;
  status: WorkflowStatus;
}

export class WorkflowClientEventError extends Error {
  readonly code: string;
  readonly recoverable = true;

  constructor(code: string, message: string) {
    super(message);
    this.name = "WorkflowClientEventError";
    this.code = code;
  }
}

type StreamChunk = { id?: string; event: string; data: unknown };
type StreamSource = AsyncIterable<StreamChunk>;
const CHECKPOINT_POLL_DELAY_MS = 50;
const CHECKPOINT_POLL_MAX_ATTEMPTS = 20;
const SAFE_STAGE_ID = /^[a-z][a-z0-9_]*$/;
const SINGLE_PASS_ASSISTANTS = new Set(["reflection", "daily_review", "news_digest"]);

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const asString = (value: unknown): string | undefined =>
  typeof value === "string" ? value : undefined;

const asWorkflowStatus = (value: unknown): WorkflowStatus => {
  const statuses: WorkflowStatus[] = [
    "pending", "running", "completed", "partial", "failed", "cancelled", "interrupted",
  ];
  return statuses.includes(value as WorkflowStatus) ? value as WorkflowStatus : "pending";
};

function valuesOf(thread: Thread<WorkflowState>): Record<string, unknown> {
  return isRecord(thread.values) ? thread.values : {};
}

function metadataOf(thread: Thread<WorkflowState>): Record<string, unknown> {
  return isRecord(thread.metadata) ? thread.metadata : {};
}

function projectThread(thread: Thread<WorkflowState>): WorkflowThreadProjection {
  const metadata = metadataOf(thread);
  const extracted = isRecord(thread.extracted) ? thread.extracted : {};
  const values = valuesOf(thread);
  const workflowStatus = asWorkflowStatus(extracted.workflow_status ?? values.workflow_status);
  return {
    threadId: thread.thread_id,
    title: asString(metadata.title),
    subject: asString(metadata.subject),
    workflowType: asString(metadata.workflow_type) ?? "",
    createdAt: thread.created_at,
    updatedAt: thread.updated_at,
    threadStatus: thread.status,
    workflowStatus,
    status: effectiveWorkflowStatus(thread.status, workflowStatus),
    resultSummary: asString(extracted.result_summary ?? values.result_summary),
  };
}

function threadMetadata(workflowType: string, input: WorkflowThreadMetadata): Record<string, unknown> {
  const metadata: Record<string, unknown> = {
    channel: "workflow",
    workflow_type: workflowType,
  };
  if (input.title !== undefined) metadata.title = input.title;
  if (input.subject !== undefined) metadata.subject = input.subject;
  if (input.config_version !== undefined) metadata.config_version = input.config_version;
  return metadata;
}

function authoritativeState(state: WorkflowStreamState, checkpoint: WorkflowState): WorkflowStreamState {
  return {
    ...state,
    runId: checkpoint.event_run_id ?? state.runId,
    lastSeq: Math.max(state.lastSeq, checkpoint.event_seq ?? 0),
    currentStage: checkpoint.current_stage ?? null,
    transient: {},
    dirtyStages: [],
    dirtyRuns: [],
    pendingCheckpointStages: [],
    checkpoint,
    checkpointRequired: false,
  };
}

function isTerminalStatus(status: unknown): status is Exclude<WorkflowStatus, "pending" | "running"> {
  return status === "completed" || status === "partial" || status === "failed"
    || status === "cancelled" || status === "interrupted";
}

function isTerminalCheckpoint(state: WorkflowState, runId: string): boolean {
  return state.event_run_id === runId && isTerminalStatus(state.workflow_status);
}

function clearCursorForRun(threadId: string, runId: string): void {
  if (loadWorkflowStreamCursor(threadId)?.runId === runId) {
    clearWorkflowStreamCursor(threadId);
  }
}

function advanceCursorForRun(
  threadId: string,
  cursor: { runId: string; eventId: string; lastSeq: number },
): void {
  const current = loadWorkflowStreamCursor(threadId);
  if (!current || (current.runId === cursor.runId && cursor.lastSeq > current.lastSeq)) {
    saveWorkflowStreamCursor(threadId, cursor);
  }
}

function isVisibleEvent(state: WorkflowStreamState, event: WorkflowEvent): boolean {
  if (event.type !== "stage.delta") return true;
  return !state.dirtyRuns.includes(event.run_id)
    && !state.dirtyStages.includes(`${event.run_id}:${event.stage_id}`);
}

function terminalPatch(state: WorkflowState, status: "cancelled" | "interrupted") {
  const completedAt = new Date().toISOString();
  const stages = Object.fromEntries(Object.entries(state.stages ?? {}).map(([id, stage]) => [
    id,
    stage.status === "running"
      ? { ...stage, status, completed_at: stage.completed_at ?? completedAt }
      : stage,
  ])) as Record<string, StageResult>;
  return {
    workflow_status: status,
    current_stage: null,
    ...(status === "cancelled" ? { completed_at: completedAt } : {}),
    stages,
  };
}

function runOptions(
  input: Record<string, unknown> | null,
  onRunCreated: (params: { run_id: string; thread_id?: string }) => void,
  signal?: AbortSignal,
  command?: Command,
) {
  return {
    input,
    ...(command ? { command } : {}),
    streamMode: ["custom", "updates"] as ["custom", "updates"],
    streamResumable: true,
    onDisconnect: "continue" as const,
    durability: "sync" as const,
    onRunCreated,
    ...(signal ? { signal } : {}),
  };
}

function throwIfAborted(signal?: AbortSignal): void {
  if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
}

async function pollDelay(signal?: AbortSignal): Promise<void> {
  if (signal?.aborted) return;
  await new Promise<void>((resolve) => setTimeout(resolve, CHECKPOINT_POLL_DELAY_MS));
}

export function createWorkflowClient(client: WorkflowClientSubset) {
  async function getThreadState(threadId: string): Promise<ThreadState<WorkflowState>> {
    return client.threads.getState<WorkflowState>(threadId);
  }

  async function getRawState(threadId: string): Promise<WorkflowState> {
    return (await getThreadState(threadId)).values;
  }

  function checkpointTarget(state: ThreadState<WorkflowState>) {
    const checkpointId = state.checkpoint.checkpoint_id;
    return typeof checkpointId === "string" ? { checkpointId } : { checkpoint: state.checkpoint };
  }

  async function getEffectiveDetail(threadId: string): Promise<WorkflowEffectiveDetail> {
    const [thread, state] = await Promise.all([
      client.threads.get(threadId),
      getRawState(threadId),
    ]);
    const workflowStatus = state.workflow_status;
    return {
      state,
      threadStatus: thread.status,
      workflowStatus,
      status: effectiveWorkflowStatus(thread.status, workflowStatus),
    };
  }

  async function consume(
    threadId: string,
    getRunId: () => string,
    source: StreamSource,
    initial: WorkflowStreamState,
    callbacks: Pick<WorkflowStartOptions, "onEvent" | "onState" | "signal">,
  ): Promise<WorkflowStreamState> {
    let state = initial;
    for await (const chunk of source) {
      const expectedRunId = getRunId();
      if (chunk.event === "custom") {
        const parsed = parseWorkflowEvent(chunk.data);
        if (!expectedRunId) {
          if (parsed.kind === "event") console.warn("忽略尚未绑定 run 的工作流事件");
          throwIfAborted(callbacks.signal);
          continue;
        }
        if (parsed.kind === "event" && parsed.event.run_id !== expectedRunId) {
          console.warn("忽略非当前 run 的工作流事件");
          throwIfAborted(callbacks.signal);
          continue;
        }
        const previous = state;
        state = reduceWorkflowStream(state, parsed);
        const accepted = state !== previous;
        const acknowledge = () => {
          if (!chunk.id) return;
          advanceCursorForRun(threadId, {
            runId: expectedRunId, eventId: chunk.id, lastSeq: state.lastSeq,
          });
        };
        if (parsed.kind === "ignored" || !accepted) {
          acknowledge();
          throwIfAborted(callbacks.signal);
          continue;
        }
        if (parsed.kind === "error") {
          acknowledge();
          callbacks.onState?.(state);
          throwIfAborted(callbacks.signal);
          throw new WorkflowClientEventError(parsed.error.code, parsed.error.message);
        }
        callbacks.onState?.(state);
        throwIfAborted(callbacks.signal);
        if (parsed.event.type === "stage.completed") {
          let reconciled = false;
          for (let attempt = 0; attempt < CHECKPOINT_POLL_MAX_ATTEMPTS; attempt += 1) {
            const checkpoint = await getRawState(threadId);
            const nextState = applyWorkflowCheckpoint(state, checkpoint);
            if (nextState !== state) {
              state = nextState;
              callbacks.onState?.(state);
              throwIfAborted(callbacks.signal);
              reconciled = true;
              break;
            }
            throwIfAborted(callbacks.signal);
            const run = await client.runs.get(threadId, expectedRunId);
            if (run.status !== "pending" && run.status !== "running") break;
            if (attempt === CHECKPOINT_POLL_MAX_ATTEMPTS - 1) {
              throw new WorkflowClientEventError(
                "CHECKPOINT_RECONCILIATION_TIMEOUT",
                "权威检查点暂未就绪，请重新连接工作流",
              );
            }
            await pollDelay(callbacks.signal);
            throwIfAborted(callbacks.signal);
          }
          if (reconciled) {
            callbacks.onEvent?.({
              ...parsed.event,
              content: state.checkpoint?.stages[parsed.event.stage_id]?.content,
            });
            throwIfAborted(callbacks.signal);
            acknowledge();
          }
        } else if (isVisibleEvent(state, parsed.event)) {
          callbacks.onEvent?.(parsed.event);
          throwIfAborted(callbacks.signal);
          acknowledge();
        } else {
          acknowledge();
        }
      } else if (chunk.id && expectedRunId) {
        advanceCursorForRun(threadId, {
          runId: expectedRunId, eventId: chunk.id, lastSeq: state.lastSeq,
        });
      }
    }
    return state;
  }

  async function start(
    options: WorkflowStartOptions,
    command?: Command,
  ): Promise<WorkflowStreamResult> {
    let runId = "";
    const source = client.runs.stream(
      options.threadId,
      options.assistantId,
      runOptions(options.input, ({ run_id }) => {
        runId = run_id;
        saveWorkflowStreamCursor(options.threadId, { runId, eventId: "-1", lastSeq: 0 });
        options.onRunCreated?.(runId);
      }, options.signal, command),
    ) as StreamSource;
    let stream = initialWorkflowStreamState();
    stream = await consume(options.threadId, () => runId, source, stream, options);
    throwIfAborted(options.signal);
    if (!runId) throw new Error("工作流服务未返回 run_id");
    const finalState = await getRawState(options.threadId);
    if (finalState.event_run_id === runId) {
      stream = authoritativeState(stream, finalState);
      options.onState?.(stream);
      if (isTerminalCheckpoint(finalState, runId)) {
        clearCursorForRun(options.threadId, runId);
      }
    }
    return { threadId: options.threadId, runId, stream };
  }

  return {
    async searchHistory(workflowType: string, subject?: string): Promise<WorkflowThreadProjection[]> {
      const metadata: Record<string, unknown> = { channel: "workflow", workflow_type: workflowType };
      if (subject !== undefined) metadata.subject = subject;
      const threads = await client.threads.search<WorkflowState>({
        metadata,
        limit: 100,
        sortBy: "updated_at",
        sortOrder: "desc",
        extract: {
          workflow_status: "values.workflow_status",
          result_summary: "values.result_summary",
        },
      });
      return threads.map(projectThread);
    },

    async createThread(workflowType: string, metadata: WorkflowThreadMetadata = {}): Promise<string> {
      const created = await client.threads.create({ metadata: threadMetadata(workflowType, metadata) });
      return created.thread_id;
    },

    getDetail: getRawState,
    getEffectiveDetail,
    start,

    async reconnect(threadId: string, options: WorkflowReconnectOptions = {}): Promise<WorkflowStreamResult> {
      const saved = loadWorkflowStreamCursor(threadId);
      const runId = options.runId ?? saved?.runId;
      if (!runId) throw new Error("没有可重连的工作流 run_id");
      const resumeCursor = saved?.runId === runId ? saved : null;

      await client.threads.get(threadId);
      const run = await client.runs.get(threadId, runId);
      const checkpoint = await getRawState(threadId);
      const initialCheckpoint = checkpoint.event_run_id === runId ? checkpoint : null;
      let stream = initialWorkflowStreamState(runId, resumeCursor?.lastSeq ?? 0, initialCheckpoint);
      options.onState?.(stream);

      if (run.status === "pending" || run.status === "running") {
        if (!resumeCursor) {
          saveWorkflowStreamCursor(threadId, { runId, eventId: "-1", lastSeq: stream.lastSeq });
        }
        const source = client.runs.joinStream(threadId, runId, {
          lastEventId: resumeCursor?.eventId || "-1",
          streamMode: ["custom", "updates"],
          signal: options.signal,
        }) as StreamSource;
        try {
          stream = await consume(threadId, () => runId, source, stream, options);
          throwIfAborted(options.signal);
        } catch (error) {
          if (!options.signal?.aborted) {
            try {
              const finalState = await getRawState(threadId);
              if (finalState.event_run_id === runId) {
                stream = authoritativeState(stream, finalState);
                options.onState?.(stream);
                if (isTerminalCheckpoint(finalState, runId)) {
                  clearCursorForRun(threadId, runId);
                  return { threadId, runId, stream };
                }
              }
            } catch {
              // 保留原始流错误，cursor 留待下次恢复。
            }
          }
          throw error;
        }
        const finalState = await getRawState(threadId);
        if (finalState.event_run_id === runId) {
          stream = authoritativeState(stream, finalState);
          options.onState?.(stream);
        }
      }
      if (stream.checkpoint && isTerminalCheckpoint(stream.checkpoint, runId)) {
        clearCursorForRun(threadId, runId);
      }
      return { threadId, runId, stream };
    },

    async cancel(threadId: string, runId: string): Promise<void> {
      await client.runs.cancel(threadId, runId, true, "interrupt");
      const state = await getThreadState(threadId);
      if (state.values.event_run_id !== runId) return;
      await client.threads.updateState(threadId, {
        values: terminalPatch(state.values, "cancelled"),
        ...checkpointTarget(state),
      });
      clearCursorForRun(threadId, runId);
    },

    async retry(
      threadId: string,
      assistantId: string,
      options: Omit<WorkflowStartOptions, "threadId" | "assistantId" | "input"> = {},
    ): Promise<WorkflowStreamResult> {
      const staged = assistantId === "debate";
      if (!staged && !SINGLE_PASS_ASSISTANTS.has(assistantId)) {
        throw new Error("未知工作流不可重试");
      }
      const state = await getThreadState(threadId);
      // 配置版本不兼容：禁止用新配置静默恢复旧 checkpoint，只允许查看或重新发起。
      const currentVersion = WORKFLOW_CONFIG_VERSIONS[assistantId];
      const checkpointVersion = state.values.config_version;
      if (currentVersion !== undefined && checkpointVersion !== undefined
        && checkpointVersion !== currentVersion) {
        throw new Error("配置版本不兼容：请查看已有状态或重新发起工作流");
      }
      let command: Command | undefined;
      if (state.next.length === 0) {
        const stages = state.values.stages ?? {};
        const orderedStages = Object.values(stages);
        const retryIndex = orderedStages.findIndex((stage) =>
          ["running", "failed", "interrupted", "cancelled"].includes(stage.status));
        if (retryIndex < 0) throw new Error("没有可重试阶段");
        const retryable = orderedStages[retryIndex];
        if (!SAFE_STAGE_ID.test(retryable.id)) throw new Error("阶段 ID 不可重试");
        if (staged && orderedStages.slice(retryIndex + 1).some((stage) => stage.status === "completed")) {
          throw new Error("后续阶段已完成，请重新发起新工作流");
        }
        command = { goto: staged ? `start_${retryable.id}` : "start_stage" };
      }
      if (Object.values(state.values.stages ?? {}).some((stage) => stage.status === "running")) {
        await client.threads.updateState(threadId, {
          values: terminalPatch(state.values, "interrupted"),
          ...checkpointTarget(state),
        });
      }
      return start({ threadId, assistantId, input: null, ...options }, command);
    },

    async delete(threadId: string): Promise<void> {
      await client.threads.delete(threadId);
      clearWorkflowStreamCursor(threadId);
    },
  };
}

const workflowClient = createWorkflowClient(langGraphClient);

export async function searchWorkflowHistory(workflowType: string, subject?: string) {
  return workflowClient.searchHistory(workflowType, subject);
}

export async function searchWorkflowThreads(workflowType: string) {
  return workflowClient.searchHistory(workflowType);
}

export async function createWorkflowThread(
  workflowType: string,
  metadata: WorkflowThreadMetadata = {},
): Promise<string> {
  return workflowClient.createThread(workflowType, metadata);
}

export async function getWorkflowState(threadId: string): Promise<WorkflowState> {
  return workflowClient.getDetail(threadId);
}

export async function getEffectiveWorkflowDetail(threadId: string): Promise<WorkflowEffectiveDetail> {
  return workflowClient.getEffectiveDetail(threadId);
}

export async function reconnectWorkflowRun(threadId: string, options?: WorkflowReconnectOptions) {
  return workflowClient.reconnect(threadId, options);
}

/** 页面工作流（useWorkflowRun）使用的规范启动入口：消费 custom/updates 流。 */
export function startWorkflowRun(options: WorkflowStartOptions): Promise<WorkflowStreamResult> {
  return workflowClient.start(options);
}

export async function cancelWorkflowRun(
  threadId: string,
  runId: string,
): Promise<void> {
  return workflowClient.cancel(threadId, runId);
}

export async function retryWorkflowRun(
  threadId: string,
  assistantId: string,
  options?: Omit<WorkflowStartOptions, "threadId" | "assistantId" | "input">,
) {
  return workflowClient.retry(threadId, assistantId, options);
}

export async function deleteWorkflowThread(threadId: string): Promise<void> {
  return workflowClient.delete(threadId);
}
