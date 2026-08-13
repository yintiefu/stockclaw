import { useEffect, useRef, useState, type FormEvent } from "react";
import { useParams, Link } from "react-router-dom";
import {
  ArrowLeft,
  ChevronRight,
  EyeOff,
  HelpCircle,
  Loader2,
  Plus,
  RotateCcw,
  Wrench,
  X,
} from "lucide-react";
import { toast } from "sonner";
import { PageHeader } from "@/components/ui/PageHeader";
import { GlassCard } from "@/components/ui/GlassCard";
import { AskAiButton } from "@/components/ui/AskAiButton";
import { Disclaimer } from "@/components/ui/Disclaimer";
import sectorsData from "@/data/sectors.json";
import { mergeLeaf, type SectorItem, type SectorTier } from "@/lib/sectorStocks";
import { useSectorStocks, type UseSectorStocks } from "@/hooks/useSectorStocks";

const ALLOWED_PREFIXES = ["SH.", "SZ.", "HK.", "US."];
// 文案红线：成分股为「数据源原序截取」，禁止任何排名/前N 表述。
const SOURCE_NOTE = "本地导入 · 数据源原序截取 · 非排名；不推荐个股";

/** 块状卡片的叶子：有 children 则取 children，否则自身即叶子。 */
function leavesOf(block: SectorItem): SectorItem[] {
  return block.children && block.children.length > 0 ? block.children : [block];
}

/** mutation 失败统一提示（hook 已回滚，此处只告知用户）。 */
function toastMutError(label: string) {
  return (err: unknown) => toast.error(err instanceof Error ? err.message : label);
}

/** 单个叶子的成分股面板：来源 / 已隐藏 / 我的关联 三区 + 添加表单。 */
function LeafList({
  leafId,
  stocks,
  openImport,
}: {
  leafId: string;
  stocks: UseSectorStocks;
  openImport: () => void;
}) {
  const leaf = stocks.data.leaves[leafId];
  const { source, mine } = mergeLeaf(leaf);
  const hidden = leaf?.hidden ?? [];
  const [code, setCode] = useState("");
  const [name, setName] = useState("");

  const onAdd = (e: FormEvent) => {
    e.preventDefault();
    const c = code.trim().toUpperCase();
    if (!ALLOWED_PREFIXES.some((p) => c.startsWith(p))) {
      toast.error("代码须以 SH./SZ./HK./US. 开头");
      return;
    }
    stocks.addMine(leafId, c, name.trim()).catch(toastMutError("添加失败，已回滚"));
    setCode("");
    setName("");
  };

  return (
    <div className="mt-2 space-y-3 border-t border-border/50 pt-3">
      {/* 来源成分股 */}
      <div>
        <h6 className="text-xs font-semibold text-muted-foreground">
          来源成分股{source.length === 0 && hidden.length > 0 ? "（已全部隐藏）" : ""}
        </h6>
        {source.length > 0 ? (
          <ul className="mt-1.5 flex flex-col gap-1.5">
            {source.map((s) => (
              <li key={s.code} className="flex items-center justify-between gap-2 rounded-md bg-muted/30 px-2.5 py-1.5">
                <span className="min-w-0 truncate text-sm">
                  {s.name} <code className="text-[11px] text-muted-foreground">{s.code}</code>
                </span>
                <button
                  type="button"
                  onClick={() => stocks.hide(leafId, s.code).catch(toastMutError("隐藏失败，已回滚"))}
                  aria-label={`隐藏 ${s.name}`}
                  className="inline-flex shrink-0 items-center gap-1 rounded-md px-2 py-1 text-xs text-muted-foreground hover:bg-muted hover:text-foreground"
                >
                  <EyeOff className="h-3.5 w-3.5" /> 隐藏
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-1.5 text-xs text-muted-foreground">
            暂无来源成分股（本地未导入）
            <button type="button" onClick={openImport} className="ml-1.5 inline-flex items-center gap-0.5 text-primary hover:underline">
              <HelpCircle className="h-3 w-3" /> 如何导入？
            </button>
          </p>
        )}
      </div>

      {/* 已隐藏 */}
      {hidden.length > 0 && (
        <div>
          <h6 className="text-xs font-semibold text-muted-foreground">已隐藏（{hidden.length}）</h6>
          <ul className="mt-1.5 flex flex-col gap-1.5">
            {hidden.map((hc) => (
              <li key={hc} className="flex items-center justify-between gap-2 rounded-md bg-muted/20 px-2.5 py-1.5">
                <code className="text-xs text-muted-foreground">{hc}</code>
                <button
                  type="button"
                  onClick={() => stocks.restore(leafId, hc).catch(toastMutError("恢复失败，已回滚"))}
                  aria-label={`恢复 ${hc}`}
                  className="inline-flex shrink-0 items-center gap-1 rounded-md px-2 py-1 text-xs text-primary hover:bg-muted"
                >
                  <RotateCcw className="h-3.5 w-3.5" /> 恢复
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* 我的关联标的 */}
      {mine.length > 0 && (
        <div>
          <h6 className="text-xs font-semibold text-muted-foreground">我的关联标的</h6>
          <ul className="mt-1.5 flex flex-col gap-1.5">
            {mine.map((s) => (
              <li key={s.code} className="flex items-center justify-between gap-2 rounded-md border border-primary/30 bg-primary/5 px-2.5 py-1.5">
                <span className="min-w-0 truncate text-sm">
                  {s.name} <code className="text-[11px] text-muted-foreground">{s.code}</code>
                </span>
                <button
                  type="button"
                  onClick={() => stocks.removeMine(leafId, s.code).catch(toastMutError("移除失败，已回滚"))}
                  aria-label={`移除 ${s.name}`}
                  className="inline-flex shrink-0 items-center gap-1 rounded-md px-2 py-1 text-xs text-muted-foreground hover:bg-muted hover:text-foreground"
                >
                  <X className="h-3.5 w-3.5" /> 移除
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* 添加我的关联 */}
      <form onSubmit={onAdd} className="flex flex-col gap-1.5 sm:flex-row sm:flex-wrap sm:items-end">
        <label className="flex flex-col gap-0.5 text-xs text-muted-foreground">
          代码
          <input
            value={code}
            onChange={(e) => setCode(e.target.value)}
            placeholder="SH.688017"
            className="w-full rounded-md border border-border/60 bg-background/60 px-2 py-1 text-sm text-foreground sm:w-32"
          />
        </label>
        <label className="flex flex-col gap-0.5 text-xs text-muted-foreground">
          名称
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="绿的谐波"
            className="w-full rounded-md border border-border/60 bg-background/60 px-2 py-1 text-sm text-foreground sm:w-32"
          />
        </label>
        <button
          type="submit"
          className="inline-flex items-center justify-center gap-1 rounded-md bg-primary/20 px-3 py-1.5 text-sm font-medium text-foreground hover:bg-primary/30"
        >
          <Plus className="h-3.5 w-3.5" /> 添加我的关联
        </button>
      </form>
    </div>
  );
}

export function SectorDetail() {
  const { key } = useParams();
  const sectorKey = key ?? "";
  const sector = sectorsData.sectors.find((s) => s.key === sectorKey);
  const stocks = useSectorStocks(sectorKey);

  const [openLeaf, setOpenLeaf] = useState<string | null>(null);
  const importDialogRef = useRef<HTMLDialogElement>(null);
  const importTriggerRef = useRef<HTMLElement | null>(null);

  // Esc 收起当前展开的叶子面板
  useEffect(() => {
    if (!openLeaf) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      // 「如何导入」对话框打开时，让原生 <dialog> 自行处理 Esc，不连带收起已展开的叶子面板
      if (importDialogRef.current?.open) return;
      setOpenLeaf(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [openLeaf]);

  if (!sector) {
    return (
      <div className="py-20 text-center text-muted-foreground">
        未找到该板块。<Link to="/sectors" className="text-primary">返回板块中心</Link>
      </div>
    );
  }

  const tiers = (sector.tiers ?? []) as unknown as SectorTier[];
  const meta = stocks.data.meta;
  const aiContext =
    `板块：${sector.label}\n定位：${sector.tagline}\n产业链环节：` +
    (sector.nodes?.length ? sector.nodes.join("、") : "（环节梳理中）");

  const openImport = () => {
    importTriggerRef.current = document.activeElement as HTMLElement | null;
    importDialogRef.current?.showModal();
  };
  const importCmd = `uv run --with futu-api==10.9.6908 python scripts/import-sector-chain.py --key ${sectorKey} --backend http://127.0.0.1:8900 --diagnose`;

  // 无 tiers：扁平回退（旧视图）
  if (tiers.length === 0) {
    return (
      <div>
        <Link to="/sectors" className="mb-3 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
          <ArrowLeft className="h-4 w-4" /> 板块中心
        </Link>
        <PageHeader
          title={sector.label}
          subtitle={sector.tagline}
          actions={
            <AskAiButton
              context={aiContext}
              label="让 AI 拆这个板块"
              suggestions={["按七维框架拆解", "这个板块的产业链地图", "哪个环节卡脖子", "有什么风险信号"]}
            />
          }
        />
        {sector.verified ? (
          <div>
            <h3 className="mb-3 text-sm font-semibold text-muted-foreground">核心环节（{sector.nodes?.length ?? 0}）</h3>
            <div className="flex flex-wrap gap-2.5">
              {(sector.nodes ?? []).map((n) => (
                <span key={n} className="rounded-full border border-primary/40 bg-primary/15 px-3.5 py-1.5 text-sm font-medium text-foreground shadow-glow transition-colors hover:bg-primary/25">
                  {n}
                </span>
              ))}
            </div>
          </div>
        ) : (
          <GlassCard>
            <div className="flex flex-col items-center gap-3 py-8 text-center">
              <Wrench className="h-8 w-8 text-muted-foreground/50" />
              <p className="text-sm text-muted-foreground">
                该板块的环节骨架尚在<b className="text-foreground">实时核实</b>补全中（不靠模型记忆）——已核实的板块见左侧。
              </p>
            </div>
          </GlassCard>
        )}
        <Disclaimer />
      </div>
    );
  }

  // 有 tiers：上中下游三段视图
  return (
    <div>
      <Link to="/sectors" className="mb-3 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="h-4 w-4" /> 板块中心
      </Link>

      <PageHeader
        title={sector.label}
        subtitle={sector.tagline}
        actions={
          <AskAiButton
            context={aiContext}
            label="让 AI 拆这个板块"
            suggestions={["按七维框架拆解", "这个板块的产业链地图", "哪个环节卡脖子", "有什么风险信号"]}
          />
        }
      />

      <p className="mb-4 text-xs text-muted-foreground/80">
        {SOURCE_NOTE}
        {meta.fetched_at && <span className="ml-2">· 已导入于 {meta.fetched_at}</span>}
      </p>

      {/* 加载失败横幅（≠ 未导入） */}
      {stocks.error && (
        <div role="alert" className="mb-4 rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-500">
          成分股加载失败：{stocks.error}。可刷新重试；本地未导入的叶子仍可手工添加「我的关联」。
        </div>
      )}

      {tiers.map((tier) => (
        <section key={tier.id} className="mb-6">
          <h3 className="mb-3 text-sm font-semibold text-muted-foreground">{tier.name}</h3>
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
            {tier.items.map((block) => (
              <GlassCard key={block.id}>
                <div className="mb-2">
                  <div className="font-semibold text-foreground">{block.name}</div>
                  {block.desc && <div className="text-xs text-muted-foreground/80">{block.desc}</div>}
                </div>
                <div className="flex flex-col gap-1.5">
                  {leavesOf(block).map((leaf) => {
                    const open = openLeaf === leaf.id;
                    const panelId = `leaf-panel-${leaf.id}`;
                    return (
                      <div key={leaf.id} className="rounded-md border border-border/40 bg-background/30">
                        <button
                          type="button"
                          aria-expanded={open}
                          aria-controls={panelId}
                          onClick={() => setOpenLeaf(open ? null : leaf.id)}
                          className="flex w-full items-center justify-between gap-2 px-2.5 py-1.5 text-left text-sm font-medium text-foreground hover:bg-muted/40"
                        >
                          <span className="flex items-center gap-1.5">
                            {leaf.name}
                            {leaf.source === "manual" && (
                              <span className="rounded bg-muted px-1 text-[10px] text-muted-foreground">手工</span>
                            )}
                          </span>
                          <ChevronRight className={`h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform ${open ? "rotate-90" : ""}`} />
                        </button>
                        {open && (
                          <div id={panelId} role="region" aria-label={`${leaf.name} 成分股`}>
                            {stocks.loading ? (
                              <div className="flex items-center gap-1.5 px-2.5 py-3 text-xs text-muted-foreground">
                                <Loader2 className="h-3.5 w-3.5 animate-spin" /> 加载中…
                              </div>
                            ) : (
                              <div className="px-2.5 pb-2.5">
                                <LeafList leafId={leaf.id} stocks={stocks} openImport={openImport} />
                              </div>
                            )}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              </GlassCard>
            ))}
          </div>
        </section>
      ))}

      {/* 导入说明对话框（原生 <dialog>：showModal 自带焦点陷阱 + Esc 关闭） */}
      <dialog
        ref={importDialogRef}
        onClose={() => {
          if (importTriggerRef.current?.isConnected) importTriggerRef.current.focus();
        }}
        onClick={(e) => {
          // 点遮罩关闭（dialog 自身即 backdrop）
          if (e.target === importDialogRef.current) importDialogRef.current?.close();
        }}
        className="w-[min(92vw,640px)] rounded-xl border border-border/60 bg-card p-0 text-foreground backdrop:bg-black/50"
      >
        <div className="p-5">
          <div className="mb-2 flex items-center justify-between">
            <h4 className="text-base font-bold">如何导入来源成分股？</h4>
            <button
              type="button"
              onClick={() => importDialogRef.current?.close()}
              aria-label="关闭"
              className="rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
          <div className="space-y-2 text-sm leading-relaxed text-muted-foreground">
            <p>
              来源成分股通过<b className="text-foreground">富途产业链板块</b>导入，经后端写入本地
              <code className="mx-1 text-xs">~/.vibe-research/sector-stocks.json</code>，不进仓库。数据为<b className="text-foreground">数据源原序截取</b>，非市值排名。
            </p>
            <p>需要本机已运行 <b className="text-foreground">富途 OpenD</b>（默认 127.0.0.1:11111），后端运行在 127.0.0.1:8900：</p>
            <pre className="overflow-x-auto rounded-lg border border-border/50 bg-muted/30 p-3 text-xs leading-relaxed text-foreground">
              <code>{importCmd}</code>
            </pre>
            <p className="text-xs text-muted-foreground/80">
              <code>--diagnose</code> 先自检连通性；去掉它即真实导入。可用 <code>--limit N</code>（1..200）控制每叶子保留只数。未导入的叶子仍可手工添加「我的关联」。
            </p>
          </div>
          <div className="mt-4 flex justify-end">
            <button
              type="button"
              onClick={() => importDialogRef.current?.close()}
              className="rounded-md bg-primary/20 px-4 py-1.5 text-sm font-medium hover:bg-primary/30"
            >
              知道了
            </button>
          </div>
        </div>
      </dialog>

      <Disclaimer />
    </div>
  );
}
