import { useEffect, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "./Button";

interface DialogProps {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  size?: "sm" | "md" | "lg" | "xl";
  /** When true, backdrop click / Escape do not close the dialog (e.g. while a mutation is pending). */
  locked?: boolean;
}

const sizeClass: Record<NonNullable<DialogProps["size"]>, string> = {
  sm: "max-w-sm",
  md: "max-w-lg",
  lg: "max-w-2xl",
  xl: "max-w-4xl",
};

/** knitlog modal: radius 16px, 20px padding, popup shadow, 50% black backdrop. */
export function Dialog({ open, onClose, title, description, children, footer, size = "md", locked = false }: DialogProps) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !locked) onClose();
    };
    document.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [open, locked, onClose]);

  if (!open) return null;

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div
        className="absolute inset-0 bg-black/50"
        onClick={() => {
          if (!locked) onClose();
        }}
        aria-hidden
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className={cn("relative flex max-h-[90vh] w-full flex-col rounded-2xl bg-card p-5 shadow-popup", sizeClass[size])}
      >
        <div className="flex items-start justify-between gap-4 pb-4">
          <div>
            <h2 className="text-lg font-bold">{title}</h2>
            {description ? <div className="mt-1 text-sm text-muted-foreground">{description}</div> : null}
          </div>
          <Button variant="ghost" size="icon" onClick={onClose} disabled={locked} aria-label="닫기" className="-mr-2 -mt-1">
            <X className="h-4 w-4" />
          </Button>
        </div>
        <div className="-mx-5 flex-1 overflow-y-auto border-t border-border px-5 py-4">{children}</div>
        {footer ? <div className="-mx-5 -mb-5 flex justify-end gap-2 rounded-b-2xl border-t border-border bg-muted px-5 py-3">{footer}</div> : null}
      </div>
    </div>,
    document.body,
  );
}
