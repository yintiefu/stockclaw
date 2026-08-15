import { storageGet, storageRemove, storageSet } from "@/lib/storage";

const AGENT_MODEL_KEY = "vr-agent-model";

export type AgentModelConfig = {
  provider: string;
  baseURL: string;
  model: string;
  apiKey: string;
};

export function loadAgentModelConfig(): AgentModelConfig | null {
  const raw = storageGet(AGENT_MODEL_KEY);
  if (!raw) return null;
  try {
    const value = JSON.parse(raw) as Partial<AgentModelConfig>;
    if ([value.provider, value.baseURL, value.model, value.apiKey].every((item) => typeof item === "string")) {
      return value as AgentModelConfig;
    }
  } catch {
    return null;
  }
  return null;
}

export function saveAgentModelConfig(config: AgentModelConfig): void {
  if (!config.provider && !config.baseURL && !config.model && !config.apiKey) {
    storageRemove(AGENT_MODEL_KEY);
    return;
  }
  storageSet(AGENT_MODEL_KEY, JSON.stringify(config));
}
