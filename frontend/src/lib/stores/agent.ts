import { create } from "zustand";
import type { AgentThread, ChatMessage, ToolTraceEvent, DecisionCardData } from "@/lib/types/agent";

interface AgentState {
  threads: AgentThread[];
  currentThreadId: string | null;
  messagesByThread: Record<string, ChatMessage[]>;
  streaming: { active: boolean; toolCalls: ToolTraceEvent[] };
  savedDecisions: DecisionCardData[];

  // actions
  setThreads: (threads: AgentThread[]) => void;
  loadThreads: (threads: AgentThread[]) => void;
  setCurrentThread: (tid: string | null) => void;
  appendMessage: (tid: string, msg: ChatMessage) => void;
  appendTextDelta: (tid: string, msgId: string, text: string) => void;
  appendToolTrace: (tid: string, msgId: string, trace: ToolTraceEvent) => void;
  setDecisionCard: (tid: string, msgId: string, card: DecisionCardData) => void;
  setCitations: (tid: string, msgId: string, items: { source: string; code?: string }[]) => void;
  finishStreaming: (tid: string, msgId: string) => void;
  resetStreaming: () => void;
  saveDecision: (card: DecisionCardData) => void;
  removeSavedDecision: (decisionId: string) => void;
}

export const useAgentStore = create<AgentState>((set) => ({
  threads: [],
  currentThreadId: null,
  messagesByThread: {},
  streaming: { active: false, toolCalls: [] },
  savedDecisions: [],

  setThreads: (threads) => set({ threads }),
  loadThreads: (threads) => set({ threads }),
  setCurrentThread: (tid) => set({ currentThreadId: tid }),

  appendMessage: (tid, msg) =>
    set((s) => ({
      messagesByThread: {
        ...s.messagesByThread,
        [tid]: [...(s.messagesByThread[tid] || []), msg],
      },
    })),

  appendTextDelta: (tid, msgId, text) =>
    set((s) => {
      const msgs = s.messagesByThread[tid] || [];
      return {
        messagesByThread: {
          ...s.messagesByThread,
          [tid]: msgs.map((m) => (m.id === msgId ? { ...m, content: m.content + text } : m)),
        },
      };
    }),

  appendToolTrace: (tid, msgId, trace) =>
    set((s) => {
      const msgs = s.messagesByThread[tid] || [];
      return {
        messagesByThread: {
          ...s.messagesByThread,
          [tid]: msgs.map((m) =>
            m.id === msgId ? { ...m, toolTraces: [...m.toolTraces, trace] } : m,
          ),
        },
        streaming: { ...s.streaming, toolCalls: [...s.streaming.toolCalls, trace] },
      };
    }),

  setDecisionCard: (tid, msgId, card) =>
    set((s) => {
      const msgs = s.messagesByThread[tid] || [];
      return {
        messagesByThread: {
          ...s.messagesByThread,
          [tid]: msgs.map((m) => (m.id === msgId ? { ...m, decisionCard: card } : m)),
        },
      };
    }),

  setCitations: (tid, msgId, items) =>
    set((s) => {
      const msgs = s.messagesByThread[tid] || [];
      return {
        messagesByThread: {
          ...s.messagesByThread,
          [tid]: msgs.map((m) => (m.id === msgId ? { ...m, citations: items } : m)),
        },
      };
    }),

  finishStreaming: (tid, msgId) =>
    set((s) => {
      const msgs = s.messagesByThread[tid] || [];
      return {
        messagesByThread: {
          ...s.messagesByThread,
          [tid]: msgs.map((m) => (m.id === msgId ? { ...m, streaming: false } : m)),
        },
      };
    }),

  resetStreaming: () => set({ streaming: { active: false, toolCalls: [] } }),

  saveDecision: (card) =>
    set((s) =>
      s.savedDecisions.some((d) => d.code === card.code)
        ? s
        : { savedDecisions: [card, ...s.savedDecisions].slice(0, 100) },
    ),

  removeSavedDecision: (decisionId) =>
    set((s) => ({ savedDecisions: s.savedDecisions.filter((d) => d.code !== decisionId) })),
}));
