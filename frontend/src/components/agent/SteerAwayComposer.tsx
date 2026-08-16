/** 待审批时的新消息入口：只经 steer-away 提交，替代普通 Composer。 */
import { useState } from "react";
import { CornerUpLeft, Send } from "lucide-react";

import { useApprovalBridge } from "@/lib/agent/approval";

export function SteerAwayComposer({ disabled }: { disabled: boolean }) {
  const { steerAway } = useApprovalBridge();
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);

  const send = async () => {
    const message = text.trim();
    if (!message || disabled || busy) return;
    setBusy(true);
    try {
      await steerAway(message);
      setText("");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="glass-card flex items-end gap-2 rounded-xl p-3">
      <CornerUpLeft className="mb-2 size-4 shrink-0 text-muted-foreground" aria-hidden />
      <textarea
        aria-label="转向新问题"
        value={text}
        disabled={disabled || busy}
        onChange={(e) => setText(e.target.value)}
        placeholder="待审批中：发送新问题将取消本次工具调用并转向（旧运行会被取消）"
        rows={2}
        className="min-h-[44px] flex-1 resize-none rounded-lg border border-border bg-black/20 px-3 py-2 text-sm outline-none focus:border-primary/50"
      />
      <button
        type="button"
        onClick={send}
        disabled={disabled || busy || !text.trim()}
        title="发送新问题（取消当前审批）"
        className="inline-flex items-center gap-1 rounded-lg bg-primary/20 px-3 py-2 text-sm font-medium text-primary hover:bg-primary/30 disabled:cursor-not-allowed disabled:opacity-50"
      >
        <Send className="size-4" aria-hidden />
        发送
      </button>
    </div>
  );
}
