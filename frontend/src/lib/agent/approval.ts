import {
  useAgUiInterrupts,
  useAgUiSteerAway,
  useAgUiSubmitInterruptResponses,
} from "@assistant-ui/react-ag-ui";

/**
 * 唯一封装三个版本敏感 interrupt hooks 的生产模块。
 * 审批 UI 与会话级许可（thread_session）在 1C 落地，1A 无人调用。
 */
import type { ApprovalDecision, McpInterruptMetadata } from "./types";

export type PendingApproval = {
  id: string;
  toolCallId?: string;
  message: string;
} & Partial<McpInterruptMetadata>;

export function useApprovalBridge() {
  const interrupts = useAgUiInterrupts();
  const submit = useAgUiSubmitInterruptResponses();
  const steerAway = useAgUiSteerAway();
  const pending: PendingApproval[] = interrupts.map((item) => ({
    id: item.id,
    toolCallId: item.toolCallId,
    message: item.message ?? "Tool approval required",
    ...(item as unknown as McpInterruptMetadata).serverId
      ? {
          serverId: (item as unknown as McpInterruptMetadata).serverId,
          serverName: (item as unknown as McpInterruptMetadata).serverName,
          toolName: (item as unknown as McpInterruptMetadata).toolName,
          toolAlias: (item as unknown as McpInterruptMetadata).toolAlias,
          arguments: (item as unknown as McpInterruptMetadata).arguments,
        }
      : {},
  }));
  return {
    pending,
    // reject 固定 scope=once；approve 可选 once / thread_session
    resolveAll: (decisions: readonly ApprovalDecision[]) =>
      submit(decisions.map((item) => ({
        interruptId: item.id,
        status: "resolved" as const,
        payload: item.decision === "reject"
          ? { decision: "reject" as const, scope: "once" as const }
          : { decision: "approve" as const, scope: item.scope },
      }))),
    steerAway,
  };
}
