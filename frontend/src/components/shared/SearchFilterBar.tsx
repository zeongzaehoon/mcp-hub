import { useEffect, useState, type ReactNode } from "react";
import { RotateCcw, Search } from "lucide-react";
import { cn } from "@/lib/utils";
import { useDebouncedValue } from "@/hooks/useDebouncedValue";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { STATUS_LABEL, STATUS_VALUES, type Status } from "@/domains/common/enums";

interface SearchFilterBarProps {
  /** Debounced (300ms) search value. Omit `onSearchChange` to hide the search input. */
  search?: string;
  onSearchChange?: (q: string) => void;
  searchPlaceholder?: string;
  status?: Status | "";
  onStatusChange?: (status: Status | "") => void;
  /** Extra filter controls rendered after the status select. */
  children?: ReactNode;
  /** Right-aligned actions (e.g. create button). */
  actions?: ReactNode;
  onReset?: () => void;
  /** total result count, shown right as "총 N개" when known */
  count?: number;
  className?: string;
}

/** Inline toolbar meant to sit inside the table card (`DataTable` `toolbar`): 36px controls, hairline below. */
export function SearchFilterBar({
  search = "",
  onSearchChange,
  searchPlaceholder = "검색",
  status,
  onStatusChange,
  children,
  actions,
  onReset,
  count,
  className,
}: SearchFilterBarProps) {
  const [text, setText] = useState(search);
  const debounced = useDebouncedValue(text, 300);

  useEffect(() => {
    if (onSearchChange && debounced !== search) onSearchChange(debounced);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debounced]);

  // keep local text in sync when parent resets the filter
  useEffect(() => {
    if (search === "" && text !== "") setText("");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search]);

  return (
    <div className={cn("flex flex-wrap items-center gap-2 px-4 py-3", className)}>
      {onSearchChange ? (
        <div className="relative w-[260px]">
          <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-subtle" />
          <Input
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder={searchPlaceholder}
            className="h-9 pl-9"
            aria-label="검색"
          />
        </div>
      ) : null}
      {onStatusChange ? (
        <Select
          value={status ?? ""}
          onChange={(e) => onStatusChange(e.target.value as Status | "")}
          className="h-9 w-36"
          aria-label="상태 필터"
        >
          <option value="">전체 상태</option>
          {STATUS_VALUES.map((s) => (
            <option key={s} value={s}>
              {STATUS_LABEL[s]}
            </option>
          ))}
        </Select>
      ) : null}
      {children}
      {onReset ? (
        <Button variant="ghost" size="sm" onClick={onReset}>
          <RotateCcw className="h-3.5 w-3.5" /> 초기화
        </Button>
      ) : null}
      <div className="ml-auto flex items-center gap-3">
        {count !== undefined ? <span className="text-xs text-muted-foreground">총 {count.toLocaleString()}개</span> : null}
        {actions}
      </div>
    </div>
  );
}
