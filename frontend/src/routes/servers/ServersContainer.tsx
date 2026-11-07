import { useCallback, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import type { ColumnDef } from "@tanstack/react-table";
import { flattenPages } from "@/lib/cursor";
import { formatDateTime, truncate } from "@/lib/utils";
import { useIsMaster } from "@/stores/auth";
import { useServersInfinite } from "@/domains/servers/queries";
import type { Server, ServerFilters } from "@/domains/servers/types";
import { useGlobalStats, useHealth } from "@/domains/ops/queries";
import { Badge } from "@/components/ui/Badge";
import { Code } from "@/components/ui/Code";
import { HealthBadge } from "@/components/shared/HealthBadge";
import { StatusBadge } from "@/components/shared/StatusBadge";
import { ServersPresenter } from "./ServersPresenter";

const EMPTY_FILTERS: ServerFilters = { status: "", server_type: "" };

export function ServersContainer() {
  const navigate = useNavigate();
  const isMaster = useIsMaster();
  const [filters, setFilters] = useState<ServerFilters>(EMPTY_FILTERS);
  const [createOpen, setCreateOpen] = useState(false);

  const query = useServersInfinite(filters);
  const servers = useMemo(() => flattenPages(query.data), [query.data]);
  // master only: /ops/* is 403 for admins
  const stats = useGlobalStats(isMaster);
  const health = useHealth(isMaster);
  const bindingsPerServer = stats.data?.bindings_per_server;

  const columns = useMemo<ColumnDef<Server>[]>(() => {
    const cols: ColumnDef<Server>[] = [
      { accessorKey: "name", header: "이름", size: 140, cell: ({ row }) => <span className="font-medium">{row.original.name}</span> },
      {
        accessorKey: "server_type",
        header: "타입",
        size: 100,
        cell: ({ row }) => <Badge variant="outline">{row.original.server_type}</Badge>,
      },
      { accessorKey: "server_id", header: "서버 ID", size: 190, cell: ({ row }) => <Code>{row.original.server_id}</Code> },
      {
        accessorKey: "endpoint",
        header: "엔드포인트",
        size: 190,
        cell: ({ row }) => (
          <span className="font-mono text-xs" title={row.original.endpoint}>
            {row.original.endpoint ? truncate(row.original.endpoint, 26) : <span className="text-muted-foreground">-</span>}
          </span>
        ),
      },
      {
        id: "tools",
        header: "도구",
        accessorFn: (s) => Object.keys(s.tools).length,
        size: 70,
        cell: ({ row }) => <Badge variant="secondary">{Object.keys(row.original.tools).length}개</Badge>,
      },
      { accessorKey: "status", header: "상태", size: 90, cell: ({ row }) => <StatusBadge status={row.original.status} /> },
      {
        id: "health",
        header: "헬스",
        accessorFn: (s) => (s.health ? (s.health.ok === true ? 2 : s.health.ok === false ? 0 : 1) : -1),
        size: 120,
        cell: ({ row }) => <HealthBadge health={row.original.health} />,
      },
    ];
    if (bindingsPerServer) {
      cols.push({
        id: "bindings",
        header: "바인딩",
        accessorFn: (s) => bindingsPerServer[s.server_id] ?? 0,
        size: 70,
        cell: ({ row }) => <span className="font-mono text-xs">{bindingsPerServer[row.original.server_id] ?? 0}</span>,
      });
    }
    cols.push({
      accessorKey: "updated_at",
      header: "수정일",
      size: 152,
      cell: ({ row }) => <span className="text-xs">{formatDateTime(row.original.updated_at)}</span>,
    });
    return cols;
  }, [bindingsPerServer]);

  const onRowClick = useCallback((s: Server) => navigate(`/servers/${encodeURIComponent(s.server_id)}`), [navigate]);

  return (
    <ServersPresenter
      servers={servers}
      columns={columns}
      filters={filters}
      onFiltersChange={(patch) => setFilters((f) => ({ ...f, ...patch }))}
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
      health={health.data}
      statsError={stats.error ?? health.error}
      createOpen={createOpen}
      onCreateOpenChange={setCreateOpen}
      onCreated={(s) => {
        setCreateOpen(false);
        navigate(`/servers/${encodeURIComponent(s.server_id)}`);
      }}
    />
  );
}
