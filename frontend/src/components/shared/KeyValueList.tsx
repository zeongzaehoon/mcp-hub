import type { ReactNode } from "react";

export interface KeyValueItem {
  label: string;
  value: ReactNode;
}

export function KeyValueList({ items }: { items: KeyValueItem[] }) {
  return (
    <dl className="grid grid-cols-1 gap-x-6 gap-y-3 text-sm sm:grid-cols-2">
      {items.map((it) => (
        <div key={it.label} className="flex flex-col gap-0.5">
          <dt className="text-[13px] font-semibold text-muted-foreground">{it.label}</dt>
          <dd className="min-w-0 break-all">{it.value}</dd>
        </div>
      ))}
    </dl>
  );
}
