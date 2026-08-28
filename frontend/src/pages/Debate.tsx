import { useEffect, useState } from "react";
import { Swords, Play, Square, Save, CheckCircle2, Circle, AlertTriangle, RotateCcw } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { PageHeader } from "@/components/ui/PageHeader";
import { GlassCard } from "@/components/ui/GlassCard";
import { Disclaimer } from "@/components/ui/Disclaimer";
import { WorkflowHistory } from "@/components/workflow/WorkflowHistory";
import { useWorkflowRun } from "@/hooks/useWorkflowRun";
import { stageContent, type WorkflowState } from "@/lib/agent/workflow-types";
import { addNote } from "@/lib/notes";

const EMPTY_STATE: WorkflowState = { workflow_status: "pending", stages: {} };

type DebateStage = "bull" | "bear" | "bull_rebut" | "bear_rebut" | "referee";

interface StageBox {
  stage: DebateStage;
  label: string;
  content: string;
  done: boolean;
}

// 多方用品牌橙、空方用蓝灰、主持用中性——刻意不用红绿，
// 免得和 A 股「红涨绿跌」撞车被读成涨跌信号。
const STAGE_TONE: Record<DebateStage, string> = {
  bull: "border-primary/50 bg-primary/6",
  bull_rebut: "border-primary/30 bg-primary/3",
  bear: "border-sky-500/40 bg-sky-500/6",
  bear_rebut: "border-sky-500/25 bg-sky-500/3",
  referee: "border-border bg-background/40",
};

// 阶段展示顺序固定为配置顺序（standard / cross_exam 都是它的子序列）。
const STAGE_ORDER: DebateStage[] = ["bull", "bear", "bull_rebut", "bear_rebut", "referee"];
const STAGE_LABEL: Record<DebateStage, string> = {
  bull: "多方研究员",
  bear: "空方研究员",
  bull_rebut: "多方反驳",
  bear_rebut: "空方反驳",
  referee: "中立主持",
};

// 底稿 section id → 展示标题（与 debate.yaml 的 13 项一一对应）。
const SECTION_TITLE: Record<string, string> = {
  quote: "实时行情",
  valuation: "估值与一致预期",
  valuation_percentile: "估值历史分位",
  financials: "最新财报关键指标",
  kline: "近 60 日价格走势",
  fund_flow: "资金流向",
  margin: "融资融券",
  holders: "股东户数",
  announcements: "近期公告",
  lockup: "限售解禁",
  concepts: "板块与概念归属",
  reports: "近期研报",
  news: "近期新闻",
};

const DOSSIER_HINT = "多空双方拿到的是同一份接口实时拉取的数据，谁也不能靠编数字赢。";

export function Debate() {
  const [code, setCode] = useState("");
  const [rounds, setRounds] = useState(1);
  const [status, setStatus] = useState("");
  const [progress, setProgress] = useState<{ id: string; title: string; ok: boolean }[]>([]);
  const [saved, setSaved] = useState(false);
  const [historyKey, setHistoryKey] = useState(0);
  const [pageError, setPageError] = useState("");

  const run = useWorkflowRun({
    assistantId: "debate",
    onDossierProgress: (event) => {
      setStatus(`正在拉取客观事实底稿… ${event.completed}/${event.total}`);
      setProgress((p) => [
        ...p.filter((t) => t.id !== event.section_id),
        {
          id: event.section_id,
          title: SECTION_TITLE[event.section_id] ?? event.section_id,
          ok: event.section_status === "completed" || event.section_status === "no_record",
        },
      ]);
    },
  });

  const running = run.running || run.status === "running";
  const stageState = run.state?.stages ?? {};
  const missing = run.state?.dossier?.missing ?? [];
  // 底稿展示优先用权威 checkpoint（历史恢复也完整），运行中回落到事件累积的进度。
  const dossierTicks = run.state?.dossier?.sections.map((s) => ({
    id: s.id,
    title: s.title,
    ok: s.status === "completed" || s.status === "no_record",
  })) ?? progress;
  const stageBoxes: StageBox[] = STAGE_ORDER
    .filter((id) => stageState[id] || run.transient[id] !== undefined)
    .map((id) => ({
      stage: id,
      label: STAGE_LABEL[id],
      content: stageContent(run.state ?? EMPTY_STATE, id) ?? run.transient[id] ?? "",
      done: stageState[id] != null && stageState[id].status !== "pending" && stageState[id].status !== "running",
    }));

  // 阶段状态文本由 values 快照派生（dossier.ready/stage.started 等事件已随 v1 协议消亡）
  const currentStage = run.state?.current_stage ?? null;
  useEffect(() => {
    if (!run.running) return;
    if (run.state?.workflow_status === "failed") { setStatus(""); return; }
    if (run.state?.dossier) {
      setStatus(currentStage
        ? `${(STAGE_LABEL as Record<string, string>)[currentStage] ?? currentStage} 正在生成…`
        : "辩论进行中");
    } else {
      // 底稿收集期 custom 进度事件经 langgraph v3 流路径被上游丢弃（实测
      // astream_events(version="v3") 0 条转发），此窗口只能给静态状态行。
      setStatus("正在拉取客观事实底稿…（约 35 秒，走公开数据接口，不消耗 token）");
    }
  }, [run.running, currentStage, run.state?.dossier, run.state?.workflow_status]);

  const finished = stageBoxes.length > 0
    && stageBoxes.every((s) => s.done)
    && (run.status === "completed" || run.status === "partial");

  const startDebate = (c: string, variant: "standard" | "cross_exam") => {
    setStatus("");
    setProgress([]);
    setSaved(false);
    void run.start({
      input: { code: c },
      variant,
      metadata: { title: `多空辩论 · ${c}`, subject: c },
    }).finally(() => setHistoryKey((k) => k + 1));
  };

  const start = () => {
    const c = code.trim();
    if (!/^\d{6}$/.test(c)) {
      setPageError("请输入 6 位 A 股代码");
      return;
    }
    setPageError("");
    startDebate(c, rounds > 1 ? "cross_exam" : "standard");
  };

  const stop = () => { void run.stop().finally(() => setHistoryKey((k) => k + 1)); };
  const retry = () => { void run.retry().finally(() => setHistoryKey((k) => k + 1)); };

  const save = () => {
    const body = stageBoxes.map((s) => `## ${s.label}\n\n${s.content}`).join("\n\n---\n\n");
    addNote("多空辩论", `多空辩论 · ${code.trim()}`, body);
    setSaved(true);
  };

  const errorText = pageError || run.error;
  const retryable = !running && run.threadId != null
    && ["failed", "interrupted", "cancelled"].includes(run.status);

  return (
    <div>
      <PageHeader
        title="多空辩论"
        subtitle="同一份客观数据，多方与空方各自立论、互相质疑，最后由中立主持归纳分歧点与验证清单——不给买卖结论，判断留给你自己。"
      />

      <GlassCard>
        <div className="flex flex-wrap items-end gap-3">
          <div>
            <label className="mb-1 block text-xs text-muted-foreground">股票代码</label>
            <input
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/[^\d]/g, "").slice(0, 6))}
              onKeyDown={(e) => { if (e.key === "Enter" && !running) start(); }}
              placeholder="6 位代码，如 600519"
              disabled={running}
              className="w-44 rounded-lg border border-border/60 bg-background/60 px-3 py-2 font-mono text-sm outline-hidden focus:border-primary/60"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs text-muted-foreground">辩论深度</label>
            <select
              value={rounds}
              onChange={(e) => setRounds(Number(e.target.value))}
              disabled={running}
              className="rounded-lg border border-border/60 bg-background/60 px-3 py-2 text-sm outline-hidden focus:border-primary/60"
            >
              <option value={1}>一轮 · 各自陈述</option>
              <option value={2}>两轮 · 加交叉反驳</option>
            </select>
          </div>
          {running ? (
            <button onClick={stop}
              className="inline-flex items-center gap-1.5 rounded-lg border border-border/60 px-4 py-2 text-sm hover:text-destructive">
              <Square className="h-4 w-4" /> 中止
            </button>
          ) : (
            <button onClick={start}
              className="inline-flex items-center gap-1.5 rounded-lg bg-primary/90 px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary">
              <Play className="h-4 w-4" /> 开始辩论
            </button>
          )}
          {retryable && (
            <button onClick={retry}
              className="inline-flex items-center gap-1.5 rounded-lg border border-border/60 px-4 py-2 text-sm text-muted-foreground hover:text-foreground">
              <RotateCcw className="h-4 w-4" /> 从失败阶段重试
            </button>
          )}
          {finished && !running && (
            <button onClick={save} disabled={saved}
              className="inline-flex items-center gap-1.5 rounded-lg border border-border/60 px-4 py-2 text-sm text-muted-foreground hover:text-foreground disabled:opacity-50">
              <Save className="h-4 w-4" /> {saved ? "已存入沉淀" : "存入沉淀"}
            </button>
          )}
        </div>

        {/* 开销提示：辩论比问答重得多，让用户在点下去之前就知道要花多久、调几次模型 */}
        {!running && !status && !errorText && (
          <p className="mt-3 text-[11px] leading-relaxed text-muted-foreground/70">
            ⏱ {rounds === 2
              ? "两轮约 3 分钟 · 5 次模型调用 · 约 6 万字进上下文"
              : "一轮约 100 秒 · 3 次模型调用 · 约 3.5 万字进上下文"}
            （每个角色都会带上完整底稿）。其中拉底稿约 35 秒、走公开数据接口，不消耗 token。
          </p>
        )}

        {status && <p className="mt-3 text-xs text-muted-foreground">{status}</p>}
        {errorText && (
          <p className="mt-3 flex items-start gap-1.5 text-xs text-destructive">
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" /> {errorText}
          </p>
        )}

        {dossierTicks.length > 0 && (
          <div className="mt-4 border-t border-border/40 pt-3">
            <p className="mb-2 text-[11px] text-muted-foreground">{DOSSIER_HINT}</p>
            <div className="flex flex-wrap gap-x-4 gap-y-1.5">
              {dossierTicks.map((p) => (
                <span key={p.id} className="inline-flex items-center gap-1 text-[11px] text-muted-foreground">
                  {p.ok
                    ? <CheckCircle2 className="h-3 w-3 text-primary/70" />
                    : <Circle className="h-3 w-3 text-muted-foreground/40" />}
                  {p.title}
                </span>
              ))}
            </div>
            {missing.length > 0 && (
              <p className="mt-2 text-[11px] text-warning">
                未取到：{missing.join("、")}（双方立论时不得臆测这部分）
              </p>
            )}
          </div>
        )}
      </GlassCard>

      <div className="mt-4 space-y-4">
        {stageBoxes.map((s) => (
          <div key={s.stage} className={`rounded-xl border p-4 ${STAGE_TONE[s.stage]}`}>
            <div className="mb-2 flex items-center gap-2">
              <Swords className="h-4 w-4 text-muted-foreground" />
              <span className="text-sm font-semibold">{s.label}</span>
              {!s.done && <span className="animate-pulse text-[11px] text-muted-foreground">生成中…</span>}
            </div>
            <div className="prose prose-sm dark:prose-invert max-w-none text-foreground prose-table:text-sm">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{s.content || "…"}</ReactMarkdown>
            </div>
          </div>
        ))}
      </div>

      {stageBoxes.length === 0 && !running && (
        <GlassCard className="mt-4">
          <div className="flex flex-col items-center gap-2 py-10 text-center text-sm text-muted-foreground">
            <Swords className="h-8 w-8 text-muted-foreground/40" />
            输入一个代码开始。后端会先拉一份客观事实底稿，再让多方 / 空方基于同一份数据互相质疑。
            <span className="text-xs">产出的是「分歧点 + 验证清单」，不是买卖建议。</span>
          </div>
        </GlassCard>
      )}

      {/* 辩论历史只在辩论页查看；打开恢复 checkpoint，重新运行创建新 thread 保留旧结果 */}
      <div className="mt-4">
        <WorkflowHistory
          workflowType="debate"
          refreshKey={historyKey}
          onOpen={(thread) => {
            const historicCode = typeof thread.subject === "string" ? thread.subject : "";
            if (historicCode) setCode(historicCode);
            setStatus("");
            setProgress([]);
            setSaved(false);
            void run.restore(thread.threadId).finally(() => setHistoryKey((k) => k + 1));
          }}
          onRerun={(thread, state) => {
            const historicCode = typeof state.input?.code === "string" ? state.input.code : thread.subject ?? "";
            if (historicCode) setCode(historicCode);
            if (!/^\d{6}$/.test(historicCode)) return;
            startDebate(historicCode, state.variant === "cross_exam" ? "cross_exam" : "standard");
          }}
        />
      </div>

      <Disclaimer />
    </div>
  );
}
