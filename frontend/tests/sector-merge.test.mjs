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

test("mergeLeaf: 来源 = base − hidden；mine 独立", () => {
  const out = mergeLeaf({
    base: [
      { code: "SH.688017", name: "绿的谐波" },
      { code: "SZ.002008", name: "大族激光" },
    ],
    hidden: ["SZ.002008"],
    mine: [{ code: "SZ.300124", name: "汇川技术" }],
  });
  assert.deepEqual(out.source, [{ code: "SH.688017", name: "绿的谐波" }]);
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

// ============================================================================
// 乐观并发状态机（乱序成功 / 部分失败 / 幂等 / 切 key）
// ============================================================================

const D = (leaves = {}) => ({ meta: {}, leaves });

test("ackSuccess 单调：过期响应不覆盖更新的已提交态", () => {
  let s = initState("k");
  s = setCommitted(s, D({ l: { base: [], hidden: [], mine: [] } }));
  // 两次 hide：A(token1)、B(token2)
  s = beginMutation(s, { kind: "hide", leaf: "l", code: "SH.1" }, 1).state;
  s = beginMutation(s, { kind: "hide", leaf: "l", code: "SH.2" }, 2).state;
  // B 先 ack（含 A、B）
  s = ackSuccess(s, 2, D({ l: { base: [], hidden: ["SH.1", "SH.2"], mine: [] } }));
  // A 的迟到的 ack（只含 A）必须被忽略，不能把 SH.2 抹掉
  s = ackSuccess(s, 1, D({ l: { base: [], hidden: ["SH.1"], mine: [] } }));
  assert.deepEqual(displayed(s).leaves.l.hidden, ["SH.1", "SH.2"]);
});

test("ackFailure 精确回滚：幂等 hide 失败不影响已隐藏项", () => {
  let s = initState("k");
  s = setCommitted(s, D({ l: { base: [], hidden: ["SH.1"], mine: [] } })); // SH.1 已隐藏
  // 对已隐藏的 SH.1 再次 hide：diff 为 none
  const { state: s2 } = beginMutation(s, { kind: "hide", leaf: "l", code: "SH.1" }, 5);
  s = ackFailure(s2, 5); // 失败丢弃 pending
  assert.deepEqual(displayed(s).leaves.l.hidden, ["SH.1"]); // 未被误恢复
});

test("ackFailure 精确回滚：幂等 addMine 失败不删除已存在项", () => {
  let s = initState("k");
  s = setCommitted(s, D({ l: { base: [], hidden: [], mine: [{ code: "SH.9", name: "X", ts: 1 }] } }));
  const { state: s2 } = beginMutation(s, { kind: "addMine", leaf: "l", code: "SH.9", name: "X" }, 6);
  s = ackFailure(s2, 6);
  assert.deepEqual(displayed(s).leaves.l.mine, [{ code: "SH.9", name: "X", ts: 1 }]);
});

test("removeMine 失败完整恢复原 entry（含 name/ts）", () => {
  let s = initState("k");
  s = setCommitted(s, D({ l: { base: [], hidden: [], mine: [{ code: "SH.9", name: "真名", ts: 42 }] } }));
  const { state: s2 } = beginMutation(s, { kind: "removeMine", leaf: "l", code: "SH.9" }, 7);
  assert.deepEqual(displayed(s2).leaves.l.mine, []); // 乐观删除
  s = ackFailure(s2, 7); // 失败回滚
  assert.deepEqual(displayed(s).leaves.l.mine, [{ code: "SH.9", name: "真名", ts: 42 }]); // 原样恢复
});

test("切 key 后旧 key 的 ack 被忽略，不污染新板块", () => {
  let s = initState("k1");
  s = setCommitted(s, D({ l: { base: [], hidden: [], mine: [] } }));
  s = beginMutation(s, { kind: "hide", leaf: "l", code: "SH.1" }, 1).state;
  s = setKey(s, "k2"); // 切板块：pending 清空
  s = setCommitted(s, D({ other: { base: [], hidden: [], mine: [] } }));
  // 跨 key ack 守卫由 hook 在调用 ackSuccess 前判断 op.key === currentKey 实现；
  // 机器本身用 setKey 已清 pending。此处锁定 pending 为空这一不变量。
  assert.deepEqual(s.pending, []);
});

test("applyDiff 幂等：同 diff 叠两次不重复", () => {
  let s = initState("k");
  s = setCommitted(s, D({ l: { base: [], hidden: [], mine: [] } }));
  s = beginMutation(s, { kind: "hide", leaf: "l", code: "SH.1" }, 1).state;
  s = beginMutation(s, { kind: "hide", leaf: "l", code: "SH.1" }, 2).state; // 第二次 diff=none
  assert.deepEqual(displayed(s).leaves.l.hidden, ["SH.1"]);
});
