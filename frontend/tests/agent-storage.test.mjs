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
  ) || readFileSync(join(process.cwd(), "package.json"), "utf8").includes(needle);
}

test("agent workspace no longer reads or writes the obsolete model storage key", async () => {
  // Agent 工作台已迁往本地静态设置文件：旧 vr-agent-model 键不再读写，
  // 而 legacy 聊天（chat/debate/reflect）的 vr-llm 契约保持不变。
  assert.equal(await sourceTreeContains("vr-agent-model"), false);
  assert.equal(await sourceTreeContains("vr-llm"), true);
});
