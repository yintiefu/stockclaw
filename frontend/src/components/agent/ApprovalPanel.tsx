/** MCP 审批面板：全量中断决定表单，一次提交。 */
import { useState } from "react";
import { ShieldAlert } from "lucide-react";

import { useApprovalBridge, type PendingApproval } from "@/lib/agent/approval";
import type { ApprovalDecision } from "@/lib/agent/types";

type Choice = "approve_once" | "approve_session" | "reject";

const LABELS: Record<Choice, (name: string) => string> = {
  approve_once: (n) => `${n}：本次允许`,
  approve_session: (n) => `${n}：本会话允许`,
  reject: (n) => `${n}：拒绝`,
};

const ARGS_CHAR_LIMIT = 8000;

export function ApprovalPanel({ disabled }: { disabled: boolean }) {
  const { pending, resolveAll } = useApprovalBridge();
  const [choices, setChoices] = useState<Record<string, Choice>>({});
  const [submitting, setSubmitting] = useState(false);

  if (pending.length === 0) return null;

  const complete = pending.every((item) => choices[item.id]);

  const submit = async () => {
    if (!complete || disabled || submitting) return;
    setSubmitting(true);
    try {
      const decisions: ApprovalDecision[] = pending.map((item) => {
        const choice = choices[item.id];
        return {
          id: item.id,
          decision: choice === "reject" ? "reject" : "approve",
          scope: choice === "approve_session" ? "thread_session" : "once",
        };
      });
      await resolveAll(decisions);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <section className="glass-card space-y-3 rounded-xl p-4" aria-label="MCP 工具审批">
      <h2 className="flex items-center gap-1.5 text-sm font-semibold">
        <ShieldAlert className="size-4 text-primary" aria-hidden />
        MCP 工具调用需要审批（{pending.length} 项）
      </h2>
      <ul className="space-y-2">
        {pending.map((item) => (
          <li key={item.id} className="rounded-lg border border-border bg-black/20 p-3">
            <p className="text-sm">
              <span className="font-medium">{item.serverName ?? ""}</span>
              <span className="ml-1 font-mono text-xs text-muted-foreground">
                {item.toolAlias ?? item.message}
              </span>
            </p>
            {item.arguments && Object.keys(item.arguments).length > 0 && (
              <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap rounded bg-black/30 p-2 text-xs">
                {JSON.stringify(item.arguments, null, 2).slice(0, ARGS_CHAR_LIMIT)}
              </pre>
            )}
            <fieldset className="mt-2 flex flex-wrap gap-3 text-xs" disabled={disabled || submitting}>
              {(["approve_once", "approve_session", "reject"] as const).map((choice) => (
                <label key={choice} className="flex cursor-pointer items-center gap-1">
                  <input
                    type="radio"
                    name={`decision-${item.id}`}
                    aria-label={LABELS[choice](item.toolName ?? item.message)}
                    checked={choices[item.id] === choice}
                    onChange={() => setChoices((prev) => ({ ...prev, [item.id]: choice }))}
                  />
                  {LABELS[choice](item.toolName ?? item.message)}
                </label>
              ))}
            </fieldset>
          </li>
        ))}
      </ul>
      <button
        type="button"
        onClick={submit}
        disabled={disabled || submitting || !complete}
        className="rounded-lg bg-primary/20 px-4 py-2 text-sm font-medium text-primary hover:bg-primary/30 disabled:cursor-not-allowed disabled:opacity-50"
      >
        提交全部决定
      </button>
    </section>
  );
}

export type { PendingApproval };
