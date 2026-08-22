import { Download, Trash2 } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import type { ArtifactDetail, ArtifactMetadata } from "@/lib/agent/types";

type Props = {
  artifact: ArtifactDetail;
  versions: ArtifactMetadata[];
  hasChildren: boolean;
  busy?: boolean;
  onSelectVersion: (artifactId: string) => void;
  onDownload: () => void | Promise<void>;
  onDelete: () => void | Promise<void>;
};

function safeHttpUrl(value: string | undefined): string | null {
  if (!value) return null;
  try {
    const parsed = new URL(value);
    return parsed.protocol === "http:" || parsed.protocol === "https:" ? value : null;
  } catch {
    return null;
  }
}

function JsonNode({ value }: { value: unknown }) {
  if (value === null) return <span className="agent-data-scalar">null</span>;
  if (Array.isArray(value)) {
    return (
      <ol className="agent-json-list">
        {value.map((item, index) => <li key={index}><JsonNode value={item} /></li>)}
      </ol>
    );
  }
  if (typeof value === "object") {
    return (
      <dl className="agent-json-tree">
        {Object.entries(value as Record<string, unknown>).map(([key, item]) => (
          <div key={key} className="agent-json-entry">
            <dt>{key}</dt>
            <dd><JsonNode value={item} /></dd>
          </div>
        ))}
      </dl>
    );
  }
  return <span className="agent-data-scalar">{String(value)}</span>;
}

function ArtifactContent({ artifact }: { artifact: ArtifactDetail }) {
  if (artifact.type === "markdown") {
    return (
      <div className="agent-artifact-markdown prose prose-sm prose-invert max-w-none">
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          skipHtml
          urlTransform={(url) => safeHttpUrl(url) ?? ""}
          components={{
            a: ({ href, children }) => {
              const safe = safeHttpUrl(href);
              return safe
                ? <a href={safe} target="_blank" rel="noopener noreferrer">{children}</a>
                : <span>{children}</span>;
            },
            img: ({ alt }) => <span className="agent-blocked-image">[图片未加载：{alt || "未命名"}]</span>,
          }}
        >
          {artifact.content.markdown}
        </ReactMarkdown>
      </div>
    );
  }
  if (artifact.type === "table") {
    const rows = artifact.content.rows.slice(0, 200);
    return (
      <div className="space-y-2">
        <p className="text-xs text-muted-foreground">
          {artifact.content.rows.length > 200
            ? `显示前 200 行，共 ${artifact.content.rows.length} 行`
            : `共 ${artifact.content.rows.length} 行`}
        </p>
        <div className="agent-artifact-table-wrap">
          <table className="agent-artifact-table">
            <thead>
              <tr>{artifact.content.columns.map((column) => <th key={column.key}>{column.label}</th>)}</tr>
            </thead>
            <tbody>
              {rows.map((row, index) => (
                <tr key={index}>
                  {artifact.content.columns.map((column) => (
                    <td key={column.key}>{row[column.key] === null ? "null" : String(row[column.key])}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    );
  }
  if (artifact.type === "json") {
    return <div className="agent-json-root"><JsonNode value={artifact.content.value} /></div>;
  }
  return (
    <ul className="space-y-2">
      {artifact.content.items.map((item) => (
        <li key={item.source_id} className="border-l-2 border-border pl-2 text-xs">
          <p className="break-all font-mono text-muted-foreground">{item.source_id}</p>
          {item.note ? <p className="mt-1 whitespace-pre-wrap wrap-break-word text-foreground">{item.note}</p> : null}
        </li>
      ))}
    </ul>
  );
}

export function ArtifactViewer({
  artifact,
  versions,
  hasChildren,
  busy = false,
  onSelectVersion,
  onDownload,
  onDelete,
}: Props) {
  return (
    <div className="space-y-4" aria-label="Artifact 预览">
      <header className="space-y-2">
        <div className="flex min-w-0 items-start justify-between gap-2">
          <div className="min-w-0">
            <h3 className="wrap-break-word text-sm font-semibold">{artifact.title}</h3>
            <p className="mt-0.5 break-all font-mono text-[10px] text-muted-foreground">{artifact.id}</p>
          </div>
          <span className="shrink-0 rounded border border-border px-1.5 py-0.5 text-[10px] text-muted-foreground">
            {artifact.type}
          </span>
        </div>
        <div className="flex items-center gap-1">
          <button
            type="button"
            aria-label="下载 Artifact"
            title="下载 Artifact"
            disabled={busy}
            onClick={() => void onDownload()}
            className="agent-inspector-command"
          >
            <Download className="size-3.5" aria-hidden />
            下载
          </button>
          {!hasChildren ? (
            <button
              type="button"
              aria-label="删除 Artifact"
              title="删除 Artifact"
              disabled={busy}
              onClick={() => void onDelete()}
              className="agent-inspector-command text-destructive"
            >
              <Trash2 className="size-3.5" aria-hidden />
              删除
            </button>
          ) : <span className="text-[10px] text-muted-foreground">存在后续版本，不能删除</span>}
        </div>
      </header>

      {versions.length > 1 ? (
        <nav aria-label="Artifact 版本" className="flex flex-wrap gap-1">
          {versions.map((version) => (
            <button
              key={version.id}
              type="button"
              aria-label={`查看版本：${version.title}`}
              aria-current={version.id === artifact.id ? "page" : undefined}
              onClick={() => onSelectVersion(version.id)}
              className={`max-w-full truncate rounded border px-2 py-1 text-xs ${
                version.id === artifact.id
                  ? "border-primary/60 bg-primary/10 text-foreground"
                  : "border-border text-muted-foreground hover:text-foreground"
              }`}
            >
              {version.title}
            </button>
          ))}
        </nav>
      ) : null}

      <section className="border-t border-border/70 pt-3">
        <ArtifactContent artifact={artifact} />
      </section>
    </div>
  );
}
