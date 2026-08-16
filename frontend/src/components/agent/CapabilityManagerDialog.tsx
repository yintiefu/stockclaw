/** 能力管理弹窗：桌面 modal / 移动端全屏 sheet；一次提交的草稿生命周期。 */
import { useEffect, useState } from "react";

import { agentApi } from "@/lib/agent/api";
import type { AgentThread, SkillSummary } from "@/lib/agent/types";
import { McpManager } from "./McpManager";
import { SkillManager } from "./SkillManager";

type Props = {
  open: boolean;
  thread: AgentThread | null;
  skills: SkillSummary[];
  onApplied: (thread: AgentThread) => void;
  onConflict: () => void;
  onClose: () => void;
  disabled: boolean;
};

export function CapabilityManagerDialog({
  open, thread, skills, onApplied, onConflict, onClose, disabled,
}: Props) {
  const [draft, setDraft] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      // 每次打开以服务端线程为准重建草稿
      setDraft(thread?.selected_skills ?? []);
      setError(null);
    }
  }, [open, thread]);

  if (!open || !thread) return null;

  const toggle = (name: string) => {
    setDraft((prev) => prev.includes(name) ? prev.filter((n) => n !== name) : [...prev, name]);
  };

  const apply = async () => {
    if (disabled || busy) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await agentApi.patchThread(thread.id, thread.revision, {
        selected_skills: draft,
      });
      onApplied(updated);
    } catch (e) {
      const status = (e as { status?: number }).status;
      if (status === 409) {
        // 丢弃草稿：权威状态已变化，交给上层刷新后重开
        setDraft(thread.selected_skills);
        onConflict();
        return;
      }
      setError(e instanceof Error ? e.message : "应用失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/50 sm:items-center"
      role="dialog" aria-modal="true" aria-label="能力管理">
      <div className="glass-card max-h-[92vh] w-full overflow-y-auto rounded-t-2xl p-4 sm:max-w-2xl sm:rounded-2xl">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-semibold">本会话能力</h2>
          <button type="button" onClick={onClose} className="text-xs text-muted-foreground hover:text-foreground">
            关闭
          </button>
        </div>

        <fieldset className="mb-4 space-y-1" disabled={disabled || busy}>
          <legend className="mb-1 text-xs text-muted-foreground">选择要启用的 Skill（应用到本会话）</legend>
          {skills.filter((s) => s.valid).map((skill) => (
            <label key={skill.directory} className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 hover:bg-black/20">
              <input
                type="checkbox"
                aria-label={skill.name ?? skill.directory}
                checked={draft.includes(skill.name ?? "")}
                onChange={() => toggle(skill.name ?? "")}
              />
              <span className="text-sm">{skill.name}</span>
              <span className="truncate text-xs text-muted-foreground">{skill.description}</span>
            </label>
          ))}
          {skills.filter((s) => s.valid).length === 0 && (
            <p className="px-2 py-3 text-xs text-muted-foreground">尚无可选 Skill，先在下方导入</p>
          )}
        </fieldset>

        {error && <p className="mb-3 text-xs text-red-400">{error}</p>}

        <div className="mb-4 flex justify-end gap-2">
          <button type="button" onClick={onClose} disabled={busy}
            className="rounded-lg px-3 py-1.5 text-xs text-muted-foreground hover:bg-black/20 disabled:opacity-50">
            取消
          </button>
          <button type="button" onClick={apply} disabled={disabled || busy}
            className="rounded-lg bg-primary/20 px-4 py-1.5 text-xs font-medium text-primary hover:bg-primary/30 disabled:cursor-not-allowed disabled:opacity-50">
            应用到本会话
          </button>
        </div>

        <div className="border-t border-border pt-3">
          <h3 className="mb-2 text-xs font-semibold text-muted-foreground">导入 / 管理 Skill</h3>
          <SkillManager skills={skills} disabled={disabled || busy} onChanged={onConflict} />
        </div>
        <div className="mt-4 border-t border-border pt-3">
          <McpManager disabled={disabled || busy} onReload={() => onConflict()} />
        </div>
      </div>
    </div>
  );
}
