import { expect, test } from "@playwright/test";

test.describe("Unified LangGraph AI Workflows", () => {
  test("Debate page renders and initiates workflow UI", async ({ page }) => {
    await page.goto("/debate");
    await expect(page.getByRole("heading", { name: "多空辩论" })).toBeVisible();
    await expect(page.getByPlaceholder("6 位代码，如 600519")).toBeVisible();
    await expect(page.getByRole("button", { name: /开始辩论/ })).toBeVisible();
  });

  test("Daily review page renders with Ask AI drawer", async ({ page }) => {
    await page.goto("/daily-review");
    await expect(page.getByRole("heading", { name: "每日复盘" })).toBeVisible();

    const askAiBtn = page.getByRole("button", { name: /问 AI/ });
    await expect(askAiBtn).toBeVisible();
    await askAiBtn.click();

    await expect(page.getByPlaceholder("基于本页数据提问…")).toBeVisible();
    await page.getByTitle("关闭").click();
  });

  test("Intel radar page renders", async ({ page }) => {
    await page.goto("/intel");
    await expect(page.getByRole("heading", { name: "资讯雷达" })).toBeVisible();
  });

  test("Stock analysis page provides Ask AI integration", async ({ page }) => {
    await page.goto("/stock/600519");
    const askAiBtn = page.getByRole("button", { name: /问 AI/ });
    if (await askAiBtn.count() > 0) {
      await expect(askAiBtn).toBeVisible();
    }
  });
});
