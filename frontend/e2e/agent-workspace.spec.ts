import { expect, test, type Page } from "@playwright/test";

test.describe.configure({ mode: "serial" });

const E2E_LANGGRAPH_URL = "http://127.0.0.1:2873";

async function send(page: Page, text: string) {
  const composer = page.getByLabel("Agent 消息", { exact: true });
  await expect(composer).toBeEnabled({ timeout: 30_000 });
  await composer.fill(text);
  await page.getByTitle("发送", { exact: true }).click();
}

test("native Agent workspace persists threads and handles MCP approval", async ({ page }) => {
  await page.goto("/agent");
  await send(page, "给出客观测试回复");
  await expect(page.getByText("客观测试回复完成")).toBeVisible();
  await page.reload();
  const originalThread = page.getByRole("button", { name: "给出客观测试回复" });
  await expect(originalThread).toBeVisible();
  await originalThread.click();
  await expect(page.getByTestId("agent-chat-column")
    .getByText("给出客观测试回复", { exact: true })).toBeVisible();
  await expect(page.getByTestId("agent-chat-column")
    .getByText("客观测试回复完成。", { exact: true })).toBeVisible();

  await page.getByLabel("新建会话").click();
  await send(page, "调用 MCP 并批准");
  await expect(page.getByRole("region", { name: "MCP 工具审批" })).toBeVisible();
  await page.getByRole("radio", { name: /批准/ }).check();
  await page.getByRole("button", { name: "提交全部决定" }).click();
  await expect(page.getByText(/MCP 客观结果/)).toBeVisible();

  await send(page, "调用 MCP 并拒绝");
  await page.getByRole("radio", { name: /拒绝/ }).check();
  await page.getByRole("button", { name: "提交全部决定" }).click();
  await expect(page.getByText(/已拒绝/)).toBeVisible();

  await send(page, "启动慢速 MCP 后停止");
  await page.getByRole("radio", { name: /批准/ }).check();
  await page.getByRole("button", { name: "提交全部决定" }).click();
  const stop = page.getByTitle("停止", { exact: true });
  await expect(stop).toBeVisible();
  await stop.click();
  await expect(page.getByTitle("发送", { exact: true })).toBeVisible();

  const activeItem = page.locator('[data-testid^="agent-thread-"][data-active="true"]');
  await activeItem.getByRole("button", { name: "重命名会话" }).click();
  await activeItem.getByRole("textbox", { name: "会话标题" }).fill("E2E 审批会话");
  await activeItem.getByRole("button", { name: "确认重命名" }).click();
  await expect(page.getByRole("button", { name: "E2E 审批会话" })).toBeVisible();

  await originalThread.click();
  await expect(page.getByTestId("agent-chat-column")
    .getByText("客观测试回复完成。", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "E2E 审批会话" }).click();
  await expect(page.getByTestId("agent-chat-column").getByText(/MCP 客观结果/)).toBeVisible();
  const originalItem = page.locator('[data-testid^="agent-thread-"]').filter({ has: originalThread });
  await originalItem.getByRole("button", { name: "删除会话" }).click();
  await expect(page.getByRole("button", { name: "给出客观测试回复" })).toHaveCount(0);
});

async function expectNoWorkspaceOverlap(page: Page) {
  expect(await page.evaluate(() =>
    document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1,
  )).toBe(true);
  const composer = await page.getByLabel("Agent 消息").boundingBox();
  const approval = await page.getByRole("region", { name: "MCP 工具审批" }).boundingBox();
  expect(composer).not.toBeNull();
  expect(approval).not.toBeNull();
  const intersects = !(
    approval!.x + approval!.width <= composer!.x ||
    composer!.x + composer!.width <= approval!.x ||
    approval!.y + approval!.height <= composer!.y ||
    composer!.y + composer!.height <= approval!.y
  );
  expect(intersects).toBe(false);
}

test("Agent proxy, CORS boundary, and responsive layout", async ({ page, request }, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/agent");
  await page.getByLabel("新建会话").click();
  await expect(page.getByRole("region", { name: "MCP 工具审批" }))
    .toContainText("暂无待审批工具调用");
  const proxyResult = await page.evaluate(async () => {
    const response = await fetch("/agent-api/threads/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ limit: 1 }),
    });
    return { ok: response.ok, body: await response.json() };
  });
  expect(proxyResult.ok).toBe(true);
  expect(Array.isArray(proxyResult.body)).toBe(true);
  await expectNoWorkspaceOverlap(page);
  await page.screenshot({ path: testInfo.outputPath("agent-desktop.png"), fullPage: true });

  const hostile = { Origin: "https://evil.example.com" };
  const preflight = await request.fetch(`${E2E_LANGGRAPH_URL}/threads`, {
    method: "OPTIONS",
    headers: {
      ...hostile,
      "Access-Control-Request-Method": "POST",
      "Access-Control-Request-Headers": "content-type",
    },
  });
  expect(preflight.status()).toBe(400);
  const actual = await request.post(`${E2E_LANGGRAPH_URL}/threads`, {
    headers: hostile,
    data: {},
  });
  expect(actual.status()).toBe(200);
  expect(actual.headers()["access-control-allow-origin"]).toBeUndefined();

  await page.setViewportSize({ width: 390, height: 844 });
  await page.reload();
  await expect(page.getByRole("button", { name: "打开会话列表" })).toBeVisible();
  await page.getByRole("button", { name: "打开会话列表" }).click();
  await expect(page.getByRole("dialog", { name: "会话" })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("agent-mobile.png"), fullPage: true });
  await page.getByRole("button", { name: "关闭" }).click();
  await expectNoWorkspaceOverlap(page);
});
