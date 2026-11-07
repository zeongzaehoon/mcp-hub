import type { HTMLAttributes } from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

/** knitlog status pill: radius 52px, 12px semibold, 3px 8px. Only `info`/`success` use the accent; `danger` is the one red.
 * State colour is carried by a dot (see `dot`). */
export const badgeVariants = cva(
  "inline-flex items-center gap-1.5 rounded-[52px] border px-2 py-[3px] text-xs font-semibold leading-4 tracking-[-0.12px] whitespace-nowrap",
  {
    variants: {
      variant: {
        default: "border-transparent bg-foreground text-card",
        secondary: "border-transparent bg-border text-muted-foreground",
        outline: "border-border-strong bg-card text-muted-foreground",
        success: "border-transparent bg-primary-soft text-primary",
        warning: "border-transparent bg-border text-muted-foreground",
        danger: "border-transparent bg-destructive-soft text-destructive",
        info: "border-transparent bg-primary-soft text-primary",
      },
    },
    defaultVariants: { variant: "default" },
  },
);

export type BadgeDot = "accent" | "muted" | "hollow" | "danger";

const dotClass: Record<BadgeDot, string> = {
  accent: "bg-primary",
  muted: "bg-subtle",
  hollow: "border border-subtle bg-transparent",
  danger: "bg-destructive",
};

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement>, VariantProps<typeof badgeVariants> {
  dot?: BadgeDot;
}

export function Badge({ className, variant, dot, children, ...props }: BadgeProps) {
  return (
    <span className={cn(badgeVariants({ variant }), className)} {...props}>
      {dot ? <span className={cn("h-2 w-2 shrink-0 rounded-full", dotClass[dot])} aria-hidden /> : null}
      {children}
    </span>
  );
}
