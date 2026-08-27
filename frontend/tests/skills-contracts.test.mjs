import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

function walkSources(dir) {
  const found = [];
  for (const entry of readdirSync(dir)) {
    if (entry === "node_modules" || entry === "dist" || entry.startsWith(".")) continue;
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) found.push(...walkSources(full));
    else if (/\.(ts|tsx)$/.test(entry) && !/\.test\./.test(entry)) found.push(full);
  }
  return found;
}

test("api request method union includes PATCH for skill toggle", () => {
  const source = readFileSync(join(process.cwd(), "src/lib/api.ts"), "utf8");
  const match = source.match(/method:\s*"GET"\s*\|\s*"POST"\s*\|\s*"PATCH"\s*\|\s*"DELETE"/);
  assert.ok(match, "request 的 method 联合类型必须包含 PATCH");
});

test("pages do not call raw fetch on the FastAPI /api namespace", () => {
  // /agent-api/*（LangGraph readiness 等）不属于 FastAPI 封装范围；
  // 对 /api 的所有 HTTP 必须走 lib/api 的集中 request。
  const offenders = walkSources(join(process.cwd(), "src/pages"))
    .filter((file) => /fetch\(\s*["`.]*(["`]|new URL\()["`]*\/api/.test(readFileSync(file, "utf8")));
  assert.deepEqual(offenders, []);
});
