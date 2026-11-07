import { useMemo, useState } from "react";
import type { ColumnDef } from "@tanstack/react-table";
import { flattenPages } from "@/lib/cursor";
import { formatDateTime } from "@/lib/utils";
import { useTeams } from "@/domains/workspace/queries";
import type { Team } from "@/domains/workspace/types";
import { Code } from "@/components/ui/Code";
import { StatusBadge } from "@/components/shared/StatusBadge";
import { SourceBadge } from "./SourceBadge";
import { TeamsTabPresenter } from "./TeamsTabPresenter";

export function TeamsTabContainer({ companyId }: { companyId: string }) {
  const [selected, setSelected] = useState<Team | null>(null);
  const query = useTeams(companyId);
  const teams = useMemo(() => flattenPages(query.data), [query.data]);

  const columns = useMemo<ColumnDef<Team>[]>(
    () => [
      { accessorKey: "name", header: "이름", size: 220 },
      {
        accessorKey: "email",
        header: "이메일",
        size: 240,
        cell: ({ row }) => row.original.email ?? <span className="text-muted-foreground">-</span>,
      },
      {
        accessorKey: "source",
        header: "출처",
        size: 110,
        cell: ({ row }) => <SourceBadge source={row.original.source} />,
      },
      {
        accessorKey: "status",
        header: "상태",
        size: 100,
        cell: ({ row }) => <StatusBadge status={row.original.status} />,
      },
      {
        accessorKey: "team_id",
        header: "팀 ID",
        size: 200,
        cell: ({ row }) => <Code>{row.original.team_id}</Code>,
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

  return (
    <TeamsTabPresenter
      companyId={companyId}
      teams={teams}
      columns={columns}
      isLoading={query.isPending}
      error={query.error}
      hasNextPage={query.hasNextPage}
      isFetchingNextPage={query.isFetchingNextPage}
      onLoadMore={() => void query.fetchNextPage()}
      selected={selected}
      onSelect={setSelected}
    />
  );
}
