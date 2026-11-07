import { useMemo } from "react";
import type { ColumnDef } from "@tanstack/react-table";
import { Badge } from "@/components/ui/Badge";
import { Code } from "@/components/ui/Code";
import { formatDateTime, truncate } from "@/lib/utils";
import { LOG_INTERNAL_FIELDS, type LogRecord } from "@/domains/logs/types";
import { DataTable } from "./DataTable";
import { LoadMore } from "./LoadMore";

interface LogsTableProps {
  logs: LogRecord[];
  isLoading: boolean;
  isRefreshing: boolean;
  hasNextPage: boolean;
  isFetchingNextPage: boolean;
  onLoadMore: () => void;
  height?: number;
  showKeyId?: boolean;
}

function extraFields(row: LogRecord): string {
  const rest: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(row)) {
    if (!LOG_INTERNAL_FIELDS.has(k)) rest[k] = v;
  }
  return Object.keys(rest).length ? JSON.stringify(rest) : "";
}

function logRowId(row: LogRecord): string {
  return `${row.PK ?? ""}|${row.SK ?? ""}|${row.ts ?? ""}|${row.key_id ?? ""}`;
}

export function LogsTable({
  logs,
  isLoading,
  isRefreshing,
  hasNextPage,
  isFetchingNextPage,
  onLoadMore,
  height = 520,
  showKeyId = true,
}: LogsTableProps) {
  const columns = useMemo<ColumnDef<LogRecord>[]>(() => {
    const cols: ColumnDef<LogRecord>[] = [
      {
        accessorKey: "ts",
        header: "시각",
        size: 180,
        cell: ({ row }) => <span className="font-mono text-xs">{formatDateTime(row.original.ts)}</span>,
      },
    ];
    if (showKeyId) {
      cols.push({
        accessorKey: "key_id",
        header: "키 ID",
        size: 160,
        cell: ({ row }) => <Code>{row.original.key_id ?? "-"}</Code>,
      });
    }
    cols.push(
      {
        accessorKey: "server_id",
        header: "서버",
        size: 140,
        cell: ({ row }) => <span className="text-xs">{row.original.server_id ?? "-"}</span>,
      },
      {
        accessorKey: "tool",
        header: "도구",
        size: 140,
        cell: ({ row }) => (row.original.tool ? <Code>{row.original.tool}</Code> : "-"),
      },
      {
        accessorKey: "status",
        header: "결과",
        size: 90,
        cell: ({ row }) => {
          const st = row.original.status;
          if (!st) return <span className="text-xs text-muted-foreground">-</span>;
          const variant = st === "ok" ? "success" : st === "denied" ? "warning" : "danger";
          const label = st === "ok" ? "성공" : st === "denied" ? "거부" : "오류";
          return <Badge variant={variant}>{label}</Badge>;
        },
      },
      {
        accessorKey: "duration_ms",
        header: "소요(ms)",
        size: 90,
        cell: ({ row }) => <span className="text-xs">{row.original.duration_ms ?? "-"}</span>,
      },
      {
        id: "detail",
        header: "상세",
        enableSorting: false,
        cell: ({ row }) => {
          const err = row.original.error;
          const args = row.original.args_summary;
          const extra = extraFields(row.original);
          const text = [err ? `error: ${err}` : "", args ? `args: ${args}` : "", extra].filter(Boolean).join(" ");
          return text ? (
            <span className="font-mono text-xs text-muted-foreground" title={text}>
              {truncate(text, 80)}
            </span>
          ) : (
            "-"
          );
        },
      },
    );
    return cols;
  }, [showKeyId]);

  return (
    <DataTable
      data={logs}
      columns={columns}
      getRowId={logRowId}
      isLoading={isLoading}
      isRefreshing={isRefreshing}
      height={height}
      emptyMessage="로그가 없습니다"
      footer={
        <LoadMore
          loaded={logs.length}
          hasNextPage={hasNextPage}
          isFetchingNextPage={isFetchingNextPage}
          onLoadMore={onLoadMore}
        />
      }
    />
  );
}
