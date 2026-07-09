import type { DecisionCardData } from "@/lib/types/agent";

export function DecisionCard({ card }: { card: DecisionCardData }) {
  return (
    <div className="mt-2 rounded-md border border-primary/30 bg-primary/5 p-3 text-xs">
      决策卡 · {card.code} · 目标价 {card.target_price}（Task 12 完整实现）
    </div>
  );
}
