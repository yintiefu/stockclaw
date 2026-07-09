import { useState } from "react";
import { AgentSidebar } from "./AgentSidebar";
import { AgentMain } from "./AgentMain";
import { ContextDrawer } from "./ContextDrawer";
import { loadLlm } from "@/lib/llm";
import { isCliProvider } from "@/lib/ai-models";
import { CliBlocker } from "./CliBlocker";

export function AgentWorkspace() {
  const [drawerOpen, setDrawerOpen] = useState(true);
  const llm = loadLlm();

  // CLI 模型拦截：function-calling 与流式 agent 不支持
  if (!llm || isCliProvider(llm.provider)) {
    return <CliBlocker />;
  }

  return (
    <div className="flex h-full gap-2 p-2">
      <AgentSidebar />
      <AgentMain />
      {drawerOpen && <ContextDrawer onClose={() => setDrawerOpen(false)} />}
    </div>
  );
}
