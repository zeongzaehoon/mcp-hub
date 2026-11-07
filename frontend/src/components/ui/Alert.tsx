import type { HTMLAttributes } from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { AlertTriangle, CheckCircle2, Info, XCircle } from "lucide-react";
import { cn } from "@/lib/utils";

/** success → blue-soft, error → red-soft, warning → grey tint with a stronger border (no warm hue), info → neutral. */
const alertVariants = cva("flex items-start gap-2 rounded-[5px] border px-3 py-2.5 text-sm", {
  variants: {
    variant: {
      info: "border-border bg-muted text-foreground",
      success: "border-primary/20 bg-primary-soft text-foreground",
      warning: "border-border-strong bg-card text-foreground",
      error: "border-destructive/30 bg-destructive-soft text-destructive",
    },
  },
  defaultVariants: { variant: "info" },
});

const icons = {
  info: Info,
  success: CheckCircle2,
  warning: AlertTriangle,
  error: XCircle,
} as const;

const iconClass = {
  info: "text-muted-foreground",
  success: "text-primary",
  warning: "text-foreground",
  error: "text-destructive",
} as const;

export interface AlertProps extends HTMLAttributes<HTMLDivElement>, VariantProps<typeof alertVariants> {}

export function Alert({ className, variant, children, ...props }: AlertProps) {
  const v = variant ?? "info";
  const Icon = icons[v];
  return (
    <div role="alert" className={cn(alertVariants({ variant }), className)} {...props}>
      <Icon className={cn("mt-0.5 h-4 w-4 shrink-0", iconClass[v])} aria-hidden />
      <div className="min-w-0 flex-1 break-words">{children}</div>
    </div>
  );
}
