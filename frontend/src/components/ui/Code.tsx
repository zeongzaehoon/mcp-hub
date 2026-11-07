import type { HTMLAttributes } from "react";
import { cn } from "@/lib/utils";

export function Code({ className, ...props }: HTMLAttributes<HTMLElement>) {
  return <code className={cn("rounded border border-border bg-muted px-1.5 py-0.5 font-mono text-xs text-foreground", className)} {...props} />;
}

export function Pre({ value, className }: { value: unknown; className?: string }) {
  const text = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  return (
    <pre className={cn("max-h-96 overflow-auto rounded-[5px] border border-border bg-muted p-3 font-mono text-xs", className)}>
      {text}
    </pre>
  );
}
