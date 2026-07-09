import { useState } from "react";

export function AgentTopBar({ contextCodes }: { contextCodes: string[] }) {
  const [style, setStyle] = useState<"conservative" | "balanced" | "aggressive">("balanced");
  return (
    <div className="flex items-center gap-3 border-b border-border/50 px-4 py-2 text-sm">
      <span className="font-medium">模型:</span>
      <span className="text-muted-foreground">当前模型</span>
      <span className="text-muted-foreground">·</span>
      <label className="text-muted-foreground">风格:</label>
      <select
        value={style}
        onChange={(e) => setStyle(e.target.value as typeof style)}
        className="rounded border border-border/50 bg-transparent px-2 py-0.5 text-xs"
      >
        <option value="conservative">保守</option>
        <option value="balanced">平衡</option>
        <option value="aggressive">激进</option>
      </select>
      <span className="text-muted-foreground">·</span>
      <span className="text-xs text-muted-foreground">
        上下文: {contextCodes.length > 0 ? contextCodes.join("、") : "无"}
      </span>
    </div>
  );
}
