import test from "node:test";
import assert from "node:assert/strict";
import { parseSSEEvents, normalizeWorkflowEvent } from "../src/lib/workflow-stream.ts";

test("parseSSEEvents parses event: custom with JSON data chunks", () => {
  const raw = [
    "event: custom\n",
    'data: {"event":"stage_started","stage_id":"bull","run_id":"r1","seq":1,"created_at":"2026-08-25T00:00:00Z"}\n\n',
    "event: custom\n",
    'data: {"event":"stage_delta","stage_id":"bull","delta":"观点A","run_id":"r1","seq":2,"created_at":"2026-08-25T00:00:01Z"}\n\n',
  ].join("");

  const events = parseSSEEvents(raw);
  assert.equal(events.length, 2);
  assert.equal(events[0].event, "stage_started");
  assert.equal(events[0].stage_id, "bull");
  assert.equal(events[1].event, "stage_delta");
  assert.equal(events[1].delta, "观点A");
});

test("normalizeWorkflowEvent normalizes legacy and custom workflow events", () => {
  // custom event
  const customEv = {
    event: "stage_delta",
    stage_id: "bull",
    delta: "观点A",
    run_id: "r1",
    seq: 2,
    created_at: "2026-08-25T00:00:01Z",
  };
  const norm1 = normalizeWorkflowEvent(customEv);
  assert.equal(norm1.event, "stage_delta");
  assert.equal(norm1.stage_id, "bull");
  assert.equal(norm1.delta, "观点A");

  // legacy round delta event
  const legacyDelta = {
    type: "delta",
    role: "bull",
    content: "观点A",
  };
  const norm2 = normalizeWorkflowEvent(legacyDelta);
  assert.equal(norm2.event, "stage_delta");
  assert.equal(norm2.stage_id, "bull");
  assert.equal(norm2.delta, "观点A");
});
