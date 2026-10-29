import {
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/form";
import { cn } from "@/lib/utils";

export function Dialog({
  open,
  onClose,
  title,
  description,
  children,
  footer,
  size = "md",
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: string;
  children: ReactNode;
  footer?: ReactNode;
  size?: "sm" | "md" | "lg" | "xl";
}) {
  const titleId = useId();
  const descriptionId = useId();
  const panelRef = useRef<HTMLDivElement>(null);

  const handleKeyDown = useCallback(
    (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        onClose();
      }
      if (event.key !== "Tab" || !panelRef.current) return;
      const focusable = panelRef.current.querySelectorAll<HTMLElement>(
        'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
      );
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    },
    [onClose],
  );

  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement as HTMLElement | null;
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", handleKeyDown);
    const timer = window.setTimeout(() => {
      panelRef.current
        ?.querySelector<HTMLElement>(
          'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled])',
        )
        ?.focus();
    }, 20);
    return () => {
      document.body.style.overflow = "";
      window.removeEventListener("keydown", handleKeyDown);
      window.clearTimeout(timer);
      previous?.focus?.();
    };
  }, [open, handleKeyDown]);

  if (!open) return null;

  const widths = {
    sm: "max-w-md",
    md: "max-w-lg",
    lg: "max-w-2xl",
    xl: "max-w-4xl",
  }[size];

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-end justify-center p-0 sm:items-center sm:p-4">
      <button
        type="button"
        aria-label="Close dialog"
        className="absolute inset-0 cursor-default bg-slate-900/45 backdrop-blur-[1px] animate-fade-in"
        onClick={onClose}
        tabIndex={-1}
      />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={description ? descriptionId : undefined}
        className={cn(
          "relative flex max-h-[92vh] w-full flex-col overflow-hidden rounded-t-2xl border border-slate-200 bg-white shadow-pop animate-fade-in sm:rounded-2xl",
          widths,
        )}
      >
        <div className="flex items-start justify-between gap-4 border-b border-slate-100 px-5 py-4">
          <div>
            <h2 id={titleId} className="text-base font-semibold text-slate-900">
              {title}
            </h2>
            {description ? (
              <p id={descriptionId} className="mt-0.5 text-sm text-slate-500">
                {description}
              </p>
            ) : null}
          </div>
          <Button
            variant="ghost"
            size="icon"
            onClick={onClose}
            aria-label="Close"
            className="-mr-1 -mt-1 shrink-0"
          >
            <X className="h-4 w-4" />
          </Button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">{children}</div>
        {footer ? (
          <div className="flex flex-wrap items-center justify-end gap-2 border-t border-slate-100 bg-slate-50/60 px-5 py-3">
            {footer}
          </div>
        ) : null}
      </div>
    </div>,
    document.body,
  );
}

export function ConfirmDialog({
  open,
  onClose,
  onConfirm,
  title,
  description,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  tone = "primary",
  loading,
  requireText,
}: {
  open: boolean;
  onClose: () => void;
  onConfirm: () => void;
  title: string;
  description: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  tone?: "primary" | "danger";
  loading?: boolean;
  /** When set, the user must type this text before confirming. */
  requireText?: string;
}) {
  const inputId = useId();
  const [value, setValue] = useState("");
  const matches = !requireText || value.trim() === requireText;

  useEffect(() => {
    if (!open) setValue("");
  }, [open]);

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={title}
      size="sm"
      footer={
        <>
          <Button variant="outline" onClick={onClose} disabled={loading}>
            {cancelLabel}
          </Button>
          <Button
            variant={tone === "danger" ? "danger" : "primary"}
            onClick={onConfirm}
            loading={loading}
            disabled={!matches}
          >
            {confirmLabel}
          </Button>
        </>
      }
    >
      <div className="space-y-4 text-sm leading-relaxed text-slate-600">
        <div>{description}</div>
        {requireText ? (
          <div>
            <label htmlFor={inputId} className="label">
              Type <span className="font-mono font-semibold text-slate-900">{requireText}</span> to
              continue
            </label>
            <Input
              id={inputId}
              value={value}
              onChange={(event) => setValue(event.target.value)}
              autoComplete="off"
              placeholder={requireText}
            />
          </div>
        ) : null}
      </div>
    </Dialog>
  );
}
