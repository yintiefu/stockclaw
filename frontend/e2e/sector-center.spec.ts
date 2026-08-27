import { expect, test } from "@playwright/test";

import { BACKEND_PORT } from "../playwright.config";

/**
 * 板块中心回归（feat/sector-industrial-chain 合并后）：
 * 只读验证，不导入、不新增成分股数据 —— 断言均基于 sectors.json 骨架
 * 与隔离数据根下的空 sector-stocks.json（来源成分股恒为「本地未导入」态）。
 *
 * 覆盖：列表页 → 产业链三段视图（人形机器人 / 光通信）→ 环节与叶子切换
 * → 未核实板块回退视图 → 后端只读 API → 侧栏导航组记忆 → 返回板块中心。
 */

/** 产业链总览里的环节卡片（右侧叶子的 aria-pressed 按钮不在该容器内，天然隔离）。 */
const itemButton = (page: import("@playwright/test").Page, name: string) =>
  page.locator("div[aria-label='产业链纵向总览'] button[aria-pressed]", { hasText: name });

test.describe("板块中心（只读回归，不新增数据）", () => {
  test("列表页：19 个板块卡片齐全，核实状态文案正确，可进入详情", async ({ page }) => {
    await page.goto("/sectors");

    await expect(page.getByRole("heading", { name: "板块中心" })).toBeVisible();
    // 全部板块卡片渲染（main 内，排除侧栏同名链接）
    const cards = page.locator("main a[href^='/sectors/']");
    await expect(cards).toHaveCount(19);
    await expect(page.getByText("共 19 个板块")).toBeVisible();

    // 已核实板块展示环节数；未核实板块展示占位文案
    const humanoidCard = page.locator("main a[href='/sectors/humanoid']");
    await expect(humanoidCard.getByText("6 个环节")).toBeVisible();
    await expect(page.locator("main a[href='/sectors/hbm']").getByText("环节梳理中")).toBeVisible();

    // 点卡片进产业链详情
    await page.locator("main a[href='/sectors/cpo']").click();
    await expect(page).toHaveURL(/\/sectors\/cpo$/);
    await expect(page.getByRole("heading", { name: "光通信" })).toBeVisible();
  });

  test("光通信：富途产业链骨架四段 20 环节渲染，默认选中首环节且为未导入态", async ({ page }) => {
    await page.goto("/sectors/cpo");

    // 上中下游四段（富途 chain_id=9610087 导入的骨架）
    for (const tierName of ["IC 设计与制造", "半导体材料", "光组件", "封装与测试"]) {
      await expect(page.getByRole("heading", { name: tierName, exact: true })).toBeVisible();
    }
    await expect(
      page.locator("div[aria-label='产业链纵向总览'] button[aria-pressed]"),
    ).toHaveCount(20);

    // 首环节自动选中，右侧面板同步；空数据下展示「未导入」而非报错
    const firstItem = itemButton(page, "ASIC与xPU设计");
    await expect(firstItem).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByText("IC 设计与制造").first()).toBeVisible();
    const panel = page.locator("#selected-leaf-panel");
    await expect(panel).toHaveAttribute("aria-label", "ASIC与xPU设计 成分股");
    await expect(panel.getByText("暂无来源成分股（本地未导入）")).toBeVisible();
    await expect(page.getByText("未导入", { exact: true })).toBeVisible();
    await expect(page.locator("[role='alert']")).toHaveCount(0);
  });

  test("人形机器人：切换环节与叶子，右侧面板跟随且不报错", async ({ page }) => {
    await page.goto("/sectors/humanoid");

    // 三段结构：大脑 5 + 身体 12 + 整机 4 = 21 环节
    for (const tierName of ["大脑 / 智能层", "身体 / 硬件层", "整机集成商"]) {
      await expect(page.getByRole("heading", { name: tierName })).toBeVisible();
    }
    await expect(
      page.locator("div[aria-label='产业链纵向总览'] button[aria-pressed]"),
    ).toHaveCount(21);

    // 默认选中首环节「大模型」；无 children 时环节自身即叶子
    await expect(itemButton(page, "大模型")).toHaveAttribute("aria-pressed", "true");
    await expect(page.locator("#selected-leaf-panel")).toHaveAttribute("aria-label", "大模型 成分股");

    // 切到「减速器」：面板跟随到其首个细分叶子「谐波减速器」
    await itemButton(page, "减速器").click();
    await expect(itemButton(page, "减速器")).toHaveAttribute("aria-pressed", "true");
    await expect(itemButton(page, "大模型")).toHaveAttribute("aria-pressed", "false");
    await expect(page.locator("#selected-leaf-panel")).toHaveAttribute("aria-label", "谐波减速器 成分股");
    await expect(page.locator("#selected-leaf-panel").getByText("暂无来源成分股（本地未导入）")).toBeVisible();

    // 同环节内切换细分叶子（右侧叶子芯片）
    const leafChip = (name: string) => page.locator("aside div[role='group'] button", { hasText: name });
    await leafChip("RV 减速器").click();
    await expect(leafChip("RV 减速器")).toHaveAttribute("aria-pressed", "true");
    await expect(page.locator("#selected-leaf-panel")).toHaveAttribute("aria-label", "RV 减速器 成分股");

    // 再次切换环节保持一致（丝杠 → 首叶子「滚珠丝杠」）
    await itemButton(page, "丝杠").click();
    await expect(page.locator("#selected-leaf-panel")).toHaveAttribute("aria-label", "滚珠丝杠 成分股");
    await expect(page.locator("[role='alert']")).toHaveCount(0);
  });

  test("未核实板块（HBM）：回退到旧视图，显示核实中占位而非产业链", async ({ page }) => {
    await page.goto("/sectors/hbm");

    await expect(page.getByRole("heading", { name: "HBM", exact: true })).toBeVisible();
    await expect(page.getByText("该板块的环节骨架尚在", { exact: false })).toBeVisible();
    await expect(page.locator("div[aria-label='产业链纵向总览']")).toHaveCount(0);
  });

  test("后端只读 API：空库下正常返回结构化数据（不写入任何内容）", async ({ page }) => {
    const res = await page.request.get(`http://127.0.0.1:${BACKEND_PORT}/api/sectors/stocks`, {
      params: { key: "humanoid" },
    });
    expect(res.status()).toBe(200);
    const body = await res.json();
    expect(body.data).toBeTruthy();
    expect(typeof body.data.meta).toBe("object");
    expect(body.data.leaves).toEqual({});
  });

  test("侧栏「板块中心」导航组：整行点击开合、刷新后记忆、直达子板块", async ({ page }) => {
    await page.goto("/sectors/humanoid");

    // 默认展开：子栏目链接可见；整行（菜单项本身）点击即收起，并照常回到总览页
    const groupLink = page.locator("nav a[href='/sectors']");
    await expect(groupLink).toHaveAttribute("aria-expanded", "true");
    await expect(page.locator("nav a[href='/sectors/cpo']")).toBeVisible();

    await groupLink.click();
    await expect(page.locator("nav a[href='/sectors/cpo']")).toBeHidden();
    await expect(groupLink).toHaveAttribute("aria-expanded", "false");
    expect(await page.evaluate(() => localStorage.getItem("vr-sectors-open"))).toBe("closed");
    await expect(page).toHaveURL(/\/sectors$/);

    // 刷新后保持收起；再点整行展开恢复
    await page.reload();
    await expect(page.locator("nav a[href='/sectors/cpo']")).toBeHidden();
    await groupLink.click();
    await expect(page.locator("nav a[href='/sectors/cpo']")).toBeVisible();
    await expect(groupLink).toHaveAttribute("aria-expanded", "true");

    // 子栏目直达光通信详情
    await page.locator("nav a[href='/sectors/cpo']").click();
    await expect(page).toHaveURL(/\/sectors\/cpo$/);
  });

  test("详情页返回链接回到板块中心", async ({ page }) => {
    await page.goto("/sectors/cpo");
    // 侧栏也有「板块中心」入口，这里点的是详情页的返回链接（main 内）
    await page.locator("main a", { hasText: "板块中心" }).click();
    await expect(page).toHaveURL(/\/sectors$/);
    await expect(page.getByRole("heading", { name: "板块中心" })).toBeVisible();
  });
});
