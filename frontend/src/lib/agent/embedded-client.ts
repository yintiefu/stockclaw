// 页面级 Ask-AI 嵌入式客户端：与 embedded_agent 图通信（LangGraph SDK）。
// 传输层由 @langchain/react 的 useStream 承担：token 级流式增量与权威 checkpoint
// 在 hook 内合并；关抽屉 / 换 scope / 卸载只断开本地订阅，Server run 继续跑完
// 并落 checkpoint。本模块只保留域逻辑：
// - 会话按 (route, scopeKey) 隔离在 Server checkpoint 中：打开抽屉只按精确
//   metadata 搜索恢复，绝不创建空 thread；首次发送才创建（metadata 仅
//   channel/route/scope_key/title，不含任何密钥）；「清空本页对话」删除精确匹配的 thread；
// - submit 输入构造（messages + page_context，缺字段在本地即拒绝）；
// - 流式消息 → 抽屉消息映射（工具芯片合并、reasoning 块跳过）。
// 旧版浏览器对话键与模型配置键不读取、不迁移、不删除。

import type { Client, Thread } from "@langchain/langgraph-sdk";
import { langGraphClient } from "./thread-adapter.ts";

/** 工厂只依赖 SDK Client 的子集，测试注入小型替身即可覆盖全部契约。 */
export type EmbeddedClientSubset = {
  threads: Pick<Client["threads"], "create" | "search" | "delete">;
};

export interface EmbeddedPageContext {
  /** 页面数据的口径时间（前端数据源时间），与 Server 盖章的 captured_at 分离。 */
  sourceAsOf: string;
  /** 当前页面快照正文，必须非空。 */
  content: string;
}

export interface EmbeddedToolUse {
  name: string;
  arg: string;
}

export interface EmbeddedMessage {
  id?: string;
  role: "user" | "assistant";
  content: string;
  tools?: EmbeddedToolUse[];
}

/** submit 输入参数（AskAiButton 组装，缺字段在本地即拒绝，不发起 SDK 调用）。 */
export interface EmbeddedSubmitParams {
  route: string;
  /** 同一路由内的细分（如个股代码）；缺省归一化为 route 本身，保证 scope_key 非空。 */
  scopeKey?: string;
  pageContext: EmbeddedPageContext;
  message: string;
}

/** embedded_agent 图的 submit 输入：用户消息 + 完整页面快照（每次提交都带）。
 *  用 type 而非 interface：需要隐式索引签名才能赋给 useStream 的 Partial<StateType>。 */
export type EmbeddedSubmitInput = {
  messages: Array<{ role: "user"; content: string }>;
  page_context: { route: string; scope_key: string; source_as_of: string; content: string };
};

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const asString = (value: unknown): string =>
  typeof value === "string" ? value : "";

/** scope_key 必须非空：无细分 scope 的页面直接用 route 隔离。 */
function normalizeScope(route: string, scopeKey?: string): string {
  const scope = (scopeKey ?? "").trim();
  return scope || route.trim();
}

/** 工具参数压成短展示串（旧 UI 的工具芯片只显示代码等关键参数）。 */
function toolArgText(args: unknown): string {
  if (!isRecord(args)) return "";
  const values = Object.values(args).flatMap((value) =>
    Array.isArray(value) ? value.map(String) : typeof value === "object" ? [] : [String(value)]);
  return values.filter((part) => part && part !== "[object Object]").join(" ").slice(0, 40);
}

/** LangChain 消息 content 可能是字符串或 content block 数组；只拼 text 块，
 *  thinking/reasoning 块是思考过程，不属于回答正文。 */
function contentText(content: unknown): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content.map((block) =>
      isRecord(block) && typeof block.text === "string" ? block.text : "",
    ).join("");
  }
  return "";
}

/** 消息类型：流式视图里是 BaseMessage 类实例（getType()），序列化 dict 里是 type 字段。 */
function messageType(raw: object): string {
  const candidate = raw as { getType?: unknown; type?: unknown };
  if (typeof candidate.getType === "function") {
    try {
      const type = (candidate.getType as () => unknown)();
      if (typeof type === "string") return type;
    } catch {
      /* 落回 dict 形状 */
    }
  }
  return asString(candidate.type);
}

function toolUsesOf(message: Record<string, unknown>): EmbeddedToolUse[] | undefined {
  const calls = message.tool_calls;
  if (!Array.isArray(calls) || calls.length === 0) return undefined;
  const tools = calls.filter(isRecord).map((call) => ({
    name: asString(call.name),
    arg: toolArgText(call.args),
  })).filter((tool) => tool.name);
  return tools.length ? tools : undefined;
}

/** 流式/权威消息（BaseMessage 类实例或序列化 dict）→ 抽屉消息：
 *  human→user、ai→assistant，跳过 system/tool 中间态；带工具调用的空正文 AI
 *  消息与最终回答合并成一个气泡（工具芯片挂在回答气泡上，与流式展示一致）。 */
export function fromBaseMessages(messages: unknown): EmbeddedMessage[] {
  const list = Array.isArray(messages) ? messages : [];
  const mapped: EmbeddedMessage[] = [];
  for (const raw of list) {
    if (!isRecord(raw)) continue;
    const type = messageType(raw);
    const content = contentText(raw.content);
    if (type === "human") {
      mapped.push({ id: asString(raw.id) || undefined, role: "user", content });
    } else if (type === "ai") {
      const tools = toolUsesOf(raw);
      const previous = mapped[mapped.length - 1];
      if (previous && previous.role === "assistant" && !content) {
        if (tools) previous.tools = [...(previous.tools ?? []), ...tools];
        continue;
      }
      if (previous && previous.role === "assistant" && !previous.content) {
        previous.content = content;
        if (tools) previous.tools = [...(previous.tools ?? []), ...tools];
        continue;
      }
      mapped.push({ id: asString(raw.id) || undefined, role: "assistant", content, tools });
    }
  }
  return mapped;
}

/** 构造 embedded_agent 的 submit 输入；任何必填字段为空都在本地抛错，不发起 SDK 调用。 */
export function buildEmbeddedSubmitInput(params: EmbeddedSubmitParams): EmbeddedSubmitInput {
  const route = params.route.trim();
  const message = params.message.trim();
  const sourceAsOf = params.pageContext.sourceAsOf.trim();
  const content = params.pageContext.content.trim();
  if (!route) throw new Error("嵌入式对话缺少页面路由 (route)");
  if (!message) throw new Error("提问内容为空");
  if (!sourceAsOf) throw new Error("页面快照缺少数据时间 (source_as_of)");
  if (!content) throw new Error("页面快照内容为空 (content)");
  return {
    messages: [{ role: "user", content: message }],
    page_context: {
      route,
      scope_key: normalizeScope(route, params.scopeKey),
      source_as_of: sourceAsOf,
      content,
    },
  };
}

const metadataOf = (thread: Thread): Record<string, unknown> =>
  (thread.metadata ?? {}) as Record<string, unknown>;

/** 搜索结果必须逐字段精确匹配，防止 Server 端 metadata 过滤语义漂移串历史。 */
function matchesScope(thread: Thread, route: string, scopeKey: string): boolean {
  const metadata = metadataOf(thread);
  return metadata.channel === "embedded"
    && metadata.route === route
    && metadata.scope_key === scopeKey;
}

export function createEmbeddedClient(client: EmbeddedClientSubset) {
  async function findThread(route: string, scopeKey?: string): Promise<string | null> {
    const normalizedRoute = route.trim();
    const scope = normalizeScope(normalizedRoute, scopeKey);
    const found = await client.threads.search({
      metadata: { channel: "embedded", route: normalizedRoute, scope_key: scope },
      limit: 1,
      sortBy: "updated_at",
      sortOrder: "desc",
    });
    const newest = found[0];
    if (newest && matchesScope(newest, normalizedRoute, scope)) return newest.thread_id;
    return null;
  }

  async function createThread(route: string, scopeKey?: string): Promise<string> {
    const normalizedRoute = route.trim();
    if (!normalizedRoute) throw new Error("嵌入式对话缺少页面路由 (route)");
    const scope = normalizeScope(normalizedRoute, scopeKey);
    const created = await client.threads.create({
      metadata: {
        channel: "embedded",
        route: normalizedRoute,
        scope_key: scope,
        title: `问 AI · ${scope}`,
      },
    });
    return created.thread_id;
  }

  async function deleteThread(route: string, scopeKey?: string): Promise<void> {
    const normalizedRoute = route.trim();
    const scope = normalizeScope(normalizedRoute, scopeKey);
    const found = await client.threads.search({
      metadata: { channel: "embedded", route: normalizedRoute, scope_key: scope },
      sortBy: "updated_at",
      sortOrder: "desc",
    });
    for (const thread of found) {
      if (matchesScope(thread, normalizedRoute, scope)) {
        await client.threads.delete(thread.thread_id);
      }
    }
  }

  return { findThread, createThread, deleteThread };
}

const embeddedClient = createEmbeddedClient(langGraphClient);

/** 抽屉打开时恢复当前 scope 的最新 thread；不存在返回 null，绝不创建。 */
export function findEmbeddedThread(route: string, scopeKey?: string): Promise<string | null> {
  return embeddedClient.findThread(route, scopeKey);
}

/** 首次发送时创建当前 scope 的 thread（metadata 精确标记，供下次恢复与清空匹配）。 */
export function createEmbeddedThread(route: string, scopeKey?: string): Promise<string> {
  return embeddedClient.createThread(route, scopeKey);
}

/** 清空本页对话：删除精确匹配 (route, scope_key) 的 embedded thread。 */
export function deleteEmbeddedThread(route: string, scopeKey?: string): Promise<void> {
  return embeddedClient.deleteThread(route, scopeKey);
}
