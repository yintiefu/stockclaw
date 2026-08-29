import { useEffect, useState } from "react";
import { useLangChainInterrupts } from "@assistant-ui/react-langchain";

import { AgentThread } from "@/components/agent/AgentThread";
import { AgentThreadList } from "@/components/agent/AgentThreadList";
import { AgentWorkspace } from "@/components/agent/AgentWorkspace";
import { ApprovalPanel } from "@/components/agent/ApprovalPanel";
import { AgentRuntimeProvider } from "@/lib/agent/runtime";

/** 待审批状态必须读自 runtime 边界内（hook 由 provider 供给）。 */
function AgentContent({ desktop }: { desktop: boolean }) {
  const approvalPending = useLangChainInterrupts().length > 0;
  return (
    <AgentWorkspace
      desktop={desktop}
      threads={<AgentThreadList />}
      approval={<ApprovalPanel disabled={false} />}
      chat={<AgentThread approvalPending={approvalPending} />}
    />
  );
}

/** Agent 工作台：native LangGraph runtime + 两栏会话/聊天壳。 */
export function Agent() {
  const [desktop, setDesktop] = useState(() =>
    typeof window !== "undefined" && window.matchMedia("(min-width: 1280px)").matches,
  );
  useEffect(() => {
    const media = window.matchMedia("(min-width: 1280px)");
    const update = (event: MediaQueryListEvent) => setDesktop(event.matches);
    setDesktop(media.matches);
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  return (
    <AgentRuntimeProvider>
      <AgentContent desktop={desktop} />
    </AgentRuntimeProvider>
  );
}
