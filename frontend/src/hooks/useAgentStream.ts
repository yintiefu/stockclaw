import { useCallback, useRef } from "react";
import { useAgentStore } from "@/lib/stores/agent";
import { agentApi, authHeaders } from "@/lib/api";
import { loadLlm } from "@/lib/llm";
import type { AgentEvent } from "@/lib/types/agent";

interface SendOpts {
  threadId: string | null;
  content: string;
  contextCodes: string[];
  style: "conservative" | "balanced" | "aggressive";
  onDone?: (summary: { thread_id?: string; rounds?: number }) => void;
  onError?: (message: string) => void;
}

export function useAgentStream() {
  const abortRef = useRef<AbortController | null>(null);

  const dispatch = useCallback((tid: string, msgId: string, event: AgentEvent) => {
    const store = useAgentStore.getState();
    switch (event.type) {
      case "text_delta":
        store.appendTextDelta(tid, msgId, event.text);
        break;
      case "tool_trace":
        store.appendToolTrace(tid, msgId, event);
        break;
      case "decision_artifact":
        store.setDecisionCard(tid, msgId, event.data);
        break;
      case "citations":
        store.setCitations(tid, msgId, event.items);
        break;
      case "done":
      case "error":
        store.finishStreaming(tid, msgId);
        break;
      // chart_artifact / table_artifact：Phase 2 处理
    }
  }, []);

  const send = useCallback(async (opts: SendOpts) => {
    // 1. 解析 / 生成 tid：没有 threadId 就 crypto.randomUUID 生成（评审 #6 简化）
    let tid = opts.threadId;
    if (!tid) {
      tid = (globalThis.crypto?.randomUUID?.() ?? `local-${Date.now()}`);
      const title = opts.content.slice(0, 30) + (opts.content.length > 30 ? "..." : "");
      // 本地先建占位
      useAgentStore.setState((s) => ({
        threads: [
          { id: tid!, title, model: "", created_at: Date.now(), updated_at: Date.now() },
          ...s.threads,
        ],
        currentThreadId: tid,
        messagesByThread: { ...s.messagesByThread, [tid!]: [] },
      }));
      // 后端建——立刻持久化，刷新页面也能看到
      try {
        await agentApi.createThread(title, "", tid);
      } catch (e) {
        console.error("createThread 失败，降级本地：", e);
      }
    }

    // 1.5. 首次发消息时把「新会话」placeholder 改成消息前 8 字
    const existingThread = useAgentStore.getState().threads.find((t) => t.id === tid);
    if (existingThread && (existingThread.title === "新会话" || existingThread.title === "")) {
      const newTitle = opts.content.slice(0, 8) + (opts.content.length > 8 ? "..." : "");
      useAgentStore.setState((s) => ({
        threads: s.threads.map((t) => t.id === tid ? { ...t, title: newTitle } : t),
      }));
      if (!tid.startsWith("local-")) {
        agentApi.renameThread(tid, newTitle).catch((e) => {
          console.error("首次发消息 rename 失败：", e);
        });
      }
    }

    const userMsgId = `u-${Date.now()}`;
    const assistantMsgId = `a-${Date.now() + 1}`;
    const store = useAgentStore.getState();

    // 2. 写入用户消息 + 占位 assistant 消息（streaming: true）
    store.appendMessage(tid, {
      id: userMsgId, role: "user", content: opts.content, toolTraces: [],
    });
    store.appendMessage(tid, {
      id: assistantMsgId, role: "assistant", content: "", toolTraces: [], streaming: true,
    });
    useAgentStore.setState({
      currentThreadId: tid,
      streaming: { active: true, toolCalls: [] },
    });

    // 3. 构造请求
    const llm = loadLlm();
    if (!llm) {
      opts.onError?.("未配置 LLM");
      useAgentStore.getState().finishStreaming(tid, assistantMsgId);
      useAgentStore.setState({ streaming: { active: false, toolCalls: [] } });
      return;
    }
    const body = {
      thread_id: tid,
      messages: [{ role: "user", content: opts.content }],
      context_codes: opts.contextCodes,
      llm,
      style: opts.style,
    };

    // 评审 #4：user 消息在 SSE fetch 之前归档——断网 / 关 tab 都不会丢
    if (!tid.startsWith("local-")) {
      try {
        await agentApi.saveMessage(tid, { role: "user", content: opts.content });
      } catch (e) {
        console.error("user 消息归档失败：", e);
      }
    }

    abortRef.current = new AbortController();
    let response: Response;
    try {
      response = await fetch("/api/agent/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify(body),
        signal: abortRef.current.signal,
      });
    } catch (e) {
      // 用户主动 abort：不报错，只清理 streaming 状态
      const aborted = e instanceof DOMException && e.name === "AbortError";
      if (!aborted) {
        opts.onError?.(`连接失败：${e instanceof Error ? e.message : "未知错误"}`);
      }
      useAgentStore.getState().finishStreaming(tid, assistantMsgId);
      useAgentStore.setState({ streaming: { active: false, toolCalls: [] } });
      return;
    }

    // 4. 鉴权失败 / CLI 拒绝：HTTP 4xx + JSON（不是 SSE 流）
    if (!response.ok) {
      let detail = `HTTP ${response.status}`;
      try {
        const j = await response.json();
        detail = j.detail || detail;
      } catch { /* 非 JSON 响应 */ }
      opts.onError?.(detail);
      useAgentStore.getState().finishStreaming(tid, assistantMsgId);
      useAgentStore.setState({ streaming: { active: false, toolCalls: [] } });
      return;
    }

    // 5. 流式解析 NDJSON：跨 chunk line buffer + TextDecoder{stream:true}
    const reader = response.body?.getReader();
    if (!reader) {
      opts.onError?.("无响应体");
      useAgentStore.getState().finishStreaming(tid, assistantMsgId);
      useAgentStore.setState({ streaming: { active: false, toolCalls: [] } });
      return;
    }

    const decoder = new TextDecoder();
    let lineBuffer = "";
    let doneSummary: { thread_id?: string; rounds?: number } | undefined;

    try {
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        // stream:true 保证多字节 UTF-8 字符跨 chunk 不被截断
        lineBuffer += decoder.decode(value, { stream: true });
        const lines = lineBuffer.split("\n");
        // 弹出最后一个（可能不完整的）行，留到下次拼接
        lineBuffer = lines.pop() ?? "";

        for (const line of lines) {
          const trimmed = line.trim();
          if (!trimmed) continue;
          try {
            const event = JSON.parse(trimmed) as AgentEvent;
            if (event.type === "done") {
              doneSummary = event.summary;
            } else if (event.type === "error") {
              opts.onError?.(event.message ?? "Agent 运行失败");
            }
            dispatch(tid, assistantMsgId, event);
          } catch (e) {
            // 单条坏帧不中断整流——只 console.error
            console.error("NDJSON 帧解析失败:", trimmed, e);
          }
        }
      }
      // flush 缓冲区里最后残留的一行
      if (lineBuffer.trim()) {
        try {
          const event = JSON.parse(lineBuffer.trim()) as AgentEvent;
          if (event.type === "done") {
            doneSummary = event.summary;
          } else if (event.type === "error") {
            opts.onError?.(event.message ?? "Agent 运行失败");
          }
          dispatch(tid, assistantMsgId, event);
        } catch (e) {
          console.error("NDJSON flush 帧解析失败:", lineBuffer, e);
        }
      }
    } finally {
      // assistant 归档：无论成功 / 失败，只要拿到内容就存
      if (!tid.startsWith("local-")) {
        try {
          const finalContent =
            useAgentStore.getState().messagesByThread[tid]?.find((m) => m.id === assistantMsgId)?.content || "";
          if (finalContent) {
            await agentApi.saveMessage(tid, { role: "assistant", content: finalContent });
          }
        } catch (e) {
          console.error("assistant 归档失败：", e);
        }
      }
      useAgentStore.getState().finishStreaming(tid, assistantMsgId);
      useAgentStore.setState({ streaming: { active: false, toolCalls: [] } });
      opts.onDone?.(doneSummary || {});
    }
  }, [dispatch]);

  const abort = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  return { send, abort };
}
