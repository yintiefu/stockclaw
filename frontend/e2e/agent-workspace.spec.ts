/** 1D 工作台浏览器套件：完整交互矩阵 + 响应式/主题截图 + 网络安全断言。

后端为生产 Agent 路由 + fixture 接缝（模型/MCP 会话/本地工具），
数据根为 playwright.config.ts 重建的临时目录；fixture 行为由消息关键词驱动。
串行执行：测试间共享同一数据根的运行历史。
 */
import { writeFileSync } from "node:fs";
import { join } from "node:path";

import { expect, test, type Page } from "@playwright/test";

import { E2E_DATA_DIR } from "../playwright.config";

const MODEL_CONFIG = {
  provider: "fixture",
  baseURL: "https://fixture.invalid/v1",
  model: "fixture-model",
  apiKey: "e2e-key",
};

test.describe.configure({ mode: "serial" });

async function openWorkspace(page: Page, theme: "dark" | "light" = "dark") {
  await page.addInitScript(([config, themeValue]) => {
    localStorage.setItem("vr-agent-model", JSON.stringify(config));
    localStorage.setItem("vr-theme", themeValue);
  }, [MODEL_CONFIG, theme] as const);
  await page.goto("/agent");
  await expect(page.getByTestId("agent-workspace")).toBeVisible();
}

async function send(page: Page, text: string) {
  const composer = page.getByLabel("Agent 消息", { exact: true });
  await expect(composer).toBeEnabled({ timeout: 30_000 });
  await composer.fill(text);
  await page.getByTitle("发送", { exact: true }).click();
}

async function waitForComposerBack(page: Page) {
  // 终态后的权威收敛会短暂禁用输入；等它恢复
  await expect(page.getByLabel("Agent 消息", { exact: true })).toBeEnabled({ timeout: 30_000 });
}

async function decideApproval(page: Page, choice: "once" | "session" | "reject") {
  // 桌面端 Inspector 的审批面板只对「选中的运行」可操作；先在 Run 页签选中最新的待审批运行
  await page.getByRole("tab", { name: /Run/ }).click();
  const runSelect = page.getByLabel("历史运行", { exact: true });
  await expect(runSelect).toBeVisible({ timeout: 30_000 });
  await runSelect.selectOption({ index: 0 });

  await page.getByRole("tab", { name: /Approval/ }).click();
  const panel = page.getByRole("region", { name: "MCP 工具审批" });
  await expect(panel).toBeVisible({ timeout: 30_000 });
  const pattern = choice === "once" ? /本次允许/ : choice === "session" ? /本会话允许/ : /：拒绝/;
  await page.getByRole("radio", { name: pattern }).check();
  await page.getByRole("button", { name: "提交全部决定" }).click();
}

test.describe("桌面工作台交互", () => {
  test("三栏布局下完成流式工具调用与文本", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await openWorkspace(page);
    await expect(page.getByTestId("agent-threads-column")).toBeVisible();
    await expect(page.getByTestId("agent-inspector-column")).toBeVisible();
    await expect(page.getByRole("heading", { name: "交互会话" })).toBeVisible();

    await send(page, "查一下行情");
    await expect(page.getByText("fetch_quote").first()).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText(/收盘价 1500\.0/).first()).toBeVisible({ timeout: 30_000 });
    await waitForComposerBack(page);

    // Inspector Sources 面板展示权威事实（执行记录 vs 模型提供未验证）
    await page.getByRole("tab", { name: /Sources/ }).click();
    await expect(page.getByText("执行记录").first()).toBeVisible();
    await expect(page.getByText("模型提供，未验证").first()).toBeVisible();
  });

  test("产物：创建、打开、版本链、下载与删除", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await openWorkspace(page);

    await send(page, "把这些整理成产物");
    await expect(page.getByRole("button", { name: "在 Inspector 打开" }).first())
      .toBeVisible({ timeout: 30_000 });
    await page.getByRole("button", { name: "在 Inspector 打开" }).first().click();

    // Inspector Artifact 页签展示非可执行 Markdown 与版本链
    await expect(page.getByRole("tab", { name: /Artifact/ })).toHaveAttribute("aria-selected", "true");
    await expect(page.getByText("客观数据（fixture）").first()).toBeVisible();
    await expect(page.getByText("行情参考").first()).toBeVisible();

    // 当前是第 1 版（非叶子）→ 删除被禁用并给出非颜色指示
    await expect(page.getByText("存在后续版本，不能删除")).toBeVisible();

    // 下载走后端 blob（文件名由响应头决定，非 Artifact 标题）
    const download = page.waitForEvent("download");
    await page.getByLabel("下载 Artifact").click();
    const downloaded = await download;
    expect(downloaded.suggestedFilename()).toMatch(/^artifact-/);

    // 切到第 2 版（叶子）→ 可删除；删除后回到第 1 版且第 1 版成为叶子
    await page.getByRole("button", { name: /查看版本：客观数据整理（第 2 版）/ }).click();
    await expect(page.getByText("第 2 版", { exact: false }).first()).toBeVisible();
    await page.getByLabel("删除 Artifact").click();
    await expect(page.getByRole("button", { name: /查看版本：客观数据整理（第 2 版）/ })).toHaveCount(0);
    await expect(page.getByText("存在后续版本，不能删除")).toHaveCount(0);

    // 删除第 1 版后回到空态
    await page.getByLabel("删除 Artifact").click();
    await expect(page.getByText("当前运行没有可查看的 Artifact")).toBeVisible({ timeout: 30_000 });
  });

  test("审批：本次允许 / 本会话允许 / 许可后直通", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await openWorkspace(page);

    await send(page, "需要审批的调用");
    await expect(page.getByLabel("转向新问题")).toBeVisible({ timeout: 30_000 });
    await decideApproval(page, "once");
    await expect(page.getByText(/审批后的 MCP 工具返回了客观数据/).first())
      .toBeVisible({ timeout: 30_000 });
    await waitForComposerBack(page);

    // approve_once 不留许可：再次审批仍要人工决定
    await send(page, "需要审批的调用");
    await expect(page.getByLabel("转向新问题")).toBeVisible({ timeout: 30_000 });
    await decideApproval(page, "session");
    await expect(page.getByText(/审批后的 MCP 工具返回了客观数据/).first())
      .toBeVisible({ timeout: 30_000 });
    await waitForComposerBack(page);

    // thread_session 许可生效：同工具直通，不再出现审批
    await send(page, "需要审批的调用");
    await expect(page.getByText(/审批后的 MCP 工具返回了客观数据/).first())
      .toBeVisible({ timeout: 30_000 });
    await expect(page.getByLabel("转向新问题")).toHaveCount(0);
  });

  test("新会话中拒绝审批调用", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await openWorkspace(page);
    await page.getByLabel("新建会话").click();
    await expect(page.getByRole("heading", { name: "新会话" })).toBeVisible({ timeout: 30_000 });

    await send(page, "需要审批的调用");
    await expect(page.getByLabel("转向新问题")).toBeVisible({ timeout: 30_000 });
    await decideApproval(page, "reject");
    await expect(page.getByText(/工具调用被拒绝/).first()).toBeVisible({ timeout: 30_000 });
  });

  test("Stop 取消慢速运行", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await openWorkspace(page);
    await send(page, "慢速回复");
    await page.getByTitle("停止", { exact: true }).click();
    // 取消后 Retry 动作出现（cancelled 运行）
    await expect(page.getByRole("button", { name: "重试本轮" })).toBeVisible({ timeout: 30_000 });
  });

  test("失败运行可重试且重试成功", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await openWorkspace(page);
    await send(page, "这次会失败");
    // 重试按钮在 cancelled 状态同样渲染：先等线程状态 chip 变为「失败」，
    // 确保点击时 last_run 已指向本轮 failed run（而非上一测试遗留的 cancelled run）
    const threadRow = page.getByRole("button", { name: /^新会话/ });
    await expect(threadRow.getByText("失败", { exact: true })).toBeVisible({ timeout: 30_000 });
    await expect(page.getByRole("button", { name: "重试本轮" })).toBeVisible({ timeout: 30_000 });
    await page.getByRole("button", { name: "重试本轮" }).click();
    await expect(page.getByText(/重试成功/).first()).toBeVisible({ timeout: 30_000 });
  });

  test("待审批时 steer-away 取消旧运行并转向", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await openWorkspace(page);
    await send(page, "需要审批的调用");
    await expect(page.getByLabel("转向新问题")).toBeVisible({ timeout: 30_000 });

    await page.getByLabel("转向新问题").fill("转向一个新问题");
    await page.getByTitle("发送新问题（取消当前审批）").click();

    await expect(page.getByText(/工具返回客观数据/).first()).toBeVisible({ timeout: 30_000 });
    await expect(page.getByLabel("转向新问题")).toHaveCount(0);
  });

  test("结构化 409：重命名冲突触发权威重载", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await openWorkspace(page);

    // 列表第一项即当前激活线程；先在 UI 外把它改标题（revision 前进）
    const threads = await (await page.request.get("/api/agent/threads")).json();
    const active = threads.threads[0];
    await page.request.patch(`/api/agent/threads/${active.id}`, {
      data: { revision: active.revision, title: "服务器标题" },
    });

    // 列表第一项即当前激活线程：行内三点菜单 → 重命名
    await page.getByLabel("会话操作").first().click();
    await page.getByRole("menuitem", { name: "重命名" }).click();
    await page.getByLabel("新会话标题", { exact: true }).fill("本地草稿标题");
    await page.getByRole("button", { name: "确认重命名" }).click();

    // 409 → 显示冲突并以服务器权威状态恢复
    await expect(page.getByRole("heading", { name: "服务器标题" })).toBeVisible({ timeout: 30_000 });
    await expect(page.getByRole("button", { name: /本地草稿标题/ })).toHaveCount(0);
  });

  test("历史运行分页与详情", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await openWorkspace(page);
    await page.getByRole("button", { name: /历史运行/ }).click();
    await expect(page.getByRole("heading", { name: "历史运行" })).toBeVisible({ timeout: 30_000 });

    // 首页 50 条 + 游标加载更早
    await page.getByRole("tab", { name: /Run/ }).click();
    await expect(page.getByLabel("历史运行", { exact: true })).toBeVisible({ timeout: 30_000 });
    await page.getByLabel("历史运行", { exact: true }).selectOption("run-hist-059");
    // exact 匹配遥测面板的 run id 段落；option 文本带「· completed」后缀不会命中
    await expect(page.getByText("run-hist-059", { exact: true })).toBeVisible({ timeout: 30_000 });
    await page.getByRole("button", { name: "加载更早运行" }).click();
    await expect(page.getByLabel("历史运行", { exact: true })
      .filter({ has: page.getByRole("option", { name: /run-hist-009/ }) }))
      .toBeVisible({ timeout: 30_000 });
  });

  test("刷新后从 REST 收敛线程与消息", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await openWorkspace(page);
    // 上一测试后列表第一项标题为「服务器标题」；点回该线程并校验历史仍在
    await page.getByRole("button", { name: /服务器标题/ }).click();
    await expect(page.getByText(/重试成功|工具调用被拒绝|审批后的 MCP/).first())
      .toBeVisible({ timeout: 30_000 });

    await page.reload();
    await expect(page.getByTestId("agent-workspace")).toBeVisible();
    await expect(page.getByText(/重试成功|工具调用被拒绝|审批后的 MCP/).first())
      .toBeVisible({ timeout: 30_000 });
  });
});

test.describe("设置与 Policy", () => {
  test("Policy CAS 保存与 revision 前进", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await openWorkspace(page);
    await page.getByRole("button", { name: "模型设置" }).click();
    await expect(page.getByRole("dialog", { name: "设置" })).toBeVisible();

    await page.getByRole("tab", { name: /Policy/ }).click();
    await expect(page.getByText(/revision 0/)).toBeVisible({ timeout: 30_000 });
    await page.getByLabel(/单次运行工具调用上限/).fill("20");
    await page.getByRole("button", { name: "保存 Policy" }).click();
    await expect(page.getByText("Policy 已保存")).toBeVisible({ timeout: 30_000 });
    await page.getByRole("button", { name: "关闭" }).click();
  });

  test("损坏 Policy 显示原因并需二次确认重置", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await openWorkspace(page);
    // 直接把 e2e 临时数据根里的 policy.json 写坏
    writeFileSync(join(E2E_DATA_DIR, "policy.json"), '{"schema_version":1,"revision":9,"max_model_calls":0}');

    await page.getByRole("button", { name: "模型设置" }).click();
    await page.getByRole("tab", { name: /Policy/ }).click();
    await expect(page.getByText(/Policy 文件损坏/)).toBeVisible({ timeout: 30_000 });
    await expect(page.getByRole("button", { name: "保存 Policy" })).toBeDisabled();

    await page.getByRole("button", { name: "重置损坏的 Policy" }).click();
    await expect(page.getByRole("button", { name: "确认重置" })).toBeVisible();
    await page.getByRole("button", { name: "确认重置" }).click();
    await expect(page.getByText(/已重置为默认 Policy/)).toBeVisible({ timeout: 30_000 });
    await page.getByRole("button", { name: "关闭" }).click();
  });

  test("MCP 设置在显式测试连接后重载", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await openWorkspace(page);
    await page.getByRole("button", { name: "模型设置" }).click();
    await page.getByRole("tab", { name: /MCP/ }).click();

    await expect(page.getByText("Fixture 行情")).toBeVisible({ timeout: 30_000 });
    await page.getByRole("button", { name: "测试连接" }).click();
    await expect(page.getByText(/tools=1/).first()).toBeVisible({ timeout: 30_000 });
    await page.getByRole("button", { name: "关闭" }).click();
  });
});

const VIEWPORTS: Array<{ name: string; width: number; height: number }> = [
  { name: "desktop-1440", width: 1440, height: 900 },
  { name: "desktop-1280", width: 1280, height: 800 },
  { name: "mobile-390", width: 390, height: 844 },
];

test.describe("响应式与主题", () => {
  for (const viewport of VIEWPORTS) {
    for (const theme of ["dark", "light"] as const) {
      test(`截图与布局：${viewport.name} ${theme}`, async ({ page }) => {
        await page.setViewportSize({ width: viewport.width, height: viewport.height });
        await openWorkspace(page, theme);

        // 无水平溢出；Composer 始终可见
        const overflow = await page.evaluate(
          () => document.scrollingElement!.scrollWidth - document.documentElement.clientWidth,
        );
        expect(overflow).toBeLessThanOrEqual(0);
        await expect(page.getByLabel("Agent 消息", { exact: true })).toBeVisible();

        if (viewport.width >= 1280) {
          // 桌面：三栏常驻，无抽屉
          await expect(page.getByTestId("agent-threads-column")).toBeVisible();
          await expect(page.getByTestId("agent-inspector-column")).toBeVisible();
          await expect(page.getByRole("dialog")).toHaveCount(0);
        } else {
          // 移动：仅聊天列；线程/设置互斥抽屉 + 焦点/Esc 语义
          await expect(page.getByTestId("agent-threads-column")).toHaveCount(0);
          await page.getByRole("button", { name: "打开线程" }).click();
          const threadsDialog = page.getByRole("dialog", { name: "会话线程" });
          await expect(threadsDialog).toBeVisible();
          await expect(page.getByLabel("新建会话")).toBeVisible();
          // 线程抽屉是模态：先 Esc 关闭，主页面「模型设置」才可达
          await page.keyboard.press("Escape");
          await expect(threadsDialog).toBeHidden();

          await page.getByRole("button", { name: "模型设置" }).click();
          const settingsDialog = page.getByRole("dialog", { name: "设置" });
          await expect(settingsDialog).toBeVisible();

          const focused = await settingsDialog.evaluate(
            (node) => node.contains(document.activeElement),
          );
          expect(focused).toBeTruthy();
          await page.keyboard.press("Escape");
          await expect(settingsDialog).toBeHidden();
        }

        await page.screenshot({
          path: test.info().outputPath(`${viewport.name}-${theme}.png`),
          fullPage: true,
        });
      });
    }
  }
});

test.describe("网络与内容安全", () => {
  test("Markdown Artifact 不触发任何远程请求", async ({ page }) => {
    const violations: string[] = [];
    await page.route("**/*", (route) => {
      const url = new URL(route.request().url());
      // fonts.googleapis/gstatic 是 index.html 声明的应用字体（首屏即加载），
      // 与 Artifact 内容无关；其余任何远程域都算违规
      const allowed = url.hostname === "127.0.0.1" || url.hostname === "localhost"
        || url.hostname === "fonts.googleapis.com" || url.hostname === "fonts.gstatic.com";
      if (!allowed) {
        violations.push(`${route.request().method()} ${url.host}${url.pathname}`);
      }
      return route.continue();
    });

    await page.setViewportSize({ width: 1440, height: 900 });
    await openWorkspace(page);
    await send(page, "把这些整理成产物");
    await expect(page.getByRole("button", { name: "在 Inspector 打开" }).first())
      .toBeVisible({ timeout: 30_000 });
    await page.getByRole("button", { name: "在 Inspector 打开" }).first().click();
    await expect(page.getByText("客观数据（fixture）").first()).toBeVisible();
    // Markdown 中的远程链接/图片没有被实例化为任何请求
    expect(violations).toEqual([]);
  });
});
