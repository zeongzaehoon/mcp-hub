import { useRef, useState, type ReactNode } from "react";
import {
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type Row,
  type SortingState,
} from "@tanstack/react-table";
import { useVirtualizer } from "@tanstack/react-virtual";
import { ArrowDown, ArrowUp, ArrowUpDown } from "lucide-react";
import { cn } from "@/lib/utils";
import { Spinner } from "@/components/ui/Spinner";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/Table";

export interface DataTableProps<T> {
  data: T[];
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  columns: ColumnDef<T, any>[];
  getRowId: (row: T) => string;
  onRowClick?: (row: T) => void;
  isLoading?: boolean;
  /** Shown while refetching with existing data (e.g. filters changed with keepPreviousData). */
  isRefreshing?: boolean;
  emptyMessage?: string;
  /** Height of the scroll container in px. Rows are virtualized inside it. */
  height?: number;
  rowHeight?: number;
  initialSorting?: SortingState;
  footer?: ReactNode;
  /** search / filter row rendered inside the card above the table head (`SearchFilterBar`) */
  toolbar?: ReactNode;
  className?: string;
}

export function DataTable<T>({
  data,
  columns,
  getRowId,
  onRowClick,
  isLoading = false,
  isRefreshing = false,
  emptyMessage = "데이터가 없습니다",
  height = 560,
  rowHeight = 48,
  initialSorting = [],
  footer,
  toolbar,
  className,
}: DataTableProps<T>) {
  const [sorting, setSorting] = useState<SortingState>(initialSorting);
  const scrollRef = useRef<HTMLDivElement>(null);

  const table = useReactTable({
    data,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getRowId: (row) => getRowId(row),
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  const rows = table.getRowModel().rows;

  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => rowHeight,
    overscan: 12,
  });

  const virtualItems = virtualizer.getVirtualItems();
  const totalSize = virtualizer.getTotalSize();
  const paddingTop = virtualItems.length > 0 ? virtualItems[0]!.start : 0;
  const paddingBottom = virtualItems.length > 0 ? totalSize - virtualItems[virtualItems.length - 1]!.end : 0;
  const colCount = table.getAllLeafColumns().length;

  return (
    <div className={cn("overflow-hidden rounded-lg border border-border bg-card", className)}>
      {toolbar ? <div className="border-b border-border">{toolbar}</div> : null}
      <div ref={scrollRef} className="relative overflow-auto p-2 pb-0" style={{ maxHeight: height }}>
        <Table>
          <THead>
            {table.getHeaderGroups().map((hg) => (
              <TR key={hg.id}>
                {hg.headers.map((header) => {
                  const canSort = header.column.getCanSort();
                  const sorted = header.column.getIsSorted();
                  return (
                    <TH
                      key={header.id}
                      style={{ width: header.getSize() !== 150 ? header.getSize() : undefined }}
                      className={cn(canSort && "cursor-pointer select-none hover:bg-sidebar-hover")}
                      onClick={canSort ? header.column.getToggleSortingHandler() : undefined}
                    >
                      <span className="inline-flex items-center gap-1">
                        {header.isPlaceholder ? null : flexRender(header.column.columnDef.header, header.getContext())}
                        {canSort ? (
                          sorted === "asc" ? (
                            <ArrowUp className="h-3 w-3" />
                          ) : sorted === "desc" ? (
                            <ArrowDown className="h-3 w-3" />
                          ) : (
                            <ArrowUpDown className="h-3 w-3 opacity-40" />
                          )
                        ) : null}
                      </span>
                    </TH>
                  );
                })}
              </TR>
            ))}
          </THead>
          <TBody>
            {isLoading && rows.length === 0 ? (
              <TR>
                <TD colSpan={colCount} className="h-32 text-center">
                  <div className="flex items-center justify-center">
                    <Spinner />
                  </div>
                </TD>
              </TR>
            ) : rows.length === 0 ? (
              <TR>
                <TD colSpan={colCount} className="h-32 text-center text-sm text-muted-foreground">
                  {emptyMessage}
                </TD>
              </TR>
            ) : (
              <>
                {paddingTop > 0 ? (
                  <tr>
                    <td style={{ height: paddingTop }} colSpan={colCount} />
                  </tr>
                ) : null}
                {virtualItems.map((vi) => {
                  const row = rows[vi.index] as Row<T>;
                  return (
                    <TR
                      key={row.id}
                      data-index={vi.index}
                      ref={virtualizer.measureElement}
                      onClick={onRowClick ? () => onRowClick(row.original) : undefined}
                      className={cn("hover:bg-black/3", onRowClick && "cursor-pointer")}
                    >
                      {row.getVisibleCells().map((cell) => (
                        <TD key={cell.id} className="whitespace-nowrap">
                          {flexRender(cell.column.columnDef.cell, cell.getContext())}
                        </TD>
                      ))}
                    </TR>
                  );
                })}
                {paddingBottom > 0 ? (
                  <tr>
                    <td style={{ height: paddingBottom }} colSpan={colCount} />
                  </tr>
                ) : null}
              </>
            )}
          </TBody>
        </Table>
        {isRefreshing && rows.length > 0 ? (
          <div className="pointer-events-none absolute right-3 top-12">
            <Spinner className="h-4 w-4" />
          </div>
        ) : null}
      </div>
      {footer ? <div className="border-t border-border px-4 py-2.5">{footer}</div> : null}
    </div>
  );
}
