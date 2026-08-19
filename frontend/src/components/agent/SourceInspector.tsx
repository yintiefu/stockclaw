import { ExternalLink } from "lucide-react";

import type { AgentSource, ModelUrlSource, ToolExecutionSource } from "@/lib/agent/types";

type Props = {
  sources: AgentSource[];
  truncated?: boolean;
};

function safeHttpUrl(value: string): string | null {
  try {
    const parsed = new URL(value);
    return parsed.protocol === "http:" || parsed.protocol === "https:" ? value : null;
  } catch {
    return null;
  }
}

function ExecutionGroup({ sources }: { sources: ToolExecutionSource[] }) {
  if (sources.length === 0) return null;
  return (
    <section role="region" aria-labelledby="source-execution-title" className="space-y-2">
      <h3 id="source-execution-title" className="text-xs font-semibold text-muted-foreground">执行记录</h3>
      <ul className="space-y-3">
        {sources.map((source) => (
          <li key={source.id} className="border-l-2 border-border pl-2.5 text-xs">
            <div className="flex min-w-0 items-center justify-between gap-2">
              <span className="truncate font-medium text-foreground">{source.tool_name}</span>
              <span className="shrink-0 text-[10px] text-muted-foreground">{source.origin}</span>
            </div>
            <p className="mt-1 whitespace-pre-wrap break-words text-muted-foreground">参数：{source.arguments_summary || "无摘要"}</p>
            <p className="mt-1 whitespace-pre-wrap break-words text-foreground">结果：{source.result_summary || "无摘要"}</p>
          </li>
        ))}
      </ul>
    </section>
  );
}

function ModelUrlGroup({ sources }: { sources: ModelUrlSource[] }) {
  if (sources.length === 0) return null;
  return (
    <section role="region" aria-labelledby="source-model-url-title" className="space-y-2 border-t border-border/70 pt-3">
      <h3 id="source-model-url-title" className="text-xs font-semibold text-muted-foreground">模型提供，未验证</h3>
      <ul className="space-y-2">
        {sources.map((source) => {
          const safe = safeHttpUrl(source.url);
          const content = source.label || source.url;
          return (
            <li key={source.id} className="min-w-0 text-xs">
              {safe ? (
                <a
                  href={safe}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex max-w-full items-start gap-1 text-primary hover:underline"
                >
                  <span className="break-all">{content}</span>
                  <ExternalLink className="mt-0.5 size-3 shrink-0" aria-hidden />
                </a>
              ) : <span className="break-all text-muted-foreground">{content}</span>}
              {source.label ? <p className="mt-0.5 break-all text-[10px] text-muted-foreground">{source.url}</p> : null}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

export function SourceInspector({ sources, truncated = false }: Props) {
  const execution = sources.filter((source): source is ToolExecutionSource => source.kind === "tool_execution");
  const modelUrls = sources.filter((source): source is ModelUrlSource => source.kind === "model_url");

  if (sources.length === 0) {
    return (
      <div role="status" className="flex min-h-24 items-center justify-center rounded-md border border-dashed border-border px-3 text-center text-xs text-muted-foreground">
        当前运行没有可查看的来源
      </div>
    );
  }
  return (
    <div className="space-y-3" aria-label="来源记录">
      {truncated ? (
        <p className="rounded-md border border-border bg-black/10 px-2.5 py-2 text-xs text-muted-foreground">
          来源记录已达到存储上限，列表可能被截断
        </p>
      ) : null}
      <ExecutionGroup sources={execution} />
      <ModelUrlGroup sources={modelUrls} />
    </div>
  );
}
