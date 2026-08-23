import test from "node:test";
import assert from "node:assert/strict";
import { resolveConfig } from "vite";

test("Agent API proxy targets LangGraph root while legacy API stays on FastAPI", async () => {
  process.env.VITE_API_URL = "http://127.0.0.1:18890";
  process.env.VITE_AGENT_API_URL = "http://127.0.0.1:12024";
  const config = await resolveConfig({ command: "serve", mode: "test" }, "serve");
  assert.equal(config.server.proxy["/api"].target, "http://127.0.0.1:18890");
  assert.equal(config.server.proxy["/agent-api"].target, "http://127.0.0.1:12024");
  assert.equal(config.server.proxy["/agent-api"].rewrite("/agent-api/threads"), "/threads");
});
