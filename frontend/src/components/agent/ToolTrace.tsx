import { useState } from "react";
import { ChevronDown, ChevronRight, Loader2, CheckCircle, XCircle } from "lucide-react";
import type { ToolTraceEvent } from "@/lib/types/agent";
import { cn } from "@/lib/utils";

export function ToolTrace({ trace }: { trace: ToolTraceEvent }) {
  const [open, setOpen] = useState(false);
  const Icon = trace.status === "running" ? Loader2
    : trace.status === "ok" ? CheckCircle
    : XCircle;
  const color = trace.status === "running" ? "text-amber-500"
    : trace.status === "ok" ? "text-emerald-500"
    : "text-red-500";

  return (
    <div className="my-1 rounded-md border border-border/40 bg-muted/30 text-xs">
      <button
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-1.5 px-2 py-1.5 text-left"
      >
        {open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
        <Icon className={cn("h-3 w-3", color, trace.status === "running" && "animate-spin")} />
        <span className="font-mono">{trace.tool}</span>
        {trace.summary && <span className="text-muted-foreground"> · {trace.summary}</span>}
      </button>
      {open && (
        <pre className="border-t border-border/40 px-2 py-1.5 font-mono text-[11px] text-muted-foreground overflow-auto">
          {JSON.stringify(trace.args, null, 2)}
        </pre>
      )}
    </div>
  );
}
