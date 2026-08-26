import { describe, expect, it, vi } from "vitest";
import {
  applyWorkflowCheckpoint,
  initialWorkflowStreamState,
  reduceWorkflowStream,
} from "./workflow-stream";
import type { StageDeltaEvent, WorkflowState } from "./workflow-types";

const delta = (seq: number, text: string, stageId = "bull"): StageDeltaEvent => ({
  type: "stage.delta", workflow_id: "debate", run_id: "run-1", seq,
  emitted_at: "2026-08-25T12:00:00Z", stage_id: stageId, delta: text,
});

describe("workflow stream reducer", () => {
  it("accepts expected sequence and ignores duplicate or older events", () => {
    const one = reduceWorkflowStream(initialWorkflowStreamState(), delta(1, "A"));
    const duplicate = reduceWorkflowStream(one, delta(1, "B"));
    const older = reduceWorkflowStream(duplicate, delta(0, "C"));
    expect(older.lastSeq).toBe(1);
    expect(older.transient).toEqual({ bull: "A" });
  });

  it("clears and dirties a stage on a sequence gap, then ignores its later deltas", () => {
    const one = reduceWorkflowStream(initialWorkflowStreamState(), delta(1, "partial"));
    const gap = reduceWorkflowStream(one, delta(3, "missing middle"));
    const later = reduceWorkflowStream(gap, delta(4, "tail"));
    expect(later.transient.bull).toBe("");
    expect(later.dirtyStages).toContain("run-1:bull");
    expect(later.lastSeq).toBe(4);
  });

  it("attributes a gap to the stage active before a new stage.started event", () => {
    const started = reduceWorkflowStream(initialWorkflowStreamState(), {
      type: "stage.started", workflow_id: "debate", run_id: "run-1", seq: 1,
      emitted_at: "2026-08-25T12:00:00Z", stage_id: "bull", label: "多方",
    });
    const partial = reduceWorkflowStream(started, delta(2, "partial"));
    const gap = reduceWorkflowStream(partial, {
      type: "stage.started", workflow_id: "debate", run_id: "run-1", seq: 4,
      emitted_at: "2026-08-25T12:00:02Z", stage_id: "bear", label: "空方",
    });
    expect(gap.currentStage).toBe("bear");
    expect(gap.transient.bull).toBe("");
    expect(gap.dirtyStages).toContain("run-1:bull");
    expect(gap.dirtyStages).not.toContain("run-1:bear");
  });

  it("uses run-level dirty state when a gap has no preceding active stage", () => {
    const gap = reduceWorkflowStream(initialWorkflowStreamState(), {
      type: "stage.started", workflow_id: "debate", run_id: "run-1", seq: 3,
      emitted_at: "2026-08-25T12:00:02Z", stage_id: "bull", label: "多方",
    });
    const later = reduceWorkflowStream(gap, delta(4, "unsafe"));
    expect(later.dirtyRuns).toContain("run-1");
    expect(later.transient.bull).toBeUndefined();
  });

  it("clears dirty state only when an authoritative checkpoint replaces it", () => {
    const partial = reduceWorkflowStream(initialWorkflowStreamState(), delta(1, "partial"));
    const dirty = reduceWorkflowStream(partial, delta(3, "tail"));
    const checkpoint = { workflow_status: "running", event_run_id: "run-1", event_seq: 3, stages: {
      bull: { id: "bull", status: "completed", content: "authoritative" },
    } } as WorkflowState;
    const recovered = applyWorkflowCheckpoint(dirty, checkpoint);
    expect(recovered.dirtyStages).not.toContain("run-1:bull");
    expect(recovered.transient.bull).toBeUndefined();
    expect(recovered.checkpoint?.stages.bull.content).toBe("authoritative");
  });

  it("synchronizes current stage and advances sequence from checkpoint without regression", () => {
    const streamed = reduceWorkflowStream(initialWorkflowStreamState(), delta(5, "partial"));
    const checkpoint = { workflow_status: "running", event_run_id: "run-1", current_stage: "bear", event_seq: 9, stages: {
      bull: { id: "bull", status: "completed", content: "authoritative" },
      bear: { id: "bear", status: "running" },
    } } as WorkflowState;
    const reconciled = applyWorkflowCheckpoint(streamed, checkpoint);
    const oldEvent = reduceWorkflowStream(reconciled, delta(8, "old", "bear"));
    expect(reconciled.currentStage).toBe("bear");
    expect(reconciled.lastSeq).toBe(9);
    expect(oldEvent).toBe(reconciled);

    const olderCheckpoint = { ...checkpoint, event_seq: 3 };
    expect(applyWorkflowCheckpoint(reconciled, olderCheckpoint).lastSeq).toBe(9);
  });

  it("requests checkpoint reconciliation for stage completion without promoting delta", () => {
    const streamed = reduceWorkflowStream(initialWorkflowStreamState(), delta(1, "temporary"));
    const completed = reduceWorkflowStream(streamed, {
      type: "stage.completed", workflow_id: "debate", run_id: "run-1", seq: 2,
      emitted_at: "2026-08-25T12:00:01Z", stage_id: "bull", truncated: false,
    });
    expect(completed.checkpointRequired).toBe(true);
    expect(completed.checkpoint?.stages.bull).toBeUndefined();

    const checkpoint = { workflow_id: "debate", workflow_status: "running", event_run_id: "run-1", event_seq: 2, stages: {
      bull: { id: "bull", status: "completed", content: "authoritative" },
    } } as WorkflowState;
    const reconciled = applyWorkflowCheckpoint(completed, checkpoint);
    expect(reconciled.checkpoint).toBe(checkpoint);
    expect(reconciled.transient.bull).toBeUndefined();
    expect(reconciled.checkpointRequired).toBe(false);
  });

  it("keeps transient content while checkpoint is stale, then atomically replaces it", () => {
    const streamed = reduceWorkflowStream(initialWorkflowStreamState(), delta(1, "temporary"));
    const completed = reduceWorkflowStream(streamed, {
      type: "stage.completed", workflow_id: "debate", run_id: "run-1", seq: 2,
      emitted_at: "2026-08-25T12:00:01Z", stage_id: "bull", truncated: false,
    });
    const stale = { workflow_status: "running", event_run_id: "run-1", current_stage: "bull", event_seq: 1, stages: {
      bull: { id: "bull", status: "running", content: null },
    } } as WorkflowState;
    expect(applyWorkflowCheckpoint(completed, stale)).toBe(completed);
    expect(completed.transient.bull).toBe("temporary");
    expect(completed.checkpointRequired).toBe(true);

    const fresh = { workflow_status: "running", event_run_id: "run-1", current_stage: "bull", event_seq: 2, stages: {
      bull: { id: "bull", status: "completed", content: "authoritative" },
    } } as WorkflowState;
    const reconciled = applyWorkflowCheckpoint(completed, fresh);
    expect(reconciled.transient.bull).toBeUndefined();
    expect(reconciled.checkpoint?.stages.bull.content).toBe("authoritative");
    expect(reconciled.checkpointRequired).toBe(false);
  });

  it("puts malformed events into recoverable state without fabricating delta", () => {
    const state = reduceWorkflowStream(initialWorkflowStreamState(), {
      kind: "error", error: { code: "MALFORMED_WORKFLOW_EVENT", message: "bad", retryable: true },
    });
    expect(state.recoverableError?.code).toBe("MALFORMED_WORKFLOW_EVENT");
    expect(state.transient).toEqual({});
  });

  it("isolates a new run from an old run checkpoint and sequence", () => {
    const runA = reduceWorkflowStream(initialWorkflowStreamState("run-A", 49), {
      type: "stage.completed", workflow_id: "debate", run_id: "run-A", seq: 50,
      emitted_at: "2026-08-25T12:00:00Z", stage_id: "bull", truncated: false,
    });
    const runB = reduceWorkflowStream(runA, {
      type: "stage.started", workflow_id: "debate", run_id: "run-B", seq: 1,
      emitted_at: "2026-08-25T12:01:00Z", stage_id: "bear", label: "空方",
    });
    expect(runB).toMatchObject({
      runId: "run-B", lastSeq: 1, checkpoint: null, checkpointRequired: false,
      transient: {}, dirtyStages: [], dirtyRuns: [], pendingCheckpointStages: [],
    });

    const oldA = { workflow_status: "completed", event_run_id: "run-A", event_seq: 50, stages: {
      bull: { id: "bull", status: "completed", content: "old" },
    } } as WorkflowState;
    expect(applyWorkflowCheckpoint(runB, oldA)).toBe(runB);
    const continued = reduceWorkflowStream(runB, {
      type: "stage.delta", workflow_id: "debate", run_id: "run-B", seq: 2,
      emitted_at: "2026-08-25T12:01:01Z", stage_id: "bear", delta: "new",
    });
    expect(continued.lastSeq).toBe(2);
    expect(continued.transient.bear).toBe("new");
  });
});

