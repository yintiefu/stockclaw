import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, saveAccessKey } from "@/lib/api";
import * as clipboard from "@/lib/clipboard";
import { Settings } from "./Settings";

vi.mock("@/lib/api", () => ({
  api: { agentStatus: vi.fn() },
  loadAccessKey: vi.fn(() => ""),
  saveAccessKey: vi.fn(),
  ApiError: class ApiError extends Error {},
}));

import { api as apiMock } from "@/lib/api";

const statusPayload = (overrides: Partial<Record<string, unknown>> = {}) => ({
  configured: true,
  settings_path: "/home/user/.vibe-research/agent/settings.json",
  model_name: "test-model",
  base_url_host: "example.invalid",
  builtin_skill_count: 5,
  mcp_server_count: 0,
  restart_required: true,
  config_template: '{\n  "model": {\n    "provider": "openai",\n    "name": "your-model",\n    "apiKey": "YOUR_API_KEY",\n    "baseURL": "https://your-provider.example/v1"\n  },\n  "skills": {\n    "path": "~/.vibe-research/agent/skills"\n  },\n  "mcpServers": {}\n}',
  ...overrides,
});

const fetchMock = vi.fn();

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  localStorage.clear();
  vi.unstubAllGlobals();
});

beforeEach(() => {
  vi.mocked(api.agentStatus).mockResolvedValue(statusPayload() as never);
  global.fetch = fetchMock as never;
});

function renderPage() {
  return render(
    <MemoryRouter>
      <Settings />
    </MemoryRouter>,
  );
}

describe("Settings read-only agent status", () => {
  it("shows the redacted summary fields and LangGraph readiness", async () => {
    fetchMock.mockResolvedValue({ ok: true });
    renderPage();

    expect(await screen.findByText("test-model")).toBeInTheDocument();
    expect(screen.getByText("example.invalid")).toBeInTheDocument();
    expect(screen.getByText(/5/)).toBeInTheDocument();
    expect(screen.getByText(/Agent 服务在线/)).toBeInTheDocument();
    expect(await screen.findByText("/home/user/.vibe-research/agent/settings.json")).toBeInTheDocument();
  });

  it("shows unreachable readiness when the LangGraph server is down", async () => {
    fetchMock.mockRejectedValue(new Error("refused"));
    renderPage();

    expect(await screen.findByText(/Agent 服务离线/)).toBeInTheDocument();
    expect(await screen.findByText("test-model")).toBeInTheDocument();
  });

  it("has no model, api-key, or CLI selection inputs", async () => {
    fetchMock.mockResolvedValue({ ok: true });
    renderPage();
    await screen.findByText("test-model");

    expect(screen.queryByPlaceholderText(/sk-…/)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/API Key/i)).not.toBeInTheDocument();
    expect(screen.queryByText("订阅接入")).not.toBeInTheDocument();
    expect(screen.queryByText(/CLI/)).not.toBeInTheDocument();
    expect(screen.queryByText("API 接入")).not.toBeInTheDocument();
  });

  it("never displays a secret from the status payload", async () => {
    fetchMock.mockResolvedValue({ ok: true });
    vi.mocked(api.agentStatus).mockResolvedValue(statusPayload() as never);
    renderPage();
    await screen.findByText("test-model");

    expect(document.body.textContent).not.toContain("sk-");
    expect(screen.getAllByText(/YOUR_API_KEY/).length).toBeGreaterThan(0);
  });

  it("copies the placeholder template through the shared clipboard helper", async () => {
    fetchMock.mockResolvedValue({ ok: true });
    const spy = vi.spyOn(clipboard, "copyText").mockResolvedValue(true);
    renderPage();
    await screen.findByText("test-model");

    await userEvent.click(screen.getByRole("button", { name: /一键复制配置模板/ }));

    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    const copied = spy.mock.calls[0]?.[0] as string;
    expect(hasPlaceholderOnly(copied)).toBe(true);
  });

  it("shows setup steps with path, chmod, and loopback restart when unconfigured", async () => {
    fetchMock.mockRejectedValue(new Error("refused"));
    vi.mocked(api.agentStatus).mockResolvedValue(statusPayload({
      configured: false,
      model_name: null,
      base_url_host: null,
      builtin_skill_count: 0,
      mcp_server_count: 0,
      reason: "Agent 配置缺失或无效",
    }) as never);
    renderPage();

    expect(await screen.findByText(/Agent 配置缺失或无效/)).toBeInTheDocument();
    expect(screen.getByText(/mkdir -p ~\/\.vibe-research\/agent\/skills/)).toBeInTheDocument();
    expect(screen.getByText(/chmod 600/)).toBeInTheDocument();
    expect(screen.getByText(/langgraph dev --host 127\.0\.0\.1 --port 2024/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /一键复制配置模板/ })).toBeInTheDocument();
  });

  it("keeps the independent FastAPI access-key control", async () => {
    fetchMock.mockResolvedValue({ ok: true });
    renderPage();
    await screen.findByText("test-model");

    const input = screen.getByPlaceholderText(/VR_API_KEY/) as HTMLInputElement;
    await userEvent.type(input, "my-key");
    await userEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => expect(saveAccessKey).toHaveBeenCalledWith("my-key"));
  });

  it("uses the api client for the status summary, not raw fetch", async () => {
    fetchMock.mockResolvedValue({ ok: true });
    renderPage();
    await screen.findByText("test-model");

    expect(apiMock.agentStatus).toHaveBeenCalledTimes(1);
  });
});

function hasPlaceholderOnly(text: string): boolean {
  return text.includes('"apiKey": "YOUR_API_KEY"') && !text.includes("sk-");
}
