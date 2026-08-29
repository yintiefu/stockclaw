/** 技能详情：名称/描述/内容三分区展示、Markdown 只读渲染、虚拟路径与用户技能启停/删除（内置只读）。 */
import { useCallback, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ArrowLeft, FolderOpen, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { SkillToggle } from "@/components/skills/SkillToggle";
import { Button, buttonVariants } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { api, type SkillDetail as SkillDetailData } from "@/lib/api";
import { cn } from "@/lib/utils";

const REFRESH_TOAST = "技能状态已更新。新会话将自动使用最新配置；已有会话请执行 /reload-skills。";

/** 剥离 SKILL.md 头部 YAML frontmatter：内容区只渲染正文（元信息已在名称/描述分区展示）。 */
function stripFrontmatter(markdown: string): string {
  return markdown.replace(/^---[ \t]*\r?\n.*?\r?\n---[ \t]*(?:\r?\n|$)/s, "");
}

/** 参考版式：带标签的字段分区（名称/描述/内容各自独立成块）。 */
function FieldSection({ label, children }: { label: string; children: ReactNode }) {
  return (
    <section aria-label={label}>
      <h2 className="mb-2 text-sm font-semibold text-foreground">{label}</h2>
      {children}
    </section>
  );
}

export function SkillDetail() {
  const { source = "", name = "" } = useParams();
  const navigate = useNavigate();
  const [skill, setSkill] = useState<SkillDetailData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const decodedName = decodeURIComponent(name);

  const validSource = source === "builtin" || source === "user";

  const load = useCallback(async () => {
    if (!validSource) return;
    setError(null);
    try {
      setSkill(await api.skillDetail(source, decodedName));
    } catch (exc) {
      setSkill(null);
      setError(exc instanceof Error ? exc.message : "技能读取失败");
    }
  }, [validSource, source, decodedName]);

  useEffect(() => { void load(); }, [load]);

  if (!validSource) {
    return (
      <div className="p-6">
        <p className="text-sm text-destructive">技能来源无效：只支持 builtin 或 user。</p>
        <Link to="/settings/skills" className={cn(buttonVariants({ variant: "outline", size: "sm" }), "mt-3")}>
          返回技能列表
        </Link>
      </div>
    );
  }

  const toggle = async (next: boolean) => {
    if (!skill) return;
    setPending(true);
    try {
      setSkill(await api.setSkillEnabled(decodedName, next));
      toast.success(REFRESH_TOAST);
    } catch (exc) {
      toast.error(exc instanceof Error ? exc.message : "技能操作失败");
    } finally {
      setPending(false);
    }
  };

  const remove = async () => {
    setPending(true);
    try {
      await api.deleteSkill(decodedName);
      toast.success("技能已永久删除。");
      navigate("/settings/skills");
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "技能删除失败");
      setConfirmOpen(false);
    } finally {
      setPending(false);
    }
  };

  return (
    <div>
      <div className="mb-4">
        <Link to="/settings/skills" className={cn(buttonVariants({ variant: "ghost", size: "sm" }))}>
          <ArrowLeft className="h-4 w-4" /> 返回技能列表
        </Link>
      </div>

      {error && (
        <div className="rounded-xl border border-destructive/40 bg-destructive/10 p-4 text-sm text-destructive">
          <p>{error}</p>
        </div>
      )}

      {!skill && !error && <p className="text-sm text-muted-foreground">加载中…</p>}

      {skill && (
        <div className="space-y-5">
          <div className="flex flex-wrap items-start justify-between gap-3 rounded-xl border border-border/60 bg-card/60 p-4">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <h1 className="font-mono text-2xl font-extrabold break-all text-glow">{skill.name}</h1>
                {skill.source === "builtin" ? (
                  <span className="rounded bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground">
                    内置 · 始终启用
                  </span>
                ) : (
                  <span className="rounded bg-primary/15 px-1.5 py-0.5 text-[11px] text-primary">全局</span>
                )}
              </div>
              <p className="mt-1.5 flex items-center gap-1.5 font-mono text-xs break-all text-muted-foreground">
                <FolderOpen className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                {skill.location ?? skill.path}
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-3">
              {skill.source === "builtin" ? null : skill.valid ? (
                <SkillToggle
                  checked={skill.enabled}
                  disabled={pending}
                  onCheckedChange={(next) => void toggle(next)}
                  label={skill.enabled ? "已启用" : "已停用"}
                />
              ) : skill.enabled ? (
                <Button variant="outline" size="sm" disabled={pending} onClick={() => void toggle(false)}>
                  停用
                </Button>
              ) : null}
              {skill.source === "user" && (
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-8 text-destructive"
                  title="删除"
                  aria-label="删除技能"
                  disabled={pending}
                  onClick={() => setConfirmOpen(true)}
                >
                  <Trash2 className="h-4 w-4" />
                </Button>
              )}
            </div>
          </div>

          <FieldSection label="名称">
            <div className="rounded-xl border border-border/60 bg-card/60 px-4 py-3">
              <p className="font-mono text-sm break-all text-foreground">{skill.name}</p>
            </div>
          </FieldSection>

          <FieldSection label="描述">
            <div className="rounded-xl border border-border/60 bg-card/60 px-4 py-3">
              <p className="text-sm leading-relaxed break-words text-foreground">
                {skill.description ?? "（无描述）"}
              </p>
            </div>
          </FieldSection>

          <FieldSection label="技能内容 (Instructions)">
            {skill.valid && skill.instructions != null ? (
              <div className="rounded-xl border border-border/60 bg-card/60 px-4 py-3">
                <div className="prose prose-sm dark:prose-invert max-w-none wrap-break-word text-foreground">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{stripFrontmatter(skill.instructions)}</ReactMarkdown>
                </div>
              </div>
            ) : (
              <p className="rounded-xl border border-warning/40 bg-warning/10 p-4 text-sm text-warning">
                {skill.error ?? "SKILL.md 无效，无法渲染内容。"}
              </p>
            )}
          </FieldSection>
        </div>
      )}

      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>永久删除这个技能？</DialogTitle>
            <DialogDescription>
              删除会移除本地托管的技能副本（{decodedName}），该操作不可撤销。原始来源文件不受影响。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" disabled={pending} onClick={() => setConfirmOpen(false)}>
              取消
            </Button>
            <Button variant="destructive" disabled={pending} onClick={() => void remove()}>
              {pending ? "删除中…" : "确认删除"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
