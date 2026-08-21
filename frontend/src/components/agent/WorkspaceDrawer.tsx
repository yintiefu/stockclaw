/** 工作台共享抽屉：portal + modal dialog 语义、焦点进入/陷阱/归还、Esc 与背板关闭。

关闭后保留 DOM（hidden），面板滚动位置与内部状态跨开合保持；首次打开前不挂载内容。
 */
import { useEffect, useId, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";

const FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

function focusableIn(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR))
    .filter((element) => !element.closest("[hidden]") && element.getAttribute("aria-hidden") !== "true");
}

type Props = {
  open: boolean;
  onClose: () => void;
  /** dialog 可访问名称（aria-labelledby 指向标题）。 */
  title: string;
  side: "left" | "right";
  /** panel：移动端普通抽屉 min(88vw,360px)；settings：手机全宽、桌面固定 480px 右覆盖层。 */
  variant?: "panel" | "settings";
  children: ReactNode;
};

export function WorkspaceDrawer({ open, onClose, title, side, variant = "panel", children }: Props) {
  const titleId = useId();
  const panelRef = useRef<HTMLDivElement>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);
  const wasOpenRef = useRef(false);
  // 首次打开前完全不挂载；之后关闭只隐藏，保留面板状态与滚动容器
  const [everOpened, setEverOpened] = useState(open);

  useEffect(() => {
    if (open) setEverOpened(true);
  }, [open]);

  // 焦点进入：面板挂载完成后把焦点移入第一个可聚焦元素（否则面板自身）
  useEffect(() => {
    if (!open || !everOpened) return;
    const panel = panelRef.current;
    if (!panel) return;
    if (returnFocusRef.current === null) {
      returnFocusRef.current = document.activeElement as HTMLElement | null;
    }
    (focusableIn(panel)[0] ?? panel).focus();
  }, [open, everOpened]);

  // 焦点归还：从打开变为关闭时回到触发器
  useEffect(() => {
    if (!open && wasOpenRef.current) {
      returnFocusRef.current?.focus?.();
      returnFocusRef.current = null;
    }
    wasOpenRef.current = open;
  }, [open]);

  // 组件卸载时归还焦点（例如测试清理/路由切换）
  useEffect(() => () => {
    if (wasOpenRef.current) returnFocusRef.current?.focus?.();
  }, []);

  if (!everOpened || typeof document === "undefined") return null;

  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (!open) return;
    if (event.key === "Escape") {
      event.stopPropagation();
      onClose();
      return;
    }
    if (event.key !== "Tab") return;
    const panel = panelRef.current;
    if (!panel) return;
    const focusables = focusableIn(panel);
    if (focusables.length === 0) {
      event.preventDefault();
      panel.focus();
      return;
    }
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    const active = document.activeElement;
    if (event.shiftKey && (active === first || !panel.contains(active))) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && (active === last || !panel.contains(active))) {
      event.preventDefault();
      first.focus();
    }
  };

  const sideClass = side === "left" ? "left-0 border-r" : "right-0 border-l";
  const widthClass = variant === "settings" ? "w-full xl:w-[480px]" : "w-[min(88vw,360px)]";

  return createPortal(
    <>
      {open ? (
        <div
          data-testid="workspace-drawer-backdrop"
          aria-hidden="true"
          onClick={onClose}
          className="fixed inset-0 z-40 bg-black/50"
        />
      ) : null}
      <div
        ref={panelRef}
        data-testid="workspace-drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        hidden={!open}
        onKeyDown={handleKeyDown}
        className={`fixed inset-y-0 ${sideClass} ${widthClass} z-50 flex-col border-border bg-background shadow-2xl outline-none${open ? " flex" : ""}`}
      >
        <header className="flex h-12 shrink-0 items-center justify-between gap-2 border-b border-border px-3">
          <h2 id={titleId} className="truncate text-sm font-semibold">{title}</h2>
          <button
            type="button"
            aria-label="关闭"
            onClick={onClose}
            className="grid h-8 w-8 shrink-0 place-items-center rounded-md text-muted-foreground hover:bg-muted/60 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60"
          >
            <X className="h-4 w-4" aria-hidden />
          </button>
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
      </div>
    </>,
    document.body,
  );
}
