import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it, vi } from "vitest";
import { effectiveWorkflowStatus, parseWorkflowEvent } from "./workflow-types";

describe("workflow event contract", () => {
  it("accepts all nine Python contract examples", () => {
    const path = resolve(process.cwd(), "../docs/contracts/workflow-custom-events.json");
    const examples = JSON.parse(readFileSync(path, "utf8")) as unknown[];
    expect(examples).toHaveLength(9);
    expect(examples.map(parseWorkflowEvent).map((result) => result.kind)).toEqual(
      Array.from({ length: 9 }, () => "event"),
    );
  });

  it("logs and ignores unknown event types", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const result = parseWorkflowEvent({ type: "future.event", seq: 1 });
    expect(result).toEqual({ kind: "ignored", type: "future.event" });
    expect(warn).toHaveBeenCalledOnce();
  });

  it("returns a recoverable parse error for missing required fields", () => {
    const result = parseWorkflowEvent({
      type: "stage.delta", workflow_id: "debate", run_id: "run-1", seq: 1,
      emitted_at: "2026-08-25T12:00:00Z", stage_id: "bull",
    });
    expect(result.kind).toBe("error");
    if (result.kind === "error") {
      expect(result.error).toMatchObject({ code: "MALFORMED_WORKFLOW_EVENT", retryable: true });
    }
  });

  it("rejects extra fields like Python extra=forbid", () => {
    const result = parseWorkflowEvent({
      type: "stage.delta", workflow_id: "debate", run_id: "run-1", seq: 1,
      emitted_at: "2026-08-25T12:00:00Z", stage_id: "bull", delta: "text",
      secret: "must-not-pass",
    });
    expect(result.kind).toBe("error");
    if (result.kind === "error") {
      expect(result.error.code).toBe("MALFORMED_WORKFLOW_EVENT");
      expect(result.error.message).not.toContain("must-not-pass");
    }
  });
});

describe("effectiveWorkflowStatus", () => {
  it.each([
    ["busy", "pending", "running"],
    ["idle", "completed", "completed"],
    ["error", "completed", "completed"],
    ["interrupted", "partial", "partial"],
    ["busy", "completed", "running"],
    ["busy", "interrupted", "running"],
    ["busy", "cancelled", "running"],
    ["idle", "running", "interrupted"],
    ["interrupted", "running", "interrupted"],
    ["error", "running", "failed"],
    ["idle", "cancelled", "cancelled"],
  ] as const)("maps %s + %s to %s", (threadStatus, workflowStatus, expected) => {
    expect(effectiveWorkflowStatus(threadStatus, workflowStatus)).toBe(expected);
  });
});
