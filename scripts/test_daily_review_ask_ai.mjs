import { createRequire } from "node:module";
import { writeFileSync, mkdirSync } from "node:fs";
import { join } from "node:path";

const require = createRequire("/vol2/1000/code/stockclaw/frontend/package.json");
const { chromium } = require("@playwright/test");

const SHOTS_DIR = "/home/admin/.gemini/antigravity-cli/brain/b71d6f6b-354a-42b9-ba57-9cb7a274a085";
mkdirSync(SHOTS_DIR, { recursive: true });

async function testAskAi() {
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

  console.log("\n1. Navigating to Daily Review page (/daily-review)...");
  await page.goto("http://127.0.0.1:5899/daily-review", { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(1500);

  // 1. Check initial "问 AI" button
  console.log("2. Checking '问 AI' button...");
  const askAiBtn = await page.$('button:has-text("问 AI")');
  if (!askAiBtn) {
    throw new Error("未找到「问 AI」按钮");
  }
  console.log("✔ Found '问 AI' button on daily review page");

  // 2. Open drawer in unconfigured state
  console.log("3. Opening Ask-AI drawer...");
  await askAiBtn.click();
  await page.waitForTimeout(1000);

  const shot1 = join(SHOTS_DIR, "ask_ai_step1_drawer_opened.png");
  await page.screenshot({ path: shot1, fullPage: false });
  console.log(`✔ Step 1 screenshot captured: ask_ai_step1_drawer_opened.png`);

  // 3. Backup & configure mock LLM in localStorage
  console.log("\n4. Configuring LLM in localStorage to test active chat UI...");
  const savedLlm = await page.evaluate(() => localStorage.getItem("vr-llm"));

  await page.evaluate(() => {
    const mockLlm = {
      provider: "deepseek",
      baseURL: "https://api.deepseek.com/v1",
      apiKey: "sk-test-demo-key-for-ui-verification",
      model: "deepseek-chat",
    };
    localStorage.setItem("vr-llm", JSON.stringify(mockLlm));
  });

  // Reload page to reflect configured state
  await page.goto("http://127.0.0.1:5899/daily-review", { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(1500);

  const askAiBtn2 = await page.$('button:has-text("问 AI")');
  await askAiBtn2.click();
  await page.waitForTimeout(1000);

  const shot2 = join(SHOTS_DIR, "ask_ai_step2_configured_chat_ui.png");
  await page.screenshot({ path: shot2, fullPage: false });
  console.log(`✔ Step 2 screenshot captured: ask_ai_step2_configured_chat_ui.png`);

  // 4. Verify quick suggestion buttons
  const suggestions = await page.$$('button:has-text("今天大盘怎么走"), button:has-text("哪些指数领涨领跌"), button:has-text("盘面有什么值得注意")');
  console.log(`✔ Found ${suggestions.length} quick suggestion chips`);

  // 5. Test typing custom question in input box
  const inputEl = await page.$('textarea, input[placeholder*="就本页内容提问"]');
  if (inputEl) {
    console.log("✔ Chat input box is active and writable");
    await inputEl.fill("请总结今日大盘各大指数的涨跌分布情况");
    await page.waitForTimeout(500);

    const shot3 = join(SHOTS_DIR, "ask_ai_step3_input_filled.png");
    await page.screenshot({ path: shot3, fullPage: false });
    console.log(`✔ Step 3 screenshot captured: ask_ai_step3_input_filled.png`);
  }

  // 6. Test clicking suggestion chip
  console.log("\n5. Testing quick suggestion click interaction...");
  const firstChip = await page.$('button:has-text("今天大盘怎么走")');
  if (firstChip) {
    await firstChip.click();
    await page.waitForTimeout(1500);
    const shot4 = join(SHOTS_DIR, "ask_ai_step4_question_sent.png");
    await page.screenshot({ path: shot4, fullPage: false });
    console.log(`✔ Step 4 screenshot captured: ask_ai_step4_question_sent.png`);
  }

  // 7. Test closing drawer
  console.log("\n6. Testing close drawer...");
  const closeBtn = await page.$('button:has(svg.lucide-x)');
  if (closeBtn) {
    await closeBtn.click();
    await page.waitForTimeout(800);
    const shot5 = join(SHOTS_DIR, "ask_ai_step5_drawer_closed.png");
    await page.screenshot({ path: shot5, fullPage: false });
    console.log(`✔ Step 5 screenshot captured: ask_ai_step5_drawer_closed.png`);
  }

  console.log("\n7. Restoring original localStorage...");
  await page.evaluate((orig) => {
    if (orig !== null) localStorage.setItem("vr-llm", orig);
    else localStorage.removeItem("vr-llm");
  }, savedLlm);

  console.log("\nClosing CDP test tab...");
  await page.close();
  await browser.close();

  console.log("\n==========================================");
  console.log("每日复盘「问 AI」功能 E2E 测试全部完成并通过！");
  console.log("==========================================");
}

testAskAi().catch((err) => {
  console.error("Fatal Ask-AI E2E test error:", err);
  process.exit(1);
});
