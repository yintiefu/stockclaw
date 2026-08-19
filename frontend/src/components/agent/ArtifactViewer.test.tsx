import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ArtifactDetail, ArtifactMetadata } from "@/lib/agent/types";
import { ArtifactViewer } from "./ArtifactViewer";

const base = {
  schema_version: 1 as const,
  id: "artifact-2",
  thread_id: "thread-1",
  run_id: "run-2",
  title: "第二版",
  created_at: "2026-08-19T00:00:00Z",
  parent_artifact_id: "artifact-1",
  source_ids: [],
};

const metadata = (id: string, parent: string | null, title: string): ArtifactMetadata => ({
  id,
  thread_id: "thread-1",
  run_id: id === "artifact-1" ? "run-1" : "run-2",
  type: "markdown",
  title,
  created_at: "2026-08-19T00:00:00Z",
  parent_artifact_id: parent,
  source_count: 0,
  has_children: id === "artifact-1",
});

const renderViewer = (artifact: ArtifactDetail, overrides: Partial<React.ComponentProps<typeof ArtifactViewer>> = {}) => render(
  <ArtifactViewer
    artifact={artifact}
    versions={[metadata("artifact-1", null, "第一版"), metadata("artifact-2", "artifact-1", "第二版")]}
    hasChildren={false}
    onSelectVersion={vi.fn()}
    onDownload={vi.fn()}
    onDelete={vi.fn()}
    {...overrides}
  />,
);

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("ArtifactViewer", () => {
  it("renders markdown without raw HTML or remote resources and only keeps safe external links", () => {
    const request = vi.spyOn(globalThis, "fetch");
    const artifact: ArtifactDetail = {
      ...base,
      type: "markdown",
      content: {
        markdown: [
          "# 安全摘录",
          '<script src="https://tracker.invalid/script.js">alert(1)</script>',
          "![跟踪像素](https://tracker.invalid/pixel.png)",
          '<iframe src="https://tracker.invalid/frame"></iframe>',
          '<embed src="https://tracker.invalid/embed">',
          '<object data="https://tracker.invalid/object"></object>',
          '<svg><use href="https://tracker.invalid/icon.svg#x" /></svg>',
          '<style>@import url("https://tracker.invalid/style.css")</style>',
          "[公开资料](https://example.test/report)",
          "[不安全](javascript:alert(1))",
        ].join("\n\n"),
      },
    };

    const { container } = renderViewer(artifact);

    expect(screen.getByRole("heading", { name: "安全摘录" })).toBeInTheDocument();
    expect(container.querySelector(".agent-artifact-markdown")?.querySelector("script,iframe,img,embed,object,svg,style")).toBeNull();
    expect(request).not.toHaveBeenCalledWith(expect.stringContaining("tracker.invalid"), expect.anything());
    const link = screen.getByRole("link", { name: "公开资料" });
    expect(link).toHaveAttribute("href", "https://example.test/report");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(screen.queryByRole("link", { name: "不安全" })).toBeNull();
  });

  it("previews only the first 200 scalar table rows and reports the total", () => {
    const artifact: ArtifactDetail = {
      ...base,
      type: "table",
      content: {
        columns: [{ key: "index", label: "序号" }, { key: "value", label: "值" }],
        rows: Array.from({ length: 205 }, (_, index) => ({
          index: index + 1,
          value: index === 0 ? null : index % 2 === 0,
        })),
      },
    };

    renderViewer(artifact);

    expect(screen.getByText("显示前 200 行，共 205 行")).toBeInTheDocument();
    expect(within(screen.getByRole("table")).getAllByRole("row")).toHaveLength(201);
    expect(screen.getByRole("table")).toHaveTextContent("null");
    expect(screen.getByRole("table")).not.toHaveTextContent("205false");
  });

  it("renders JSON recursively as escaped data instead of document nodes", () => {
    const artifact: ArtifactDetail = {
      ...base,
      type: "json",
      content: { value: { '<img src="https://tracker.invalid/json.png">': [true, null, "<script>alert(1)</script>"] } },
    };

    const { container } = renderViewer(artifact);

    expect(screen.getByText('<img src="https://tracker.invalid/json.png">')).toBeInTheDocument();
    expect(screen.getByText("<script>alert(1)</script>")).toBeInTheDocument();
    expect(container.querySelector("img,script")).toBeNull();
  });

  it("renders Sources Artifact notes as text", () => {
    const artifact: ArtifactDetail = {
      ...base,
      type: "sources",
      source_ids: ["source-1"],
      content: { items: [{ source_id: "source-1", note: '<img src="https://tracker.invalid/note.png">' }] },
    };

    const { container } = renderViewer(artifact);

    expect(screen.getByText('<img src="https://tracker.invalid/note.png">')).toBeInTheDocument();
    expect(container.querySelector("img")).toBeNull();
  });

  it("navigates immutable versions and exposes deletion only for a leaf", async () => {
    const onSelectVersion = vi.fn();
    const onDelete = vi.fn();
    const artifact: ArtifactDetail = { ...base, type: "markdown", content: { markdown: "正文" } };
    const user = userEvent.setup();
    const { rerender } = renderViewer(artifact, { onSelectVersion, onDelete });

    await user.click(screen.getByRole("button", { name: "查看版本：第一版" }));
    expect(onSelectVersion).toHaveBeenCalledWith("artifact-1");
    expect(screen.getByRole("button", { name: "删除 Artifact" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "删除 Artifact" }));
    expect(onDelete).toHaveBeenCalledTimes(1);

    rerender(
      <ArtifactViewer
        artifact={{ ...artifact, id: "artifact-1", title: "第一版", parent_artifact_id: null }}
        versions={[metadata("artifact-1", null, "第一版"), metadata("artifact-2", "artifact-1", "第二版")]}
        hasChildren
        onSelectVersion={onSelectVersion}
        onDownload={vi.fn()}
        onDelete={onDelete}
      />,
    );
    expect(screen.queryByRole("button", { name: "删除 Artifact" })).toBeNull();
    expect(screen.getByText("存在后续版本，不能删除")).toBeInTheDocument();
  });
});
