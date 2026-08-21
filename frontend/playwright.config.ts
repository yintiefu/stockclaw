import os from "node:os";
import path from "node:path";

import { defineConfig } from "@playwright/test";

/**
 * 1D 浏览器测试：固定隔离端口 + 独立临时数据根。
 *
 * 后端跑的是生产 Agent 路由 + 三处确定性接缝（tests.agent_e2e_app），
 * 绝不指向 8900 的常规服务，也绝不触碰用户数据根。
 * 首次安装浏览器：npm run test:e2e:install（受限网络可设 PLAYWRIGHT_DOWNLOAD_HOST，
 * 例如 https://cdn.npmmirror.com/binaries/playwright）。
 *
 * 注意：本模块必须保持无副作用 —— spec 会导入 E2E_DATA_DIR 常量，
 * 若在模块顶层清理数据根，会在 worker 重新执行本模块时误删后端已写入的种子。
 * 数据根清理由后端 webServer 命令完成（见下方 command）。
 */
export const BACKEND_PORT = 8873;
export const FRONTEND_PORT = 5873;

const dataRoot = path.join(os.tmpdir(), "vr-agent-e2e-1d");
export const E2E_DATA_ROOT = dataRoot;
export const E2E_DATA_DIR = path.join(dataRoot, "agent");

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
      // rm -rf 保证每次运行从干净数据根开始；exec 让 uvicorn 成为直接子进程，
      // 结束时可被可靠终止（不残留孤儿进程占用端口）
      command: `cd ../backend && rm -rf '${dataRoot}' && exec .venv/bin/python -m uvicorn tests.agent_e2e_app:app --host 127.0.0.1 --port ${BACKEND_PORT} --log-level warning`,
      url: `http://127.0.0.1:${BACKEND_PORT}/api/agent/policy`,
      env: { VR_E2E_DATA_DIR: E2E_DATA_DIR, VR_DATA_DIR: dataRoot },
      reuseExistingServer: false,
      stdout: "ignore",
      stderr: "pipe",
      timeout: 90_000,
    },
    {
      command: `exec node node_modules/vite/bin/vite.js --port ${FRONTEND_PORT} --strictPort --host 127.0.0.1`,
      url: `http://127.0.0.1:${FRONTEND_PORT}`,
      env: { VITE_API_URL: `http://127.0.0.1:${BACKEND_PORT}` },
      reuseExistingServer: false,
      stdout: "ignore",
      stderr: "pipe",
      timeout: 120_000,
    },
  ],
});
