import { cn } from "@/lib/utils";
import type { MetricsPoint } from "@/domains/ops/types";

interface SparklineProps {
  series: MetricsPoint[];
  /** draw `denied` as a second thin muted line */
  showDenied?: boolean;
  height?: number;
  className?: string;
}

const W = 240;

function path(values: number[], max: number, h: number): string {
  if (values.length === 0) return "";
  const step = values.length > 1 ? W / (values.length - 1) : 0;
  const y = (v: number) => (max > 0 ? h - 1 - (v / max) * (h - 2) : h - 1);
  return values.map((v, i) => `${i === 0 ? "M" : "L"}${(i * step).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
}

/** Inline SVG, no axes, no chart lib. Total calls in the accent, denied as a muted hairline. */
export function Sparkline({ series, showDenied = true, height = 32, className }: SparklineProps) {
  const totals = series.map((p) => p.n);
  const denied = series.map((p) => p.denied);
  const max = Math.max(1, ...totals);
  const line = path(totals, max, height);
  const area = line ? `${line} L${W},${height} L0,${height} Z` : "";
  return (
    <svg
      viewBox={`0 0 ${W} ${height}`}
      preserveAspectRatio="none"
      className={cn("block w-full", className)}
      style={{ height }}
      role="img"
      aria-label={`최근 ${series.length}시간 호출 추이`}
    >
      {area ? <path d={area} className="fill-primary/10" /> : null}
      {line ? <path d={line} className="fill-none stroke-primary" strokeWidth={1.5} vectorEffect="non-scaling-stroke" strokeLinejoin="round" /> : null}
      {showDenied && denied.some((v) => v > 0) ? (
        <path d={path(denied, max, height)} className="fill-none stroke-subtle" strokeWidth={1} vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
      ) : null}
    </svg>
  );
}
