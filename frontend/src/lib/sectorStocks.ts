export interface SectorStock {
  code: string;
  name: string;
  ts?: number;
}

export interface LeafStocks {
  base: SectorStock[];
  mine: SectorStock[];
}

export interface SectorImportMeta {
  sdk?: string;
  opend_host?: string;
  fetched_at?: string;
  mapping_version?: string;
  import_note?: string;
  totals?: Record<string, number>;
  [k: string]: unknown;
}

export interface SectorStocksData {
  meta: SectorImportMeta;
  leaves: Record<string, LeafStocks>;
}

/** 骨架类型（与 sectors.json tiers 对齐） */
export interface SectorItem {
  id: string;
  name: string;
  desc?: string;
  plate_id?: string;
  source?: "futu" | "manual";
  children?: SectorItem[];
}

export interface SectorTier {
  id: string;
  name: string;
  items: SectorItem[];
}

export function leavesOf(item: SectorItem): SectorItem[] {
  return item.children && item.children.length > 0 ? item.children : [item];
}

export function firstLeafId(item: SectorItem): string {
  return leavesOf(item)[0].id;
}

export function countItemStocks(
  item: SectorItem,
  leaves: Record<string, LeafStocks>,
): number {
  return leavesOf(item).reduce((total, leaf) => {
    const merged = mergeLeaf(leaves[leaf.id]);
    return total + merged.source.length + merged.mine.length;
  }, 0);
}

export function mergeLeaf(
  lf: LeafStocks | undefined | null,
): { source: SectorStock[]; mine: SectorStock[] } {
  return { source: lf?.base ?? [], mine: lf?.mine ?? [] };
}

// ============================================================================
// 乐观并发状态机（纯函数，无 React 依赖，可直接被 node --test 覆盖）。
// 解决 hook 竞态（审评 Critical #1）：
//   - 成功响应按 token 单调推进，乱序/过期响应被忽略（不覆盖更新的已提交态）。
//   - 失败时「丢弃该 token 的 pending diff」即精确回滚——无需「无条件逆操作」，
//     因此幂等操作（对已满足态再 hide/add）失败不会误删/误恢复。
//   - 每个 pending 带 sector key；切换 key 后旧 key 的 ack 被忽略，不污染新板块。
//   - applyDiff 幂等：即便某 diff 已被服务端含入 committed 再叠加也不重复显示。
// hook 仅作薄封装（epoch 守卫 GET、token 计数、把 machine state 映射成 React state）。
// ============================================================================

export type SectorOp =
  | { kind: "delete"; leaf: string; code: string }
  | { kind: "addMine"; leaf: string; code: string; name: string }
  | { kind: "removeMine"; leaf: string; code: string };

/** 一次操作相对当前 displayed 态实际产生的增量（none=幂等无变化）。 */
export type OpDiff =
  | { type: "none" }
  | { type: "delete"; leaf: string; entry: SectorStock } // entry 含被删项原 name/ts，回滚可完整恢复
  | { type: "mine-add"; leaf: string; entry: SectorStock }
  | { type: "mine-remove"; leaf: string; entry: SectorStock };

export interface PendingOp {
  token: number; // 全局单调递增（跨 key 不重置）
  key: string; // 提交时的 sector key
  diff: OpDiff;
}

export interface OptimisticState {
  key: string;
  committed: SectorStocksData | null; // 最近一次被服务端确认的态（当前 key）
  pending: PendingOp[]; // 已乐观应用、尚未 ack 的操作（按提交序）
  lastAckToken: number; // 已推进到的最高 token；<= 它的 ack 视为过期
}

export function initState(key: string): OptimisticState {
  return { key, committed: null, pending: [], lastAckToken: 0 };
}

function leafOf(d: SectorStocksData, leaf: string): LeafStocks {
  return d.leaves[leaf] ?? { base: [], mine: [] };
}

/** 计算 op 对 displayed 态产生的 diff（纯）。幂等情形返回 none。 */
export function captureDiff(displayed: SectorStocksData, op: SectorOp): OpDiff {
  const l = leafOf(displayed, op.leaf);
  switch (op.kind) {
    case "delete": {
      const existing = l.base.find((s) => s.code === op.code);
      return existing ? { type: "delete", leaf: op.leaf, entry: existing } : { type: "none" };
    }
    case "addMine":
      return l.mine.some((s) => s.code === op.code)
        ? { type: "none" }
        : { type: "mine-add", leaf: op.leaf, entry: { code: op.code, name: op.name } };
    case "removeMine": {
      const existing = l.mine.find((s) => s.code === op.code);
      return existing ? { type: "mine-remove", leaf: op.leaf, entry: existing } : { type: "none" };
    }
  }
}

/** 幂等地把 diff 叠到 state（纯）。 */
export function applyDiff(state: SectorStocksData, diff: OpDiff): SectorStocksData {
  if (diff.type === "none") return state;
  const leaves = { ...state.leaves };
  const cur = leafOf(state, diff.leaf);
  const next: LeafStocks = { base: [...cur.base], mine: [...cur.mine] };
  switch (diff.type) {
    case "delete":
      next.base = next.base.filter((s) => s.code !== diff.entry.code);
      break;
    case "mine-add":
      if (!next.mine.some((s) => s.code === diff.entry.code)) next.mine = [...next.mine, diff.entry];
      break;
    case "mine-remove":
      next.mine = next.mine.filter((s) => s.code !== diff.entry.code);
      break;
  }
  leaves[diff.leaf] = next;
  return { ...state, leaves };
}

/** 折叠全部 pending 到 committed → 当前应展示态（纯）。 */
export function displayed(state: OptimisticState): SectorStocksData {
  const base = state.committed ?? { meta: {}, leaves: {} };
  return state.pending.reduce((acc, p) => applyDiff(acc, p.diff), base);
}

/** 发起一次 mutation：返回新 state（push pending）与捕获的 diff。 */
export function beginMutation(
  state: OptimisticState,
  op: SectorOp,
  token: number,
): { state: OptimisticState; diff: OpDiff } {
  const diff = captureDiff(displayed(state), op);
  const pending: PendingOp = { token, key: state.key, diff };
  return { state: { ...state, pending: [...state.pending, pending] }, diff };
}

/**
 * 成功 ack：仅当 token 严格大于 lastAckToken 才推进（单调），过期/乱序响应被忽略。
 * 推进时 committed = server（含 token 及更早已处理项），pending 只保留 token 之后的新操作。
 */
export function ackSuccess(
  state: OptimisticState,
  token: number,
  server: SectorStocksData,
): OptimisticState {
  if (token <= state.lastAckToken) return state; // 过期响应：丢弃，不覆盖更新的已提交态
  const pending = state.pending.filter((p) => p.token > token);
  return { ...state, committed: server, pending, lastAckToken: token };
}

/** 失败 ack：丢弃该 token 的 pending diff（精确回滚，无需逆操作）。 */
export function ackFailure(state: OptimisticState, token: number): OptimisticState {
  return { ...state, pending: state.pending.filter((p) => p.token !== token) };
}

/** 切换 sector key：丢弃旧 key 的 pending 与 committed，重置 ack 计数。 */
export function setKey(state: OptimisticState, key: string): OptimisticState {
  if (key === state.key) return state;
  return { key, committed: null, pending: [], lastAckToken: 0 };
}

/** GET 回来的权威态写入 committed（hook 层已做 epoch 守卫）。 */
export function setCommitted(state: OptimisticState, data: SectorStocksData): OptimisticState {
  return { ...state, committed: data };
}
