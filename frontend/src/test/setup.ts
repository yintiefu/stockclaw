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
