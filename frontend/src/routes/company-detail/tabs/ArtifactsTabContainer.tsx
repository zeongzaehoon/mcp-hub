import { useMemo, useState } from "react";
import type { ColumnDef } from "@tanstack/react-table";
import { formatBytes, formatDateTime } from "@/lib/utils";
import { useCompanyArtifacts } from "@/domains/companies/queries";
import type { Artifact, ArtifactFilters } from "@/domains/companies/types";
import { useServersPage } from "@/domains/servers/queries";
import { useUsersPage } from "@/domains/users/queries";
import { Badge } from "@/components/ui/Badge";
import { Code } from "@/components/ui/Code";
import { ArtifactsTabPresenter } from "./ArtifactsTabPresenter";

const EMPTY_FILTERS: ArtifactFilters = { user_id: "", include_expired: false };

export function ArtifactsTabContainer({ companyId }: { companyId: string }) {
  const [filters, setFilters] = useState<ArtifactFilters>(EMPTY_FILTERS);
  const query = useCompanyArtifacts(companyId, filters);
  const usersQuery = useUsersPage(companyId, {});
  const serversQuery = useServersPage();

  const users = useMemo(() => usersQuery.data?.items ?? [], [usersQuery.data]);
  const emailById = useMemo(() => new Map(users.map((u) => [u.user_id, u.email])), [users]);
  const serverNameById = useMemo(() => new Map((serversQuery.data?.items ?? []).map((s) => [s.server_id, s.name])), [serversQuery.data]);

  const columns = useMemo<ColumnDef<Artifact>[]>(
    () => [
      { accessorKey: "title", header: "제목", size: 240, cell: ({ row }) => <span className="font-medium" title={row.original.artifact_id}>{row.original.title}</span> },
      { accessorKey: "kind", header: "종류", size: 100, cell: ({ row }) => <Badge variant="outline">{row.original.kind}</Badge> },
      {
        id: "server",
        header: "서버",
        accessorFn: (a) => serverNameById.get(a.server_id) ?? a.server_id,
        size: 180,
        cell: ({ row }) => {
          const name = serverNameById.get(row.original.server_id);
          return name ? <span className="text-sm">{name}</span> : <Code>{row.original.server_id}</Code>;
        },
      },
      {
        id: "user",
        header: "사용자",
        accessorFn: (a) => emailById.get(a.user_id) ?? a.user_id,
        size: 200,
        cell: ({ row }) => {
          const email = emailById.get(row.original.user_id);
          return email ? <span className="text-sm">{email}</span> : <Code>{row.original.user_id}</Code>;
        },
      },
      { accessorKey: "bytes", header: "크기", size: 90, cell: ({ row }) => <span className="font-mono text-xs">{formatBytes(row.original.bytes)}</span> },
      { accessorKey: "created_at", header: "생성", size: 170, cell: ({ row }) => <span className="text-xs">{formatDateTime(row.original.created_at)}</span> },
      {
        accessorKey: "expires_at",
        header: "만료",
        size: 200,
        cell: ({ row }) => (
          <span className="inline-flex items-center gap-1 text-xs">
            {formatDateTime(row.original.expires_at)}
            {row.original.expired ? <Badge variant="warning" dot="muted">만료됨</Badge> : null}
          </span>
        ),
      },
    ],
    [emailById, serverNameById],
  );

  return (
    <ArtifactsTabPresenter
      artifacts={query.data ?? []}
      columns={columns}
      filters={filters}
      onFiltersChange={(patch) => setFilters((f) => ({ ...f, ...patch }))}
      users={users}
      isLoading={query.isPending}
      isRefreshing={query.isFetching}
      error={query.error}
    />
  );
}
