import type { ColumnDef } from "@tanstack/react-table";
import { DataTable } from "@/components/shared/DataTable";
import { ErrorAlert } from "@/components/shared/ErrorAlert";
import { LoadMore } from "@/components/shared/LoadMore";
import type { Team } from "@/domains/workspace/types";
import { TeamMembersDialog } from "./TeamMembersDialog";

interface TeamsTabPresenterProps {
  companyId: string;
  teams: Team[];
  columns: ColumnDef<Team>[];
  isLoading: boolean;
  error: unknown;
  hasNextPage: boolean;
  isFetchingNextPage: boolean;
  onLoadMore: () => void;
  selected: Team | null;
  onSelect: (team: Team | null) => void;
}

export function TeamsTabPresenter(p: TeamsTabPresenterProps) {
  return (
    <div className="space-y-3" data-testid="teams-tab">
      <p className="text-xs text-muted-foreground">
        팀은 Google Workspace 디렉터리 동기화로 관리됩니다. 행을 클릭하면 멤버를 볼 수 있습니다.
      </p>
      <ErrorAlert error={p.error} />
      <DataTable
        data={p.teams}
        columns={p.columns}
        getRowId={(t) => t.team_id}
        onRowClick={(t) => p.onSelect(t)}
        isLoading={p.isLoading}
        emptyMessage="팀이 없습니다"
        footer={
          <LoadMore loaded={p.teams.length} hasNextPage={p.hasNextPage} isFetchingNextPage={p.isFetchingNextPage} onLoadMore={p.onLoadMore} />
        }
      />
      <TeamMembersDialog companyId={p.companyId} team={p.selected} onClose={() => p.onSelect(null)} />
    </div>
  );
}
