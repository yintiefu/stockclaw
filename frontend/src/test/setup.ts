import "@testing-library/jest-dom/vitest";

if (!globalThis.crypto) {
  Object.defineProperty(globalThis, "crypto", { value: {} });
}
if (!globalThis.crypto.randomUUID) {
  Object.defineProperty(globalThis.crypto, "randomUUID", {
    value: () => "00000000-0000-4000-8000-000000000001",
  });
}

// jsdom 没有 ResizeObserver，assistant-ui 需要
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
if (!globalThis.ResizeObserver) {
  Object.defineProperty(globalThis, "ResizeObserver", { value: ResizeObserverStub });
}

// jsdom 没有 matchMedia，Agent 页面用它判断桌面布局
if (!window.matchMedia) {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }),
  });
}

// jsdom 没有 scrollTo，assistant-ui 自动滚屏需要
if (typeof Element !== "undefined" && !Element.prototype.scrollTo) {
  Element.prototype.scrollTo = () => {};
}
if (typeof window !== "undefined" && window.HTMLElement && !window.HTMLElement.prototype.scrollTo) {
  window.HTMLElement.prototype.scrollTo = () => {};
}
