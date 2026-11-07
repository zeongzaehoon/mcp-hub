import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

interface PageHeaderProps {
  /** Omit on list pages: the app header already carries the route title. Detail pages pass the entity name. */
  title?: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  breadcrumb?: ReactNode;
  /** small uppercase mono label above the title */
  label?: ReactNode;
}

export function PageHeader({ title, description, actions, breadcrumb, label }: PageHeaderProps) {
  return (
    <div className="mb-4 flex min-h-[38px] flex-wrap items-center justify-between gap-3">
      <div className="min-w-0">
        {breadcrumb ? <div className="mb-1.5 text-xs text-muted-foreground">{breadcrumb}</div> : null}
        {label ? <div className="eyebrow mb-1">{label}</div> : null}
        {title ? <h1 className="truncate text-xl font-bold tracking-tight">{title}</h1> : null}
        {description ? <div className={cn("text-[13px] text-muted-foreground", title && "mt-1")}>{description}</div> : null}
      </div>
      {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
    </div>
  );
}
