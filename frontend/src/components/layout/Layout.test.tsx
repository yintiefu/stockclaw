import { act, cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useNavigate } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Layout } from "./Layout";

let viewportWidth = 1280;
const listeners = new Set<(event: MediaQueryListEvent) => void>();

function matchesWidth(query: string) {
  if (query === "(max-width: 1399px)") return viewportWidth <= 1399;
  if (query === "(min-width: 1280px) and (max-width: 1399px)") {
    return viewportWidth >= 1280 && viewportWidth <= 1399;
  }
  return false;
}

function Content() {
  const navigate = useNavigate();
  return <button onClick={() => navigate("/daily-review")}>离开工作台</button>;
}

function renderLayout(path = "/agent") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route element={<Layout />}>
          <Route path="agent" element={<Content />} />
          <Route path="daily-review" element={<div>普通页面</div>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  localStorage.clear();
  localStorage.setItem("vr-sidebar", "expanded");
  viewportWidth = 1280;
  listeners.clear();
  vi.stubGlobal("matchMedia", vi.fn().mockImplementation((query: string) => ({
    matches: matchesWidth(query),
    media: query,
    onchange: null,
    addEventListener: (_: string, listener: (event: MediaQueryListEvent) => void) => listeners.add(listener),
    removeEventListener: (_: string, listener: (event: MediaQueryListEvent) => void) => listeners.delete(listener),
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => true,
  })));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Layout Agent 工作台外壳", () => {
  it("Agent 路由移除普通内容宽度与外层滚动链", () => {
    renderLayout();
    expect(screen.getByRole("main")).toHaveClass("min-w-0", "overflow-hidden");
    expect(screen.getByRole("main").firstElementChild).toBe(screen.getByRole("button", { name: "离开工作台" }));
    expect(screen.getByRole("main").querySelector(".max-w-6xl")).toBeNull();
  });

  it("Agent 尾斜杠路由也移除普通内容宽度与外层滚动链", () => {
    renderLayout("/agent/");
    expect(screen.getByRole("main")).toHaveClass("min-w-0", "overflow-hidden");
    expect(screen.getByRole("main").querySelector(".max-w-6xl")).toBeNull();
  });

  it("1280px 临时折叠导航但不改偏好，离开后恢复", async () => {
    const user = userEvent.setup();
    renderLayout();
    expect(screen.getByRole("complementary")).toHaveClass("w-14");
    expect(localStorage.getItem("vr-sidebar")).toBe("expanded");
    await user.click(screen.getByRole("button", { name: "离开工作台" }));
    expect(screen.getByRole("complementary")).toHaveClass("w-60");
    expect(localStorage.getItem("vr-sidebar")).toBe("expanded");
  });

  it("无既有偏好时 1280px Agent 首次渲染不创建 sidebar key", () => {
    localStorage.removeItem("vr-sidebar");
    renderLayout();
    expect(screen.getByRole("complementary")).toHaveClass("w-14");
    expect(localStorage.getItem("vr-sidebar")).toBeNull();
  });

  it("无既有偏好时 390px Agent 强制折叠且不创建 sidebar key", () => {
    localStorage.removeItem("vr-sidebar");
    viewportWidth = 390;
    renderLayout();
    expect(screen.getByRole("complementary")).toHaveClass("w-14");
    expect(localStorage.getItem("vr-sidebar")).toBeNull();
  });

  it("用户主动切换导航时仍持久化偏好", async () => {
    localStorage.removeItem("vr-sidebar");
    const user = userEvent.setup();
    renderLayout("/daily-review");
    await user.click(screen.getByTitle("收起"));
    expect(localStorage.getItem("vr-sidebar")).toBe("collapsed");
  });

  it("视口加宽后恢复已保存导航偏好", () => {
    renderLayout();
    viewportWidth = 1440;
    act(() => listeners.forEach((listener) => listener({ matches: false } as MediaQueryListEvent)));
    expect(screen.getByRole("complementary")).toHaveClass("w-60");
    expect(localStorage.getItem("vr-sidebar")).toBe("expanded");
  });
});
