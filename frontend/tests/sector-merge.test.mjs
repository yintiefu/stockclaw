import { test } from "node:test";
import assert from "node:assert/strict";
// 直接 import 生产实现（经 tsx 编译加载），不再是副本；mergeLeaf + 状态机统一一处导入
import {
  mergeLeaf,
  initState,
  beginMutation,
  ackSuccess,
  ackFailure,
  setKey,
  setCommitted,
  displayed,
} from "../src/lib/sectorStocks.ts";
import * as sectorStocks from "../src/lib/sectorStocks.ts";

test("mergeLeaf: source = base；mine 独立", () => {
  const out = mergeLeaf({
    base: [
      { code: "SH.688017", name: "绿的谐波" },
      { code: "SZ.002008", name: "大族激光" },
    ],
    mine: [{ code: "SZ.300124", name: "汇川技术" }],
  });
  assert.deepEqual(out.source, [
    { code: "SH.688017", name: "绿的谐波" },
    { code: "SZ.002008", name: "大族激光" },
  ]);
  assert.deepEqual(out.mine, [{ code: "SZ.300124", name: "汇川技术" }]);
});

test("mergeLeaf: undefined → 两空", () => {
  const out = mergeLeaf(undefined);
  assert.deepEqual(out.source, []);
  assert.deepEqual(out.mine, []);
});

test("mergeLeaf: null 也安全", () => {
  const out = mergeLeaf(null);
  assert.deepEqual(out.source, []);
  assert.deepEqual(out.mine, []);
});

test("产业链父节点数量 = 来源 base + 我的关联", () => {
  assert.equal(typeof sectorStocks.countItemStocks, "function");
  const item = {
    id: "reducer",
    name: "减速器",
    children: [
      { id: "harmonic", name: "谐波减速器" },
      { id: "rv", name: "RV 减速器" },
    ],
  };
  const leaves = {
    harmonic: {
      base: [
        { code: "SH.1", name: "甲" },
        { code: "SH.2", name: "乙" },
      ],
      mine: [{ code: "SZ.3", name: "丙" }],
    },
    rv: {
      base: [{ code: "SZ.4", name: "丁" }],
      mine: [],
    },
  };

  assert.equal(sectorStocks.countItemStocks(item, leaves), 4); // harmonic(2+1) + rv(1)
});

test("产业链父节点默认选择第一个细分叶子；无 children 时选择自身", () => {
  assert.equal(typeof sectorStocks.firstLeafId, "function");
  assert.equal(
    sectorStocks.firstLeafId({
      id: "reducer",
      name: "减速器",
      children: [
        { id: "harmonic", name: "谐波减速器" },
        { id: "rv", name: "RV 减速器" },
      ],
    }),
    "harmonic",
  );
  assert.equal(sectorStocks.firstLeafId({ id: "bearing", name: "轴承" }), "bearing");
});

// ============================================================================
// 乐观并发状态机（乱序成功 / 部分失败 / 幂等 / 切 key）
// 删除 = 从 base 真移除；失败回滚恢复原 entry。
// ============================================================================

const D = (leaves = {}) => ({ meta: {}, leaves });
const codes = (arr) => arr.map((x) => x.code);

test("ackSuccess 单调：过期响应不覆盖更新的已提交态", () => {
  let s = initState("k");
  s = setCommitted(s, D({ l: { base: [{ code: "SH.1", name: "一" }, { code: "SH.2", name: "二" }, { code: "SH.3", name: "三" }], mine: [] } }));
  // 两次 delete：A(token1) 删 SH.1、B(token2) 删 SH.2
  s = beginMutation(s, { kind: "delete", leaf: "l", code: "SH.1" }, 1).state;
  s = beginMutation(s, { kind: "delete", leaf: "l", code: "SH.2" }, 2).state;
  // B 先 ack（base 只剩 SH.3）
  s = ackSuccess(s, 2, D({ l: { base: [{ code: "SH.3", name: "三" }], mine: [] } }));
  // A 的迟到 ack（base 剩 SH.2,SH.3）必须被忽略，不能把 SH.2 加回来
  s = ackSuccess(s, 1, D({ l: { base: [{ code: "SH.2", name: "二" }, { code: "SH.3", name: "三" }], mine: [] } }));
  assert.deepEqual(codes(displayed(s).leaves.l.base), ["SH.3"]);
});

test("ackFailure 精确回滚：幂等 delete 失败不影响已删除态", () => {
  let s = initState("k");
  s = setCommitted(s, D({ l: { base: [{ code: "SH.2", name: "二" }], mine: [] } })); // SH.1 已不在 base
  // 对不在 base 的 SH.1 再次 delete：diff 为 none
  const { state: s2 } = beginMutation(s, { kind: "delete", leaf: "l", code: "SH.1" }, 5);
  s = ackFailure(s2, 5); // 失败丢弃 pending
  assert.deepEqual(codes(displayed(s).leaves.l.base), ["SH.2"]); // 未被误改
});

test("delete 失败完整恢复原 entry（含 name/ts）", () => {
  let s = initState("k");
  s = setCommitted(s, D({ l: { base: [{ code: "SH.9", name: "真名", ts: 42 }], mine: [] } }));
  const { state: s2 } = beginMutation(s, { kind: "delete", leaf: "l", code: "SH.9" }, 7);
  assert.deepEqual(displayed(s2).leaves.l.base, []); // 乐观删除
  s = ackFailure(s2, 7); // 失败回滚
  assert.deepEqual(displayed(s).leaves.l.base, [{ code: "SH.9", name: "真名", ts: 42 }]); // 原样恢复
});

test("ackFailure 精确回滚：幂等 addMine 失败不删除已存在项", () => {
  let s = initState("k");
  s = setCommitted(s, D({ l: { base: [], mine: [{ code: "SH.9", name: "X", ts: 1 }] } }));
  const { state: s2 } = beginMutation(s, { kind: "addMine", leaf: "l", code: "SH.9", name: "X" }, 6);
  s = ackFailure(s2, 6);
  assert.deepEqual(displayed(s).leaves.l.mine, [{ code: "SH.9", name: "X", ts: 1 }]);
});

test("切 key 后旧 key 的 ack 被忽略，不污染新板块", () => {
  let s = initState("k1");
  s = setCommitted(s, D({ l: { base: [{ code: "SH.1", name: "一" }], mine: [] } }));
  s = beginMutation(s, { kind: "delete", leaf: "l", code: "SH.1" }, 1).state;
  s = setKey(s, "k2"); // 切板块：pending 清空
  s = setCommitted(s, D({ other: { base: [], mine: [] } }));
  // 跨 key ack 守卫由 hook 在调用 ackSuccess 前判断 op.key === currentKey 实现；
  // 机器本身用 setKey 已清 pending。此处锁定 pending 为空这一不变量。
  assert.deepEqual(s.pending, []);
});

test("applyDiff 幂等：对已删除项再删不重复/不报错", () => {
  let s = initState("k");
  s = setCommitted(s, D({ l: { base: [{ code: "SH.1", name: "一" }], mine: [] } }));
  s = beginMutation(s, { kind: "delete", leaf: "l", code: "SH.1" }, 1).state;
  s = beginMutation(s, { kind: "delete", leaf: "l", code: "SH.1" }, 2).state; // 第二次 diff=none（base 已无 SH.1）
  assert.deepEqual(displayed(s).leaves.l.base, []); // 删除一次，base 空
});
