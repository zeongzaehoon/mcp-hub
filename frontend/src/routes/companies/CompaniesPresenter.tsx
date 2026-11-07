import type { ColumnDef } from "@tanstack/react-table";
import { Plus, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { DataTable } from "@/components/shared/DataTable";
import { ErrorAlert } from "@/components/shared/ErrorAlert";
import { LoadMore } from "@/components/shared/LoadMore";
import { PageHeader } from "@/components/shared/PageHeader";
import { SearchFilterBar } from "@/components/shared/SearchFilterBar";
import { StatTile, StatTileRow } from "@/components/shared/StatTile";
import type { Status } from "@/domains/common/enums";
import type { Company, CompanyFilters } from "@/domains/companies/types";
import type { GlobalStatsOut } from "@/domains/ops/types";
import { CompanyCreateDialog } from "./CompanyCreateDialog";

interface CompaniesPresenterProps {
  companies: Company[];
  columns: ColumnDef<Company>[];
  filters: CompanyFilters;
  onSearchChange: (q: string) => void;
  onStatusChange: (status: Status | "") => void;
  onReset: () => void;
  isLoading: boolean;
  isRefreshing: boolean;
  error: unknown;
  hasNextPage: boolean;
  isFetchingNextPage: boolean;
  onLoadMore: () => void;
  onRowClick: (c: Company) => void;
  isMaster: boolean;
  /** master only (undefined while loading or for admins) */
  stats: GlobalStatsOut | undefined;
  statsError: unknown;
  onRefreshStats: () => void;
  statsRefreshing: boolean;
  createOpen: boolean;
  onCreateOpenChange: (open: boolean) => void;
  onCreated: (c: Company) => void;
}

function GlobalStatTiles({ stats, error }: { stats: GlobalStatsOut | undefined; error: unknown }) {
  const c = stats?.counts;
  const n = (v: number | undefined) => (stats ? (v ?? 0).toLocaleString() : "–");
  const exp = stats?.keys_expiring;
  return (
    <div className="space-y-4">
      <StatTileRow>
        <StatTile label="회사" value={n(c?.companies?.active)} dot="accent" />
        <StatTile label="사용자" value={n(c?.users?.active)} dot="accent" />
        <StatTile label="MCP 서버" value={n(c?.servers?.active)} dot="accent" />
        <StatTile label="바인딩" value={n(c?.bindings?.active)} dot="accent" />
        <StatTile label="활성 API 키" value={n(c?.api_keys?.active)} dot="accent" />
        <StatTile
          label="만료 임박 키"
          value={n(exp?.count)}
          dot={exp && exp.count > 0 ? "hollow" : undefined}
          sub={
            exp ? (
              <>
                {exp.days}일 내
                {exp.expired_still_active > 0 ? <span className="text-destructive"> · 만료 후 활성 {exp.expired_still_active}</span> : null}
              </>
            ) : null
          }
        />
      </StatTileRow>
      <ErrorAlert error={error} />
    </div>
  );
}

export function CompaniesPresenter(p: CompaniesPresenterProps) {
  return (
    <div>
      <PageHeader
        description={p.isMaster ? "회사를 생성하고 사용자·바인딩·API 키를 관리합니다" : "소속 회사의 사용자와 API 키를 관리합니다"}
        actions={
          p.isMaster ? (
            <>
              <Button variant="ghost" size="icon" onClick={p.onRefreshStats} loading={p.statsRefreshing} aria-label="새로고침" title="새로고침">
                {p.statsRefreshing ? null : <RefreshCw className="h-4 w-4" />}
              </Button>
              <Button onClick={() => p.onCreateOpenChange(true)}>
                <Plus className="h-4 w-4" /> 회사 생성
              </Button>
            </>
          ) : null
        }
      />
      <div className="space-y-4">
        {p.isMaster ? <GlobalStatTiles stats={p.stats} error={p.statsError} /> : null}
        <ErrorAlert error={p.error} />
        <DataTable
          toolbar={
            <SearchFilterBar
              search={p.filters.q ?? ""}
              onSearchChange={p.onSearchChange}
              searchPlaceholder="회사 ID / 이름 검색"
              status={p.filters.status ?? ""}
              onStatusChange={p.onStatusChange}
              onReset={p.onReset}
            />
          }
          data={p.companies}
          columns={p.columns}
          getRowId={(c) => c.company_id}
          onRowClick={p.onRowClick}
          isLoading={p.isLoading}
          isRefreshing={p.isRefreshing}
          emptyMessage="조건에 맞는 회사가 없습니다"
          footer={
            <LoadMore
              loaded={p.companies.length}
              hasNextPage={p.hasNextPage}
              isFetchingNextPage={p.isFetchingNextPage}
              onLoadMore={p.onLoadMore}
            />
          }
        />
      </div>
      {p.isMaster ? (
        <CompanyCreateDialog open={p.createOpen} onClose={() => p.onCreateOpenChange(false)} onCreated={p.onCreated} />
      ) : null}
    </div>
  );
}
