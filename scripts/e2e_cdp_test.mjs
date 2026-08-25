import { createRequire } from "node:module";
import { writeFileSync, mkdirSync } from "node:fs";
import { join } from "node:path";

const require = createRequire("/vol2/1000/code/stockclaw/frontend/package.json");
const { chromium } = require("@playwright/test");

const SHOTS_DIR = "/home/admin/.gemini/antigravity-cli/brain/b71d6f6b-354a-42b9-ba57-9cb7a274a085";
mkdirSync(SHOTS_DIR, { recursive: true });

async function runE2E() {
  console.log("Connecting to Chrome over CDP at 127.0.0.1:16002...");
  const browser = await chromium.connectOverCDP("http://127.0.0.1:16002");
  const ctx = browser.contexts()[0];
  const page = await ctx.newPage();
  await page.setViewportSize({ width: 1440, height: 900 });

  const errors = [];
  page.on("pageerror", (err) => {
    console.error("Page error:", err.message);
    errors.push({ type: "pageerror", message: err.message });
  });
  page.on("console", (msg) => {
    if (msg.type() === "error") {
      console.warn("Console error:", msg.text());
      errors.push({ type: "console.error", message: msg.text() });
    }
  });

  const testResults = [];

  // Helper to test a page route
  async function testRoute(name, path, screenshotName, extraChecks = null) {
    console.log(`\n--- Testing ${name} (${path}) ---`);
    const start = Date.now();
    try {
      await page.goto(`http://127.0.0.1:5899${path}`, { waitUntil: "domcontentloaded", timeout: 15000 });
      await page.waitForTimeout(1000); // wait for initial render

      const shotPath = join(SHOTS_DIR, `${screenshotName}.png`);
      await page.screenshot({ path: shotPath, fullPage: false });

      if (extraChecks) {
        await extraChecks(page);
      }

      const duration = Date.now() - start;
      console.log(`✔ ${name} passed in ${duration}ms (Screenshot: ${screenshotName}.png)`);
      testResults.push({ name, path, status: "PASS", duration, screenshot: `${screenshotName}.png` });
    } catch (err) {
      console.error(`✖ ${name} failed:`, err.message);
      testResults.push({ name, path, status: "FAIL", error: err.message });
    }
  }

  // 1. Daily Review Page
  await testRoute("每日复盘", "/daily-review", "e2e_daily_review", async (p) => {
    const header = await p.textContent("h1, h2");
    console.log("  Header found:", header);
  });

  // 2. Stock Data Page
  await testRoute("个股数据", "/stock-data?code=600519", "e2e_stock_data", async (p) => {
    const input = await p.$('input[placeholder*="代码"], input[placeholder*="股票"]');
    if (input) {
      console.log("  Stock search input present");
    }
  });

  // 3. Debate Page
  await testRoute("多空辩论", "/debate", "e2e_debate", async (p) => {
    const input = await p.$('input[placeholder*="6 位"]');
    if (input) {
      console.log("  Debate stock input present");
      await input.fill("600519");
    }
  });

  // 4. Intel Page
  await testRoute("资讯雷达", "/intel", "e2e_intel", async (p) => {
    const tabs = await p.$$('button:has-text("Investment News"), button:has-text("A股公告")');
    console.log(`  Intel tabs count: ${tabs.length}`);
  });

  // 5. Sectors Page
  await testRoute("板块中心", "/sectors", "e2e_sectors", async (p) => {
    console.log("  Sectors rendered");
  });

  // 6. Portfolio Page
  await testRoute("我的持仓", "/portfolio", "e2e_portfolio", async (p) => {
    console.log("  Portfolio rendered");
  });

  // 7. My Reports Page
  await testRoute("我的研报", "/my-reports", "e2e_my_reports", async (p) => {
    console.log("  My Reports rendered");
  });

  // 8. Research Notes Page
  await testRoute("研究记录", "/notes", "e2e_notes", async (p) => {
    console.log("  Notes rendered");
  });

  // 9. Settings Page
  await testRoute("系统设置", "/settings", "e2e_settings", async (p) => {
    console.log("  Settings rendered");
  });

  // 10. Agent Workspace Page
  await testRoute("投研智能体 (Agent)", "/agent", "e2e_agent", async (p) => {
    await p.waitForTimeout(1500);
    const shotPath = join(SHOTS_DIR, "e2e_agent_workspace.png");
    await p.screenshot({ path: shotPath, fullPage: false });
    console.log("  Agent workspace screenshot captured");
  });

  // 11. Ask-AI Drawer Interaction Test (on Daily Review page)
  await testRoute("Ask-AI 抽屉交互", "/daily-review", "e2e_ask_ai_drawer", async (p) => {
    const askAiBtn = await p.$('button:has-text("问 AI"), button[aria-label*="AI"]');
    if (askAiBtn) {
      console.log("  Clicking Ask-AI button...");
      await askAiBtn.click();
      await p.waitForTimeout(1000);
      const drawerShot = join(SHOTS_DIR, "e2e_ask_ai_opened.png");
      await p.screenshot({ path: drawerShot, fullPage: false });
      console.log("  Ask-AI drawer opened screenshot captured");
    }
  });

  console.log("\nClosing CDP test tab...");
  await page.close();
  await browser.close();

  console.log("\n==========================================");
  console.log("E2E CDP Test Summary:");
  console.log("==========================================");
  console.log(`Total Errors logged: ${errors.length}`);
  const hasFailures = testResults.some((r) => r.status === "FAIL");
  if (hasFailures) {
    console.error("Some E2E routes failed!");
    process.exit(1);
  }
  return testResults;
}

runE2E().catch((err) => {
  console.error("Fatal E2E error:", err);
  process.exit(1);
});
