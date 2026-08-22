import type { AgentPolicySnapshot, AgentRunDetail } from "@/lib/agent/types";

const STATUS_LABELS: Record<AgentRunDetail["status"], string> = {
  running: "运行中",
  awaiting_approval: "等待审批",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
  interrupted: "已中断",
};

function duration(ms: number) {
  if (ms < 1000) return `${ms} 毫秒`;
  const seconds = ms / 1000;
  return `${Number.isInteger(seconds) ? seconds : seconds.toFixed(1)} 秒`;
}

function count(value: number | null) {
  return value === null ? "未提供" : value.toLocaleString("zh-CN");
}

function hasBudget(snapshot: AgentRunDetail["budget_snapshot"]): snapshot is AgentPolicySnapshot {
  return "max_model_calls" in snapshot;
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="mt-0.5 wrap-break-word text-sm tabular-nums">{value}</dd>
    </div>
  );
}

export function RunInspector({ run }: { run: AgentRunDetail }) {
  const budget = hasBudget(run.budget_snapshot) ? run.budget_snapshot : null;
  const contextChars = run.context_truncation.retained_chars ?? run.context_truncation.original_chars;

  return (
    <div className="space-y-4" aria-label="运行遥测">
      <section className="space-y-2" aria-labelledby="run-overview-title">
        <div className="flex items-center justify-between gap-2">
          <h3 id="run-overview-title" className="text-xs font-semibold text-muted-foreground">运行</h3>
          <span className="rounded border border-border px-1.5 py-0.5 text-xs">
            {run.status} · {STATUS_LABELS[run.status]}
          </span>
        </div>
        <p className="truncate text-xs text-muted-foreground" title={run.id}>{run.id}</p>
        <p className="text-sm">{run.model_ref.provider} · {run.model_ref.model}</p>
        <dl className="grid grid-cols-3 gap-3">
          <Fact label="Active" value={duration(run.active_elapsed_ms)} />
          <Fact label="审批等待" value={duration(run.approval_wait_ms)} />
          <Fact label="Wall" value={duration(run.elapsed_ms)} />
        </dl>
      </section>

      <section className="border-t border-border/70 pt-3" aria-labelledby="run-budget-title">
        <h3 id="run-budget-title" className="text-xs font-semibold text-muted-foreground">调用与限制</h3>
        {budget ? (
          <dl className="mt-2 grid grid-cols-2 gap-3">
            <Fact label="模型 reservation" value={`${run.usage.model_calls} / ${budget.max_model_calls}`} />
            <Fact label="工具 reservation" value={`${run.usage.tool_calls} / ${budget.max_tool_calls}`} />
          </dl>
        ) : (
          <div className="mt-2 space-y-2">
            <div className="rounded-md border border-border bg-black/10 px-2.5 py-2 text-xs text-muted-foreground">
              旧版运行未记录预算快照
            </div>
            <dl className="grid grid-cols-2 gap-3">
              <Fact label="模型 reservation" value={`${run.usage.model_calls} / 未记录`} />
              <Fact label="工具 reservation" value={`${run.usage.tool_calls} / 未记录`} />
            </dl>
          </div>
        )}
      </section>

      <section className="border-t border-border/70 pt-3" aria-labelledby="run-token-title">
        <div className="flex items-center justify-between gap-2">
          <h3 id="run-token-title" className="text-xs font-semibold text-muted-foreground">Provider 实报 Token</h3>
          <span className="text-xs text-muted-foreground">{run.usage.token_status}</span>
        </div>
        <dl className="mt-2 grid grid-cols-3 gap-3">
          <Fact label="输入" value={count(run.usage.input_tokens)} />
          <Fact label="输出" value={count(run.usage.output_tokens)} />
          <Fact label="合计" value={count(run.usage.total_tokens)} />
        </dl>
      </section>

      <section className="border-t border-border/70 pt-3" aria-labelledby="run-context-title">
        <h3 id="run-context-title" className="text-xs font-semibold text-muted-foreground">上下文</h3>
        <dl className="mt-2 grid grid-cols-2 gap-3">
          <Fact label="最新字符数" value={contextChars === null ? "未记录" : contextChars.toLocaleString("zh-CN")} />
          <Fact label="移除轮次" value={run.context_truncation.removed_turns === null ? "未记录" : `${run.context_truncation.removed_turns} 轮`} />
        </dl>
        <p className="mt-2 text-xs text-muted-foreground">
          {run.context_truncation.occurred ? "已发生上下文裁剪" : "未发生上下文裁剪"}
        </p>
      </section>

      {(run.error_code || run.error_message) ? (
        <section className="border-t border-border/70 pt-3" aria-labelledby="run-error-title">
          <h3 id="run-error-title" className="text-xs font-semibold text-muted-foreground">终态错误</h3>
          {run.error_code ? <p className="mt-2 font-mono text-xs text-destructive">{run.error_code}</p> : null}
          {run.error_message ? <p className="mt-1 wrap-break-word text-sm">{run.error_message}</p> : null}
        </section>
      ) : null}
    </div>
  );
}
