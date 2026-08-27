// 工作流线程簿记：创建（metadata 打标，含 subject 隔离）/ 历史检索 / 删除 /
// 恢复时的权威详情读取。传输与对账已由 useWorkflowStream（useStream v2）承担。
import type { Client, Thread, ThreadState } from "@langchain/langgraph-sdk";
import { langGraphClient } from "./thread-adapter.ts";
import {
  effectiveWorkflowStatus,
  type WorkflowState,
  type WorkflowStatus,
} from "./workflow-types.ts";

export type WorkflowThreadClientSubset = {
  threads: Pick<Client["threads"], "create" | "delete" | "get" | "getState" | "search">;
};

export interface WorkflowThreadMetadata {
  title?: string;
  subject?: string;
}

export interface WorkflowThreadProjection {
  threadId: string;
  title?: string;
  subject?: string;
  workflowType: string;
  createdAt: string;
  updatedAt: string;
  threadStatus: Thread["status"];
  workflowStatus: WorkflowStatus;
  status: WorkflowStatus;
  resultSummary?: string;
}

export interface WorkflowEffectiveDetail {
  state: WorkflowState;
  threadStatus: Thread["status"];
  workflowStatus: WorkflowStatus;
  status: WorkflowStatus;
}

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

function projectThread(thread: Thread<WorkflowState>): WorkflowThreadProjection {
  const metadata = isRecord(thread.metadata) ? thread.metadata : {};
  const extracted = isRecord(thread.extracted) ? thread.extracted : {} as Record<string, unknown>;
  const values = isRecord(thread.values) ? thread.values : {} as Record<string, unknown>;
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

export function createWorkflowThreadClient(client: WorkflowThreadClientSubset) {
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
      const md: Record<string, unknown> = { channel: "workflow", workflow_type: workflowType };
      if (metadata.title !== undefined) md.title = metadata.title;
      if (metadata.subject !== undefined) md.subject = metadata.subject;
      const created = await client.threads.create({ metadata: md });
      return created.thread_id;
    },

    async getState(threadId: string): Promise<WorkflowState> {
      const state: ThreadState<WorkflowState> = await client.threads.getState<WorkflowState>(threadId);
      return state.values;
    },

    async getEffectiveDetail(threadId: string): Promise<WorkflowEffectiveDetail> {
      const [thread, state] = await Promise.all([
        client.threads.get(threadId),
        this.getState(threadId),
      ]);
      const workflowStatus = state.workflow_status;
      return {
        state,
        threadStatus: thread.status,
        workflowStatus,
        status: effectiveWorkflowStatus(thread.status, workflowStatus),
      };
    },

    async delete(threadId: string): Promise<void> {
      await client.threads.delete(threadId);
    },
  };
}

const workflowThreadClient = createWorkflowThreadClient(langGraphClient);

export async function searchWorkflowHistory(workflowType: string, subject?: string) {
  return workflowThreadClient.searchHistory(workflowType, subject);
}

export async function createWorkflowThread(
  workflowType: string,
  metadata: WorkflowThreadMetadata = {},
): Promise<string> {
  return workflowThreadClient.createThread(workflowType, metadata);
}

export async function getWorkflowState(threadId: string): Promise<WorkflowState> {
  return workflowThreadClient.getState(threadId);
}

export async function getEffectiveWorkflowDetail(threadId: string): Promise<WorkflowEffectiveDetail> {
  return workflowThreadClient.getEffectiveDetail(threadId);
}

export async function deleteWorkflowThread(threadId: string): Promise<void> {
  return workflowThreadClient.delete(threadId);
}
