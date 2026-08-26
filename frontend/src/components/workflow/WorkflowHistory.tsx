// 工作流历史列表：挂在各业务页面自己的历史区，不做全局历史路由。
// 列表只发一次 threads.search(metadata+extract)（见 workflow-client），绝不逐条 getState；
// 「查看 / 重新运行」才对所选 thread 做一次 getState，把权威 state 交给页面。
import { useCallback, useEffect, useState } from "react";
import { History, Loader2, RefreshCw, RotateCcw, Trash2 } from "lucide-react";
import {
  deleteWorkflowThread,
  getWorkflowState,
  searchWorkflowHistory,
  type WorkflowThreadProjection,
} from "@/lib/agent/workflow-client";
import type { WorkflowState, WorkflowStatus } from "@/lib/agent/workflow-types";
import { cn } from "@/lib/utils";

const STATUS_LABEL: Record<WorkflowStatus, string> = {
  pending: "等待中",
  running: "生成中",
  completed: "已完成",
  partial: "部分完成",
  failed: "失败",
  cancelled: "已取消",
  interrupted: "已中断",
};

const STATUS_TONE: Record<WorkflowStatus, string> = {
  pending: "bg-muted/50 text-muted-foreground",
  running: "bg-primary/15 text-primary",
  completed: "bg-success/15 text-success",
  partial: "bg-warning/15 text-warning",
  failed: "bg-destructive/15 text-destructive",
  cancelled: "bg-muted/50 text-muted-foreground",
  interrupted: "bg-warning/15 text-warning",
};

interface Props {
  workflowType: string;
  /** 可选 subject 过滤（如反思的 note.id、资讯的赛道 key）。 */
  subject?: string;
  /** 打开一条历史：组件内做一次 getState 后把权威 state 交给页面。 */
  onOpen?: (thread: WorkflowThreadProjection, state: WorkflowState) => void;
  /** 基于历史重新发起：一次 getState 取原始输入，页面用它创建新 thread。 */
  onRerun?: (thread: WorkflowThreadProjection, state: WorkflowState) => void;
  /** 外部刷新信号（新 run 结束后递增即可重查）。 */
  refreshKey?: string | number;
}

export function WorkflowHistory({ workflowType, subject, onOpen, onRerun, refreshKey }: Props) {
  const [rows, setRows] = useState<WorkflowThreadProjection[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setRows(await searchWorkflowHistory(workflowType, subject));
    } catch (e) {
      setRows([]);
      setError(e instanceof Error ? e.message : "历史加载失败");
    } finally {
      setLoading(false);
    }
  }, [workflowType, subject]);

  useEffect(() => { void load(); }, [load, refreshKey]);

  const open = async (thread: WorkflowThreadProjection) => {
    if (busyId) return;
    setBusyId(thread.threadId);
    try {
      onOpen?.(thread, await getWorkflowState(thread.threadId));
    } catch (e) {
      setError(e instanceof Error ? e.message : "历史打开失败");
    } finally {
      setBusyId(null);
    }
  };

  const rerun = async (thread: WorkflowThreadProjection) => {
    if (busyId) return;
    setBusyId(thread.threadId);
    try {
      onRerun?.(thread, await getWorkflowState(thread.threadId));
    } catch (e) {
      setError(e instanceof Error ? e.message : "重新运行失败");
    } finally {
      setBusyId(null);
    }
  };

  const remove = async (thread: WorkflowThreadProjection) => {
    if (busyId) return;
    setBusyId(thread.threadId);
    try {
      await deleteWorkflowThread(thread.threadId);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "历史删除失败");
    } finally {
      setBusyId(null);
    }
  };

  if (loading) {
    return (
      <p className="flex items-center gap-2 py-2 text-xs text-muted-foreground">
        <Loader2 className="h-3.5 w-3.5 animate-spin" /> 正在加载历史…
      </p>
    );
  }

  return (
    <div className="rounded-xl border border-border/50 bg-background/30 p-3">
      <div className="mb-2 flex items-center justify-between">
        <span className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
          <History className="h-3.5 w-3.5" /> 历史记录
        </span>
        <button onClick={() => void load()} className="text-muted-foreground/60 hover:text-primary" title="刷新历史">
          <RefreshCw className="h-3.5 w-3.5" />
        </button>
      </div>

      {error && <p className="mb-2 text-xs text-destructive">{error}</p>}

      {rows.length === 0 ? (
        <p className="py-1 text-xs text-muted-foreground/60">暂无历史记录</p>
      ) : (
        <ul className="space-y-1.5">
          {rows.map((row) => (
            <li key={row.threadId} className="flex items-center gap-2 rounded-lg bg-muted/20 px-2.5 py-2">
              <span className={cn("shrink-0 rounded-full px-2 py-0.5 text-[10px]", STATUS_TONE[row.status])}>
                {STATUS_LABEL[row.status]}
              </span>
              <div className="min-w-0 flex-1">
                <p className="truncate text-xs font-medium">{row.title || row.subject || row.threadId}</p>
                <p className="truncate text-[10px] text-muted-foreground/70">
                  {row.resultSummary ? `${row.resultSummary.slice(0, 80)} · ` : ""}
                  {new Date(row.updatedAt).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })}
                </p>
              </div>
              {onOpen && (
                <button onClick={() => void open(row)} disabled={busyId === row.threadId}
                  className="shrink-0 rounded-md border border-border/60 px-2 py-1 text-[10px] text-muted-foreground hover:text-primary disabled:opacity-40">
                  查看
                </button>
              )}
              {onRerun && (
                <button onClick={() => void rerun(row)} disabled={busyId === row.threadId}
                  className="inline-flex shrink-0 items-center gap-0.5 rounded-md border border-border/60 px-2 py-1 text-[10px] text-muted-foreground hover:text-primary disabled:opacity-40">
                  <RotateCcw className="h-2.5 w-2.5" /> 重新运行
                </button>
              )}
              <button onClick={() => void remove(row)} disabled={busyId === row.threadId}
                className="shrink-0 text-muted-foreground/50 hover:text-destructive disabled:opacity-40" title="删除" aria-label="删除">
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
