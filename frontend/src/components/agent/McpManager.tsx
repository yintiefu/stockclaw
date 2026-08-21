/** MCP 管理器：server 增删改、分段式 transport、trust、test/refresh、工具启停。

只显示 env/header 引用名，绝不显示解析值；每次修改携带当前文档 revision，
409 时丢弃本地状态并触发一次刷新。
 */
import { useCallback, useEffect, useState } from "react";
import { PlugZap, Plus, RefreshCw, ShieldCheck, Trash2 } from "lucide-react";

import { agentApi } from "@/lib/agent/api";
import type { McpDocument, McpServer, StdioTrustPreview } from "@/lib/agent/types";

const BTN = "inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs text-muted-foreground "
  + "hover:bg-black/20 hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50";

type Props = {
  onReload: () => void;
  disabled: boolean;
};

const EMPTY_DOC: McpDocument = { schema_version: 1, revision: 0, servers: [] };

export function McpManager({ onReload, disabled }: Props) {
  const [doc, setDoc] = useState<McpDocument>(EMPTY_DOC);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [trustPreview, setTrustPreview] = useState<StdioTrustPreview | null>(null);
  const [trustServerId, setTrustServerId] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);

  const reload = useCallback(async () => {
    try {
      setDoc(await agentApi.getMcp());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载 MCP 配置失败");
    }
  }, []);

  useEffect(() => { void reload(); }, [reload]);

  const run = async (fn: () => Promise<void>) => {
    if (disabled || busy) return;
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      const preview = (e as { preview?: StdioTrustPreview }).preview;
      if (preview) {
        setTrustPreview(preview); // 先呈现完整命令供确认（归属 server 由 trust() 记录）
      }
      const status = (e as { status?: number }).status;
      if (status === 409) {
        if (!preview) {
          await reload(); // 丢弃本地状态，刷新一次，不自动重放
          onReload();
        }
        return;
      }
      setError(e instanceof Error ? e.message : "操作失败");
    } finally {
      setBusy(false);
    }
  };

  const toggleTool = (server: McpServer, name: string, next: boolean) =>
    run(async () => {
      const tool_enabled: Record<string, boolean> = { [name]: next };
      await agentApi.patchMcp(server.id, doc.revision, { tool_enabled });
      await reload();
      onReload();
    });

  const trust = (server: McpServer) =>
    run(async () => {
      setTrustServerId(server.id); // 预览只归属该 server，防止跨 server 误确认
      // 触发信任预览；错误交给 run 统一呈现 preview
      await agentApi.testMcp(server.id, doc.revision);
    });

  const confirmTrust = (server: McpServer) =>
    run(async () => {
      if (!trustPreview) return;
      await agentApi.trustMcp(server.id, doc.revision, trustPreview.fingerprint);
      setTrustPreview(null);
      setTrustServerId(null);
      await reload();
      onReload();
    });

  const test = (server: McpServer) =>
    run(async () => {
      await agentApi.testMcp(server.id, doc.revision);
      await reload();
      onReload();
    });

  const refresh = (server: McpServer) =>
    run(async () => {
      await agentApi.refreshMcp(server.id, doc.revision);
      await reload();
      onReload();
    });

  const remove = (server: McpServer) =>
    run(async () => {
      await agentApi.deleteMcp(server.id, doc.revision);
      await reload();
      onReload();
    });

  const addServer = (payload: Record<string, unknown>) =>
    run(async () => {
      await agentApi.addMcp(doc.revision, payload);
      setAdding(false);
      await reload();
      onReload();
    });

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-semibold text-muted-foreground">MCP 服务器（全局配置，工具按 server 启用）</h3>
        <button type="button" className={BTN} onClick={() => setAdding((v) => !v)} disabled={disabled || busy}>
          <Plus className="size-3.5" aria-hidden />
          新增 server
        </button>
      </div>

      {error && <p className="text-xs text-red-400">{error}</p>}

      {adding && (
        <AddServerForm disabled={disabled || busy} onSubmit={addServer} onCancel={() => setAdding(false)} />
      )}

      {doc.servers.length === 0 && !adding && (
        <p className="px-2 py-3 text-xs text-muted-foreground">尚未配置 MCP server</p>
      )}

      <ul className="space-y-2">
        {doc.servers.map((server) => (
          <li key={server.id} className="rounded-lg border border-border bg-black/20 p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <p className="text-sm font-medium">{server.display_name}
                  <span className="ml-2 text-xs text-muted-foreground">#{server.id}</span>
                </p>
                <p className="text-xs text-muted-foreground">
                  {server.transport.type === "stdio"
                    ? `stdio：${server.transport.executable} ${server.transport.args.join(" ")}`
                    : `HTTP：${server.transport.url}`}
                </p>
                {server.transport.type === "stdio" && Object.keys(server.transport.env).length > 0 && (
                  <p className="text-xs text-muted-foreground">
                    env 引用：{Object.values(server.transport.env).map((r) => r.from_env).join("、")}
                  </p>
                )}
                {server.transport.type === "streamable_http" && Object.keys(server.transport.headers).length > 0 && (
                  <p className="text-xs text-muted-foreground">
                    header 引用：{Object.values(server.transport.headers).map((r) => r.from_env).join("、")}
                  </p>
                )}
                <p className="text-xs text-muted-foreground">
                  {server.health.state === "ok" ? `✓ ${server.health.detail}` : server.health.state}
                </p>
              </div>
              <div className="flex items-center gap-1">
                {server.transport.type === "stdio" && !server.trust_fingerprint && (
                  <button type="button" className={BTN} onClick={() => trust(server)} disabled={disabled || busy}
                    title="显示完整命令并确认信任">
                    <ShieldCheck className="size-3.5" aria-hidden />
                    信任
                  </button>
                )}
                <button type="button" className={BTN} onClick={() => test(server)} disabled={disabled || busy}>
                  <PlugZap className="size-3.5" aria-hidden />
                  测试连接
                </button>
                <button type="button" className={BTN} onClick={() => refresh(server)} disabled={disabled || busy}>
                  <RefreshCw className="size-3.5" aria-hidden />
                  刷新目录
                </button>
                <button type="button" className={BTN} onClick={() => remove(server)} disabled={disabled || busy}>
                  <Trash2 className="size-3.5" aria-hidden />
                  删除
                </button>
              </div>
            </div>

            {trustPreview && trustServerId === server.id && (
              <div className="mt-2 rounded bg-black/30 p-2 text-xs">
                <p className="font-medium">信任确认（stdio 是用户明确选择的本地程序，不是沙箱）</p>
                <pre className="mt-1 whitespace-pre-wrap">{[
                  `executable: ${trustPreview.executable}`,
                  `resolved:   ${trustPreview.resolved_executable}`,
                  `args:       ${trustPreview.args.join(" ")}`,
                  `fingerprint: ${trustPreview.fingerprint}`,
                ].join("\n")}</pre>
                <div className="mt-1 flex items-center gap-2">
                  <button type="button" className={`${BTN} mt-1`}
                    onClick={() => confirmTrust(server)} disabled={disabled || busy}>
                    <ShieldCheck className="size-3.5" aria-hidden />
                    确认信任此命令
                  </button>
                  <button type="button" className={`${BTN} mt-1`}
                    onClick={() => { setTrustPreview(null); setTrustServerId(null); }}
                    disabled={disabled || busy}>
                    取消
                  </button>
                </div>
              </div>
            )}

            {server.tools.length > 0 && (
              <ul className="mt-2 space-y-1">
                {server.tools.map((toolEntry) => (
                  <li key={toolEntry.alias} className="flex items-center gap-2 text-xs">
                    <input
                      type="checkbox"
                      aria-label={toolEntry.original_name}
                      checked={toolEntry.enabled}
                      disabled={disabled || busy}
                      onChange={(e) => toggleTool(server, toolEntry.original_name, e.target.checked)}
                    />
                    <span className="font-mono">{toolEntry.original_name}</span>
                    <span className="truncate text-muted-foreground">{toolEntry.description}</span>
                  </li>
                ))}
              </ul>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

const INPUT = "w-full rounded-lg border border-border bg-black/20 px-2.5 py-1.5 text-xs outline-none focus:border-primary/50";

function AddServerForm({ disabled, onSubmit, onCancel }: {
  disabled: boolean;
  onSubmit: (payload: Record<string, unknown>) => void;
  onCancel: () => void;
}) {
  const [transport, setTransport] = useState<"stdio" | "streamable_http">("stdio");
  const [id, setId] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [executable, setExecutable] = useState("");
  const [args, setArgs] = useState("");
  const [envName, setEnvName] = useState("");
  const [url, setUrl] = useState("");
  const [headerName, setHeaderName] = useState("");

  const submit = () => {
    const env = envName ? { [envName]: { from_env: envName } } : {};
    const headers = headerName ? { [headerName]: { from_env: headerName } } : {};
    onSubmit({
      id,
      display_name: displayName || id,
      enabled: false,
      transport: transport === "stdio"
        ? { type: "stdio", executable, args: args.split(/\s+/).filter(Boolean), env }
        : { type: "streamable_http", url, headers },
    });
  };

  return (
    <div className="space-y-2 rounded-lg border border-border bg-black/20 p-3">
      <div className="flex gap-3 text-xs">
        <label className="flex cursor-pointer items-center gap-1">
          <input type="radio" name="mcp-transport" checked={transport === "stdio"}
            onChange={() => setTransport("stdio")} />
          stdio
        </label>
        <label className="flex cursor-pointer items-center gap-1">
          <input type="radio" name="mcp-transport" checked={transport === "streamable_http"}
            onChange={() => setTransport("streamable_http")} />
          Streamable HTTP
        </label>
      </div>
      <div className="grid gap-2 sm:grid-cols-2">
        <input aria-label="server id" className={INPUT} placeholder="server-id（小写字母数字连字符）"
          value={id} onChange={(e) => setId(e.target.value)} />
        <input aria-label="显示名" className={INPUT} placeholder="显示名"
          value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
      </div>
      {transport === "stdio" ? (
        <div className="grid gap-2 sm:grid-cols-3">
          <input aria-label="executable" className={INPUT} placeholder="executable（如 npx）"
            value={executable} onChange={(e) => setExecutable(e.target.value)} />
          <input aria-label="args" className={INPUT} placeholder="参数（空格分隔）"
            value={args} onChange={(e) => setArgs(e.target.value)} />
          <input aria-label="env 引用名" className={INPUT} placeholder="env 变量名（引用宿主环境）"
            value={envName} onChange={(e) => setEnvName(e.target.value)} />
        </div>
      ) : (
        <div className="grid gap-2 sm:grid-cols-2">
          <input aria-label="url" className={INPUT} placeholder="https://host/mcp"
            value={url} onChange={(e) => setUrl(e.target.value)} />
          <input aria-label="header 引用名" className={INPUT} placeholder="header 名（值来自环境变量）"
            value={headerName} onChange={(e) => setHeaderName(e.target.value)} />
        </div>
      )}
      <div className="flex justify-end gap-2">
        <button type="button" className={BTN} onClick={onCancel} disabled={disabled}>取消</button>
        <button type="button" className={BTN} onClick={submit} disabled={disabled || !id}>
          保存 server
        </button>
      </div>
    </div>
  );
}
