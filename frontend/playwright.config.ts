import os from "node:os";
import path from "node:path";
import { mkdtempSync } from "node:fs";

import { defineConfig } from "@playwright/test";

/**
 * Task 12 浏览器测试：三个隔离服务（FastAPI :8873 + LangGraph :2873 + Vite :5873）
 * + 每次运行全新的 OS 临时数据根。
 *
 * 后端 FastAPI 跑生产 app:app（数据 + 传统 AI），Agent 走独立 LangGraph Server
 * （tests/agent_e2e/start_langgraph.py 负责隔离装配），绝不指向 8900/2024 的
 * 常规服务，也绝不触碰用户数据根。
 * 首次安装浏览器：npm run test:e2e:install（受限网络可设 PLAYWRIGHT_DOWNLOAD_HOST，
 * 例如 https://cdn.npmmirror.com/binaries/playwright）。
 */
export const BACKEND_PORT = 8873;
export const LANGGRAPH_PORT = 2873;
export const FRONTEND_PORT = 5873;

// 数据根必须全进程唯一：spec 里 import 本模块会再次执行，靠 env 变量保证
// worker 进程与 webServer 进程拿到的是同一个根（否则各建各的临时目录）。
const dataRoot = process.env.VR_E2E_DATA_ROOT
  ?? mkdtempSync(path.join(os.tmpdir(), "vr-agent-e2e-"));
process.env.VR_E2E_DATA_ROOT = dataRoot;
/** 隔离数据根：上传 fixture 等测试资产只允许出现在这里 */
export const DATA_ROOT = dataRoot;
const langGraphRoot = path.join(dataRoot, "langgraph");
/** 隔离临时根内的 trace 目录（start_langgraph.py 的 settings.trace.dir 指向这里） */
export const TRACES_ROOT = path.join(langGraphRoot, "traces");
/**
 * Task 11：FastAPI 与 LangGraph 共享同一份隔离 settings（start_langgraph.py
 * 是唯一写入者，先建目录/文件再拉起 FastAPI——webServer 按声明顺序启动，
 * FastAPI 的技能管理器懒初始化，读同一文件即得同一技能根快照）。
 */
export const E2E_SETTINGS_PATH = path.join(langGraphRoot, "settings.json");

export default defineConfig({
  testDir: "./e2e",
  timeout: 90_000,
  expect: { timeout: 30_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  outputDir: "./test-results",
  use: {
    baseURL: `http://127.0.0.1:${FRONTEND_PORT}`,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  webServer: [
    {
      command: `cd ../backend && exec .venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port ${BACKEND_PORT} --log-level warning`,
      url: `http://127.0.0.1:${BACKEND_PORT}/api/health`,
      env: {
        VR_DATA_DIR: dataRoot,
        VR_REPORTS_DIR: path.join(dataRoot, "myreports"),
        // /api/skills 与 /api/agent/status 共用 LangGraph 侧同一份隔离配置
        VR_AGENT_SETTINGS: E2E_SETTINGS_PATH,
      },
      reuseExistingServer: false,
      stdout: "ignore",
      stderr: "pipe",
      timeout: 90_000,
    },
    {
      command: `cd ../backend && exec .venv/bin/python tests/agent_e2e/start_langgraph.py`,
      url: `http://127.0.0.1:${LANGGRAPH_PORT}/docs`,
      env: { VR_E2E_ROOT: langGraphRoot, VR_E2E_LANGGRAPH_PORT: String(LANGGRAPH_PORT) },
      reuseExistingServer: false,
      stdout: "ignore",
      stderr: "pipe",
      timeout: 120_000,
    },
    {
      command: `exec node node_modules/vite/bin/vite.js --port ${FRONTEND_PORT} --strictPort --host 127.0.0.1`,
      url: `http://127.0.0.1:${FRONTEND_PORT}`,
      env: {
        VITE_API_URL: `http://127.0.0.1:${BACKEND_PORT}`,
        VITE_AGENT_API_URL: `http://127.0.0.1:${LANGGRAPH_PORT}`,
      },
      reuseExistingServer: false,
      stdout: "ignore",
      stderr: "pipe",
      timeout: 120_000,
    },
  ],
});
