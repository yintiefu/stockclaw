import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const SRC = new URL("../src/components/ui/AskAiButton.tsx", import.meta.url);
const CLIENT = new URL("../src/lib/agent/embedded-client.ts", import.meta.url);
const source = await readFile(SRC, "utf8");
const clientSource = await readFile(CLIENT, "utf8");

// 统一 LangGraph 迁移后，问 AI 的持久化契约：
// - 对话历史存在 Server 的 embedded thread checkpoint 里，不再写 localStorage；
// - 打开抽屉只按 (route, scope_key) 精确 metadata 搜索恢复，绝不创建空 thread；
// - 首次发送才创建 thread；「清空本页对话」删除该 scope 的 thread；
// - 旧键 vr-askai-chat:* / vr-llm 属于迁移前数据：不读取、不迁移、不删除。
// 传输层是 @langchain/react 的 useStream：流式增量与权威 checkpoint 在 hook 内合并，
// 本地断开（关抽屉/换 scope/卸载）只停订阅，从不取消 Server run。

test("history is checkpoint-backed: the component no longer persists chat to localStorage", () => {
  assert.doesNotMatch(source, /vr-askai-chat/);
  assert.doesNotMatch(source, /storageGet|storageSet|storageRemove/);
  assert.doesNotMatch(clientSource, /vr-askai-chat|vr-askai-thread|storageGet|storageSet|storageRemove/);
  // 恢复/创建/清空走 LangGraph embedded client，传输走标准 useStream hook
  assert.match(source, /findEmbeddedThread/);
  assert.match(source, /createEmbeddedThread/);
  assert.match(source, /deleteEmbeddedThread/);
  assert.match(source, /useStream\(\{ assistantId: "embedded_agent"/);
});

test("legacy browser chat and model keys are neither read nor deleted", async () => {
  const pages = [
    "../src/components/ui/AskAiButton.tsx",
    "../src/lib/agent/embedded-client.ts",
    "../src/pages/DailyReview.tsx",
    "../src/pages/StockData.tsx",
  ];
  for (const rel of pages) {
    const text = await readFile(new URL(rel, import.meta.url), "utf8");
    assert.doesNotMatch(text, /vr-askai-chat/, `${rel} 不得读写旧对话键`);
    assert.doesNotMatch(text, /"vr-llm"|'vr-llm'/, `${rel} 不得读取旧模型配置键`);
  }
});

test("the embedded client searches with exact scope metadata and never creates on lookup", () => {
  assert.match(clientSource, /channel: "embedded"/);
  assert.match(clientSource, /scope_key: scope/);
  assert.match(clientSource, /sortBy: "updated_at"/);
  // 搜索恢复路径绝不创建 thread（创建只发生在首次发送发现缺失时）
  const findImpl = clientSource.match(/async function findThread[\s\S]*?\n  \}/);
  assert.ok(findImpl, "未找到 findThread 实现");
  assert.doesNotMatch(findImpl[0], /threads\.create/);
});

test("first send validates the page snapshot and stamps it into the run input", () => {
  assert.match(clientSource, /page_context: \{\s*route,\s*scope_key: normalizeScope\(route, params\.scopeKey\),\s*source_as_of: sourceAsOf,\s*content,\s*\}/);
  assert.match(clientSource, /页面快照内容为空/);
  assert.match(clientSource, /页面快照缺少数据时间/);
});

test("clear deletes exactly the matching embedded thread", () => {
  const clearImpl = clientSource.match(/async function deleteThread[\s\S]*?\n  \}/);
  assert.ok(clearImpl, "未找到 deleteThread 实现");
  assert.match(clearImpl[0], /threads\.delete/);
  assert.match(clearImpl[0], /matchesScope/);
});

test("local disconnect never cancels the server run", () => {
  // 关抽屉/换 scope/卸载只断开本地订阅，Server run 继续跑完落 checkpoint；
  // useStream 的 stop() 默认会服务端取消，组件必须根本不调用它。
  assert.doesNotMatch(clientSource, /runs\.cancel/);
  assert.doesNotMatch(source, /stream\.stop\(/);
  assert.match(source, /只断开本地订阅[\s\S]{0,160}从不取消[\s\S]{0,80}Server run/);
});

test("conversations stay scoped per route and symbol", () => {
  assert.match(source, /scopeKey\?: string/);
  assert.match(clientSource, /function normalizeScope/);
  // scope_key 非空：无细分 scope 的页面归一化为路由本身
  assert.match(clientSource, /return scope \|\| route\.trim\(\)/);
});

test("streaming and the authoritative checkpoint merge inside the hook", () => {
  // 不再手写「流结束后拉一次 getState」的双读；消息映射只认 text 块，
  // thinking/reasoning 是思考过程，绝不进回答正文。
  assert.doesNotMatch(clientSource, /threads\.getState/);
  assert.match(clientSource, /fromBaseMessages/);
  assert.match(clientSource, /typeof block\.text === "string" \? block\.text : ""/);
});
