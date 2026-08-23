/** Task 11：legacy vr-llm localStorage 契约保持不变（Agent 模型键已移除）。 */
import { describe, expect, it } from "vitest";

import { loadLlm } from "@/lib/llm";

describe("legacy model storage", () => {
  it("still reads the saved vr-llm configuration", () => {
    localStorage.setItem("vr-llm", JSON.stringify({
      provider: "openai",
      baseURL: "https://example.test/v1",
      model: "test-model",
      apiKey: "sk-legacy",
    }));
    const loaded = loadLlm();
    expect(loaded?.model).toBe("test-model");
    expect(loaded?.apiKey).toBe("sk-legacy");
    localStorage.clear();
  });
});
