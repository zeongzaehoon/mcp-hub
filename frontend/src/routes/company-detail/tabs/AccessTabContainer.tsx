import { useCallback, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import type { ColumnDef } from "@tanstack/react-table";
import { Pencil } from "lucide-react";
import { flattenPages } from "@/lib/cursor";
import { formatDateTime } from "@/lib/utils";
import type { Status } from "@/domains/common/enums";
import { usePatchAccess } from "@/domains/access/mutations";
import { useAccessInfinite } from "@/domains/access/queries";
import type { Access, AccessFilters } from "@/domains/access/types";
import { useBindingsInfinite } from "@/domains/bindings/queries";
import { useServersPage } from "@/domains/servers/queries";
import type { Server } from "@/domains/servers/types";
import { useUsersPage } from "@/domains/users/queries";
import type { User } from "@/domains/users/types";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Code } from "@/components/ui/Code";
import { PermissionBadges } from "@/components/shared/PermissionBadges";
import { StatusActions, TRANSITION_TARGET, type StatusTransition } from "@/components/shared/StatusActions";
import { StatusBadge } from "@/components/shared/StatusBadge";
import { AccessTabPresenter } from "./AccessTabPresenter";

export interface AccessTransitionRequest {
  access: Access;
  transition: StatusTransition;
}

interface AccessTabContainerProps {
  companyId: string;
  companyStatus: Status;
  /** preset user filter (e.g. from the users tab "서버 접근" button) */
  initialUserId?: string;
  onUserFilterChange?: (userId: string) => void;
}

export function AccessTabContainer({ companyId, companyStatus, initialUserId = "", onUserFilterChange }: AccessTabContainerProps) {
  const [filters, setFilters] = useState<AccessFilters>({ user_id: initialUserId, server_id: "" });
  const [createOpen, setCreateOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<Access | null>(null);
  /** policy-made row waiting for the "becomes manual" confirmation before the edit dialog opens */
  const [editConfirm, setEditConfirm] = useState<Access | null>(null);
  const [transition, setTransition] = useState<AccessTransitionRequest | null>(null);

  const query = useAccessInfinite(companyId, filters);
  const accesses = useMemo(() => flattenPages(query.data), [query.data]);

  const bindingsQuery = useBindingsInfinite(companyId);
  const bindings = useMemo(() => flattenPages(bindingsQuery.data), [bindingsQuery.data]);
  const activeBindings = useMemo(() => bindings.filter((b) => b.status === "active"), [bindings]);

  const serversQuery = useServersPage();
  const serversById = useMemo(() => {
    const m = new Map<string, Server>();
    for (const s of serversQuery.data?.items ?? []) m.set(s.server_id, s);
    return m;
  }, [serversQuery.data]);

  const usersQuery = useUsersPage(companyId, {});
  const users = useMemo(() => usersQuery.data?.items ?? [], [usersQuery.data]);
  const usersById = useMemo(() => {
    const m = new Map<string, User>();
    for (const u of users) m.set(u.user_id, u);
    return m;
  }, [users]);
  const activeUsers = useMemo(() => users.filter((u) => u.status === "active"), [users]);

  const patch = usePatchAccess(companyId);

  const requestTransition = useCallback((access: Access, t: StatusTransition) => setTransition({ access, transition: t }), []);
  const openEdit = useCallback((access: Access) => {
    if (access.source === "policy") setEditConfirm(access);
    else setEditTarget(access);
  }, []);

  const columns = useMemo<ColumnDef<Access>[]>(
    () => [
      {
        id: "user",
        header: "사용자",
        accessorFn: (a) => usersById.get(a.user_id)?.email ?? a.user_id,
        size: 220,
        cell: ({ row }) => {
          const u = usersById.get(row.original.user_id);
          const policyBadge =
            row.original.source === "policy" ? (
              <Badge variant="info" title={`팀 정책이 만든 접근 (정책 ${row.original.policy_ids.length}개)`}>
                정책
              </Badge>
            ) : null;
          return u ? (
            <span className="flex flex-col">
              <span className="inline-flex items-center gap-1.5 text-sm">
                {u.email} {policyBadge}
              </span>
              <span className="text-xs text-muted-foreground">{u.name}</span>
            </span>
          ) : (
            <span className="inline-flex items-center gap-1.5">
              <Code>{row.original.user_id}</Code> {policyBadge}
            </span>
          );
        },
      },
      {
        id: "server",
        header: "서버",
        accessorFn: (a) => serversById.get(a.server_id)?.name ?? a.server_id,
        size: 190,
        cell: ({ row }) => {
          const s = serversById.get(row.original.server_id);
          return (
            <Link
              to={`/servers/${encodeURIComponent(row.original.server_id)}`}
              className="text-primary hover:underline"
              onClick={(e) => e.stopPropagation()}
            >
              {s?.name ?? row.original.server_id} <span className="text-xs text-muted-foreground">({row.original.server_type})</span>
            </Link>
          );
        },
      },
      {
        id: "permissions",
        header: "권한",
        enableSorting: false,
        size: 220,
        cell: ({ row }) => <PermissionBadges permissions={row.original.permissions} />,
      },
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
      {
        accessorKey: "status",
        header: "상태",
        size: 100,
        cell: ({ row }) => <StatusBadge status={row.original.status} />,
      },
      {
        accessorKey: "updated_at",
        header: "수정일",
        size: 170,
        cell: ({ row }) => <span className="text-xs">{formatDateTime(row.original.updated_at)}</span>,
      },
      {
        id: "actions",
        header: "작업",
        enableSorting: false,
        size: 180,
        cell: ({ row }) => {
          const a = row.original;
          return (
            <span className="inline-flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
              <Button variant="outline" size="icon" title="권한·도구 편집" onClick={() => openEdit(a)} disabled={a.status === "deleted"}>
                <Pencil className="h-3.5 w-3.5" />
              </Button>
              <StatusActions status={a.status} size="icon" onAction={(t) => requestTransition(a, t)} />
            </span>
          );
        },
      },
    ],
    [usersById, serversById, openEdit, requestTransition],
  );

  const confirmTransition = () => {
    if (!transition) return;
    const { access } = transition;
    patch.mutate(
      { userId: access.user_id, serverId: access.server_id, body: { status: TRANSITION_TARGET[transition.transition] } },
      { onSuccess: () => setTransition(null) },
    );
  };

  const changeFilters = (next: Partial<AccessFilters>) => {
    setFilters((f) => ({ ...f, ...next }));
    if (next.user_id !== undefined) onUserFilterChange?.(next.user_id);
  };

  return (
    <AccessTabPresenter
      companyId={companyId}
      companyDeleted={companyStatus === "deleted"}
      accesses={accesses}
      columns={columns}
      filters={filters}
      onFiltersChange={changeFilters}
      onReset={() => changeFilters({ user_id: "", server_id: "" })}
      isLoading={query.isPending}
      isRefreshing={query.isFetching && !query.isFetchingNextPage}
      error={query.error}
      hasNextPage={query.hasNextPage}
      isFetchingNextPage={query.isFetchingNextPage}
      onLoadMore={() => void query.fetchNextPage()}
      serversById={serversById}
      usersById={usersById}
      bindings={bindings}
      activeBindings={activeBindings}
      users={users}
      activeUsers={activeUsers}
      pickersLoading={bindingsQuery.isPending || serversQuery.isPending || usersQuery.isPending}
      createOpen={createOpen}
      onCreateOpenChange={setCreateOpen}
      editTarget={editTarget}
      onEditClose={() => setEditTarget(null)}
      editConfirm={editConfirm}
      onEditConfirmCancel={() => setEditConfirm(null)}
      onEditConfirm={() => {
        setEditTarget(editConfirm);
        setEditConfirm(null);
      }}
      transition={transition}
      onTransitionCancel={() => {
        setTransition(null);
        patch.reset();
      }}
      onTransitionConfirm={confirmTransition}
      transitionPending={patch.isPending}
      transitionError={patch.error}
    />
  );
}
