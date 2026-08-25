import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { parseSSEEvents, normalizeWorkflowEvent } from "../src/lib/workflow-stream.ts";

const CONTRACT_PATH = new URL("../../docs/contracts/workflow-custom-events.json", import.meta.url);

test("normalizeWorkflowEvent correctly validates all 9 contract fixture events", async () => {
  const rawData = await readFile(CONTRACT_PATH, "utf8");
  const contractEvents = JSON.parse(rawData);
  assert.equal(contractEvents.length, 9, "Contract fixture must have 9 events");

  const typesSeen = new Set();
  for (const raw of contractEvents) {
    const normalized = normalizeWorkflowEvent(raw);
    assert.equal(normalized.type, raw.type, `Event type mismatch for ${raw.type}`);
    assert.equal(normalized.workflow_id, raw.workflow_id);
    assert.equal(normalized.run_id, raw.run_id);
    assert.equal(normalized.seq, raw.seq);
    assert.equal(normalized.emitted_at, raw.emitted_at);
    typesSeen.add(normalized.type);
  }

  assert.deepEqual(
    Array.from(typesSeen).sort(),
    [
      "dossier_completed",
      "dossier_progress",
      "stage_completed",
      "stage_delta",
      "stage_failed",
      "stage_started",
      "workflow_completed",
      "workflow_failed",
      "workflow_started",
    ].sort(),
  );
});

test("parseSSEEvents parses event: custom with contract JSON data chunks", () => {
  const raw = [
    "event: custom\n",
    'data: {"type":"stage_started","stage_id":"bull","workflow_id":"debate","run_id":"r1","seq":1,"emitted_at":"2026-08-25T00:00:00Z"}\n\n',
    "event: custom\n",
    'data: {"type":"stage_delta","stage_id":"bull","delta":"观点A","workflow_id":"debate","run_id":"r1","seq":2,"emitted_at":"2026-08-25T00:00:01Z"}\n\n',
  ].join("");

  const events = parseSSEEvents(raw);
  assert.equal(events.length, 2);
  assert.equal(events[0].type, "stage_started");
  assert.equal(events[0].stage_id, "bull");
  assert.equal(events[1].type, "stage_delta");
  assert.equal(events[1].delta, "观点A");
});

test("normalizeWorkflowEvent normalizes legacy NDJSON events correctly", () => {
  const legacyDelta = {
    type: "delta",
    role: "bull",
    content: "观点A",
  };
  const norm = normalizeWorkflowEvent(legacyDelta);
  assert.equal(norm.type, "stage_delta");
  assert.equal(norm.stage_id, "bull");
  assert.equal(norm.delta, "观点A");
});
