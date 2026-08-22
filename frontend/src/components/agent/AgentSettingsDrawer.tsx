/** 设置抽屉：模型 / Skill / MCP / Policy 四页签。

- 模型：仅本机 localStorage（vr-agent-model），任一线程运行/待审批时禁用身份修改；
- Skill：本会话选择（ThreadSkillSection，一次 PATCH）+ 全局导入管理；
- MCP：McpManager（每次测试/刷新/变更后自行 REST 重载）；
- Policy：服务端范围/默认 + revision CAS；仅提交变更字段；损坏需二次确认重置；
  运行期间仍可编辑（快照不可变）。关闭前若有未保存 Policy 修改需确认。
 */
import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from "react";

import { agentApi } from "@/lib/agent/api";
import {
  loadAgentModelConfig,
  saveAgentModelConfig,
  type AgentModelConfig,
} from "@/lib/agent/model-config";
import type { AgentPolicy, AgentThread, SkillSummary } from "@/lib/agent/types";
import { McpManager } from "./McpManager";
import { SkillManager } from "./SkillManager";
import { ThreadSkillSection } from "./CapabilityManagerDialog";
import { WorkspaceDrawer } from "./WorkspaceDrawer";

export type AgentSettingsTab = "model" | "skills" | "mcp" | "policy";

const TABS: Array<{ id: AgentSettingsTab; label: string }> = [
  { id: "model", label: "模型" },
  { id: "skills", label: "Skill" },
  { id: "mcp", label: "MCP" },
  { id: "policy", label: "Policy" },
];

const INPUT_CLASS =
  "w-full rounded-md border border-border bg-black/20 px-2.5 py-2 text-sm outline-hidden focus:border-primary/50";

type Props = {
  open: boolean;
  onClose: () => void;
  /** 打开时定位到的页签（例如从能力条进入 Skills）。 */
  focusTab?: AgentSettingsTab;
  thread: AgentThread | null;
  skills: SkillSummary[];
  /** 任一已知线程 running / awaiting_approval 时禁用模型身份。 */
  modelBusy: boolean;
  /** 既有忙碌规则：本会话 Skill 选择不可用。 */
  selectionDisabled: boolean;
  onModelSaved: () => void;
  onThreadReloaded: () => void | Promise<void>;
  onSkillsChanged: () => void | Promise<void>;
};

function ModelSection({ busy, onSaved }: { busy: boolean; onSaved: () => void }) {
  const [draft, setDraft] = useState<AgentModelConfig>(
    () => loadAgentModelConfig() ?? { provider: "", baseURL: "", model: "", apiKey: "" },
  );
  const update = (field: keyof AgentModelConfig, value: string) => {
    setDraft((prev) => ({ ...prev, [field]: value }));
  };
  return (
    <form
      className="space-y-3"
      onSubmit={(event) => { event.preventDefault(); saveAgentModelConfig(draft); onSaved(); }}
    >
      <p className="text-xs text-muted-foreground">
        配置仅保存在本机浏览器（不随请求外的任何通道发送），运行中的会话使用打开时的模型身份。
      </p>
      <fieldset disabled={busy} className="space-y-3">
        <label className="block text-xs text-muted-foreground" htmlFor="settings-provider">Provider</label>
        <input id="settings-provider" value={draft.provider} onChange={(e) => update("provider", e.target.value)} className={INPUT_CLASS} />
        <label className="block text-xs text-muted-foreground" htmlFor="settings-base-url">Base URL</label>
        <input id="settings-base-url" value={draft.baseURL} onChange={(e) => update("baseURL", e.target.value)} className={INPUT_CLASS} />
        <label className="block text-xs text-muted-foreground" htmlFor="settings-model">模型名称</label>
        <input id="settings-model" value={draft.model} onChange={(e) => update("model", e.target.value)} className={INPUT_CLASS} />
        <label className="block text-xs text-muted-foreground" htmlFor="settings-api-key">API Key</label>
        <input id="settings-api-key" type="password" value={draft.apiKey} onChange={(e) => update("apiKey", e.target.value)} className={INPUT_CLASS} />
        {busy ? <p className="text-xs text-muted-foreground">有线程正在运行或待审批，暂不能修改模型身份</p> : null}
        <button type="submit" className="w-full rounded-md bg-primary/15 px-3 py-2 text-sm font-medium text-primary hover:bg-primary/25">
          保存模型配置
        </button>
      </fieldset>
    </form>
  );
}

function SkillsSection({
  thread, skills, selectionDisabled, onThreadReloaded, onSkillsChanged,
}: {
  thread: AgentThread | null;
  skills: SkillSummary[];
  selectionDisabled: boolean;
  onThreadReloaded: () => void | Promise<void>;
  onSkillsChanged: () => void | Promise<void>;
}) {
  return (
    <div className="space-y-4">
      {thread ? (
        <ThreadSkillSection
          thread={thread}
          skills={skills}
          disabled={selectionDisabled}
          onApplied={() => { void onThreadReloaded(); }}
          onConflict={() => { void onThreadReloaded(); }}
        />
      ) : (
        <p className="text-xs text-muted-foreground">暂无选中会话，先在左侧创建或选择一个会话</p>
      )}
      <div className="border-t border-border pt-3">
        <h3 className="mb-2 text-xs font-semibold text-muted-foreground">导入 / 管理 Skill</h3>
        <SkillManager skills={skills} disabled={false} onChanged={onSkillsChanged} />
      </div>
    </div>
  );
}

const POLICY_FIELDS = [
  { key: "max_model_calls", label: "单次运行模型调用上限", min: 1, max: 32, fallback: 8 },
  { key: "max_tool_calls", label: "单次运行工具调用上限", min: 1, max: 64, fallback: 16 },
  { key: "tool_timeout_seconds", label: "工具超时（秒）", min: 5, max: 120, fallback: 30 },
  { key: "max_active_seconds", label: "运行时限（秒）", min: 30, max: 1800, fallback: 300 },
  { key: "max_context_chars", label: "上下文字符上限", min: 16_000, max: 500_000, fallback: 120_000 },
] as const;

type PolicyFieldKey = (typeof POLICY_FIELDS)[number]["key"];
type PolicyDraft = Record<PolicyFieldKey, string>;

function policyDraftOf(policy: AgentPolicy): PolicyDraft {
  return {
    max_model_calls: String(policy.max_model_calls),
    max_tool_calls: String(policy.max_tool_calls),
    tool_timeout_seconds: String(policy.tool_timeout_seconds),
    max_active_seconds: String(policy.max_active_seconds),
    max_context_chars: String(policy.max_context_chars),
  };
}

function errorStatus(error: unknown): number | null {
  if (typeof error !== "object" || error === null) return null;
  const status = (error as { status?: number }).status;
  return typeof status === "number" ? status : null;
}

function errorCode(error: unknown): string | null {
  return (error as { code?: string } | null)?.code ?? null;
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

function PolicySection({ onDirtyChange }: { onDirtyChange: (dirty: boolean) => void }) {
  const [policy, setPolicy] = useState<AgentPolicy | null>(null);
  const [draft, setDraft] = useState<PolicyDraft | null>(null);
  const [corruptReason, setCorruptReason] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmReset, setConfirmReset] = useState(false);

  const load = useCallback(async (conflict: boolean) => {
    try {
      const loaded = await agentApi.getPolicy();
      setPolicy(loaded);
      setDraft(policyDraftOf(loaded));
      setCorruptReason(null);
      setConfirmReset(false);
      setNotice(conflict ? `Policy 已被其他会话修改，已加载最新版本（revision ${loaded.revision}）` : null);
    } catch (error) {
      if (errorStatus(error) === 503 && errorCode(error) === "POLICY_CORRUPT") {
        setCorruptReason(errorMessage(error, "Policy 文件损坏"));
        setPolicy(null);
        setDraft(null);
        setNotice(null);
      } else {
        setNotice(errorMessage(error, "Policy 加载失败"));
      }
    }
  }, []);

  useEffect(() => { void load(false); }, [load]);
  useEffect(() => () => onDirtyChange(false), [onDirtyChange]);

  const parsed: Partial<Record<PolicyFieldKey, number>> = {};
  let invalidLabel: string | null = null;
  if (draft) {
    for (const field of POLICY_FIELDS) {
      const value = Number(draft[field.key]);
      if (!Number.isInteger(value) || value < field.min || value > field.max) invalidLabel = field.label;
      else parsed[field.key] = value;
    }
  }
  const changedFields = policy && draft
    ? POLICY_FIELDS.filter((field) => parsed[field.key] !== policy[field.key])
    : [];
  const dirty = changedFields.length > 0;
  useEffect(() => { onDirtyChange(dirty); }, [dirty, onDirtyChange]);

  const canSave = policy !== null && draft !== null && corruptReason === null
    && !busy && !invalidLabel && dirty;

  const save = async () => {
    if (!canSave || !policy) return;
    setBusy(true);
    setNotice(null);
    const patch: Record<string, number> = { revision: policy.revision };
    for (const field of changedFields) patch[field.key] = parsed[field.key]!;
    try {
      const updated = await agentApi.patchPolicy(patch as Parameters<typeof agentApi.patchPolicy>[0]);
      setPolicy(updated);
      setDraft(policyDraftOf(updated));
      setNotice("Policy 已保存");
    } catch (error) {
      if (errorStatus(error) === 409) {
        await load(true);
      } else if (errorStatus(error) === 503 && errorCode(error) === "POLICY_CORRUPT") {
        setCorruptReason(errorMessage(error, "Policy 文件损坏"));
        setPolicy(null);
        setDraft(null);
      } else {
        setNotice(errorMessage(error, "Policy 保存失败"));
      }
    } finally {
      setBusy(false);
    }
  };

  const resetCorrupt = async () => {
    if (busy) return;
    setBusy(true);
    setNotice(null);
    try {
      const reset = await agentApi.resetPolicy({ confirm_corrupt: true });
      setPolicy(reset);
      setDraft(policyDraftOf(reset));
      setCorruptReason(null);
      setConfirmReset(false);
      setNotice("已重置为默认 Policy（revision 1）");
    } catch (error) {
      setNotice(errorMessage(error, "Policy 重置失败"));
    } finally {
      setBusy(false);
    }
  };

  const smallContext = draft !== null
    && Number.isFinite(Number(draft.max_context_chars))
    && Number(draft.max_context_chars) <= 60_000;

  return (
    <div className="space-y-3">
      {corruptReason !== null ? (
        <div role="alert" className="space-y-2 rounded-md border border-destructive/40 bg-destructive/10 p-3">
          <p className="text-xs text-destructive">Policy 文件损坏，普通保存不可用：{corruptReason}</p>
          <p className="text-xs text-muted-foreground">
            确认重置会把当前文件改名隔离并写回默认值（revision 1）；正在运行/待恢复的会话仍使用其原始快照。
          </p>
          {confirmReset ? (
            <div className="space-y-2">
              <p className="text-xs font-medium">确认重置损坏的 Policy？</p>
              <div className="flex gap-2">
                <button type="button" onClick={() => void resetCorrupt()} disabled={busy}
                  className="rounded-md bg-destructive/20 px-3 py-1.5 text-xs font-medium text-destructive hover:bg-destructive/30 disabled:opacity-50">
                  确认重置
                </button>
                <button type="button" onClick={() => setConfirmReset(false)} disabled={busy}
                  className="rounded-md px-3 py-1.5 text-xs text-muted-foreground hover:bg-black/20 disabled:opacity-50">
                  取消重置
                </button>
              </div>
            </div>
          ) : (
            <button type="button" onClick={() => setConfirmReset(true)} disabled={busy}
              className="rounded-md border border-border px-3 py-1.5 text-xs font-medium hover:bg-black/20 disabled:opacity-50">
              重置损坏的 Policy
            </button>
          )}
        </div>
      ) : null}
      <p className="text-xs text-muted-foreground">
        当前 revision {policy?.revision ?? "…"}{policy && !policy.persisted ? "（未持久化，展示默认值）" : ""}。
        Policy 在运行期间也可编辑：每个运行使用启动时的不可变快照。
      </p>
      {POLICY_FIELDS.map((field) => (
        <label key={field.key} className="block text-xs text-muted-foreground" htmlFor={`policy-${field.key}`}>
          {field.label}（{field.min}–{field.max}，默认 {field.fallback}）
          <input
            id={`policy-${field.key}`}
            type="number"
            min={field.min}
            max={field.max}
            step={1}
            disabled={busy || draft === null}
            value={draft?.[field.key] ?? ""}
            onChange={(event) => setDraft((prev) => prev ? { ...prev, [field.key]: event.target.value } : prev)}
            className={`${INPUT_CLASS} mt-1`}
          />
        </label>
      ))}
      {invalidLabel ? <p role="alert" className="text-xs text-destructive">{invalidLabel} 超出允许范围</p> : null}
      {smallContext ? (
        <p role="status" className="text-xs text-amber-500">
          Skill 指令可能占满上下文：预算过小时将优先保留系统提示与 Skill 指令并裁剪更早的历史
        </p>
      ) : null}
      {notice ? <p role="status" className="text-xs text-muted-foreground">{notice}</p> : null}
      <button
        type="button"
        onClick={() => void save()}
        disabled={!canSave}
        className="w-full rounded-md bg-primary/15 px-3 py-2 text-sm font-medium text-primary hover:bg-primary/25 disabled:cursor-not-allowed disabled:opacity-50"
      >
        保存 Policy
      </button>
    </div>
  );
}

export function AgentSettingsDrawer({
  open, onClose, focusTab, thread, skills, modelBusy, selectionDisabled,
  onModelSaved, onThreadReloaded, onSkillsChanged,
}: Props) {
  const [tab, setTab] = useState<AgentSettingsTab>("model");
  const [visited, setVisited] = useState<ReadonlySet<AgentSettingsTab>>(() => new Set());
  const [policyDirty, setPolicyDirty] = useState(false);
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([]);

  // 每次打开重置到入口页签并清空访问记录（关闭即卸载各页签草稿）
  useEffect(() => {
    if (!open) return;
    const initial = focusTab ?? "model";
    setTab(initial);
    setVisited(new Set([initial]));
    setPolicyDirty(false);
  }, [open]);

  const requestClose = () => {
    if (policyDirty && !window.confirm("Policy 有未保存的修改，确定丢弃并关闭设置？")) return;
    onClose();
  };

  const selectTab = (next: AgentSettingsTab) => {
    setTab(next);
    setVisited((prev) => prev.has(next) ? prev : new Set(prev).add(next));
  };

  const handleTabKey = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    const offset = event.key === "ArrowRight" ? 1 : -1;
    const next = (index + offset + TABS.length) % TABS.length;
    selectTab(TABS[next].id);
    tabRefs.current[next]?.focus();
  };

  const panelClass = (id: AgentSettingsTab) => tab === id ? "space-y-3" : "hidden space-y-3";

  return (
    <WorkspaceDrawer open={open} onClose={requestClose} title="设置" side="right" variant="settings">
      <div role="tablist" aria-label="设置视图" className="grid grid-cols-4 border-b border-border/70">
        {TABS.map((entry, index) => {
          const selected = tab === entry.id;
          const badge = entry.id === "skills" ? skills.length : null;
          return (
            <button
              key={entry.id}
              ref={(node) => { tabRefs.current[index] = node; }}
              id={`agent-settings-tab-${entry.id}`}
              role="tab"
              type="button"
              aria-selected={selected}
              aria-controls={`agent-settings-panel-${entry.id}`}
              tabIndex={selected ? 0 : -1}
              onClick={() => selectTab(entry.id)}
              onKeyDown={(event) => handleTabKey(event, index)}
              className={`min-w-0 border-b-2 px-1 py-2 text-xs ${selected ? "border-primary text-foreground" : "border-transparent text-muted-foreground"}`}
            >
              <span className="block truncate">{entry.label}</span>
              {badge !== null ? <span className="mt-0.5 block text-[10px] tabular-nums">{badge}</span> : null}
            </button>
          );
        })}
      </div>
      <div className="min-h-0 px-3 py-3">
        {open && visited.has("model") ? (
          <div id="agent-settings-panel-model" role="tabpanel" aria-labelledby="agent-settings-tab-model" className={panelClass("model")}>
            <ModelSection busy={modelBusy} onSaved={onModelSaved} />
          </div>
        ) : null}
        {open && visited.has("skills") ? (
          <div id="agent-settings-panel-skills" role="tabpanel" aria-labelledby="agent-settings-tab-skills" className={panelClass("skills")}>
            <SkillsSection
              thread={thread}
              skills={skills}
              selectionDisabled={selectionDisabled}
              onThreadReloaded={onThreadReloaded}
              onSkillsChanged={onSkillsChanged}
            />
          </div>
        ) : null}
        {open && visited.has("mcp") ? (
          <div id="agent-settings-panel-mcp" role="tabpanel" aria-labelledby="agent-settings-tab-mcp" className={panelClass("mcp")}>
            <McpManager disabled={false} onReload={() => {}} />
          </div>
        ) : null}
        {open && visited.has("policy") ? (
          <div id="agent-settings-panel-policy" role="tabpanel" aria-labelledby="agent-settings-tab-policy" className={panelClass("policy")}>
            <PolicySection onDirtyChange={setPolicyDirty} />
          </div>
        ) : null}
      </div>
    </WorkspaceDrawer>
  );
}
