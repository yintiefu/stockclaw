import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { expect, it } from "vitest";

const HOOKS = [
  "useAgUiInterrupts",
  "useAgUiSteerAway",
  "useAgUiSubmitInterruptResponses",
];

function collectProductionSources(dir: string): string[] {
  const files: string[] = [];
  for (const entry of readdirSync(dir)) {
    if (entry.endsWith(".test.") || entry === "test" || entry.includes("__")) continue;
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      files.push(...collectProductionSources(full));
    } else if (/\.(ts|tsx)$/.test(entry)) {
      files.push(full);
    }
  }
  return files;
}

it("only approval.ts imports the version-sensitive interrupt hooks", () => {
  const root = join(process.cwd(), "src");
  const sources = collectProductionSources(root).filter(
    (file) => !file.includes("agent/approval.ts") && !file.endsWith(".test.ts") && !file.endsWith(".test.tsx"),
  );
  for (const file of sources) {
    const text = readFileSync(file, "utf8");
    for (const hook of HOOKS) {
      expect(text, `${file} 不应直接引用 ${hook}`).not.toContain(hook);
    }
  }
});
