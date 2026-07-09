import { Link } from "react-router-dom";
import { X, Star, Wallet, Search } from "lucide-react";
import { useAgentStore } from "@/lib/stores/agent";

export function ContextDrawer({ onClose }: { onClose: () => void }) {
  const savedDecisions = useAgentStore((s) => s.savedDecisions);
  const removeSavedDecision = useAgentStore((s) => s.removeSavedDecision);

  return (
    <aside className="glass w-72 rounded-2xl p-3 text-sm flex flex-col">
      <div className="flex items-center justify-between border-b border-border/40 pb-2">
        <span className="font-medium">上下文</span>
        <button onClick={onClose} className="text-muted-foreground hover:text-foreground">
          <X className="h-3.5 w-3.5" />
        </button>
      </div>

      <div className="mt-3">
        <div className="text-xs text-muted-foreground mb-1">快速跳转</div>
        <div className="space-y-1">
          <Link to="/watchlist" className="flex items-center gap-2 rounded-lg px-2 py-1.5 text-xs hover:bg-muted/50">
            <Star className="h-3 w-3" /> 自选股
          </Link>
          <Link to="/portfolio" className="flex items-center gap-2 rounded-lg px-2 py-1.5 text-xs hover:bg-muted/50">
            <Wallet className="h-3 w-3" /> 我的持仓
          </Link>
          <Link to="/stock-data" className="flex items-center gap-2 rounded-lg px-2 py-1.5 text-xs hover:bg-muted/50">
            <Search className="h-3 w-3" /> 个股数据
          </Link>
        </div>
      </div>

      <div className="mt-4 flex-1 overflow-auto">
        <div className="text-xs text-muted-foreground mb-2">收藏的决策卡（{savedDecisions.length}）</div>
        {savedDecisions.length === 0 ? (
          <p className="text-[11px] text-muted-foreground/60">在决策卡上点 ♡ 收藏</p>
        ) : (
          <div className="space-y-2">
            {savedDecisions.map((d, i) => (
              <div key={`${d.code}-${i}`} className="rounded-md border border-border/40 bg-muted/20 p-2 text-xs">
                <div className="flex items-center justify-between">
                  <span className="font-medium">{d.name}（{d.code}）</span>
                  <button
                    onClick={() => removeSavedDecision(d.code)}
                    className="text-muted-foreground hover:text-red-500"
                  >
                    <X className="h-3 w-3" />
                  </button>
                </div>
                <div className="mt-1 font-mono text-muted-foreground">
                  目标 ¥{d.target_price.toFixed(0)} · 止损 ¥{d.stop_loss.toFixed(0)}
                </div>
                <div className="text-[10px] text-muted-foreground/70 mt-0.5">
                  依据：{d.basis_type}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </aside>
  );
}
