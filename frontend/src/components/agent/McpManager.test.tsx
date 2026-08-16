/** Task 1C-10：McpManager —— server/transport/trust/test/refresh/tool 控件。 */
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  getMcp: vi.fn(),
  addMcp: vi.fn(),
  patchMcp: vi.fn(),
  deleteMcp: vi.fn(),
  trustMcp: vi.fn(),
  testMcp: vi.fn(),
  refreshMcp: vi.fn(),
}));
vi.mock("@/lib/agent/api", () => ({ agentApi: api }));

import { McpManager } from "./McpManager";
import type { McpDocument, McpToolEntry } from "@/lib/agent/types";

const tool = (name: string, enabled: boolean): McpToolEntry => ({
  original_name: name,
  alias: `mcp__fixture__${name}`,
  description: `${name} 工具`,
  input_schema: {},
  enabled,
  discovered_at: "t",
});

const doc = (revision: number, extra: Partial<McpDocument["servers"][number]> = {}): McpDocument => ({
  schema_version: 1,
  revision,
  servers: [{
    id: "fixture",
    display_name: "本地夹具",
    enabled: true,
    transport: { type: "stdio", executable: "python", args: ["srv.py"], env: {} },
    trust_fingerprint: null,
    trusted_at: null,
    tools: [tool("echo", false), tool("sleep", false)],
    health: { state: "unknown", detail: "", checked_at: "" },
    ...extra,
  }],
});

beforeEach(() => {
  vi.clearAllMocks();
  api.getMcp.mockResolvedValue(doc(3));
});
afterEach(cleanup);

describe("McpManager", () => {
  it("列出 server、完整命令与工具开关", async () => {
    render(<McpManager onReload={vi.fn()} disabled={false} />);
    expect(await screen.findByText("本地夹具")).toBeInTheDocument();
    expect(screen.getByText(/python srv\.py/)).toBeInTheDocument(); // 完整命令
    expect(screen.getByRole("checkbox", { name: /echo/ })).toBeInTheDocument();
  });

  it("显示 env/header 名而不显示值", async () => {
    api.getMcp.mockResolvedValue({
      ...doc(3),
      servers: [{
        ...doc(3).servers[0],
        transport: {
          type: "stdio", executable: "npx", args: ["x"],
          env: { TOKEN: { from_env: "VR_TOKEN" } },
        },
      }],
    });
    render(<McpManager onReload={vi.fn()} disabled={false} />);
    expect(await screen.findByText(/VR_TOKEN/)).toBeInTheDocument();
  });

  it("trust：显示指纹并提交确认", async () => {
    const user = userEvent.setup();
    api.trustMcp.mockResolvedValue(doc(4, { trust_fingerprint: "fp-1" }));
    api.testMcp.mockRejectedValueOnce(Object.assign(new Error("需要信任"), {
      status: 409,
      code: "STDIO_TRUST_REQUIRED",
      preview: {
        executable: "python", resolved_executable: "/usr/bin/python",
        args: ["srv.py"], fingerprint: "fp-1",
      },
    }));
    render(<McpManager onReload={vi.fn()} disabled={false} />);
    await screen.findByText("本地夹具");
    await user.click(screen.getByRole("button", { name: /信任/ }));
    // 先显示完整命令与指纹，再由用户确认
    const previewText = await screen.findByText(/fingerprint/);
    expect(previewText).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /确认信任此命令/ }));
    await waitFor(() => expect(api.trustMcp).toHaveBeenCalled());
    const args = api.trustMcp.mock.calls[0];
    expect(args[0]).toBe("fixture");
    expect(args[2]).toBe("fp-1"); // 页面展示的指纹
  });

  it("test/refresh 调用对应 API 并刷新", async () => {
    const onReload = vi.fn();
    const user = userEvent.setup();
    api.testMcp.mockResolvedValue({ ...doc(3), health: { state: "ok", detail: "tools=2", checked_at: "t" } });
    api.refreshMcp.mockResolvedValue(doc(5));
    render(<McpManager onReload={onReload} disabled={false} />);
    await screen.findByText("本地夹具");
    await user.click(screen.getByRole("button", { name: /测试连接/ }));
    await waitFor(() => expect(api.testMcp).toHaveBeenCalledWith("fixture", 3));
    await user.click(screen.getByRole("button", { name: /刷新目录/ }));
    await waitFor(() => expect(api.refreshMcp).toHaveBeenCalledWith("fixture", expect.any(Number)));
    expect(onReload).toHaveBeenCalled();
  });

  it("工具启停走 PATCH tool_enabled", async () => {
    const user = userEvent.setup();
    api.patchMcp.mockResolvedValue(doc(4));
    render(<McpManager onReload={vi.fn()} disabled={false} />);
    await screen.findByText("本地夹具");
    await user.click(screen.getByRole("checkbox", { name: /echo/ }));
    await waitFor(() => expect(api.patchMcp).toHaveBeenCalledWith(
      "fixture", 3, { tool_enabled: { echo: true } }));
  });

  it("409 冲突丢弃并重载一次", async () => {
    const onReload = vi.fn();
    const user = userEvent.setup();
    api.patchMcp.mockRejectedValueOnce(
      Object.assign(new Error("revision 冲突"), { status: 409, code: "MCP_REVISION_CONFLICT" }));
    render(<McpManager onReload={onReload} disabled={false} />);
    await screen.findByText("本地夹具");
    await user.click(screen.getByRole("checkbox", { name: /echo/ }));
    await waitFor(() => expect(onReload).toHaveBeenCalledTimes(1));
  });

  it("新增 server 表单：分段式 transport 控件", async () => {
    const user = userEvent.setup();
    api.addMcp.mockResolvedValue(doc(1));
    render(<McpManager onReload={vi.fn()} disabled={false} />);
    await screen.findByText("本地夹具");
    await user.click(screen.getByRole("button", { name: /新增 server/ }));
    expect(screen.getByRole("radio", { name: /stdio/ })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /Streamable HTTP/ })).toBeInTheDocument();
  });

  it("disabled 时全部操作禁用", async () => {
    render(<McpManager onReload={vi.fn()} disabled />);
    await screen.findByText("本地夹具");
    expect(screen.getByRole("button", { name: /测试连接/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: /刷新目录/ })).toBeDisabled();
  });
});
