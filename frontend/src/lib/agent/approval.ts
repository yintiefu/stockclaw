import {
  useAgUiInterrupts,
  useAgUiSteerAway,
  useAgUiSubmitInterruptResponses,
} from "@assistant-ui/react-ag-ui";

/**
 * 唯一封装三个版本敏感 interrupt hooks 的生产模块。
 * 审批 UI 与会话级许可（thread_session）在 1C 落地，1A 无人调用。
 */
export function useApprovalBridge() {
  const interrupts = useAgUiInterrupts();
  const submit = useAgUiSubmitInterruptResponses();
  const steerAway = useAgUiSteerAway();
  return {
    pending: interrupts.map((item) => ({
      id: item.id,
      toolCallId: item.toolCallId,
      message: item.message ?? "Tool approval required",
    })),
    resolveAll: (decisions: readonly { id: string; decision: "approve" | "reject" }[]) =>
      submit(decisions.map((item) => ({
        interruptId: item.id,
        status: "resolved" as const,
        payload: { decision: item.decision, scope: "once" },
      }))),
    steerAway,
  };
}
