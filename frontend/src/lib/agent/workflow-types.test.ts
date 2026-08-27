import { describe, expect, it } from "vitest";
import {
  effectiveWorkflowStatus,
  messageText,
  parseDossierProgress,
  stageContent,
  type WorkflowState,
} from "./workflow-types";

const state: WorkflowState = {
  workflow_status: "completed",
  stages: {
    bull: { id: "bull", status: "completed", message_id: "m1" },
    bear: { id: "bear", status: "failed" },
  },
  messages: [
    { id: "m1", content: "多方正文" },
    { id: "m2", content: [{ type: "text", text: "裁" }, { type: "text", text: "判" }] },
  ],
};

describe("workflow v2 contract", () => {
  it("resolves stage content via message pointer", () => {
    expect(stageContent(state, "bull")).toBe("多方正文");
    expect(stageContent(state, "bear")).toBeNull();
    expect(stageContent(state, "nope")).toBeNull();
  });

  it("joins only text blocks", () => {
    expect(messageText(state.messages![1])).toBe("裁判");
  });

  it("parses dossier progress payloads and rejects others", () => {
    expect(parseDossierProgress({ type: "dossier.progress", section_id: "q", section_status: "completed", completed: 1, total: 13 }))
      .toEqual({ type: "dossier.progress", section_id: "q", section_status: "completed", completed: 1, total: 13 });
    expect(parseDossierProgress({ type: "stage.delta" })).toBeNull();
    expect(parseDossierProgress(null)).toBeNull();
  });

  it("keeps effective status derivation (terminal beats stale busy)", () => {
    expect(effectiveWorkflowStatus("busy", "pending")).toBe("running");
    expect(effectiveWorkflowStatus("busy", "running")).toBe("running");
    // C2 回归：restore 读到的 busy 可能已过时（attach 的 run 早已完成），
    // 终态必须压过陈旧 busy，否则页面永远「生成中」。
    expect(effectiveWorkflowStatus("busy", "completed")).toBe("completed");
    expect(effectiveWorkflowStatus("busy", "failed")).toBe("failed");
    expect(effectiveWorkflowStatus("interrupted", "running")).toBe("interrupted");
    expect(effectiveWorkflowStatus("idle", "completed")).toBe("completed");
  });
});
