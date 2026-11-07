import { useMemo } from "react";
import { Link } from "react-router-dom";
import type { ColumnDef } from "@tanstack/react-table";
import { flattenPages } from "@/lib/cursor";
import { formatDateTime } from "@/lib/utils";
import { TENANT_MODE_LABEL } from "@/domains/common/enums";
import { describeTenant, type Binding } from "@/domains/bindings/types";
import { useServerBindingsInfinite } from "@/domains/servers/queries";
import { Code } from "@/components/ui/Code";
import { DataTable } from "@/components/shared/DataTable";
import { ErrorAlert } from "@/components/shared/ErrorAlert";
import { LoadMore } from "@/components/shared/LoadMore";
import { StatusBadge } from "@/components/shared/StatusBadge";

export function ServerBindingsTab({ serverId }: { serverId: string }) {
  const query = useServerBindingsInfinite(serverId);
  const bindings = useMemo(() => flattenPages(query.data), [query.data]);

  const columns = useMemo<ColumnDef<Binding>[]>(
    () => [
      {
        accessorKey: "company_id",
        header: "회사",
        size: 220,
        cell: ({ row }) => (
          <Link to={`/companies/${encodeURIComponent(row.original.company_id)}?tab=bindings`} className="text-primary hover:underline">
            <Code>{row.original.company_id}</Code>
          </Link>
        ),
      },
      {
        id: "mode",
        header: "격리 방식",
        accessorFn: (b) => b.tenant.mode,
        size: 140,
        cell: ({ row }) => <span className="text-xs">{TENANT_MODE_LABEL[row.original.tenant.mode] ?? row.original.tenant.mode}</span>,
      },
      {
        id: "tenant",
        header: "테넌트",
        accessorFn: (b) => describeTenant(b.tenant),
        cell: ({ row }) => <Code>{describeTenant(row.original.tenant)}</Code>,
      },
      {
        id: "credentials",
        header: "자격증명",
        enableSorting: false,
        size: 140,
        cell: ({ row }) => {
          const c = row.original.tenant.credentials;
          return c ? <span className="text-xs">{c.username} / ***</span> : <span className="text-xs text-muted-foreground">기본</span>;
        },
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
        data={bindings}
        columns={columns}
        getRowId={(b) => `${b.company_id}:${b.server_id}`}
        isLoading={query.isPending}
        emptyMessage="이 서버에 바인딩된 회사가 없습니다"
        footer={
          <LoadMore loaded={bindings.length} hasNextPage={query.hasNextPage} isFetchingNextPage={query.isFetchingNextPage} onLoadMore={() => void query.fetchNextPage()} />
        }
      />
    </div>
  );
}
