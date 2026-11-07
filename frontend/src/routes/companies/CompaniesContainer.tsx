import { useCallback, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import type { ColumnDef } from "@tanstack/react-table";
import { flattenPages } from "@/lib/cursor";
import { formatDateTime } from "@/lib/utils";
import { useIsMaster } from "@/stores/auth";
import { useCompaniesInfinite } from "@/domains/companies/queries";
import type { Company, CompanyFilters } from "@/domains/companies/types";
import { useGlobalStats, useRefreshGlobalStats } from "@/domains/ops/queries";
import { Code } from "@/components/ui/Code";
import { StatusBadge } from "@/components/shared/StatusBadge";
import { CompaniesPresenter } from "./CompaniesPresenter";

const EMPTY_FILTERS: CompanyFilters = { q: "", status: "" };

export function CompaniesContainer() {
  const navigate = useNavigate();
  const isMaster = useIsMaster();
  const [filters, setFilters] = useState<CompanyFilters>(EMPTY_FILTERS);
  const [createOpen, setCreateOpen] = useState(false);

  const query = useCompaniesInfinite(filters);
  const companies = useMemo(() => flattenPages(query.data), [query.data]);
  // master only: /ops/stats is 403 for admins
  const stats = useGlobalStats(isMaster);
  const refreshStats = useRefreshGlobalStats();

  const columns = useMemo<ColumnDef<Company>[]>(
    () => [
      {
        accessorKey: "company_id",
        header: "회사 ID",
        size: 200,
        cell: ({ row }) => <Code>{row.original.company_id}</Code>,
      },
      { accessorKey: "name", header: "이름" },
      {
        accessorKey: "status",
        header: "상태",
        size: 110,
        cell: ({ row }) => <StatusBadge status={row.original.status} />,
      },
      { accessorKey: "created_by", header: "생성자", size: 160 },
      {
        accessorKey: "created_at",
        header: "생성일",
        size: 170,
        cell: ({ row }) => <span className="text-xs">{formatDateTime(row.original.created_at)}</span>,
      },
      {
        accessorKey: "updated_at",
        header: "수정일",
        size: 170,
        cell: ({ row }) => <span className="text-xs">{formatDateTime(row.original.updated_at)}</span>,
      },
    ],
    [],
  );

  const onRowClick = useCallback((c: Company) => navigate(`/companies/${encodeURIComponent(c.company_id)}`), [navigate]);

  return (
    <CompaniesPresenter
      companies={companies}
      columns={columns}
      filters={filters}
      onSearchChange={(q) => setFilters((f) => ({ ...f, q }))}
      onStatusChange={(status) => setFilters((f) => ({ ...f, status }))}
      onReset={() => setFilters(EMPTY_FILTERS)}
      isLoading={query.isPending}
      isRefreshing={query.isFetching && !query.isFetchingNextPage}
      error={query.error}
      hasNextPage={query.hasNextPage}
      isFetchingNextPage={query.isFetchingNextPage}
      onLoadMore={() => void query.fetchNextPage()}
      onRowClick={onRowClick}
      isMaster={isMaster}
      stats={stats.data}
      statsError={stats.error ?? refreshStats.error}
      onRefreshStats={() => refreshStats.mutate()}
      statsRefreshing={refreshStats.isPending || stats.isFetching}
      createOpen={createOpen}
      onCreateOpenChange={setCreateOpen}
      onCreated={(c) => {
        setCreateOpen(false);
        navigate(`/companies/${encodeURIComponent(c.company_id)}`);
      }}
    />
  );
}
