import type { ReactNode } from "react";
import { cn } from "@/lib/utils";
import { Card } from "@/components/ui/Card";
import type { BadgeDot } from "@/components/ui/Badge";

const dotClass: Record<BadgeDot, string> = {
  accent: "bg-primary",
  muted: "bg-subtle",
  hollow: "border border-subtle bg-transparent",
  danger: "bg-destructive",
};

interface StatTileProps {
  label: string;
  value: ReactNode;
  /** small muted line under the number */
  sub?: ReactNode;
  dot?: BadgeDot;
  /** e.g. a small Sparkline, rendered right of the number */
  children?: ReactNode;
  className?: string;
}

/** One cell of a `StatTileRow` strip: eyebrow label + 26px number (+ sub). State is a dot, never a hue (decision 14). */
export function StatTile({ label, value, sub, dot, children, className }: StatTileProps) {
  return (
    <div className={cn("flex min-w-0 flex-col justify-center gap-1 px-5 py-4", className)} data-testid="stat-tile">
      <div className="eyebrow flex items-center gap-1.5">
        {dot ? <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", dotClass[dot])} aria-hidden /> : null}
        <span className="truncate">{label}</span>
      </div>
      <div className="flex items-end justify-between gap-3">
        <div className="text-[26px] font-bold leading-none tabular-nums tracking-tight">{value}</div>
        {children ? <div className="w-24 shrink-0">{children}</div> : null}
      </div>
      <div className="h-4 truncate text-xs leading-4 text-muted-foreground">{sub}</div>
    </div>
  );
}

/** One card holding the stats as hairline-separated cells. `data-testid="stat-tiles"` is what the e2e looks for. */
export function StatTileRow({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <Card
      className={cn(
        "grid grid-cols-2 divide-x divide-border overflow-hidden md:grid-cols-3 xl:grid-cols-6 [&>*]:border-b [&>*]:border-border xl:[&>*]:border-b-0",
        className,
      )}
      data-testid="stat-tiles"
    >
      {children}
    </Card>
  );
}
