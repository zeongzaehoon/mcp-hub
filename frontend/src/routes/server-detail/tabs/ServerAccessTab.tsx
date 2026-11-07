import { useMemo } from "react";
import { Link } from "react-router-dom";
import type { ColumnDef } from "@tanstack/react-table";
import { flattenPages } from "@/lib/cursor";
import { formatDateTime } from "@/lib/utils";
import type { Access } from "@/domains/access/types";
import { useServerAccessInfinite } from "@/domains/servers/queries";
import { Badge } from "@/components/ui/Badge";
import { Code } from "@/components/ui/Code";
import { DataTable } from "@/components/shared/DataTable";
import { ErrorAlert } from "@/components/shared/ErrorAlert";
import { LoadMore } from "@/components/shared/LoadMore";
import { PermissionBadges } from "@/components/shared/PermissionBadges";
import { StatusBadge } from "@/components/shared/StatusBadge";

/** Who (company × user) has access to this server. Master only. */
export function ServerAccessTab({ serverId }: { serverId: string }) {
  const query = useServerAccessInfinite(serverId);
  const accesses = useMemo(() => flattenPages(query.data), [query.data]);

  const columns = useMemo<ColumnDef<Access>[]>(
    () => [
      {
        accessorKey: "company_id",
        header: "회사",
        size: 200,
        cell: ({ row }) => (
          <Link to={`/companies/${encodeURIComponent(row.original.company_id)}?tab=access`} className="text-primary hover:underline">
            <Code>{row.original.company_id}</Code>
          </Link>
        ),
      },
      {
        accessorKey: "user_id",
        header: "사용자 ID",
        size: 220,
        cell: ({ row }) => (
          <Link
            to={`/companies/${encodeURIComponent(row.original.company_id)}?tab=access&user=${encodeURIComponent(row.original.user_id)}`}
            className="text-primary hover:underline"
          >
            <Code>{row.original.user_id}</Code>
          </Link>
        ),
      },
      { id: "permissions", header: "권한", enableSorting: false, size: 220, cell: ({ row }) => <PermissionBadges permissions={row.original.permissions} /> },
      {
        id: "tools",
        header: "도구",
        accessorFn: (a) => a.allowed_tools.length,
        size: 80,
        cell: ({ row }) => (
          <Badge variant="secondary" title={row.original.allowed_tools.join(", ")}>
            {row.original.allowed_tools.length}개
          </Badge>
        ),
      },
      { accessorKey: "status", header: "상태", size: 100, cell: ({ row }) => <StatusBadge status={row.original.status} /> },
      {
        accessorKey: "updated_at",
        header: "수정일",
        size: 170,
        cell: ({ row }) => <span className="text-xs">{formatDateTime(row.original.updated_at)}</span>,
      },
    ],
    [],
  );

  return (
    <div className="space-y-3">
      <ErrorAlert error={query.error} />
      <DataTable
        data={accesses}
        columns={columns}
        getRowId={(a) => `${a.company_id}:${a.user_id}`}
        isLoading={query.isPending}
        emptyMessage="이 서버에 접근 권한이 부여된 사용자가 없습니다"
        footer={
          <LoadMore loaded={accesses.length} hasNextPage={query.hasNextPage} isFetchingNextPage={query.isFetchingNextPage} onLoadMore={() => void query.fetchNextPage()} />
        }
      />
    </div>
  );
}
