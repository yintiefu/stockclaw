/** Task 11：技能管理端到端生命周期——导入（默认停用）→ 详情/启用 → 新旧会话可见性
 * → /reload-skills 显式刷新 → HITL 锁定 Composer → 停用/永久删除。
 * 全程隔离数据根：上传 fixture、技能目录、检查点都不离开 VR_E2E_DATA_ROOT。 */
import { mkdirSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";

import { expect, test, type Page } from "@playwright/test";

import { DATA_ROOT } from "../playwright.config";

test.describe.configure({ mode: "serial" });

const UPLOAD_ROOT = path.join(DATA_ROOT, "e2e-upload");
const SKILL_DIR = path.join(UPLOAD_ROOT, "e2e-skill");
const SKILL_MD = [
  "---",
  "name: e2e-skill",
  "description: 端到端生命周期验证技能。",
  "---",
  "",
  "# E2E 技能指令",
  "",
  "用于断言导入、启停与会话刷新的确定性内容。",
  "",
].join("\n");

test.beforeAll(() => {
  mkdirSync(path.join(SKILL_DIR, "assets"), { recursive: true });
  writeFileSync(path.join(SKILL_DIR, "SKILL.md"), SKILL_MD, "utf-8");
  writeFileSync(path.join(SKILL_DIR, "assets", "note.txt"), "ASSET_MARKER", "utf-8");
});

test.afterAll(() => {
  rmSync(UPLOAD_ROOT, { recursive: true, force: true });
});

async function send(page: Page, text: string) {
  const composer = page.getByLabel("Agent 消息", { exact: true });
  await expect(composer).toBeEnabled({ timeout: 60_000 });
  await composer.fill(text);
  await page.getByTitle("发送", { exact: true }).click();
}

test("skill management lifecycle", async ({ page }) => {
  test.setTimeout(240_000);
  // 1. 导入文件夹技能，默认停用
  await page.goto("/skills");
  await expect(page.getByRole("heading", { name: "技能管理" })).toBeVisible();
  await page.getByRole("button", { name: /导入技能/ }).click();
  // 指向技能目录本身：Chromium 以所选目录为相对根，webkitRelativePath = e2e-skill/…
  await page.setInputFiles('input[aria-label="选择技能文件夹"]', SKILL_DIR);
  await page.getByRole("button", { name: "导入", exact: true }).click();
  await expect(page.getByText(/技能已导入/)).toBeVisible({ timeout: 30_000 });
  const importedCard = page.locator("div", { hasText: "e2e-skill" }).filter({ has: page.getByText("已停用") }).first();
  await expect(importedCard).toBeVisible();

  // 2. 技能停用时创建线程 A：不可见
  await page.goto("/agent");
  await send(page, "列出当前技能");
  await expect(page.getByText(/可见技能：/)).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText(/可见技能：/)).not.toContainText("e2e-skill");

  // 3. 详情页：Markdown、虚拟路径、启用
  await page.goto("/skills/user/e2e-skill");
  await expect(page.getByRole("heading", { name: "E2E 技能指令" })).toBeVisible();
  await expect(page.getByText("/user/e2e-skill/SKILL.md")).toBeVisible();
  await page.getByRole("switch", { name: /已停用/ }).click();
  await expect(page.getByText(/新会话将自动使用最新配置；已有会话请执行 \/reload-skills/)).toBeVisible();
  await expect(page.getByRole("switch", { name: /已启用/ })).toBeVisible();

  // 4. 启用后新线程 B 可见；线程 A 保持旧缓存
  await page.goto("/agent");
  await page.getByLabel("新建会话").click();
  await send(page, "列出当前技能");
  await expect(page.getByText(/e2e-skill/).first()).toBeVisible({ timeout: 60_000 });

  // 5. 回线程 A：精确 /reload-skills 刷新（无审批/工具 UI），随后可见
  await page.getByTestId("agent-threads-column").getByRole("button", { name: /列出当前技能/ }).first().click({ timeout: 60_000 });
  await send(page, "/reload-skills");
  await expect(page.getByText(/技能已刷新：当前可见 \d+ 个技能/)).toBeVisible({ timeout: 60_000 });
  await expect(page.getByRole("region", { name: "MCP 工具审批" }))
    .toContainText("暂无待审批工具调用");
  await send(page, "列出当前技能");
  await expect(page.getByText(/可见技能：/)).toContainText("e2e-skill", { timeout: 60_000 });

  // 6. HITL 待审批时 Composer 锁定；拒绝后恢复
  await send(page, "触发工具审批");
  await expect(page.getByRole("radio", { name: /拒绝/ }).first()).toBeVisible({ timeout: 60_000 });
  await expect(page.getByLabel("Agent 消息", { exact: true })).toBeDisabled();
  await expect(page.getByText("请先处理待审批工具调用")).toBeVisible({ timeout: 30_000 });
  await page.getByRole("radio", { name: /拒绝/ }).check();
  await page.getByRole("button", { name: "提交全部决定" }).click();
  await expect(page.getByText("拒绝已记录，工具未执行。")).toBeVisible({ timeout: 60_000 });
  await expect(page.getByLabel("Agent 消息", { exact: true })).toBeEnabled();

  // 7. 停用并永久删除
  await page.goto("/skills/user/e2e-skill");
  await page.getByRole("switch", { name: /已启用/ }).click();
  await expect(page.getByRole("switch", { name: /已停用/ })).toBeVisible({ timeout: 30_000 });
  await page.getByRole("button", { name: "删除技能" }).click();
  await expect(page.getByText(/永久删除/)).toBeVisible();
  await page.getByRole("button", { name: /确认删除/ }).click();
  await expect(page.getByRole("heading", { name: "技能管理" })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText("e2e-skill")).toHaveCount(0);
});

test("skill pages stay within bounds on desktop and mobile", async ({ page }) => {
  for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
    await page.setViewportSize(viewport);
    const isDesktop = viewport.width >= 1024;

    await page.goto("/skills");
    await expect(page.getByRole("heading", { name: "技能管理" })).toBeVisible();
    await expect(page.getByText("内置技能")).toBeVisible();
    expect(await page.evaluate(() =>
      document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1,
    )).toBe(true);
    // 卡片文字都在自身包围盒内（不溢出裁切）
    for (const card of await page.locator("section[aria-label='内置技能'] [role='button']").all()) {
      const box = await card.boundingBox();
      expect(box).not.toBeNull();
      const overflowing = await card.evaluate((node) => {
        const rect = node.getBoundingClientRect();
        return Array.from(node.querySelectorAll("*")).some((child) => {
          const inner = child.getBoundingClientRect();
          return inner.width > rect.width + 1 || inner.left < rect.left - 1;
        });
      });
      expect(overflowing).toBe(false);
    }
    // 桌面两列 / 移动一列
    const cards = page.locator("section[aria-label='内置技能'] [role='button']");
    const count = await cards.count();
    if (count >= 2) {
      const [first, second] = await Promise.all([(await cards.nth(0).boundingBox()), (await cards.nth(1).boundingBox())]);
      const sameRow = Math.abs(first!.y - second!.y) < 4;
      expect(sameRow).toBe(isDesktop);
    }
    await page.screenshot({ path: `test-results/skills-${viewport.width}.png`, fullPage: true });

    await page.goto("/skills/builtin/debate");
    await expect(page.getByText("/builtin/debate/SKILL.md")).toBeVisible();
    expect(await page.evaluate(() =>
      document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1,
    )).toBe(true);
    await page.screenshot({ path: `test-results/skill-detail-${viewport.width}.png`, fullPage: true });
  }
});
