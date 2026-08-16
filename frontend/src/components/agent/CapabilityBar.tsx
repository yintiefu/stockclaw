/** 能力条：会话内 Skill 选择的紧凑摘要与「能力管理」入口。 */
import { Settings } from "lucide-react";

import type { AgentThread } from "@/lib/agent/types";

type Props = {
  thread: AgentThread | null;
  onOpenManager: () => void;
  disabled: boolean;
};

export function CapabilityBar({ thread, onOpenManager, disabled }: Props) {
  const count = thread?.selected_skills.length ?? 0;
  return (
    <div className="flex items-center justify-between gap-2 py-1">
      <span className="text-xs text-muted-foreground">
        {count > 0 ? `已选 ${count} 个 Skill：${thread?.selected_skills.join("、")}` : "未选择 Skill"}
      </span>
      <button
        type="button"
        onClick={onOpenManager}
        disabled={disabled}
        title="管理本会话可用的 Skill"
        className="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-medium text-muted-foreground hover:bg-black/20 hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
      >
        <Settings className="size-3.5" aria-hidden />
        能力管理
      </button>
    </div>
  );
}
