import { readFile } from "node:fs/promises";
import { test } from "node:test";
import assert from "node:assert/strict";

test("删除 dialog 仅在 open 状态使用 flex 布局", async () => {
  const source = await readFile(
    new URL("../src/pages/SectorDetail.tsx", import.meta.url),
    "utf8",
  );
  const dialog = source.match(
    /<dialog\s+ref=\{delDialogRef\}[\s\S]*?className="([^"]*)"/,
  );

  assert.ok(dialog, "应能定位删除确认 dialog");
  const className = dialog[1]?.split(/\s+/);
  assert.ok(className, "删除确认 dialog 应声明 className");
  assert.ok(className.includes("open:flex"), "dialog 打开时应使用 flex 布局");
  assert.ok(!className.includes("flex"), "dialog 关闭时不得被 flex 强制显示");
});
