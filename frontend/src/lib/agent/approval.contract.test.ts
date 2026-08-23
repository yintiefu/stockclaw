/** 原生 LangChain HITL 中断解析：action_requests + review_configs 严格同长配对。 */
import { describe, expect, it } from "vitest";

import { parseHitlRequest } from "./approval";

const VALID = {
  action_requests: [
    { name: "fixture_echo", args: { value: "a" }, description: "审批 A" },
    { name: "fixture_echo", args: { value: "b" }, description: "审批 B" },
  ],
  review_configs: [
    { action_name: "fixture_echo", allowed_decisions: ["approve", "reject"] },
    { action_name: "fixture_echo", allowed_decisions: ["approve", "reject"] },
  ],
};

describe("parseHitlRequest", () => {
  it("returns ordered actions paired with review configs", () => {
    const request = parseHitlRequest(VALID);
    expect(request).not.toBeNull();
    expect(request!.actions).toHaveLength(2);
    expect(request!.actions[0]).toEqual({
      name: "fixture_echo",
      args: { value: "a" },
      description: "审批 A",
      allowedDecisions: ["approve", "reject"],
    });
    expect(request!.actions[1].args).toEqual({ value: "b" });
  });

  it("rejects mismatched lengths", () => {
    expect(parseHitlRequest({
      ...VALID,
      review_configs: VALID.review_configs.slice(0, 1),
    })).toBeNull();
  });

  it("rejects missing or non-array sections", () => {
    expect(parseHitlRequest(undefined)).toBeNull();
    expect(parseHitlRequest(null)).toBeNull();
    expect(parseHitlRequest("text")).toBeNull();
    expect(parseHitlRequest({})).toBeNull();
    expect(parseHitlRequest({ action_requests: VALID.action_requests })).toBeNull();
    expect(parseHitlRequest({ review_configs: VALID.review_configs })).toBeNull();
    expect(parseHitlRequest({ action_requests: {}, review_configs: [] })).toBeNull();
  });

  it("rejects malformed entries inside the arrays", () => {
    expect(parseHitlRequest({
      action_requests: [{ name: "x", args: {}, description: "d" }, null],
      review_configs: VALID.review_configs,
    })).toBeNull();
    expect(parseHitlRequest({
      action_requests: VALID.action_requests,
      review_configs: [{ action_name: "x" }, { action_name: "y" }],
    })).toBeNull();
  });

  it("returns null for an empty interrupt pair", () => {
    expect(parseHitlRequest({ action_requests: [], review_configs: [] })).toBeNull();
  });
});
