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

const dataRoot = mkdtempSync(path.join(os.tmpdir(), "vr-agent-e2e-"));
const langGraphRoot = path.join(dataRoot, "langgraph");

export default defineConfig({
  testDir: "./e2e",
  timeout: 90_000,
  expect: { timeout: 15_000 },
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
      env: { VR_DATA_DIR: dataRoot, VR_REPORTS_DIR: path.join(dataRoot, "myreports") },
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
