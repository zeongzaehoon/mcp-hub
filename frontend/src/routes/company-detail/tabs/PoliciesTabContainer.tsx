import { useCallback, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import type { ColumnDef } from "@tanstack/react-table";
import { Pencil } from "lucide-react";
import { flattenPages } from "@/lib/cursor";
import { formatDateTime } from "@/lib/utils";
import { useAuthStore } from "@/stores/auth";
import type { Status } from "@/domains/common/enums";
import { useBindingsInfinite } from "@/domains/bindings/queries";
import { useApplyPolicies, usePatchPolicy } from "@/domains/policies/mutations";
import { usePoliciesInfinite } from "@/domains/policies/queries";
import type { Policy } from "@/domains/policies/types";
import { useServersPage } from "@/domains/servers/queries";
import type { Server } from "@/domains/servers/types";
import { useTeams } from "@/domains/workspace/queries";
import type { Team } from "@/domains/workspace/types";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Code } from "@/components/ui/Code";
import { PermissionBadges } from "@/components/shared/PermissionBadges";
import { StatusActions, TRANSITION_TARGET, type StatusTransition } from "@/components/shared/StatusActions";
import { StatusBadge } from "@/components/shared/StatusBadge";
import { SourceBadge } from "./SourceBadge";
import { PoliciesTabPresenter } from "./PoliciesTabPresenter";

export interface PolicyTransitionRequest {
  policy: Policy;
  transition: StatusTransition;
}

interface PoliciesTabContainerProps {
  companyId: string;
  companyStatus: Status;
  isMaster: boolean;
}

export function PoliciesTabContainer({ companyId, companyStatus, isMaster }: PoliciesTabContainerProps) {
  const [createOpen, setCreateOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<Policy | null>(null);
  const [transition, setTransition] = useState<PolicyTransitionRequest | null>(null);

  // backend: master, or an admin of this company (require_company_access)
  const isCompanyAdmin = useAuthStore((s) => s.user?.role === "admin" && s.user.company_id === companyId);
  const deleted = companyStatus === "deleted";
  const canEdit = !deleted && (isMaster || isCompanyAdmin);

  const query = usePoliciesInfinite(companyId);
  const policies = useMemo(() => flattenPages(query.data), [query.data]);

  const bindingsQuery = useBindingsInfinite(companyId);
  const bindings = useMemo(() => flattenPages(bindingsQuery.data), [bindingsQuery.data]);
  const activeBindings = useMemo(() => bindings.filter((b) => b.status === "active"), [bindings]);

  const serversQuery = useServersPage();
  const serversById = useMemo(() => {
    const m = new Map<string, Server>();
    for (const s of serversQuery.data?.items ?? []) m.set(s.server_id, s);
    return m;
  }, [serversQuery.data]);

  const teamsQuery = useTeams(companyId);
  const teams = useMemo(() => flattenPages(teamsQuery.data), [teamsQuery.data]);
  const teamsById = useMemo(() => {
    const m = new Map<string, Team>();
    for (const t of teams) m.set(t.team_id, t);
    return m;
  }, [teams]);
  const activeTeams = useMemo(() => teams.filter((t) => t.status === "active"), [teams]);

  const patch = usePatchPolicy(companyId);
  const apply = useApplyPolicies(companyId);

  const requestTransition = useCallback((policy: Policy, t: StatusTransition) => setTransition({ policy, transition: t }), []);
  const openEdit = useCallback((policy: Policy) => setEditTarget(policy), []);

  const columns = useMemo<ColumnDef<Policy>[]>(
    () => [
      {
        id: "target",
        header: "대상",
        accessorFn: (x) => (x.team_id === null ? "" : (teamsById.get(x.team_id)?.name ?? x.team_id)),
        size: 220,
        cell: ({ row }) => {
          const id = row.original.team_id;
          if (id === null) return <Badge variant="info">전원</Badge>;
          const t = teamsById.get(id);
          return t ? (
            <span className="inline-flex items-center gap-1.5">
              {t.name} <SourceBadge source={t.source} />
            </span>
          ) : (
            <Code>{id}</Code>
          );
        },
      },
      {
        id: "server",
        header: "서버",
        accessorFn: (x) => serversById.get(x.server_id)?.name ?? x.server_id,
        size: 190,
        cell: ({ row }) => {
          const s = serversById.get(row.original.server_id);
          return (
            <Link to={`/servers/${encodeURIComponent(row.original.server_id)}`} className="text-primary hover:underline" onClick={(e) => e.stopPropagation()}>
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
        accessorFn: (x) => x.allowed_tools.length,
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
      ...(canEdit
        ? [
            {
              id: "actions",
              header: "작업",
              enableSorting: false,
              size: 180,
              cell: ({ row }) => {
                const x = row.original;
                return (
                  <span className="inline-flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
                    <Button variant="outline" size="icon" title="권한·도구 편집" onClick={() => openEdit(x)} disabled={x.status === "deleted"}>
                      <Pencil className="h-3.5 w-3.5" />
                    </Button>
                    <StatusActions status={x.status} size="icon" onAction={(t) => requestTransition(x, t)} />
                  </span>
                );
              },
            } satisfies ColumnDef<Policy>,
          ]
        : []),
    ],
    [teamsById, serversById, canEdit, openEdit, requestTransition],
  );

  const confirmTransition = () => {
    if (!transition) return;
    patch.mutate(
      { policyId: transition.policy.policy_id, body: { status: TRANSITION_TARGET[transition.transition] } },
      { onSuccess: () => setTransition(null) },
    );
  };

  return (
    <PoliciesTabPresenter
      companyId={companyId}
      canEdit={canEdit}
      readOnlyHint={deleted ? "삭제된 회사는 수정할 수 없습니다" : "마스터 또는 이 회사의 관리자만 수정할 수 있습니다"}
      policies={policies}
      columns={columns}
      isLoading={query.isPending}
      error={query.error}
      hasNextPage={query.hasNextPage}
      isFetchingNextPage={query.isFetchingNextPage}
      onLoadMore={() => void query.fetchNextPage()}
      serversById={serversById}
      teamsById={teamsById}
      activeTeams={activeTeams}
      activeBindings={activeBindings}
      pickersLoading={bindingsQuery.isPending || serversQuery.isPending || teamsQuery.isPending}
      createOpen={createOpen}
      onCreateOpenChange={(open) => {
        if (open) apply.reset();
        setCreateOpen(open);
      }}
      editTarget={editTarget}
      onEditClose={() => setEditTarget(null)}
      transition={transition}
      onTransitionCancel={() => {
        setTransition(null);
        patch.reset();
      }}
      onTransitionConfirm={confirmTransition}
      transitionPending={patch.isPending}
      transitionError={patch.error}
      onApply={() => apply.mutate()}
      applyPending={apply.isPending}
      applyResult={apply.data}
      applyError={apply.error}
    />
  );
}
