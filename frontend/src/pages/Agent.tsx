import { useCallback, useState } from "react";

import { AgentThread } from "@/components/agent/AgentThread";
import {
  loadAgentModelConfig,
  saveAgentModelConfig,
  type AgentModelConfig,
} from "@/lib/agent/model-config";
import { AgentRuntimeProvider } from "@/lib/agent/runtime";

const INPUT_CLASS =
  "w-full rounded-lg border border-border bg-black/20 px-3 py-2 text-sm outline-none focus:border-primary/50";

/** Agent 工作台（1A 最小入口）：独立模型配置 + 会话线程。 */
export function Agent() {
  const [saved, setSaved] = useState<AgentModelConfig | null>(() => loadAgentModelConfig());
  const [draft, setDraft] = useState<AgentModelConfig>(
    () => loadAgentModelConfig() ?? { provider: "", baseURL: "", model: "", apiKey: "" },
  );
  const [conflict, setConflict] = useState<string | null>(null);
  const [runtimeError, setRuntimeError] = useState<string | null>(null);

  const save = () => {
    saveAgentModelConfig(draft);
    setSaved(loadAgentModelConfig());
  };

  const onConflict = useCallback((value: { code?: string; detail?: string }) => {
    setConflict(value.detail ?? value.code ?? "线程正忙，请稍候");
  }, []);
  const onError = useCallback((error: Error) => {
    setRuntimeError(error.message || "Agent 运行出错");
  }, []);

  const complete = Boolean(
    draft.provider && draft.baseURL && draft.model && draft.apiKey,
  ) || Boolean(saved?.provider && saved?.baseURL && saved?.model && saved?.apiKey);

  return (
    <div className="mx-auto max-w-6xl space-y-4 p-4">
      <header>
        <h1 className="text-lg font-semibold">Agent 工作台</h1>
        <p className="mt-1 text-xs text-muted-foreground">
          客观数据 + 分析框架。不给出买卖建议。模型密钥只保存在本地浏览器，仅经请求头传输。
        </p>
      </header>

      <section className="glass-card rounded-xl p-4">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <div>
            <label htmlFor="agent-provider" className="mb-1.5 block text-xs font-medium text-muted-foreground">Provider</label>
            <input id="agent-provider" value={draft.provider}
              onChange={(e) => setDraft({ ...draft, provider: e.target.value })}
              placeholder="deepseek / openai / …"
              className={INPUT_CLASS} />
          </div>
          <div>
            <label htmlFor="agent-base-url" className="mb-1.5 block text-xs font-medium text-muted-foreground">Base URL</label>
            <input id="agent-base-url" value={draft.baseURL}
              onChange={(e) => setDraft({ ...draft, baseURL: e.target.value })}
              placeholder="https://api.deepseek.com/v1"
              className={INPUT_CLASS} />
          </div>
          <div>
            <label htmlFor="agent-model" className="mb-1.5 block text-xs font-medium text-muted-foreground">模型</label>
            <input id="agent-model" value={draft.model}
              onChange={(e) => setDraft({ ...draft, model: e.target.value })}
              placeholder="模型名称"
              className={INPUT_CLASS} />
          </div>
          <div>
            <label htmlFor="agent-api-key" className="mb-1.5 block text-xs font-medium text-muted-foreground">API Key</label>
            <input id="agent-api-key" type="password" value={draft.apiKey}
              onChange={(e) => setDraft({ ...draft, apiKey: e.target.value })}
              placeholder="sk-…"
              className={INPUT_CLASS} />
          </div>
        </div>
        <div className="mt-3 flex items-center gap-3">
          <button onClick={save}
            className="inline-flex items-center gap-1.5 rounded-lg bg-primary/15 px-4 py-2 text-sm font-medium text-primary shadow-glow hover:bg-primary/25">
            保存（存本地）
          </button>
          {!complete && (
            <span className="text-xs text-muted-foreground">填写完整配置后即可开始会话</span>
          )}
        </div>
      </section>

      {conflict && (
        <div className="rounded-lg border border-border bg-black/20 px-3 py-2 text-sm text-muted-foreground">
          {conflict}
        </div>
      )}
      {runtimeError && (
        <div className="rounded-lg border border-border bg-black/20 px-3 py-2 text-sm text-muted-foreground">
          {runtimeError}
        </div>
      )}

      <section className="glass-card rounded-xl p-4">
        {complete ? (
          <AgentRuntimeProvider
            config={saved ?? draft}
            onConflict={onConflict}
            onError={onError}
          >
            <AgentThread />
          </AgentRuntimeProvider>
        ) : (
          <div className="flex min-h-[560px] items-center justify-center text-sm text-muted-foreground">
            开始前请先完成模型配置
          </div>
        )}
      </section>
    </div>
  );
}
