import { useEffect, useRef, useState, type FormEvent } from "react";
import { useParams, Link } from "react-router-dom";
import {
  ArrowLeft,
  ChevronDown,
  Loader2,
  Plus,
  Trash2,
  Wrench,
  X,
} from "lucide-react";
import { toast } from "sonner";
import { PageHeader } from "@/components/ui/PageHeader";
import { GlassCard } from "@/components/ui/GlassCard";
import { AskAiButton } from "@/components/ui/AskAiButton";
import { Disclaimer } from "@/components/ui/Disclaimer";
import sectorsData from "@/data/sectors.json";
import {
  countItemStocks,
  firstLeafId,
  leavesOf,
  mergeLeaf,
  type SectorItem,
  type SectorTier,
} from "@/lib/sectorStocks";
import { useSectorStocks, type UseSectorStocks } from "@/hooks/useSectorStocks";

const ALLOWED_PREFIXES = ["SH.", "SZ.", "HK.", "US."];
// 文案红线：成分股为「数据源原序截取」，禁止任何排名/前N 表述。
const SOURCE_NOTE = "本地导入 · 数据源原序截取 · 非排名；不推荐个股";

/** mutation 失败统一提示（hook 已回滚，此处只告知用户）。 */
function toastMutError(label: string) {
  return (err: unknown) => toast.error(err instanceof Error ? err.message : label);
}

/** 单个叶子的成分股面板：来源 / 我的关联 两区 + 添加表单。 */
function LeafList({
  leafId,
  stocks,
}: {
  leafId: string;
  stocks: UseSectorStocks;
}) {
  const leaf = stocks.data.leaves[leafId];
  const { source, mine } = mergeLeaf(leaf);
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [pending, setPending] = useState<{ code: string; name: string } | null>(null);
  const [delOpen, setDelOpen] = useState(false);
  const delDialogRef = useRef<HTMLDialogElement>(null);

  // dialog 的开关由 React state 驱动（effect 同步到原生 showModal/close）。
  // 这样 HMR / Fast Refresh 重新挂载组件时 state 保留、effect 自愈，
  // 不会留下脱离 React 控制的「幽灵弹窗」（open 残留、按钮 onClick 失效）。
  useEffect(() => {
    const dlg = delDialogRef.current;
    if (!dlg) return;
    if (delOpen) {
      if (!dlg.open) dlg.showModal();
    } else if (dlg.open) {
      dlg.close();
    }
  }, [delOpen]);

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
    <div className="space-y-4">
      {/* 来源成分股 */}
      <div>
        <h6 className="text-xs font-semibold text-muted-foreground">
          来源成分股
        </h6>
        {source.length > 0 ? (
          <ul className="mt-1.5 flex flex-col gap-1.5">
            {source.map((s) => (
              <li key={s.code} className="flex min-h-12 items-center justify-between gap-2 border-b border-border/40 px-1 py-2 last:border-b-0">
                <span className="min-w-0 truncate text-sm">
                  {s.name} <code className="text-[11px] text-muted-foreground">{s.code}</code>
                </span>
                <button
                  type="button"
                  onClick={() => {
                    setPending({ code: s.code, name: s.name });
                    setDelOpen(true);
                  }}
                  aria-label={`删除 ${s.name}`}
                  className="inline-flex min-h-9 shrink-0 items-center justify-center rounded-md px-2 text-xs text-muted-foreground hover:bg-red-500/10 hover:text-red-500"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-1.5 text-xs text-muted-foreground">
            暂无来源成分股（本地未导入）
          </p>
        )}
      </div>


      {/* 我的关联标的 */}
      {mine.length > 0 && (
        <div>
          <h6 className="text-xs font-semibold text-muted-foreground">我的关联标的</h6>
          <ul className="mt-1.5 flex flex-col gap-1.5">
            {mine.map((s) => (
              <li key={s.code} className="flex min-h-12 items-center justify-between gap-2 border-b border-primary/20 bg-primary/5 px-1 py-2 last:border-b-0">
                <span className="min-w-0 truncate text-sm">
                  {s.name} <code className="text-[11px] text-muted-foreground">{s.code}</code>
                </span>
                <button
                  type="button"
                  onClick={() => stocks.removeMine(leafId, s.code).catch(toastMutError("移除失败，已回滚"))}
                  aria-label={`移除 ${s.name}`}
                  className="inline-flex min-h-9 shrink-0 items-center gap-1 rounded-md px-2 text-xs text-muted-foreground hover:bg-muted hover:text-foreground"
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
            className="min-h-10 w-full rounded-md border border-border/60 bg-background/60 px-2 py-1 text-sm text-foreground sm:w-32"
          />
        </label>
        <label className="flex flex-col gap-0.5 text-xs text-muted-foreground">
          名称
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="绿的谐波"
            className="min-h-10 w-full rounded-md border border-border/60 bg-background/60 px-2 py-1 text-sm text-foreground sm:w-32"
          />
        </label>
        <button
          type="submit"
          className="inline-flex min-h-10 items-center justify-center gap-1 rounded-md bg-primary/20 px-3 py-1.5 text-sm font-medium text-foreground hover:bg-primary/30"
        >
          <Plus className="h-3.5 w-3.5" /> 添加我的关联
        </button>
      </form>

      {/* 删除确认弹窗（原生 <dialog>：showModal 自带焦点陷阱 + Esc 取消 + 点遮罩取消） */}
      <dialog
        ref={delDialogRef}
        onClose={() => { setDelOpen(false); setPending(null); }}
        onClick={(e) => { if (e.target === e.currentTarget) setDelOpen(false); }}
        className="fixed left-1/2 top-0 m-0 h-screen w-screen -translate-x-1/2 items-center justify-center bg-transparent p-0 open:flex backdrop:bg-black/50"
      >
        <div className="w-[min(92vw,420px)] rounded-xl border border-border/60 bg-card p-5 text-foreground">
          <div className="flex items-start gap-3">
            <span className="mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-full bg-red-500/15 text-red-500">
              <Trash2 className="h-4 w-4" />
            </span>
            <div className="min-w-0">
              <h4 className="text-base font-bold text-foreground">确认删除该成分股？</h4>
              <p className="mt-1 text-sm text-muted-foreground">
                {pending?.name} <code className="text-xs">{pending?.code}</code>
              </p>
              <p className="mt-2 text-xs leading-relaxed text-muted-foreground/80">
                删除后该标的从来源成分股移除；重新导入富途数据可恢复。
              </p>
            </div>
          </div>
          <div className="mt-5 flex justify-end gap-2">
            <button
              type="button"
              onClick={() => setDelOpen(false)}
              className="rounded-md border border-border/60 px-4 py-1.5 text-sm text-muted-foreground hover:bg-muted"
            >
              取消
            </button>
            <button
              type="button"
              onClick={() => {
                if (pending) stocks.deleteStock(leafId, pending.code).catch(toastMutError("删除失败，已回滚"));
                setDelOpen(false);
              }}
              className="rounded-md bg-red-500 px-4 py-1.5 text-sm font-medium text-white hover:bg-red-600"
            >
              确认删除
            </button>
          </div>
        </div>
      </dialog>
    </div>
  );
}

export function SectorDetail() {
  const { key } = useParams();
  const sectorKey = key ?? "";
  const sector = sectorsData.sectors.find((s) => s.key === sectorKey);
  const stocks = useSectorStocks(sectorKey);

  const [selectedItemId, setSelectedItemId] = useState<string | null>(null);
  const [selectedLeafId, setSelectedLeafId] = useState<string | null>(null);

  if (!sector) {
    return (
      <div className="py-20 text-center text-muted-foreground">
        未找到该板块。<Link to="/sectors" className="text-primary">返回板块中心</Link>
      </div>
    );
  }

  const tiers = (sector.tiers ?? []) as unknown as SectorTier[];
  const allItems = tiers.flatMap((tier) => tier.items);
  const selectedItem = allItems.find((item) => item.id === selectedItemId) ?? allItems[0];
  const selectedLeaves = selectedItem ? leavesOf(selectedItem) : [];
  const selectedLeaf = selectedLeaves.find((leaf) => leaf.id === selectedLeafId) ?? selectedLeaves[0];
  const selectedTier = tiers.find((tier) => tier.items.some((item) => item.id === selectedItem?.id));
  const meta = stocks.data.meta;
  const aiContext =
    `板块：${sector.label}\n定位：${sector.tagline}\n产业链环节：` +
    (sector.nodes?.length ? sector.nodes.join("、") : "（环节梳理中）");

  const selectItem = (item: SectorItem) => {
    setSelectedItemId(item.id);
    setSelectedLeafId(firstLeafId(item));
  };

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

      <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(320px,380px)]">
        <div className="space-y-0" aria-label="产业链纵向总览">
          {tiers.map((tier, tierIndex) => (
            <div key={tier.id}>
              {tierIndex > 0 && (
                <div className="flex h-10 justify-center" aria-hidden="true">
                  <div className="relative h-full w-px bg-border/70">
                    <ChevronDown className="absolute -bottom-0.5 -left-[7px] h-4 w-4 text-muted-foreground" />
                  </div>
                </div>
              )}
              <section className="rounded-lg border border-border/70 bg-card/45 p-3.5 sm:p-4" aria-labelledby={`tier-${tier.id}`}>
                <header className="mb-3 flex items-center gap-2.5">
                  <span className={`grid h-7 w-7 shrink-0 place-items-center rounded-full text-[11px] font-bold ${tierIndex === 0 ? "bg-primary/20 text-primary" : "bg-muted text-muted-foreground"}`}>
                    {String(tierIndex + 1).padStart(2, "0")}
                  </span>
                  <h3 id={`tier-${tier.id}`} className="text-sm font-semibold text-foreground">{tier.name}</h3>
                </header>
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
                  {tier.items.map((item) => {
                    const selected = selectedItem?.id === item.id;
                    const count = countItemStocks(item, stocks.data.leaves);
                    return (
                      <button
                        key={item.id}
                        type="button"
                        aria-pressed={selected}
                        onClick={() => selectItem(item)}
                        className={`min-h-24 min-w-0 rounded-md border px-4 py-3.5 text-left transition-colors ${selected ? "border-primary bg-primary/10 shadow-[inset_3px_0_0_hsl(var(--primary))]" : "border-border/50 bg-background/30 hover:border-border hover:bg-muted/30"}`}
                      >
                        <span className="flex items-start justify-between gap-2">
                          <span className="min-w-0 text-[15px] font-semibold leading-5 text-foreground">{item.name}</span>
                          <span className={`shrink-0 font-mono text-xs leading-5 ${selected ? "text-primary" : "text-muted-foreground"}`}>
                            {count > 0 ? count : "—"}
                          </span>
                        </span>
                        <span className="mt-2 block min-h-9 max-w-full overflow-hidden text-xs leading-[18px] text-muted-foreground [display:-webkit-box] [-webkit-box-orient:vertical] [-webkit-line-clamp:2]">
                          {[item.desc, leavesOf(item).slice(0, 3).map((leaf) => leaf.name).join(" / ")].filter(Boolean).join(" · ")}
                        </span>
                      </button>
                    );
                  })}
                </div>
              </section>
            </div>
          ))}
        </div>

        <aside className="overflow-hidden rounded-lg border border-border/70 bg-card/70 lg:sticky lg:top-6 lg:max-h-[calc(100vh-3rem)] lg:overflow-y-auto" aria-live="polite">
          {selectedItem && selectedLeaf ? (
            <>
              <header className="border-b border-border/60 p-4">
                <p className="text-[11px] font-semibold text-primary">{selectedTier?.name}</p>
                <div className="mt-1 flex items-start justify-between gap-3">
                  <div>
                    <h3 className="text-lg font-bold text-foreground">{selectedItem.name}</h3>
                    {selectedItem.desc && <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{selectedItem.desc}</p>}
                  </div>
                  <span className="shrink-0 font-mono text-xs text-muted-foreground">
                    {countItemStocks(selectedItem, stocks.data.leaves) || "未导入"}
                  </span>
                </div>
                <div className="mt-3 flex flex-wrap gap-2" role="group" aria-label={`${selectedItem.name} 细分环节`}>
                  {selectedLeaves.map((leaf) => {
                    const active = leaf.id === selectedLeaf.id;
                    return (
                      <button
                        key={leaf.id}
                        type="button"
                        aria-pressed={active}
                        onClick={() => setSelectedLeafId(leaf.id)}
                        className={`min-h-10 rounded-md border px-3 py-2 text-sm transition-colors ${active ? "border-info/70 bg-info/15 text-foreground" : "border-border/60 bg-background/30 text-muted-foreground hover:text-foreground"}`}
                      >
                        {leaf.name}
                        {leaf.source === "manual" && <span className="ml-1 text-[10px] opacity-70">手工</span>}
                      </button>
                    );
                  })}
                </div>
              </header>
              <div id="selected-leaf-panel" role="region" aria-label={`${selectedLeaf.name} 成分股`} className="p-4">
                {stocks.loading ? (
                  <div className="flex items-center gap-1.5 py-6 text-xs text-muted-foreground">
                    <Loader2 className="h-3.5 w-3.5 animate-spin" /> 加载中…
                  </div>
                ) : (
                  <LeafList key={selectedLeaf.id} leafId={selectedLeaf.id} stocks={stocks} />
                )}
              </div>
            </>
          ) : (
            <div className="p-6 text-sm text-muted-foreground">请选择一个产业环节。</div>
          )}
        </aside>
      </div>

      <Disclaimer />
    </div>
  );
}
