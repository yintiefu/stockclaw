/** 技能详情：Markdown 只读渲染、虚拟路径与用户技能启停/删除（内置只读）。 */
import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ArrowLeft, Trash2 } from "lucide-react";
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
        <Link to="/skills" className={cn(buttonVariants({ variant: "outline", size: "sm" }), "mt-3")}>
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
      navigate("/skills");
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
        <Link to="/skills" className={cn(buttonVariants({ variant: "ghost", size: "sm" }))}>
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
        <div className="space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border/40 pb-4">
            <div className="min-w-0">
              <h1 className="font-mono text-2xl font-extrabold break-all text-glow">{skill.name}</h1>
              <p className="mt-1 text-xs break-all text-muted-foreground">{skill.description ?? ""}</p>
            </div>
            <div className="flex flex-wrap items-center gap-3">
              {skill.source === "builtin" ? (
                <span className="rounded bg-muted px-2 py-1 text-xs text-muted-foreground">内置 / 始终启用</span>
              ) : skill.valid ? (
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

          <p className="rounded-lg bg-muted/50 px-3 py-2 font-mono text-xs break-all text-muted-foreground">
            {skill.path}
          </p>

          {skill.valid && skill.instructions != null ? (
            <div className="prose prose-sm dark:prose-invert max-w-none wrap-break-word text-foreground">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{skill.instructions}</ReactMarkdown>
            </div>
          ) : (
            <p className="rounded-xl border border-warning/40 bg-warning/10 p-4 text-sm text-warning">
              {skill.error ?? "SKILL.md 无效，无法渲染内容。"}
            </p>
          )}
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
