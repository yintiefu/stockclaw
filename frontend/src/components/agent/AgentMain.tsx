import { useState } from "react";
import { AgentTopBar } from "./AgentTopBar";
import { CustomAgentChat } from "./CustomAgentChat";
import { AgentComposer } from "./AgentComposer";

export function AgentMain() {
  const [contextCodes] = useState<string[]>([]);
  return (
    <main className="glass flex flex-1 flex-col rounded-2xl">
      <AgentTopBar contextCodes={contextCodes} />
      <CustomAgentChat />
      <AgentComposer />
    </main>
  );
}
