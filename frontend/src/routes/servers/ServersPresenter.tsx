import type { ColumnDef } from "@tanstack/react-table";
import { Plus } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Select } from "@/components/ui/Select";
import { DataTable } from "@/components/shared/DataTable";
import { ErrorAlert } from "@/components/shared/ErrorAlert";
import { LoadMore } from "@/components/shared/LoadMore";
import { PageHeader } from "@/components/shared/PageHeader";
import { SearchFilterBar } from "@/components/shared/SearchFilterBar";
import { StatTile, StatTileRow } from "@/components/shared/StatTile";
import { SERVER_TYPE_VALUES, type ServerType } from "@/domains/common/enums";
import type { Server, ServerFilters } from "@/domains/servers/types";
import type { GlobalStatsOut, HealthOut } from "@/domains/ops/types";
import { ServerCreateDialog } from "./ServerCreateDialog";

interface ServersPresenterProps {
  servers: Server[];
  columns: ColumnDef<Server>[];
  filters: ServerFilters;
  onFiltersChange: (patch: Partial<ServerFilters>) => void;
  onReset: () => void;
  isLoading: boolean;
  isRefreshing: boolean;
  error: unknown;
  hasNextPage: boolean;
  isFetchingNextPage: boolean;
  onLoadMore: () => void;
  onRowClick: (s: Server) => void;
  isMaster: boolean;
  /** master only (undefined while loading or for admins) */
  stats: GlobalStatsOut | undefined;
  health: HealthOut[] | undefined;
  statsError: unknown;
  createOpen: boolean;
  onCreateOpenChange: (open: boolean) => void;
  onCreated: (s: Server) => void;
}

function ServerStatTiles({ stats, health }: { stats: GlobalStatsOut | undefined; health: HealthOut[] | undefined }) {
  const n = (v: number | undefined) => (stats ? (v ?? 0).toLocaleString() : "–");
  const okCount = health?.filter((h) => h.ok === true).length ?? 0;
  const downCount = health?.filter((h) => h.ok === false).length ?? 0;
  return (
    <StatTileRow className="md:grid-cols-4 xl:grid-cols-4 md:[&>*]:border-b-0">
      <StatTile label="서버" value={n(stats?.counts.servers?.active)} dot="accent" />
      <StatTile label="바인딩" value={n(stats?.counts.bindings?.active)} dot="accent" />
      <StatTile label="24h 호출" value={n(stats?.calls_24h?.calls)} sub={stats?.calls_24h ? `거부 ${stats.calls_24h.denied.toLocaleString()} · 오류 ${stats.calls_24h.errors.toLocaleString()}` : null} />
      <StatTile
        label="정상 서비스"
        value={health ? `${okCount} / ${health.length}` : "–"}
        dot={health ? (downCount > 0 ? "danger" : health.length > 0 && okCount === health.length ? "accent" : "hollow") : undefined}
        sub={health && health.length === 0 ? "헬스 체크 대상 없음" : null}
      />
    </StatTileRow>
  );
}

export function ServersPresenter(p: ServersPresenterProps) {
  return (
    <div>
      <PageHeader
        description="클러스터에 등록된 MCP 서버입니다. 서버는 전역 리소스이며 마스터만 관리할 수 있습니다."
        actions={
          p.isMaster ? (
            <Button onClick={() => p.onCreateOpenChange(true)}>
              <Plus className="h-4 w-4" /> 서버 등록
            </Button>
          ) : null
        }
      />
      <div className="space-y-4">
        {p.isMaster ? <ServerStatTiles stats={p.stats} health={p.health} /> : null}
        {p.isMaster ? <ErrorAlert error={p.statsError} /> : null}
        <ErrorAlert error={p.error} />
        <DataTable
          toolbar={
            <SearchFilterBar status={p.filters.status ?? ""} onStatusChange={(status) => p.onFiltersChange({ status })} onReset={p.onReset}>
              <Select
                value={p.filters.server_type ?? ""}
                onChange={(e) => p.onFiltersChange({ server_type: e.target.value as ServerType | "" })}
                className="h-9 w-40"
                aria-label="서버 타입 필터"
              >
                <option value="">전체 타입</option>
                {SERVER_TYPE_VALUES.map((t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ))}
              </Select>
            </SearchFilterBar>
          }
          data={p.servers}
          columns={p.columns}
          getRowId={(s) => s.server_id}
          onRowClick={p.onRowClick}
          isLoading={p.isLoading}
          isRefreshing={p.isRefreshing}
          emptyMessage="등록된 서버가 없습니다"
          footer={<LoadMore loaded={p.servers.length} hasNextPage={p.hasNextPage} isFetchingNextPage={p.isFetchingNextPage} onLoadMore={p.onLoadMore} />}
        />
      </div>
      {p.isMaster ? <ServerCreateDialog open={p.createOpen} onClose={() => p.onCreateOpenChange(false)} onCreated={p.onCreated} /> : null}
    </div>
  );
}
