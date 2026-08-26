import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

async function sourceTreeContains(needle) {
  const walk = (dir) => {
    const found = [];
    for (const entry of readdirSync(dir)) {
      if (entry === "node_modules" || entry === "dist" || entry.startsWith(".")) continue;
      const full = join(dir, entry);
      if (statSync(full).isDirectory()) found.push(...walk(full));
      else if (/\.(ts|tsx|mjs|json)$/.test(entry) && !/\.test\./.test(entry)) found.push(full);
    }
    return found;
  };
  return walk(join(process.cwd(), "src")).some(
    (file) => readFileSync(file, "utf8").includes(needle),
  );
}

test("frontend no longer reads any legacy AI model storage keys", async () => {
  // 统一 LangGraph 迁移后，模型只存在服务端 settings.json：
  // 旧 vr-agent-model / vr-llm 键都不再被生产代码读取（浏览器里的旧值保留但无人使用）。
  assert.equal(await sourceTreeContains("vr-agent-model"), false);
  assert.equal(await sourceTreeContains("vr-llm"), false);
});

test("legacy AI stream modules are fully removed from the source tree", async () => {
  assert.equal(await sourceTreeContains("lib/llm"), false);
  assert.equal(await sourceTreeContains("lib/agents"), false);
});
