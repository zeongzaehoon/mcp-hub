import type { HTMLAttributes } from "react";
import { cn } from "@/lib/utils";

/** knitlog panel: radius 8px, 1px border, panel shadow. */
export function Card({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("rounded-lg border border-border bg-card", className)} {...props} />;
}

export function CardHeader({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("flex items-center justify-between gap-2 border-b border-border px-5 py-3.5", className)} {...props} />;
}

export function CardTitle({ className, ...props }: HTMLAttributes<HTMLHeadingElement>) {
  return <h3 className={cn("text-base font-semibold tracking-tight", className)} {...props} />;
}

/** Small uppercase mono label above a card title or a section (`eyebrow` in index.css). */
export function CardLabel({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("eyebrow", className)} {...props} />;
}

export function CardContent({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("px-5 py-4", className)} {...props} />;
}

/** knitlog section title: 18px bold grey-500, used above a group of cards or a table. */
export function SectionTitle({ className, ...props }: HTMLAttributes<HTMLHeadingElement>) {
  return <h2 className={cn("text-lg font-bold text-muted-foreground", className)} {...props} />;
}
