/** 原生 runtime 边界：固定 assistant / API 与线程列表适配器，无请求级模型配置。 */
import type { ReactNode } from "react";
import { AssistantRuntimeProvider } from "@assistant-ui/react";
import { useStreamRuntime } from "@assistant-ui/react-langchain";
import { langGraphThreadAdapter, resolveAgentApiUrl } from "./thread-adapter";

export function AgentRuntimeProvider({ onThreadIdChange, children }: {
  onThreadIdChange?: (threadId: string | undefined) => void;
  children: ReactNode;
}) {
  const runtime = useStreamRuntime({
    assistantId: "agent",
    apiUrl: resolveAgentApiUrl(),
    onThreadIdChange,
    unstable_threadListAdapter: langGraphThreadAdapter,
  });
  return <AssistantRuntimeProvider runtime={runtime}>{children}</AssistantRuntimeProvider>;
}
