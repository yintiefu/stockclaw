/** 技能导入对话框：本地文件夹或 ZIP，分段切换；导入后默认停用。 */
import { useEffect, useRef, useState } from "react";
import { FolderOpen, FileArchive } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { api, type SkillImportPayload } from "@/lib/api";
import { cn } from "@/lib/utils";

type ImportMode = "folder" | "zip";

interface PendingFile {
  path: string;
  file: File;
}

/** 目录选择的扩展属性（webkitdirectory）在 React 类型之外，单独声明。 */
type DirectoryInputProps = React.InputHTMLAttributes<HTMLInputElement> & {
  webkitdirectory?: string;
  directory?: string;
};

const PRIVACY_NOTE =
  "文件在导入时保留在本地；启用技能后，其元数据会写入本地会话检查点，读取的指令内容会发送到你所配置的模型。";

function readFileAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(new Error("文件读取失败"));
    reader.readAsDataURL(file);
  });
}

export function SkillImportDialog({
  open,
  onOpenChange,
  onImported,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onImported: () => void;
}) {
  const [mode, setMode] = useState<ImportMode>("folder");
  const [files, setFiles] = useState<PendingFile[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) {
      setFiles([]);
      setError(null);
      setMode("folder");
    }
  }, [open]);

  const collect = (selected: FileList | null) => {
    setError(null);
    if (!selected || selected.length === 0) return;
    setFiles(
      Array.from(selected).map((file) => ({
        path: (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name,
        file,
      })),
    );
  };

  const submit = async () => {
    if (files.length === 0) {
      setError("请先选择要导入的文件");
      return;
    }
    setPending(true);
    setError(null);
    try {
      const encoded = await Promise.all(
        files.map(async (entry) => ({ path: entry.path, content_b64: await readFileAsDataUrl(entry.file) })),
      );
      const payload: SkillImportPayload =
        mode === "folder"
          ? { kind: "folder", files: encoded }
          : { kind: "zip", filename: files[0].file.name, content_b64: encoded[0].content_b64 };
      await api.importSkill(payload);
      toast.success("技能已导入（默认停用）。启用后新会话生效，已有会话请执行 /reload-skills。");
      onImported();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "技能导入失败");
    } finally {
      setPending(false);
    }
  };

  const pickerProps: DirectoryInputProps =
    mode === "folder"
      ? { webkitdirectory: "", directory: "", multiple: true }
      : { accept: ".zip", multiple: false };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>导入技能</DialogTitle>
          <DialogDescription>
            本地导入：选择一个技能文件夹（含 SKILL.md）或单个 ZIP 包；导入后默认停用。
          </DialogDescription>
        </DialogHeader>

        <div className="grid grid-cols-2 gap-2" role="group" aria-label="导入方式">
          <button
            type="button"
            aria-pressed={mode === "folder"}
            onClick={() => { setMode("folder"); setFiles([]); }}
            className={cn(
              "flex items-center justify-center gap-2 rounded-lg border p-2.5 text-sm transition-colors",
              mode === "folder"
                ? "border-primary bg-primary/10 font-medium text-primary"
                : "border-border text-muted-foreground hover:text-foreground",
            )}
          >
            <FolderOpen className="h-4 w-4" /> 文件夹
          </button>
          <button
            type="button"
            aria-pressed={mode === "zip"}
            onClick={() => { setMode("zip"); setFiles([]); }}
            className={cn(
              "flex items-center justify-center gap-2 rounded-lg border p-2.5 text-sm transition-colors",
              mode === "zip"
                ? "border-primary bg-primary/10 font-medium text-primary"
                : "border-border text-muted-foreground hover:text-foreground",
            )}
          >
            <FileArchive className="h-4 w-4" /> ZIP 包
          </button>
        </div>

        <input
          ref={inputRef}
          type="file"
          aria-label={mode === "folder" ? "选择技能文件夹" : "选择 ZIP 文件"}
          className="w-full text-sm text-muted-foreground"
          {...pickerProps}
          onChange={(event) => collect(event.target.files)}
        />

        {files.length > 0 && (
          <p className="text-xs text-muted-foreground">
            已选择 {files.length} 个文件
            {files[0] ? `：${files[0].path}${files.length > 1 ? " 等" : ""}` : ""}
          </p>
        )}
        {error && <p className="text-sm text-destructive">{error}</p>}

        <p className="rounded-lg bg-muted/50 p-3 text-xs leading-relaxed text-muted-foreground">{PRIVACY_NOTE}</p>

        <DialogFooter>
          <Button variant="outline" disabled={pending} onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button disabled={pending || files.length === 0} onClick={() => void submit()}>
            {pending ? "导入中…" : "导入"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
