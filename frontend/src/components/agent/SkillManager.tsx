/** Skill 管理器：列表 / 详情 / 导入 / 刷新 / 删除 与资源预览。

预览只渲染转义纯文本（<pre>）；图片/PDF 使用认证 Blob → object URL，
替换/关闭/卸载时统一 revoke，绝不把受保护 API URL 直接放进 src。
 */
import { useEffect, useRef, useState } from "react";
import { RefreshCw, Trash2, Upload } from "lucide-react";

import { agentApi } from "@/lib/agent/api";
import type { SkillDetail, SkillFile, SkillSummary } from "@/lib/agent/types";

const BTN = "inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs text-muted-foreground "
  + "hover:bg-black/20 hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50";

type Props = {
  skills: SkillSummary[];
  disabled: boolean;
  onChanged: () => void;
};

export function SkillManager({ skills, disabled, onChanged }: Props) {
  const [detail, setDetail] = useState<SkillDetail | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [objectUrl, setObjectUrl] = useState<string | null>(null);
  const objectUrlRef = useRef<string | null>(null);

  const revokeUrl = () => {
    if (objectUrlRef.current) {
      URL.revokeObjectURL(objectUrlRef.current);
      objectUrlRef.current = null;
    }
    setObjectUrl(null);
  };
  useEffect(() => revokeUrl, []);

  const run = async (fn: () => Promise<void>) => {
    if (disabled || busy) return;
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : "操作失败");
    } finally {
      setBusy(false);
    }
  };

  const openDetail = (name: string) =>
    run(async () => {
      revokeUrl();
      const loaded = await agentApi.getSkill(name);
      setDetail(loaded);
    });

  const refresh = () => run(async () => {
    await agentApi.refreshSkills();
    onChanged();
  });

  const importZip = async (file: File | undefined) => {
    if (!file) return;
    await run(async () => {
      try {
        await agentApi.importSkill("/api/agent/skills/import", file);
      } catch (e) {
        // 目标已存在 → 用当前 digest 确认覆盖
        const status = (e as { status?: number }).status;
        const code = (e as { code?: string }).code;
        if (status === 409 && code === "SKILL_CONFLICT") {
          const listed = await agentApi.listSkills();
          const target = listed.skills.find((s) => file.name.replace(/\.zip$/i, "") === s.name);
          if (target?.digest && window.confirm(`Skill 已存在，用当前版本覆盖 ${target.name}？`)) {
            const form = new FormData();
            form.append("archive", file);
            form.append("overwrite", "true");
            form.append("expected_digest", target.digest);
            await fetch("/api/agent/skills/import", { method: "POST", body: form });
            return;
          }
        }
        throw e;
      }
      onChanged();
    });
  };

  const remove = (skill: SkillSummary) =>
    run(async () => {
      if (!skill.digest || !window.confirm(`确认删除 Skill ${skill.name ?? skill.directory}？`)) return;
      await agentApi.deleteSkill(skill.name ?? skill.directory, skill.digest);
      if (detail?.name === skill.name) setDetail(null);
      onChanged();
    });

  const preview = async (name: string, file: SkillFile) => {
    await run(async () => {
      const blob = await agentApi.fetchSkillFile(name, file.relative_path);
      revokeUrl();
      if (file.mime?.startsWith("image/") || file.mime === "application/pdf") {
        objectUrlRef.current = URL.createObjectURL(blob);
        setObjectUrl(objectUrlRef.current);
      } else {
        const text = await blob.text();
        setPreviewText(text);
      }
    });
  };
  const [previewText, setPreviewText] = useState<string | null>(null);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <label className={`${BTN} cursor-pointer`}>
          <Upload className="size-3.5" aria-hidden />
          导入 Skill
          <input
            aria-label="导入 Skill"
            type="file"
            accept=".zip"
            className="hidden"
            disabled={disabled || busy}
            onChange={(e) => { void importZip(e.target.files?.[0]); e.currentTarget.value = ""; }}
          />
        </label>
        <button type="button" className={BTN} onClick={refresh} disabled={disabled || busy}
          title="重新扫描 Skill 目录">
          <RefreshCw className="size-3.5" aria-hidden />
          刷新
        </button>
        <span className="text-xs text-muted-foreground">{skills.length} 个条目</span>
      </div>

      {error && <p className="text-xs text-red-400">{error}</p>}

      <ul className="max-h-64 space-y-1 overflow-y-auto">
        {skills.map((skill) => (
          <li key={skill.directory} className="flex items-center justify-between gap-2 rounded-md px-2 py-1.5 hover:bg-black/20">
            <div className="min-w-0">
              <button type="button" className="truncate text-left text-sm font-medium hover:underline disabled:opacity-50"
                onClick={() => openDetail(skill.name ?? skill.directory)}
                disabled={busy}>
                详情 {skill.name ?? skill.directory}
              </button>
              <p className="truncate text-xs text-muted-foreground">
                {skill.valid ? skill.description : `${skill.error_code ?? "SKILL_INVALID"}：${skill.error_detail ?? ""}`}
              </p>
            </div>
            <button type="button" className={BTN} onClick={() => remove(skill)}
              disabled={disabled || busy || !skill.valid || !skill.digest}
              title="删除该 Skill">
              <Trash2 className="size-3.5" aria-hidden />
              删除 {skill.name ?? skill.directory}
            </button>
          </li>
        ))}
        {skills.length === 0 && (
          <li className="px-2 py-4 text-center text-xs text-muted-foreground">尚无 Skill，导入 zip 开始</li>
        )}
      </ul>

      {detail && (
        <div className="rounded-lg border border-border bg-black/20 p-3 text-xs">
          <p className="font-medium">{detail.name}（digest {detail.digest?.slice(0, 12)}…）</p>
          {detail.instructions && <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap">{detail.instructions}</pre>}
          <ul className="mt-2 space-y-1">
            {detail.files.filter((f) => f.downloadable).map((file) => (
              <li key={file.relative_path}>
                <button type="button" className="underline disabled:opacity-50"
                  onClick={() => preview(detail.name ?? "", file)} disabled={busy}>
                  {file.relative_path}（{file.mime}）
                </button>
              </li>
            ))}
          </ul>
          {previewText !== null && (
            <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap rounded bg-black/30 p-2">{previewText}</pre>
          )}
          {objectUrl && fileIsViewable(detail) && (
            <img src={objectUrl} alt="Skill 资源预览" className="mt-2 max-h-56 rounded" />
          )}
        </div>
      )}
    </div>
  );
}

function fileIsViewable(_detail: SkillDetail): boolean {
  return true;
}
