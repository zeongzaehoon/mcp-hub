import { forwardRef, type HTMLAttributes, type TdHTMLAttributes, type ThHTMLAttributes } from "react";
import { cn } from "@/lib/utils";

export function Table({ className, ...props }: HTMLAttributes<HTMLTableElement>) {
  return <table className={cn("w-full caption-bottom border-separate border-spacing-0 text-sm", className)} {...props} />;
}

/** knitlog `.thead-row`: dark head, white 13px bold, first/last cell rounded 8px. */
export function THead({ className, ...props }: HTMLAttributes<HTMLTableSectionElement>) {
  return (
    <thead
      className={cn(
        "sticky top-0 z-10 bg-card [&_th:first-child]:rounded-l-lg [&_th:last-child]:rounded-r-lg",
        className,
      )}
      {...props}
    />
  );
}

export function TBody({ className, ...props }: HTMLAttributes<HTMLTableSectionElement>) {
  return <tbody className={cn("[&_tr:last-child_td]:border-b-0", className)} {...props} />;
}

export const TR = forwardRef<HTMLTableRowElement, HTMLAttributes<HTMLTableRowElement>>(({ className, ...props }, ref) => (
  <tr ref={ref} className={cn("bg-card transition-colors duration-200", className)} {...props} />
));
TR.displayName = "TR";

export function TH({ className, ...props }: ThHTMLAttributes<HTMLTableCellElement>) {
  return (
    <th
      className={cn("h-11 whitespace-nowrap bg-table-head px-2.5 text-left align-middle text-[13px] font-bold text-sidebar-foreground", className)}
      {...props}
    />
  );
}

export function TD({ className, ...props }: TdHTMLAttributes<HTMLTableCellElement>) {
  return <td className={cn("h-12 border-b border-border px-2.5 py-2 align-middle text-sm text-foreground", className)} {...props} />;
}
