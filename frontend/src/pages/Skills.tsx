/** 技能管理：双分区列表（用户可管理 / 内置只读），本地导入默认停用。 */
import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Plus, RefreshCw, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { PageHeader } from "@/components/ui/PageHeader";
import { SkillImportDialog } from "@/components/skills/SkillImportDialog";
import { SkillToggle } from "@/components/skills/SkillToggle";
import { Button } from "@/components/ui/button";
import { api, type SkillSummary, type SkillsResponse } from "@/lib/api";
import { cn } from "@/lib/utils";

const REFRESH_TOAST = "技能状态已更新。新会话将自动使用最新配置；已有会话请执行 /reload-skills。";

function SkillCard({
  skill,
  onToggle,
  pending,
}: {
  skill: SkillSummary;
  onToggle?: (skill: SkillSummary, next: boolean) => void;
  pending?: boolean;
}) {
  const navigate = useNavigate();
  const isUser = skill.source === "user";
  // 启用了却未生效：技能无效，或与内置技能/停用目录冲突
  const blocked = isUser && skill.enabled && !skill.effective;
  // 左侧状态条即启停/生效状态（hover 提亮加宽，替代「已加载」标签的冗余信息）
  const statusBar = isUser
    ? skill.effective
      ? "bg-success/60 group-hover:bg-success"
      : skill.enabled
        ? "bg-warning/60 group-hover:bg-warning"
        : "bg-border/50 group-hover:bg-border"
    : "bg-border/40 group-hover:bg-border/70";
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={() => navigate(`/settings/skills/${skill.source}/${encodeURIComponent(skill.name)}`)}
      onKeyDown={(event) => {
        if (event.key === "Enter") navigate(`/settings/skills/${skill.source}/${encodeURIComponent(skill.name)}`);
      }}
      className={cn(
        "group flex min-h-20 cursor-pointer items-stretch gap-3 rounded-xl border border-border/60 bg-card/60 p-4 text-left transition-colors hover:border-border",
        !isUser && "border-border/40",
      )}
    >
      <span
        aria-hidden="true"
        className={cn("w-[3px] shrink-0 rounded-full transition-all group-hover:w-1.5", statusBar)}
      />
      <div className="flex min-w-0 flex-1 flex-col gap-1.5">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span className="font-mono text-sm font-semibold break-all text-foreground">{skill.name}</span>
          {blocked && (
            <span className="rounded bg-warning/15 px-1.5 py-0.5 text-[11px] text-warning">
              {skill.valid ? "未生效" : "已阻止"}
            </span>
          )}
          {!isUser && (
            <span className="rounded bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground">内置 · 始终启用</span>
          )}
          {isUser && skill.valid && onToggle && (
            <div className="ml-auto">
              <SkillToggle
                checked={skill.enabled}
                disabled={pending}
                onCheckedChange={(next) => onToggle(skill, next)}
                label={skill.enabled ? "已启用" : "已停用"}
              />
            </div>
          )}
        </div>
        <p className="line-clamp-2 text-xs leading-relaxed break-words text-muted-foreground">
          {skill.error ?? skill.description ?? "（无描述）"}
        </p>
        {isUser && !skill.valid && skill.enabled && (
          <div className="mt-auto flex items-center gap-2 pt-1.5">
            <Button
              variant="outline"
              size="sm"
              disabled={pending}
              onClick={(event) => {
                event.stopPropagation();
                onToggle?.(skill, false);
              }}
            >
              停用
            </Button>
            <Button
              variant="ghost"
              size="icon"
              className="size-7"
              title="删除"
              aria-label="删除技能"
              disabled={pending}
              onClick={(event) => {
                event.stopPropagation();
                navigate(`/settings/skills/user/${encodeURIComponent(skill.name)}`);
              }}
            >
              <Trash2 className="h-4 w-4" />
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}

export function Skills() {
  const navigate = useNavigate();
  const [data, setData] = useState<SkillsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pendingName, setPendingName] = useState<string | null>(null);
  const [importOpen, setImportOpen] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    try {
      setData(await api.skills());
    } catch (exc) {
      setData(null);
      setError(exc instanceof Error ? exc.message : "技能读取失败");
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const mutate = async (skill: SkillSummary, next: boolean) => {
    setPendingName(skill.name);
    try {
      await api.setSkillEnabled(skill.name, next);
      await load();
      toast.success(REFRESH_TOAST);
    } catch (exc) {
      toast.error(exc instanceof Error ? exc.message : "技能操作失败");
    } finally {
      setPendingName(null);
    }
  };

  const loadedCount = (data?.user ?? []).filter((skill) => skill.effective).length;

  return (
    <div>
      <PageHeader
        title="技能管理"
        subtitle="用户技能导入后默认停用；启用后新会话自动生效，已有会话需 /reload-skills 刷新。"
        actions={
          <Button onClick={() => setImportOpen(true)}>
            <Plus className="h-4 w-4" /> 导入技能
          </Button>
        }
      />

      {error && (
        <div className="mb-4 rounded-xl border border-destructive/40 bg-destructive/10 p-4 text-sm text-destructive">
          <p>{error}</p>
          <Button variant="outline" size="sm" className="mt-2" onClick={() => void load()}>
            <RefreshCw className="h-4 w-4" /> 重试
          </Button>
        </div>
      )}

      {!data && !error && <p className="text-sm text-muted-foreground">加载中…</p>}

      {data && (
        <>
          <section aria-label="用户技能" className="mb-8">
            <div className="mb-3 flex items-baseline justify-between border-b border-border/40 pb-2">
              <h2 className="text-base font-bold text-foreground">用户技能</h2>
              {!data.user_available ? (
                <span className="text-xs text-warning">{data.user_error ?? "用户配置不可用"}</span>
              ) : (
                <span className="text-xs text-muted-foreground">
                  {data.user.length} 个 / 已加载 {loadedCount} 个
                </span>
              )}
            </div>
            {data.user.length === 0 ? (
              <p className="py-6 text-center text-sm text-muted-foreground">
                {data.user_available ? "暂无技能：点击右上角「导入技能」添加本地文件夹或 ZIP。" : "无法读取用户技能目录。"}
              </p>
            ) : (
              <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
                {data.user.map((skill) => (
                  <SkillCard
                    key={skill.name}
                    skill={skill}
                    pending={pendingName === skill.name}
                    onToggle={(target, next) => void mutate(target, next)}
                  />
                ))}
              </div>
            )}
          </section>

          <section aria-label="内置技能">
            <div className="mb-3 flex items-baseline justify-between border-b border-border/40 pb-2">
              <h2 className="text-base font-bold text-foreground">内置技能</h2>
              <span className="text-xs text-muted-foreground">{data.builtin.length} 个 · 始终启用</span>
            </div>
            {data.builtin.length === 0 && !error ? (
              <p className="py-6 text-center text-sm text-muted-foreground">暂无技能</p>
            ) : (
              <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
                {data.builtin.map((skill) => (
                  <SkillCard key={skill.name} skill={skill} />
                ))}
              </div>
            )}
          </section>
        </>
      )}

      <SkillImportDialog
        open={importOpen}
        onOpenChange={setImportOpen}
        onImported={() => {
          setImportOpen(false);
          void load();
          navigate("/settings/skills");
        }}
      />
    </div>
  );
}
