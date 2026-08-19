import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AgentSource } from "@/lib/agent/types";
import { SourceInspector } from "./SourceInspector";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("SourceInspector", () => {
  it("groups execution records and unverified model URLs while preserving stored order", () => {
    const sources: AgentSource[] = [
      { id: "url-1", kind: "model_url", url: "https://example.test/first", label: "第一条链接", created_at: "t1", verification: "model_provided_unverified" },
      { id: "tool-1", kind: "tool_execution", tool_call_id: "call-1", tool_name: "get_stock_quote", origin: "builtin", completed_at: "t2", arguments_summary: "代码 600519", result_summary: "收盘 1430", verification: "executed_record" },
      { id: "url-2", kind: "model_url", url: "https://example.test/second", label: null, created_at: "t3", verification: "model_provided_unverified" },
      { id: "tool-2", kind: "tool_execution", tool_call_id: "call-2", tool_name: "load_skill", origin: "skill", completed_at: "t4", arguments_summary: "财务框架", result_summary: "已读取", verification: "executed_record" },
    ];

    render(<SourceInspector sources={sources} truncated />);

    const execution = screen.getByRole("region", { name: "执行记录" });
    const modelUrls = screen.getByRole("region", { name: "模型提供，未验证" });
    expect(within(execution).getAllByRole("listitem").map((item) => item.textContent)).toEqual([
      expect.stringContaining("get_stock_quote"),
      expect.stringContaining("load_skill"),
    ]);
    expect(within(modelUrls).getAllByRole("listitem").map((item) => item.textContent)).toEqual([
      expect.stringContaining("第一条链接"),
      expect.stringContaining("https://example.test/second"),
    ]);
    expect(screen.getByText("来源记录已达到存储上限，列表可能被截断")).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/评分|排名|推荐|score|rank|stars?|quality/i);
  });

  it("links only HTTP and HTTPS URLs without fetching previews", () => {
    const request = vi.spyOn(globalThis, "fetch");
    const sources = [
      { id: "safe", kind: "model_url" as const, url: "https://example.test/report", label: null, created_at: "t1", verification: "model_provided_unverified" as const },
      { id: "unsafe", kind: "model_url" as const, url: "javascript:alert(1)", label: "不安全链接", created_at: "t2", verification: "model_provided_unverified" as const },
    ];

    render(<SourceInspector sources={sources} />);

    const safe = screen.getByRole("link", { name: "https://example.test/report" });
    expect(safe).toHaveAttribute("target", "_blank");
    expect(safe).toHaveAttribute("rel", "noopener noreferrer");
    expect(screen.queryByRole("link", { name: "不安全链接" })).toBeNull();
    expect(request).not.toHaveBeenCalled();
  });
});
