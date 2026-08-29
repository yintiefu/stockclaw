/** 技能启停开关：固定尺寸二元控件，外部可见标签，pending 时禁用无布局偏移。 */
import { useId } from "react";

import { cn } from "@/lib/utils";

interface SkillToggleProps {
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
  disabled?: boolean;
  /** 开关旁的可见文字标签（可访问名称的一部分） */
  label: string;
}

export function SkillToggle({ checked, onCheckedChange, disabled = false, label }: SkillToggleProps) {
  const labelId = useId();
  return (
    <span className="inline-flex items-center gap-2">
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-labelledby={labelId}
        disabled={disabled}
        onClick={(event) => {
          event.stopPropagation();
          if (!disabled) onCheckedChange(!checked);
        }}
        className={cn(
          "relative inline-flex h-6 w-10 shrink-0 items-center rounded-full border transition-colors",
          checked ? "border-primary bg-primary/80" : "border-border bg-muted",
          disabled ? "cursor-not-allowed opacity-50" : "cursor-pointer",
        )}
      >
        <span
          aria-hidden="true"
          className={cn(
            "absolute top-0.5 h-4.5 w-4.5 rounded-full bg-background shadow transition-transform",
            checked ? "translate-x-4.5" : "translate-x-0.5",
          )}
        />
      </button>
      <span id={labelId} className="text-xs text-muted-foreground select-none">
        {label}
      </span>
    </span>
  );
}
