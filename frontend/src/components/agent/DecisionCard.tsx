import { useState } from "react";
import { Heart, Copy, RefreshCw, ChevronDown, ChevronRight } from "lucide-react";
import type { DecisionCardData, BasisType } from "@/lib/types/agent";
import { useAgentStore } from "@/lib/stores/agent";
import { cn } from "@/lib/utils";

// basis_type 4 档色标（spec §6 约束 3）
const BASIS_COLORS: Record<BasisType, { bg: string; text: string; label: string }> = {
  model: { bg: "bg-blue-500/15", text: "text-blue-500", label: "model" },
  model_fallback: { bg: "bg-amber-500/15", text: "text-amber-500", label: "model_fallback" },
  hybrid: { bg: "bg-orange-500/15", text: "text-orange-500", label: "hybrid" },
  llm_reasoning: { bg: "bg-zinc-500/15", text: "text-zinc-500", label: "llm_reasoning" },
};

const BASIS_DESC: Record<BasisType, string> = {
  model: "A 股数据齐全，走完整公式（最可信）",
  model_fallback: "数据不足，Python 简化公式降级",
  hybrid: "model 出基础值 + LLM 微调",
  llm_reasoning: "仅 LLM 推理（target_price 字段）",
};

export function DecisionCard({ card }: { card: DecisionCardData }) {
  const [showBasis, setShowBasis] = useState(false);
  const [saved, setSaved] = useState(false);
  const saveDecision = useAgentStore((s) => s.saveDecision);

  const basis = BASIS_COLORS[card.basis_type];
  const changePct = ((card.target_price - card.current_price) / card.current_price) * 100;

  const handleSave = () => {
    saveDecision(card);
    setSaved(true);
  };

  const handleCopy = () => {
    const lines = [
      `${card.name}（${card.code}） 决策卡`,
      `目标价 ¥${card.target_price.toFixed(2)}（${changePct >= 0 ? "+" : ""}${changePct.toFixed(1)}%）`,
      `入场区 ¥${card.entry_low.toFixed(2)} – ¥${card.entry_high.toFixed(2)}`,
      `止损 ¥${card.stop_loss.toFixed(2)}`,
      `止盈 ¥${card.take_profit.toFixed(2)}`,
      "",
      "仓位节奏：",
      ...card.cadence.map((c) =>
        `  第${c.batch}批 ${Math.round(c.pct * 100)}% ${c.trigger} ¥${(c.price || c.ref_price || 0).toFixed(2)}`,
      ),
      "",
      `依据：${basis.label} - ${BASIS_DESC[card.basis_type]}`,
      card.explanation,
    ];
    navigator.clipboard.writeText(lines.join("\n"));
  };

  return (
    <div className="mt-2 rounded-xl border border-primary/30 bg-primary/5 p-4">
      <div className="flex items-start justify-between">
        <div>
          <h3 className="text-base font-bold">
            决策卡 · {card.code} {card.name}
          </h3>
          <p className="text-xs text-muted-foreground mt-0.5">
            目标价 <span className="font-mono text-primary">¥{card.target_price.toFixed(2)}</span>
            <span className={cn("ml-1", changePct >= 0 ? "text-red-500" : "text-emerald-500")}>
              ({changePct >= 0 ? "+" : ""}{changePct.toFixed(1)}%)
            </span>
          </p>
        </div>
        <div className="flex gap-1">
          <button
            onClick={handleSave}
            className={cn("rounded p-1.5 hover:bg-muted/50", saved && "text-red-500")}
            title={saved ? "已收藏" : "收藏"}
          >
            <Heart className={cn("h-4 w-4", saved && "fill-current")} />
          </button>
          <button onClick={handleCopy} className="rounded p-1.5 hover:bg-muted/50" title="复制">
            <Copy className="h-4 w-4" />
          </button>
          <button className="rounded p-1.5 hover:bg-muted/50" title="复盘追踪（Phase 3）">
            <RefreshCw className="h-4 w-4" />
          </button>
        </div>
      </div>

      <div className="mt-3 grid grid-cols-2 gap-3 text-sm">
        <div>
          <div className="text-xs text-muted-foreground">入场区</div>
          <div className="font-mono">¥{card.entry_low.toFixed(2)} – ¥{card.entry_high.toFixed(2)}</div>
        </div>
        <div>
          <div className="text-xs text-muted-foreground">止损</div>
          <div className="font-mono text-red-500/80">¥{card.stop_loss.toFixed(2)}</div>
        </div>
        <div>
          <div className="text-xs text-muted-foreground">止盈</div>
          <div className="font-mono text-emerald-500/80">¥{card.take_profit.toFixed(2)}</div>
        </div>
        <div>
          <div className="text-xs text-muted-foreground">仓位节奏</div>
          <div className="font-mono">{card.cadence.length} 批</div>
        </div>
      </div>

      {card.cadence.length > 0 && (
        <div className="mt-3">
          <div className="text-xs text-muted-foreground mb-1">分批计划</div>
          <div className="space-y-1">
            {card.cadence.map((c) => (
              <div key={c.batch} className="flex items-center gap-3 text-xs">
                <span className="w-12 text-muted-foreground">第 {c.batch} 批</span>
                <span className="w-12 font-mono">{Math.round(c.pct * 100)}%</span>
                <span className="flex-1 text-muted-foreground">{c.trigger}</span>
                <span className="font-mono">¥{(c.price || c.ref_price || 0).toFixed(2)}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 依据：4 档色标 + 字段级 model_versions_json 展开 */}
      <div className="mt-3 border-t border-border/40 pt-2">
        <button
          onClick={() => setShowBasis(!showBasis)}
          className="flex w-full items-center gap-1 text-xs"
        >
          {showBasis ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
          <span className="text-muted-foreground">依据</span>
          <span className={cn("ml-1 rounded-full px-2 py-0.5 text-[11px] font-mono", basis.bg, basis.text)}>
            ● {basis.label}
          </span>
          <span className="ml-auto text-muted-foreground/60">{BASIS_DESC[card.basis_type]}</span>
        </button>
        {showBasis && (
          <div className="mt-2 pl-4 text-[11px] text-muted-foreground">
            <div className="mb-1 font-medium">字段级来源：</div>
            <ul className="space-y-0.5 font-mono">
              {Object.entries(card.model_versions_json).map(([field, ver]) => (
                <li key={field}>
                  <span className="text-foreground/80">{field}</span>
                  <span className="mx-2">←</span>
                  <span>{ver}</span>
                </li>
              ))}
            </ul>
            {card.assumptions.length > 0 && (
              <>
                <div className="mb-1 mt-2 font-medium">假设：</div>
                <ul className="list-disc pl-4">
                  {card.assumptions.map((a, i) => <li key={i}>{a}</li>)}
                </ul>
              </>
            )}
            {card.explanation && (
              <p className="mt-2 border-t border-border/30 pt-1">{card.explanation}</p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
