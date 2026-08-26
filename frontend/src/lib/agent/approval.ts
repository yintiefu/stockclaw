/** 原生 LangChain HITL 中断载荷解析：action_requests 与 review_configs 严格同长配对。 */

export type HitlAction = {
  name: string;
  args: Record<string, unknown>;
  description: string;
  allowedDecisions: readonly string[];
};

export type HitlRequest = { actions: readonly HitlAction[] };

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

/**
 * 解析 HumanInTheLoopMiddleware 的聚合中断 value。只有当 action_requests 与
 * review_configs 是同长数组且逐项结构合法时才返回有序动作列表，否则 null（面板
 * 显示空态）。任何字段不合法都整体拒绝，不做部分渲染。
 */
export function parseHitlRequest(value: unknown): HitlRequest | null {
  const payload = asRecord(value);
  if (!payload) return null;
  const requests = payload.action_requests;
  const configs = payload.review_configs;
  if (!Array.isArray(requests) || !Array.isArray(configs)) return null;
  if (requests.length === 0 || requests.length !== configs.length) return null;

  const actions: HitlAction[] = [];
  for (let index = 0; index < requests.length; index += 1) {
    const request = asRecord(requests[index]);
    const config = asRecord(configs[index]);
    if (!request || !config) return null;
    const name = request.name;
    const description = request.description;
    const args = request.args;
    const allowed = config.allowed_decisions;
    if (typeof name !== "string" || !name) return null;
    if (typeof description !== "string") return null;
    if (!asRecord(args) && args !== undefined) return null;
    if (!Array.isArray(allowed) || !allowed.every((entry) => typeof entry === "string")) return null;
    actions.push({
      name,
      args: (asRecord(args) ?? {}) as Record<string, unknown>,
      description,
      allowedDecisions: allowed as string[],
    });
  }
  return { actions };
}
