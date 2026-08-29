import { useEffect, useState } from "react";
import { KeyRound, ShieldCheck, ServerCog, Cpu, FolderCog, Plug, Copy, Check, RefreshCw, AlertCircle } from "lucide-react";
import { PageHeader } from "@/components/ui/PageHeader";
import { GlassCard } from "@/components/ui/GlassCard";
import { toast } from "sonner";
import { api, loadAccessKey, saveAccessKey, type AgentStatusSummary } from "@/lib/api";
import { copyText } from "@/lib/clipboard";

// 「模型设置」（设置中心分区）为 Agent 只读状态页：模型与密钥只在服务端
// ~/.vibe-research/agent/settings.json（权限 0600）里配置，浏览器不再保存、
// 不再回读任何密钥；页面只展示脱敏摘要 + 启动指引 + 独立的 FastAPI 访问密钥。

type Readiness = "checking" | "online" | "offline";

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-border/30 py-2 last:border-0">
      <span className="text-xs text-muted-foreground">{label}</span>
      <span className="truncate font-mono text-xs text-foreground" title={value}>{value}</span>
    </div>
  );
}

export function Settings() {
  const [status, setStatus] = useState<AgentStatusSummary | null>(null);
  const [statusErr, setStatusErr] = useState<string | null>(null);
  const [readiness, setReadiness] = useState<Readiness>("checking");
  const [copied, setCopied] = useState(false);
  // 后端访问密钥（对应部署时的 VR_API_KEY）；本机自用不设鉴权时留空
  const [accessKey, setAccessKey] = useState(loadAccessKey());

  const load = () => {
    setStatusErr(null);
    api.agentStatus().then(setStatus).catch((e) => {
      setStatus(null);
      setStatusErr(e instanceof Error ? e.message : "状态读取失败");
    });
    setReadiness("checking");
    // LangGraph 实际 readiness 以 /agent-api/ok 为准（本机开发代理，仅 loopback）。
    fetch(new URL("/agent-api/ok", window.location.origin).toString())
      .then((r) => setReadiness(r.ok ? "online" : "offline"))
      .catch(() => setReadiness("offline"));
  };

  useEffect(() => { load(); }, []);

  const copyTemplate = async () => {
    if (!status) return;
    const ok = await copyText(status.config_template);
    if (ok) {
      setCopied(true);
      toast.success("配置模板已复制：填入你的模型与 API Key 后保存");
      window.setTimeout(() => setCopied(false), 2000);
    } else {
      toast.error("复制失败，请手动选择模板文本复制");
    }
  };

  const saveAccess = () => {
    const k = accessKey.trim();
    saveAccessKey(k);
    setAccessKey(k);
    toast.success(k ? "已保存后端访问密钥（存本地）" : "已清除后端访问密钥");
  };

  return (
    <div>
      <PageHeader
        title="模型设置"
        subtitle="模型与密钥只配置在服务端 Agent 设置文件里；本页只读展示状态与启动指引"
        actions={
          <button onClick={load} className="text-muted-foreground hover:text-primary" title="刷新状态">
            <RefreshCw className="h-4 w-4" />
          </button>
        }
      />

      <div className="mb-4 flex items-start gap-2 rounded-lg border border-success/25 bg-success/5 p-3 text-xs text-muted-foreground">
        <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-success" />
        <span>API Key <b className="text-foreground">只存在本机服务端</b> <code className="rounded bg-muted/50 px-1">settings.json</code>（权限 0600），本页不读取、不显示、不保存任何密钥。所有分析由你自己配置的模型给出，本产品不校准、不背书。</span>
      </div>

      {/* LangGraph 就绪 + 脱敏摘要 */}
      <GlassCard className="mb-4">
        <div className="mb-3 flex items-center justify-between">
          <h3 className="flex items-center gap-1.5 text-sm font-semibold">
            <ServerCog className="h-4 w-4 text-primary" /> Agent 服务状态
          </h3>
          <span className={`rounded-full px-2.5 py-0.5 text-[11px] font-medium ${
            readiness === "online" ? "bg-success/15 text-success"
              : readiness === "offline" ? "bg-destructive/15 text-destructive"
                : "bg-muted/50 text-muted-foreground"
          }`}>
            {readiness === "online" ? "Agent 服务在线" : readiness === "offline" ? "Agent 服务离线" : "检测中…"}
          </span>
        </div>

        {statusErr && (
          <p className="mb-2 flex items-center gap-2 text-xs text-destructive">
            <AlertCircle className="h-3.5 w-3.5 shrink-0" /> {statusErr}
          </p>
        )}

        {status ? (
          <div>
            <Row label="模型" value={status.model_name ?? "（未配置）"} />
            <Row label="模型服务主机" value={status.base_url_host ?? "（未配置）"} />
            <Row label="配置文件" value={status.settings_path} />
            <div className="mt-2 grid grid-cols-2 gap-2">
              <div className="flex items-center gap-2 rounded-lg bg-muted/25 p-2.5">
                <FolderCog className="h-4 w-4 text-primary/70" />
                <div>
                  <p className="text-[11px] text-muted-foreground">内置 Skill</p>
                  <p className="font-mono text-sm font-bold">{status.builtin_skill_count}</p>
                </div>
              </div>
              <div className="flex items-center gap-2 rounded-lg bg-muted/25 p-2.5">
                <Plug className="h-4 w-4 text-primary/70" />
                <div>
                  <p className="text-[11px] text-muted-foreground">MCP Server</p>
                  <p className="font-mono text-sm font-bold">{status.mcp_server_count}</p>
                </div>
              </div>
            </div>
            {status.restart_required && (
              <p className="mt-2 text-[11px] text-muted-foreground/70">修改配置文件后需重启 Agent 服务才会生效。</p>
            )}
          </div>
        ) : !statusErr ? (
          <p className="text-xs text-muted-foreground">正在读取配置状态…</p>
        ) : null}
      </GlassCard>

      {/* 配置模板 + （未配置/离线时）启动指引 */}
      <GlassCard className="mb-4">
        <h3 className="mb-2 flex items-center gap-1.5 text-sm font-semibold">
          <Cpu className="h-4 w-4 text-primary" /> 配置与启动
        </h3>
        {(!status?.configured || readiness === "offline") && (
          <>
            {status && !status.configured && status.reason && (
              <p className="mb-3 rounded-lg border border-warning/30 bg-warning/5 p-2.5 text-xs text-warning">{status.reason}</p>
            )}
            <ol className="list-decimal space-y-1.5 pl-5 text-xs leading-relaxed text-muted-foreground">
              <li>创建 Skills 目录：<code className="rounded bg-muted/50 px-1">mkdir -p ~/.vibe-research/agent/skills</code></li>
              <li>把配置写到 <code className="rounded bg-muted/50 px-1">{status?.settings_path ?? "~/.vibe-research/agent/settings.json"}</code>（模板见下方，<code className="rounded bg-muted/50 px-1">apiKey</code> 填你自己的）</li>
              <li>收紧权限：<code className="rounded bg-muted/50 px-1">chmod 600 {status?.settings_path ?? "~/.vibe-research/agent/settings.json"}</code></li>
              <li>在本机启动 Agent 服务（仅监听 loopback）：<code className="rounded bg-muted/50 px-1">langgraph dev --host 127.0.0.1 --port 2024 --no-browser</code></li>
            </ol>
          </>
        )}
        <div className="mt-3">
          <button onClick={copyTemplate}
            className="inline-flex items-center gap-1.5 rounded-lg bg-primary/15 px-3 py-1.5 text-xs font-medium text-primary hover:bg-primary/25">
            {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
            {copied ? "已复制" : "一键复制配置模板"}
          </button>
          <pre className="mt-2 overflow-x-auto rounded-lg bg-black/30 p-3 text-[11px] leading-relaxed text-foreground/90">{status?.config_template ?? '{\n  "model": { … }\n}'}</pre>
          <p className="mt-1 text-[10px] text-muted-foreground/60">模板中的 YOUR_API_KEY 只是占位符；本页与状态接口都不会读取或显示真实密钥。</p>
        </div>
      </GlassCard>

      {/* 后端访问密钥：仅当后端部署时设置了 VR_API_KEY（公网防蹭用）才需要填 */}
      <GlassCard>
        <h3 className="mb-1 flex items-center gap-1.5 text-sm font-semibold">
          <KeyRound className="h-4 w-4 text-primary" /> 后端访问密钥（可选）
        </h3>
        <p className="mb-3 text-xs text-muted-foreground">
          仅当后端部署时设置了 <code className="rounded bg-muted/50 px-1">VR_API_KEY</code>（公网部署防蹭用）才需要填，填后端同一个值；
          本机自用没设鉴权就留空。它只保护 FastAPI 数据接口，<b className="text-foreground">不配置、也不保护</b> Agent 服务（Agent 仅监听本机 loopback）。
        </p>
        <div className="flex items-center gap-2">
          <input type="password" value={accessKey} onChange={(e) => setAccessKey(e.target.value)} placeholder="与后端 VR_API_KEY 保持一致"
            className="flex-1 rounded-lg border border-border bg-black/20 px-3 py-2 text-sm outline-hidden focus:border-primary/50" />
          <button onClick={saveAccess} className="rounded-lg bg-primary/15 px-4 py-2 text-sm font-medium text-primary hover:bg-primary/25">
            保存
          </button>
        </div>
      </GlassCard>
    </div>
  );
}
