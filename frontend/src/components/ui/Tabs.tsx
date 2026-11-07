import { cn } from "@/lib/utils";

export interface TabItem<K extends string> {
  key: K;
  label: string;
  count?: number;
}

interface TabsProps<K extends string> {
  items: readonly TabItem<K>[];
  value: K;
  onChange: (key: K) => void;
  className?: string;
}

/** Segmented control: white track with a hairline, the active tab is a blue pill. */
export function Tabs<K extends string>({ items, value, onChange, className }: TabsProps<K>) {
  return (
    <div role="tablist" className={cn("inline-flex max-w-full flex-wrap gap-0.5 rounded-lg border border-border bg-card p-1", className)}>
      {items.map((item) => {
        const active = item.key === value;
        return (
          <button
            key={item.key}
            role="tab"
            type="button"
            aria-selected={active}
            onClick={() => onChange(item.key)}
            className={cn(
              "rounded-[5px] px-3 py-1.5 text-sm font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              active ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-muted hover:text-foreground",
            )}
          >
            {item.label}
            {item.count !== undefined ? (
              <span className={cn("ml-1 font-mono text-xs", active ? "text-primary-foreground/70" : "text-subtle")}>{item.count}</span>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}
