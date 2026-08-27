// 统一 LangGraph AI 工作流浏览器验收 v2（隔离、确定性、零真实网络/模型/数据源）。
//
// 三个隔离服务（FastAPI :8873 / LangGraph :2873 / Vite :5873）由 playwright.config 启动：
// - LangGraph 六图全部来自 tests/agent_e2e/unified_graphs.py（脚本化模型 + 假工具）；
// - 工作流断言走 v2 契约：values = 状态机、messages = 阶段正文（StageResult.message_id
//   指针）、custom = 底稿进度；重试 = {resume: true} 经后端 entry 版本门控 + auto_resume 路由；
// - FastAPI 数据接口在页面级用 route.abort() 掐断（页面自带优雅降级），资讯雷达用固定 JSON；
// - 断言全程没有任何 /api/chat、/api/debate、/api/reflect 流量，浏览器请求不带模型密钥。
//
// 服务重启说明：langgraph dev 是内存运行时——thread 元数据落盘，checkpoint 不落盘。
// 重启场景按真实行为断言：历史列表仍在、丢态行派生为「已中断」而非永久「生成中」、
// 新 run 可继续发起。checkpoint 级恢复由「运行中刷新」场景覆盖（同进程权威 checkpoint）。
import { spawn, spawnSync } from "node:child_process";
import path from "node:path";

import { expect, test, type Page } from "@playwright/test";

import { BACKEND_PORT, LANGGRAPH_PORT } from "../playwright.config";

const LG = `http://127.0.0.1:${LANGGRAPH_PORT}`;

test.describe.configure({ mode: "serial" });

// ---------------------------------------------------------------- LangGraph API helpers
async function lg<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(`${LG}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!resp.ok) throw new Error(`LangGraph ${path} -> ${resp.status}: ${(await resp.text()).slice(0, 300)}`);
  const text = await resp.text();
  return (text ? JSON.parse(text) : {}) as T;
}

interface LgThread {
  thread_id: string;
  metadata: Record<string, unknown>;
  status: string;
  values?: Record<string, unknown>;
}

async function createThread(metadata: Record<string, unknown>): Promise<string> {
  const t = await lg<LgThread>("/threads", { method: "POST", body: JSON.stringify({ metadata }) });
  return t.thread_id;
}

async function searchThreads(metadata: Record<string, unknown>): Promise<LgThread[]> {
  return lg<LgThread[]>("/threads/search", {
    method: "POST",
    body: JSON.stringify({ metadata, limit: 20, sortBy: "updated_at", sortOrder: "desc" }),
  });
}

async function threadState(threadId: string): Promise<{ values: Record<string, unknown> }> {
  return lg(`/threads/${threadId}/state`);
}

/** 页面级掐断真实数据接口（页面自带降级），后注册的具体 fulfill 优先生效。 */
async function blockDataApi(page: Page) {
  await page.route("**/api/**", (route) => route.abort());
}

// ---------------------------------------------------------------- T1 工作台过滤
test("workspace 只显示 workspace 与无 channel 遗留会话，隐藏 embedded/workflow", async ({ page }) => {
  await createThread({ channel: "workspace", title: "E2E 工作台会话甲" });
  await createThread({ channel: "embedded", route: "/daily-review", scope_key: "/daily-review", title: "E2E 嵌入式会话乙" });
  await createThread({ channel: "workflow", workflow_type: "debate", title: "E2E 工作流会话丙" });
  await createThread({ title: "E2E 遗留会话丁" });

  await page.goto("/agent");
  await expect(page.getByRole("button", { name: "E2E 工作台会话甲" })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole("button", { name: "E2E 遗留会话丁" })).toBeVisible();
  await expect(page.getByRole("button", { name: "E2E 嵌入式会话乙" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "E2E 工作流会话丙" })).toHaveCount(0);
});

// ---------------------------------------------------------------- T2 嵌入式问答
test("嵌入式问答按 scope 持久化、更新快照版本、恢复、隔离并显式删除", async ({ page }) => {
  const seenRequests: string[] = [];
  page.on("request", (req) => seenRequests.push(req.url()));
  // 旧浏览器键属于迁移前数据：种进去，验收全程不得被动过。
  await page.addInitScript(() => {
    localStorage.setItem("vr-askai-chat:/stock-data#600519", JSON.stringify([
      { role: "user", content: "旧提问" }, { role: "assistant", content: "旧回答" },
    ]));
    localStorage.setItem("vr-llm", JSON.stringify({ apiKey: "sk-legacy-e2e" }));
  });
  await blockDataApi(page);
  // 个股页 AskAiButton 依赖估值查询结果（scopeKey=code）：用固定 JSON 喂出按钮
  await page.route("**/api/valuation*", (route) => {
    const code = new URL(route.request().url()).searchParams.get("code") ?? "";
    return route.fulfill({
      status: 200, headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        code, name: code === "600519" ? "贵州茅台" : "平安银行", price: 1500, mcap_yi: 18000,
        pe_ttm: 25, pb: 8, pe_percentile: null, pb_percentile: null, dividend_yield: null,
      }),
    });
  });

  await page.goto("/stock-data");
  const query = async (stock: string) => {
    await page.getByPlaceholder(/A 股 6 位代码/).fill(stock);
    await page.getByRole("button", { name: "查询" }).click();
    await page.getByRole("button", { name: "让 AI 读这些数据" }).waitFor({ timeout: 15_000 });
  };
  const openDrawer = async () => {
    await page.getByRole("button", { name: "让 AI 读这些数据" }).click();
    await page.getByPlaceholder("就本页内容提问…").waitFor({ timeout: 15_000 });
  };

  await query("600519");
  await openDrawer();
  await page.getByPlaceholder("就本页内容提问…").fill("当前价格如何？");
  await page.getByPlaceholder("就本页内容提问…").press("Enter");
  await expect(page.getByText("嵌入式脚本回答").first()).toBeVisible({ timeout: 30_000 });
  // 旧对话绝不出现（不读旧键）
  await expect(page.getByText("旧回答")).toHaveCount(0);

  // 刷新后从 checkpoint 恢复（重查一次喂出按钮）
  await page.reload();
  await query("600519");
  await openDrawer();
  await expect(page.getByText("嵌入式脚本回答").first()).toBeVisible({ timeout: 15_000 });

  // 第二次发送 → Server 盖章快照版本递增到 2
  await page.getByPlaceholder("就本页内容提问…").fill("再问一句");
  await page.getByPlaceholder("就本页内容提问…").press("Enter");
  await expect(page.getByText("嵌入式脚本回答").nth(1)).toBeVisible({ timeout: 30_000 });
  const embedded = await searchThreads({ channel: "embedded", route: "/stock-data", scope_key: "600519" });
  expect(embedded.length).toBe(1);
  const state = await threadState(embedded[0].thread_id);
  const snapshot = state.values.page_context as { version: number; scope_key: string } | undefined;
  expect(snapshot?.version).toBe(2);
  expect(snapshot?.scope_key).toBe("600519");
  await page.getByRole("button", { name: "关闭" }).click();

  // 换标的 = 换 scope：抽屉里看不到 600519 的历史
  await query("000001");
  await openDrawer();
  await expect(page.getByText("嵌入式脚本回答")).toHaveCount(0);
  await page.getByRole("button", { name: "关闭" }).click();

  // 回到 600519 仍在；清空显式删除该 thread
  await query("600519");
  await openDrawer();
  await expect(page.getByText("嵌入式脚本回答").first()).toBeVisible({ timeout: 15_000 });
  await page.getByRole("button", { name: "清空本页对话" }).click();
  await expect(page.getByText("嵌入式脚本回答")).toHaveCount(0);
  await expect(async () => {
    const left = await searchThreads({ channel: "embedded", route: "/stock-data", scope_key: "600519" });
    expect(left.length).toBe(0);
  }).toPass({ timeout: 10_000 });

  // 旧浏览器键原样保留 + 无旧 AI 端点流量 + 请求不携带密钥
  const legacyChat = await page.evaluate(() => localStorage.getItem("vr-askai-chat:/stock-data#600519"));
  expect(JSON.parse(legacyChat ?? "[]")).toEqual([
    { role: "user", content: "旧提问" }, { role: "assistant", content: "旧回答" },
  ]);
  expect(await page.evaluate(() => localStorage.getItem("vr-llm"))).toContain("sk-legacy-e2e");
  expect(seenRequests.some((u) => /\/api\/(chat|debate|reflect)(\?|$)/.test(u))).toBe(false);
  expect(seenRequests.some((u) => u.includes("sk-legacy-e2e"))).toBe(false);
});

// ---------------------------------------------------------------- T3 辩论 standard
// 选择器依据（真实 DOM，勿凭记忆改）：
// - WorkflowHistory 列表常驻渲染，没有「历史」按钮；标题区刷新按钮 title="刷新历史"
//   ——getByRole("button", { name: /历史/ }) 会误中它，禁止使用；
// - 行内动作按钮：「查看」「重新运行」（RotateCcw 图标+文本）、删除（aria-label）；
// - 行标题 = metadata.title（Debate 发起时写「多空辩论 · <code>」）；
// - Debate 页按钮：「开始辩论」「中止」（仅 running）「从失败阶段重试」
//   （仅 status∈{failed,interrupted,cancelled} 且非 running）。
const historyRow = (page: Page, title: string) =>
  page.locator("li", { hasText: title }).first();

// T3 standard 辩论：values 驱动阶段 + 正文经指针渲染
test("standard 辩论按状态机推进，裁判正文可恢复", async ({ page }) => {
  await blockDataApi(page);
  await page.goto("/debate");
  await page.getByPlaceholder("6 位代码，如 600519").fill("600519");
  await page.getByRole("button", { name: "开始辩论" }).click();

  await expect(page.getByText("多方脚本观点").first()).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText("空方脚本观点").first()).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText(/中立主持脚本归纳/).first()).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText(/未取到：板块与概念归属/)).toBeVisible();

  // 中立边界（产品硬边界，重写时不得删除）：不出现任何胜负裁决文案
  for (const banned of [page.getByText(/赢家/), page.getByText(/获胜/), page.getByText(/多方胜/)]) {
    await expect(banned).toHaveCount(0);
  }

  // 刷新恢复：历史列表常驻，「查看」触发 run.restore → v2 hydrate 装载 checkpoint
  await page.reload();
  await page.goto("/debate");
  await historyRow(page, "多空辩论 · 600519").getByRole("button", { name: "查看" }).click();
  await expect(page.getByText(/中立主持脚本归纳/).first()).toBeVisible({ timeout: 15_000 });

  // 重新运行 = 新 thread，旧结果保留（两条历史）
  await historyRow(page, "多空辩论 · 600519").getByRole("button", { name: "重新运行" }).click();
  await expect(page.getByText("多方脚本观点").first()).toBeVisible({ timeout: 60_000 });
  await expect(async () => {
    const threads = await searchThreads({ channel: "workflow", workflow_type: "debate", subject: "600519" });
    expect(threads.length).toBeGreaterThanOrEqual(2);
  }).toPass({ timeout: 15_000 });
});

// ---------------------------------------------------------------- T4 辩论 cross_exam
test("cross_exam 辩论执行五个配置阶段（含交叉反驳）", async ({ page }) => {
  await blockDataApi(page);
  await page.goto("/debate");
  await page.getByPlaceholder("6 位代码，如 600519").fill("600519");
  await page.getByRole("combobox").selectOption("2");
  await page.getByRole("button", { name: "开始辩论" }).click();

  await expect(page.getByText("多方脚本观点").first()).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText("多方反驳脚本").first()).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText("空方反驳脚本").first()).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText(/中立主持脚本归纳/).first()).toBeVisible({ timeout: 60_000 });
});

// ---------------------------------------------------------------- T5 停止 + resume（不重放已完成阶段，按内容计数，不按 id）
test("辩论可中止并经 resume 从失败阶段继续，已完成阶段不重放", async ({ page }) => {
  await blockDataApi(page);
  await page.goto("/debate");
  await page.getByPlaceholder("6 位代码，如 600519").fill("600519");
  await page.getByRole("button", { name: "开始辩论" }).click();
  // 阶段模型带 1.5s 延迟；等首阶段开始（生成中标记 = validate_input 已落 checkpoint，
  // 过早取消会留下无可恢复状态的空线程）再中止
  await expect(page.getByText("生成中…").first()).toBeVisible({ timeout: 30_000 });
  await page.getByRole("button", { name: "中止" }).click();
  // stop() 内部会轮询到线程脱离 busy 才返回（≤10s），页面 finally 里的 historyKey
  // 自增发生在其后——badge「已中断」与重试按钮的出现是确定性的，不存在竞态。
  await expect(page.getByRole("button", { name: "从失败阶段重试" })).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("已中断").first()).toBeVisible({ timeout: 15_000 });

  await page.getByRole("button", { name: "从失败阶段重试" }).click();
  await expect(page.getByText(/中立主持脚本归纳/).first()).toBeVisible({ timeout: 60_000 });

  const threads = await searchThreads({ channel: "workflow", workflow_type: "debate" });
  const state = await threadState(threads[0].thread_id);
  const contents = (state.values.messages ?? []).map((m: { content: unknown }) =>
    typeof m.content === "string" ? m.content : JSON.stringify(m.content));
  // 无论中止落在 bull 之前还是之后：resume 只补非终态阶段，bull 正文始终恰好一条
  expect(contents.filter((c: string) => c.includes("多方脚本观点"))).toHaveLength(1);
  expect(state.values.stages.referee.status).toBe("completed");
  expect(state.values.input).toEqual({ code: "600519" });  // resume 不覆写 input
  expect(state.values.stages.bull.message_id).toBeTruthy();
});

// ---------------------------------------------------------------- T6 运行中刷新
// 断开只停本地，Server run 跑完，重开 hydrate 完整恢复；
// 且陈旧 busy 不得让页面永远「生成中」（C2 回归）
test("运行中刷新页面后重开历史，完整结果从 checkpoint 恢复", async ({ page }) => {
  await blockDataApi(page);
  await page.goto("/debate");
  await page.getByPlaceholder("6 位代码，如 600519").fill("600519");
  await page.getByRole("button", { name: "开始辩论" }).click();
  await expect(page.getByText("多方脚本观点").first()).toBeVisible({ timeout: 60_000 });

  await page.reload();  // 杀页面连接；fixture 服务端继续跑完
  await page.goto("/debate");
  // 服务端可能仍在跑：反复打开历史行（restore 幂等），直到 run 跑完
  await expect(async () => {
    await historyRow(page, "多空辩论 · 600519")
      .getByRole("button", { name: "查看" }).click({ timeout: 2_000 });
    await expect(page.getByText(/中立主持脚本归纳/).first()).toBeVisible({ timeout: 5_000 });
  }).toPass({ timeout: 90_000 });

  // C2 断言：attached run 已完成 → 中止按钮与「生成中」标记都必须消失，
  // 不能只看正文出现（正文可见 ≠ running 复位）。
  await expect(page.getByRole("button", { name: "中止" })).toHaveCount(0);
  await expect(page.getByText("生成中…")).toHaveCount(0);
});

// ---------------------------------------------------------------- T7 取消孤儿 → interrupted
test("取消后页面关闭的历史派生为已中断，不是永久生成中也不是猜测的已取消", async ({ page }) => {
  await blockDataApi(page);
  await page.goto("/debate");
  await page.getByPlaceholder("6 位代码，如 600519").fill("600519");
  await page.getByRole("button", { name: "开始辩论" }).click();
  await expect(page.getByText("生成中…").first()).toBeVisible({ timeout: 30_000 });

  // 模拟客户端消失：只做服务端 cancel，不回写取消 checkpoint，然后直接换页
  const thread = (await searchThreads({ channel: "workflow", workflow_type: "debate" }))[0];
  const runs = await lg<Array<{ run_id: string; status: string }>>(`/threads/${thread.thread_id}/runs`);
  const active = runs.find((r) => r.status === "pending" || r.status === "running");
  expect(active).toBeTruthy();
  await lg(`/threads/${thread.thread_id}/runs/${active!.run_id}/cancel`, {
    method: "POST",
    body: JSON.stringify({ action: "interrupt", wait: true }),
  });

  await page.goto("/debate");
  await expect(page.getByText("已中断").first()).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("生成中…")).toHaveCount(0);
});

// ---------------------------------------------------------------- T8 反思审计
test("反思审计按来源记录过滤历史，审计结果可存入研究记录", async ({ page }) => {
  const NOTE_ID = "note-e2e-42";
  await page.addInitScript((id) => {
    localStorage.setItem("vr-notes", JSON.stringify([{
      id, kind: "复盘", title: "E2E 反思来源记录", content: "缩量整理的推理文本。", ts: Date.now(),
    }]));
  }, NOTE_ID);
  await blockDataApi(page);

  await page.goto("/notes");
  await page.getByText("E2E 反思来源记录").click();
  await page.getByRole("button", { name: "反思审计" }).click();
  await expect(page.getByText(/反思脚本审计/)).toBeVisible({ timeout: 30_000 });

  // 历史只挂在这条记录下（subject=note.id）
  await expect(page.getByText("反思 · E2E 反思来源记录").first()).toBeVisible({ timeout: 15_000 });
  const reflection = await searchThreads({ channel: "workflow", workflow_type: "reflection", subject: NOTE_ID });
  expect(reflection.length).toBe(1);

  await page.getByRole("button", { name: "把审计结果存为新记录" }).click();
  const notes = await page.evaluate(() => JSON.parse(localStorage.getItem("vr-notes") || "[]"));
  expect(notes.some((n: { kind: string; content: string }) =>
    n.kind === "反思审计" && n.content.includes("反思脚本审计"))).toBe(true);
});

// ---------------------------------------------------------------- T9 复盘 + 资讯提炼
test("每日复盘与资讯提炼消费页面快照并展示各自历史", async ({ page }) => {
  await blockDataApi(page);
  await page.goto("/daily-review");
  await page.getByRole("button", { name: "让 AI 复盘今天" }).click();
  await expect(page.getByText(/复盘脚本结论/)).toBeVisible({ timeout: 30_000 });

  const reviews = await searchThreads({ channel: "workflow", workflow_type: "daily_review" });
  expect(reviews.length).toBeGreaterThanOrEqual(1);
  const reviewState = await threadState(reviews[0].thread_id);
  // 图只吃页面快照：输入里是已渲染的快照文本，不再抓数据
  expect(typeof (reviewState.values.input as Record<string, unknown>)?.market_snapshot).toBe("string");

  // 资讯提炼：雷达数据用固定 JSON，提炼历史按赛道隔离
  await page.route("**/api/radar", (route) => route.fulfill({
    status: 200, headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      generated_at: "2026-08-25 11:00", recent_days: 3,
      stats: { total_sources: 2 },
      industries: [
        { key: "ai", name: "AI 算力", accent: "#f80", items: [
          { time: "08-24", source: "源A", title: "AI news one", url: "https://example.com/1" },
        ] },
        { key: "energy", name: "能源", accent: "#08f", items: [
          { time: "08-25", source: "源C", title: "Energy news", url: "https://example.com/3" },
        ] },
      ],
    }),
  }));
  await page.goto("/intel");
  await page.getByRole("button", { name: "让 AI 提炼今日要点" }).click();
  await expect(page.getByText(/资讯脚本要点/)).toBeVisible({ timeout: 30_000 });

  const digests = await searchThreads({ channel: "workflow", workflow_type: "news_digest", subject: "ai" });
  expect(digests.length).toBe(1);
  const digestState = await threadState(digests[0].thread_id);
  const snapshot = (digestState.values.input as Record<string, unknown>)?.news_snapshot;
  expect(String(snapshot)).toContain("AI news one");
  expect(String(snapshot)).not.toContain("Energy news");
});

// ---------------------------------------------------------------- T10 设置页只读脱敏
test("设置页只读展示脱敏配置状态与占位模板，不出现密钥输入", async ({ page }) => {
  await blockDataApi(page);
  // /api/agent/status 返回未配置安全形状（隔离 VR_AGENT_SETTINGS 指向不存在的文件）
  await page.route("**/api/agent/status", (route) => route.fulfill({
    status: 200, headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      configured: false,
      settings_path: "/tmp/vr-e2e/fastapi-agent-settings.json",
      model_name: null, base_url_host: null,
      builtin_skill_count: 0, mcp_server_count: 0,
      restart_required: true,
      config_template: '{\n  "model": {\n    "provider": "openai",\n    "name": "your-model",\n    "apiKey": "YOUR_API_KEY",\n    "baseURL": "https://your-provider.example/v1"\n  },\n  "skills": {\n    "path": "~/.vibe-research/agent/skills"\n  },\n  "mcpServers": {}\n}',
      reason: "Agent 配置缺失或无效，LangGraph Server 未就绪；请按模板创建配置后重启。",
    }),
  }));
  await page.goto("/settings");
  await expect(page.getByText(/Agent 服务在线/)).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText(/配置缺失或无效/)).toBeVisible();
  await expect(page.getByText(/mkdir -p ~\/\.vibe-research\/agent\/skills/)).toBeVisible();
  await expect(page.getByText(/chmod 600/)).toBeVisible();
  await expect(page.getByText(/langgraph dev --host 127\.0\.0\.1 --port 2024/)).toBeVisible();
  await expect(page.getByRole("button", { name: /一键复制配置模板/ })).toBeVisible();
  // 没有任何模型/密钥/CLI 输入
  await expect(page.getByText("订阅接入")).toHaveCount(0);
  await expect(page.getByText("API 接入")).toHaveCount(0);
  expect(await page.textContent("body")).not.toContain("sk-");
  // FastAPI 访问密钥（VR_API_KEY）控件仍在
  await expect(page.getByPlaceholder(/VR_API_KEY/)).toBeVisible();
});

// ---------------------------------------------------------------- T11 版本门控（后端持有）
// 篡改 config_version 后 UI 触发 resume 被拒。
// 注意：重试按钮仅在 status∈{failed,interrupted,cancelled} 时渲染——篡改时必须
// 同时把 workflow_status 写成 "failed"，否则按钮不出现、场景无法驱动。
test("配置版本不兼容的历史允许查看但 resume 被后端拒绝", async ({ page }) => {
  await blockDataApi(page);
  await page.goto("/debate");
  await page.getByPlaceholder("6 位代码，如 600519").fill("600519");
  await page.getByRole("button", { name: "开始辩论" }).click();
  await expect(page.getByText(/中立主持脚本归纳/).first()).toBeVisible({ timeout: 60_000 });

  const threads = await searchThreads({ channel: "workflow", workflow_type: "debate" });
  await lg(`/threads/${threads[0].thread_id}/state`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ values: { config_version: 999, workflow_status: "failed" } }),
  });

  await page.reload();
  await page.goto("/debate");
  await historyRow(page, "多空辩论 · 600519").getByRole("button", { name: "查看" }).click();
  await page.getByRole("button", { name: "从失败阶段重试" }).click();
  // 后端 entry 抛「配置版本不兼容」→ run failed → 页面错误横幅（errorText）
  await expect(page.getByText(/配置版本不兼容/).first()).toBeVisible({ timeout: 15_000 });
});

// ---------------------------------------------------------------- T12 服务重启（内存运行时真实行为）
test("LangGraph 重启后历史仍在、丢态行派生为已中断、新 run 可继续发起", async ({ page }) => {
  await blockDataApi(page);
  // 重启前先完成一条复盘（快照图无延迟，秒级完成）
  const tid = await createThread({ channel: "workflow", workflow_type: "daily_review", title: "E2E 重启前复盘" });
  await lg(`/threads/${tid}/runs/wait`, {
    method: "POST",
    body: JSON.stringify({ assistant_id: "daily_review", input: { input: { market_snapshot: "重启前快照" } } }),
  });

  // 优雅停掉 langgraph dev（SIGTERM 触发 ops pickle 落盘：thread 记录保留、checkpoint 仍丢失）
  spawnSync("bash", ["-c", `fuser -k -TERM ${LANGGRAPH_PORT}/tcp || true`], { stdio: "ignore" });
  await expect(async () => {
    try {
      const resp = await fetch(`${LG}/docs`, { signal: AbortSignal.timeout(1500) });
      expect(resp.ok).toBe(false);
    } catch {
      // 连接被拒 = 服务已下线，符合预期
    }
  }).toPass({ timeout: 15_000 });

  // 用同一隔离根重启
  const dataRoot = process.env.VR_E2E_DATA_ROOT!;
  const child = spawn("../backend/.venv/bin/python", ["../backend/tests/agent_e2e/start_langgraph.py"], {
    cwd: process.cwd(),  // Playwright worker 的 cwd 即 frontend/
    env: { ...process.env, VR_E2E_ROOT: path.join(dataRoot, "langgraph"), VR_E2E_LANGGRAPH_PORT: String(LANGGRAPH_PORT) },
    detached: true,
    stdio: "ignore",
  });
  child.unref();
  try {
    await expect(async () => {
      const resp = await fetch(`${LG}/docs`, { signal: AbortSignal.timeout(2000) });
      expect(resp.ok).toBe(true);
    }).toPass({ timeout: 60_000 });

    // 历史列表仍在（元数据落盘）；丢态行派生为已中断，绝不永久「生成中」
    await page.goto("/daily-review");
    await expect(page.getByText("E2E 重启前复盘")).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText("生成中").first()).toHaveCount(0);

    // 重启后可继续发起新 run（迁移链路健康）
    await page.getByRole("button", { name: /重新复盘|让 AI 复盘今天/ }).click();
    await expect(page.getByText(/复盘脚本结论/)).toBeVisible({ timeout: 30_000 });
  } finally {
    // 无论断言成败都清掉本测试拉起的 detached 进程，避免残留端口拖垮后续运行
    spawnSync("bash", ["-c", `fuser -k -TERM ${LANGGRAPH_PORT}/tcp || true`], { stdio: "ignore" });
  }
});

// FastAPI 数据 API 合同不受迁移影响（冒烟：/api/health 始终可用）
test("FastAPI 数据服务保持可用", async () => {
  const resp = await fetch(`http://127.0.0.1:${BACKEND_PORT}/api/health`);
  expect(resp.ok).toBe(true);
  const payload = await resp.json();
  expect(payload.ok).toBe(true);
});
