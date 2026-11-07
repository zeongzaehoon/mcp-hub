import { useCallback, useMemo, useState } from "react";
import type { ColumnDef } from "@tanstack/react-table";
import { flattenPages } from "@/lib/cursor";
import { formatDateTime } from "@/lib/utils";
import { useAuthStore } from "@/stores/auth";
import { ROLE_LABEL, type Status } from "@/domains/common/enums";
import { usePatchUser } from "@/domains/users/mutations";
import { useUsersInfinite } from "@/domains/users/queries";
import type { User, UserFilters } from "@/domains/users/types";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Code } from "@/components/ui/Code";
import { StatusActions, TRANSITION_TARGET, type StatusTransition } from "@/components/shared/StatusActions";
import { StatusBadge } from "@/components/shared/StatusBadge";
import { KeyRound, Server } from "lucide-react";
import { UsersTabPresenter } from "./UsersTabPresenter";

const EMPTY_FILTERS: UserFilters = { q: "", status: "" };

export interface UserTransitionRequest {
  user: User;
  transition: StatusTransition;
}

interface UsersTabContainerProps {
  companyId: string;
  companyStatus: Status;
  /** open the access tab filtered to this user */
  onOpenAccess: (userId: string) => void;
}

export function UsersTabContainer({ companyId, companyStatus, onOpenAccess }: UsersTabContainerProps) {
  const me = useAuthStore((s) => s.user);
  const [filters, setFilters] = useState<UserFilters>(EMPTY_FILTERS);
  const [createOpen, setCreateOpen] = useState(false);
  const [passwordTarget, setPasswordTarget] = useState<User | null>(null);
  const [transition, setTransition] = useState<UserTransitionRequest | null>(null);

  const query = useUsersInfinite(companyId, filters);
  const users = useMemo(() => flattenPages(query.data), [query.data]);
  const patch = usePatchUser(companyId);

  const requestTransition = useCallback((user: User, t: StatusTransition) => setTransition({ user, transition: t }), []);
  const openPassword = useCallback((user: User) => setPasswordTarget(user), []);

  const columns = useMemo<ColumnDef<User>[]>(
    () => [
      {
        accessorKey: "email",
        header: "이메일",
        size: 240,
        cell: ({ row }) => {
          const ids = row.original.identities ?? [];
          return (
            <span className="inline-flex items-center gap-1.5">
              {row.original.email}
              {ids.length > 0 ? (
                <Badge variant="info" title={`SSO 연결됨: ${ids.map((i) => i.issuer).join(", ")}`}>
                  SSO
                </Badge>
              ) : null}
              {row.original.source === "workspace" ? (
                <Badge variant="secondary" title="Google Workspace 디렉터리에서 동기화된 사용자">
                  Workspace
                </Badge>
              ) : null}
            </span>
          );
        },
      },
      { accessorKey: "name", header: "이름", size: 160 },
      {
        accessorKey: "role",
        header: "역할",
        size: 100,
        cell: ({ row }) => (
          <Badge variant={row.original.role === "master" ? "default" : row.original.role === "admin" ? "info" : "secondary"}>
            {ROLE_LABEL[row.original.role] ?? row.original.role}
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
        accessorKey: "can_login",
        header: "로그인",
        size: 90,
        cell: ({ row }) => (
          <span className="text-xs">{row.original.can_login ? "가능" : <span className="text-muted-foreground">불가</span>}</span>
        ),
      },
      {
        accessorKey: "user_id",
        header: "사용자 ID",
        size: 200,
        cell: ({ row }) => <Code>{row.original.user_id}</Code>,
      },
      {
        accessorKey: "created_at",
        header: "생성일",
        size: 170,
        cell: ({ row }) => <span className="text-xs">{formatDateTime(row.original.created_at)}</span>,
      },
      {
        id: "actions",
        header: "작업",
        enableSorting: false,
        size: 360,
        cell: ({ row }) => {
          const u = row.original;
          const isSelf = me?.user_id === u.user_id;
          return (
            <span className="inline-flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
              <Button variant="outline" size="sm" onClick={() => onOpenAccess(u.user_id)} title="이 사용자의 서버 접근 관리">
                <Server className="h-3.5 w-3.5" /> 서버 접근
              </Button>
              {u.status !== "deleted" ? (
                <Button variant="outline" size="sm" onClick={() => openPassword(u)} title="비밀번호 설정">
                  <KeyRound className="h-3.5 w-3.5" /> 비밀번호
                </Button>
              ) : null}
              <StatusActions status={u.status} disabled={isSelf} onAction={(t) => requestTransition(u, t)} />
            </span>
          );
        },
      },
    ],
    [me?.user_id, openPassword, requestTransition, onOpenAccess],
  );

  const confirmTransition = () => {
    if (!transition) return;
    patch.mutate(
      { userId: transition.user.user_id, body: { status: TRANSITION_TARGET[transition.transition] } },
      { onSuccess: () => setTransition(null) },
    );
  };

  return (
    <UsersTabPresenter
      companyId={companyId}
      companyDeleted={companyStatus === "deleted"}
      users={users}
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
      createOpen={createOpen}
      onCreateOpenChange={setCreateOpen}
      passwordTarget={passwordTarget}
      onPasswordClose={() => setPasswordTarget(null)}
      transition={transition}
      onTransitionCancel={() => {
        setTransition(null);
        patch.reset();
      }}
      onTransitionConfirm={confirmTransition}
      transitionPending={patch.isPending}
      transitionError={patch.error}
      isMasterViewer={me?.role === "master"}
    />
  );
}
