import type { ColumnDef } from "@tanstack/react-table";
import { Checkbox } from "@/components/ui/Checkbox";
import { Select } from "@/components/ui/Select";
import { DataTable } from "@/components/shared/DataTable";
import { ErrorAlert } from "@/components/shared/ErrorAlert";
import type { Artifact, ArtifactFilters } from "@/domains/companies/types";
import type { User } from "@/domains/users/types";

interface ArtifactsTabPresenterProps {
  artifacts: Artifact[];
  columns: ColumnDef<Artifact>[];
  filters: ArtifactFilters;
  onFiltersChange: (patch: Partial<ArtifactFilters>) => void;
  users: User[];
  isLoading: boolean;
  isRefreshing: boolean;
  error: unknown;
}

export function ArtifactsTabPresenter(p: ArtifactsTabPresenterProps) {
  return (
    <div className="space-y-3" data-testid="artifacts-tab">
      <p className="text-sm text-muted-foreground">
        MCP 도구가 S3에 저장한 산출물의 메타데이터입니다. 내용과 다운로드는 MCP <code className="font-mono">export</code> 경로에서만 가능합니다.
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <Select value={p.filters.user_id ?? ""} onChange={(e) => p.onFiltersChange({ user_id: e.target.value })} className="w-56" aria-label="사용자 필터">
          <option value="">전체 사용자</option>
          {p.users.map((u) => (
            <option key={u.user_id} value={u.user_id}>
              {u.email}
            </option>
          ))}
        </Select>
        <Checkbox
          label="만료된 산출물 포함"
          checked={!!p.filters.include_expired}
          onChange={(e) => p.onFiltersChange({ include_expired: e.target.checked })}
          className="py-1.5"
        />
      </div>
      <ErrorAlert error={p.error} />
      <DataTable
        data={p.artifacts}
        columns={p.columns}
        getRowId={(a) => a.artifact_id}
        isLoading={p.isLoading}
        isRefreshing={p.isRefreshing}
        emptyMessage="산출물이 없습니다"
      />
    </div>
  );
}
