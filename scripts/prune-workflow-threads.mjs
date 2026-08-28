// 定向删除 channel=workflow 的线程（工作流 v2 迁移前置，一次性人工步骤）。
// 用法：node scripts/prune-workflow-threads.mjs [agentUrl]
// 必须在【旧代码】Agent 仍在运行时执行——threads.delete 走公开 API、由 runtime
// 自己落盘，不做任何 pickle 手术；workspace/embedded 线程不受影响。
//
// 依赖解析：脚本在 scripts/ 下，node 按脚本自身路径向上找 node_modules，直接
// import "@langchain/langgraph-sdk" 会 ERR_MODULE_NOT_FOUND（同 AGENTS.md 记录的
// createRequire 坑）——必须对 frontend/package.json createRequire。
import { createRequire } from "node:module";

const { Client } = createRequire(new URL("../frontend/package.json", import.meta.url))(
  "@langchain/langgraph-sdk",
);

const apiUrl = process.argv[2] ?? "http://127.0.0.1:2024";
const client = new Client({ apiUrl });
let removed = 0;
for (;;) {
  // 每轮从头搜：删除会改变分页游标，offset 递增翻页会漏删。
  const threads = await client.threads.search({
    metadata: { channel: "workflow" }, limit: 100, offset: 0,
  });
  if (!threads.length) break;
  for (const t of threads) {
    await client.threads.delete(t.thread_id);
    removed += 1;
  }
}
console.log(`已删除 ${removed} 条旧 workflow 线程（workspace/embedded 历史不受影响）`);
