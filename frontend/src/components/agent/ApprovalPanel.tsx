/** MCP 审批面板：解析原生 LangChain HITL 中断，聚合决定一次提交。 */
import { useEffect, useState } from "react";
import { ShieldAlert } from "lucide-react";
import { useLangChainInterrupts, useLangChainRespond } from "@assistant-ui/react-langchain";

import { parseHitlRequest } from "@/lib/agent/approval";

const REJECT_MESSAGE = "用户拒绝该工具调用。";
const ARGS_CHAR_LIMIT = 8000;

type Choice = "approve" | "reject";

export function ApprovalPanel({ disabled }: { disabled: boolean }) {
  const interrupts = useLangChainInterrupts();
  const respond = useLangChainRespond();
  const request = parseHitlRequest(interrupts[0]?.value);
  const [choices, setChoices] = useState<Record<number, Choice>>({});
  const [submitting, setSubmitting] = useState(false);

  // 新中断到达时清空上一轮选择
  const interruptId = interrupts[0]?.id;
  useEffect(() => {
    setChoices({});
  }, [interruptId]);

  if (!request) {
    return (
      <section aria-label="MCP 工具审批" className="flex min-h-24 items-center justify-center rounded-md border border-dashed border-border px-3 text-center">
        <p role="status" className="text-xs text-muted-foreground">暂无待审批工具调用</p>
      </section>
    );
  }

  const complete = request.actions.every((_, index) => choices[index]);

  const submit = async () => {
    if (disabled || !complete || submitting) return;
    setSubmitting(true);
    try {
      await respond({
        decisions: request.actions.map((_, index) => choices[index] === "approve"
          ? { type: "approve" as const }
          : { type: "reject" as const, message: REJECT_MESSAGE }),
      });
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <section className="space-y-3 rounded-md border border-border p-3" aria-label="MCP 工具审批">
      <h2 className="flex items-center gap-1.5 text-sm font-semibold">
        <ShieldAlert className="size-4 text-primary" aria-hidden />
        MCP 工具调用需要审批（{request.actions.length} 项）
      </h2>
      <ul className="space-y-2">
        {request.actions.map((action, index) => (
          <li key={index} className="rounded-lg border border-border bg-black/20 p-3">
            <p className="text-sm">
              <span className="font-medium">{action.name}</span>
              {action.description ? (
                <span className="ml-1.5 text-xs text-muted-foreground">{action.description}</span>
              ) : null}
            </p>
            {action.args && Object.keys(action.args).length > 0 && (
              <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap rounded bg-black/30 p-2 text-xs">
                {JSON.stringify(action.args, null, 2).slice(0, ARGS_CHAR_LIMIT)}
              </pre>
            )}
            <fieldset className="mt-2 flex flex-wrap gap-3 text-xs" disabled={disabled || submitting}>
              {(["approve", "reject"] as const).map((choice) => (
                <label key={choice} className="flex cursor-pointer items-center gap-1">
                  <input
                    type="radio"
                    name={`decision-${interruptId ?? "0"}-${index}`}
                    aria-label={`${choice === "approve" ? "批准" : "拒绝"} ${action.name}`}
                    checked={choices[index] === choice}
                    onChange={() => setChoices((prev) => ({ ...prev, [index]: choice }))}
                  />
                  {choice === "approve" ? "批准" : "拒绝"}
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
