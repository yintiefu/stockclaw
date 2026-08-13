import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import {
  initState,
  setKey,
  setCommitted,
  beginMutation,
  ackSuccess,
  ackFailure,
  displayed,
  type OptimisticState,
  type SectorOp,
  type SectorStocksData,
} from "@/lib/sectorStocks";

export function useSectorStocks(key: string) {
  const [machine, setMachine] = useState<OptimisticState>(() => initState(key));
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // GET epoch：仅最新请求可写 committed
  const getEpochRef = useRef(0);
  // 全局单调 token（跨 key 不重置）
  const tokenRef = useRef(0);
  // 当前 key 的镜像，供 ack 时校验「响应所属 key 是否仍是当前 key」
  const keyRef = useRef(key);
  keyRef.current = key;

  const data = displayed(machine);

  const refresh = useCallback(async () => {
    if (!key) {
      getEpochRef.current += 1;
      setMachine(initState(""));
      setError(null);
      setLoading(false);
      return;
    }
    const epoch = ++getEpochRef.current;
    setLoading(true);
    try {
      const result = await api.sectorStocks(key);
      if (getEpochRef.current !== epoch || keyRef.current !== key) return; // 已过期/已切 key
      setMachine((m) => setCommitted(setKey(m, key), result));
      setError(null);
    } catch (e) {
      if (getEpochRef.current !== epoch || keyRef.current !== key) return;
      setMachine((m) => setKey(m, key)); // 保留空 committed
      setError(e instanceof Error ? e.message : "成分股加载失败");
    } finally {
      if (getEpochRef.current === epoch) setLoading(false);
    }
  }, [key]);

  useEffect(() => {
    setMachine((m) => setKey(m, key)); // 切 key：机器内部清 pending/committed/lastAckToken
    void refresh();
  }, [key, refresh]);

  /** 提交一次操作：乐观入机 → 调 api → 按结果 ack（带 key/token 守卫）。失败抛出由组件 toast。 */
  const run = useCallback(
    async (op: SectorOp, mut: () => Promise<SectorStocksData>) => {
      const opKey = keyRef.current;
      const token = ++tokenRef.current;
      setMachine((m) => beginMutation(m, op, token).state);
      try {
        const server = await mut();
        // 仅当本次操作所属 key 仍是当前 key 时才用其 server 推进 committed
        if (keyRef.current === opKey) {
          setMachine((m) => ackSuccess(m, token, server));
          setError(null);
        }
        // 若已切 key：丢弃响应（pending 已随 setKey 清空，不污染）
      } catch (e) {
        if (keyRef.current === opKey) {
          setMachine((m) => ackFailure(m, token)); // 精确丢弃本次 diff
        }
        throw e;
      }
    },
    [],
  );

  const hide = useCallback((leaf: string, code: string) => run({ kind: "hide", leaf, code }, () => api.hideSector(key, leaf, code)), [run, key]);
  const restore = useCallback((leaf: string, code: string) => run({ kind: "restore", leaf, code }, () => api.restoreSector(key, leaf, code)), [run, key]);
  const addMine = useCallback((leaf: string, code: string, name: string) => run({ kind: "addMine", leaf, code, name }, () => api.addSectorMine(key, leaf, code, name)), [run, key]);
  const removeMine = useCallback((leaf: string, code: string) => run({ kind: "removeMine", leaf, code }, () => api.removeSectorMine(key, leaf, code)), [run, key]);

  return { data, loading, error, refresh, hide, restore, addMine, removeMine };
}

export type UseSectorStocks = ReturnType<typeof useSectorStocks>;
