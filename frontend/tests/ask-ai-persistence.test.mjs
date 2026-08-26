import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const SRC = new URL("../src/components/ui/AskAiButton.tsx", import.meta.url);
const CLIENT = new URL("../src/lib/agent/embedded-client.ts", import.meta.url);
const source = await readFile(SRC, "utf8");
const clientSource = await readFile(CLIENT, "utf8");

// 统一 LangGraph 迁移后，问 AI 的持久化契约变了：
// - 对话历史存在 Server 的 embedded thread checkpoint 里，不再写 localStorage；
// - 打开抽屉只按 (route, scope_key) 精确 metadata 搜索恢复，绝不创建空 thread；
// - 首次发送才创建 thread；「清空本页对话」删除该 scope 的 thread；
// - 旧键 vr-askai-chat:* / vr-llm 属于迁移前数据：不读取、不迁移、不删除。

test("history is checkpoint-backed: the component no longer persists chat to localStorage", () => {
  assert.doesNotMatch(source, /vr-askai-chat/);
  assert.doesNotMatch(source, /storageGet|storageSet|storageRemove/);
  assert.doesNotMatch(clientSource, /vr-askai-chat|vr-askai-thread|storageGet|storageSet|storageRemove/);
  // 恢复与发送必须走 LangGraph embedded client
  assert.match(source, /findEmbeddedThread/);
  assert.match(source, /loadEmbeddedMessages/);
  assert.match(source, /sendEmbeddedMessage/);
  assert.match(source, /deleteEmbeddedThread/);
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
  // 搜索恢复路径绝不创建 thread（创建只发生在 send 发现缺失时）
  const findImpl = clientSource.match(/async function findThread[\s\S]*?\n  \}/);
  assert.ok(findImpl, "未找到 findThread 实现");
  assert.doesNotMatch(findImpl[0], /threads\.create/);
});

test("first send validates the page snapshot and stamps it into the run input", () => {
  assert.match(clientSource, /page_context: \{ route, scope_key: scope, source_as_of: sourceAsOf, content \}/);
  assert.match(clientSource, /页面快照内容为空/);
  assert.match(clientSource, /页面快照缺少数据时间/);
});

test("clear deletes exactly the matching embedded thread", () => {
  const clearImpl = clientSource.match(/async function deleteThread[\s\S]*?\n  \}/);
  assert.ok(clearImpl, "未找到 deleteThread 实现");
  assert.match(clearImpl[0], /threads\.delete/);
  assert.match(clearImpl[0], /matchesScope/);
});

test("local abort never cancels the server run", () => {
  // 断开=continue：抽屉关闭/换 scope 只中止本地消费，Server run 继续落 checkpoint。
  assert.match(clientSource, /onDisconnect: "continue"/);
  assert.doesNotMatch(clientSource, /runs\.cancel/);
  assert.match(source, /只断开本地消费，不取消 Server run/);
});

test("conversations stay scoped per route and symbol", () => {
  assert.match(source, /scopeKey\?: string/);
  assert.match(clientSource, /function normalizeScope/);
  // scope_key 非空：无细分 scope 的页面归一化为路由本身
  assert.match(clientSource, /return scope \|\| route\.trim\(\)/);
});

test("transient streamed answers are replaced by the authoritative checkpoint", () => {
  assert.match(clientSource, /流结束后的 checkpoint 才是权威回答/);
  assert.match(source, /权威 checkpoint 消息整体替换临时流式文本/);
  assert.match(source, /partial\?: boolean|partial: true/);
});
