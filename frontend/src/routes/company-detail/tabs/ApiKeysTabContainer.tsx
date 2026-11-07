import { useCallback, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import type { ColumnDef } from "@tanstack/react-table";
import { CalendarPlus, FileClock, Pencil, RefreshCw } from "lucide-react";
import { flattenPages } from "@/lib/cursor";
import { daysUntil, formatDateTime, isExpired } from "@/lib/utils";
import type { Status } from "@/domains/common/enums";
import { usePatchApiKey, useRotateApiKey } from "@/domains/apikeys/mutations";
import { useApiKeysInfinite } from "@/domains/apikeys/queries";
import { extendedExpiry, type ApiKey, type ApiKeyFilters, type ApiKeyIssued } from "@/domains/apikeys/types";
import { useBindingsInfinite } from "@/domains/bindings/queries";
import { useServersPage } from "@/domains/servers/queries";
import { useSso } from "@/domains/sso/queries";
import type { Server } from "@/domains/servers/types";
import { useUsersPage } from "@/domains/users/queries";
import type { User } from "@/domains/users/types";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Code } from "@/components/ui/Code";
import { StatusActions, TRANSITION_TARGET, type StatusTransition } from "@/components/shared/StatusActions";
import { StatusBadge } from "@/components/shared/StatusBadge";
import { ApiKeysTabPresenter } from "./ApiKeysTabPresenter";

const EMPTY_FILTERS: ApiKeyFilters = { q: "", status: "", user_id: "" };
const EXPIRY_WARN_DAYS = 14;
const EXPIRY_DANGER_DAYS = 3;

export interface KeyTransitionRequest {
  key: ApiKey;
  transition: StatusTransition;
}

export interface IssuedKey extends ApiKeyIssued {
  kind: "created" | "rotated";
}

export function ApiKeysTabContainer({ companyId, companyStatus }: { companyId: string; companyStatus: Status }) {
  const [filters, setFilters] = useState<ApiKeyFilters>(EMPTY_FILTERS);
  const [createOpen, setCreateOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<ApiKey | null>(null);
  const [rotateTarget, setRotateTarget] = useState<ApiKey | null>(null);
  const [logsTarget, setLogsTarget] = useState<ApiKey | null>(null);
  const [transition, setTransition] = useState<KeyTransitionRequest | null>(null);
  const [extendTarget, setExtendTarget] = useState<ApiKey | null>(null);
  const [issued, setIssued] = useState<IssuedKey | null>(null);

  const query = useApiKeysInfinite(companyId, filters);
  const keys = useMemo(() => flattenPages(query.data), [query.data]);

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
  const usersById = useMemo(() => {
    const m = new Map<string, User>();
    for (const u of usersQuery.data?.items ?? []) m.set(u.user_id, u);
    return m;
  }, [usersQuery.data]);
  const activeUsers = useMemo(() => (usersQuery.data?.items ?? []).filter((u) => u.status === "active"), [usersQuery.data]);

  const patch = usePatchApiKey(companyId);
  const rotate = useRotateApiKey(companyId);
  // guidance only: SSO companies get sso:<client> keys issued by the connector login
  const sso = useSso(companyId);

  const requestTransition = useCallback((key: ApiKey, t: StatusTransition) => setTransition({ key, transition: t }), []);
  const openEdit = useCallback((key: ApiKey) => setEditTarget(key), []);
  const openRotate = useCallback((key: ApiKey) => setRotateTarget(key), []);
  const openLogs = useCallback((key: ApiKey) => setLogsTarget(key), []);
  const openExtend = useCallback((key: ApiKey) => setExtendTarget(key), []);

  const columns = useMemo<ColumnDef<ApiKey>[]>(
    () => [
      {
        accessorKey: "name",
        header: "이름",
        size: 180,
        cell: ({ row }) => <span className="font-medium">{row.original.name}</span>,
      },
      {
        accessorKey: "key_prefix",
        header: "프리픽스",
        size: 130,
        cell: ({ row }) => <Code>{row.original.key_prefix}…</Code>,
      },
      {
        id: "user",
        header: "사용자",
        accessorFn: (k) => usersById.get(k.user_id)?.email ?? k.user_id,
        size: 200,
        cell: ({ row }) => {
          const u = usersById.get(row.original.user_id);
          return u ? (
            <span className="flex flex-col">
              <span className="text-sm">{u.email}</span>
              <span className="text-xs text-muted-foreground">{u.name}</span>
            </span>
          ) : (
            <Code>{row.original.user_id}</Code>
          );
        },
      },
      {
        id: "scope",
        header: "서버 범위",
        accessorFn: (k) => (k.server_ids === null ? "" : k.server_ids.map((id) => serversById.get(id)?.name ?? id).join(", ")),
        size: 220,
        cell: ({ row }) => {
          const ids = row.original.server_ids;
          if (ids === null) return <Badge variant="info" title="소유자가 접근 권한을 가진 모든 서버 (향후 포함)">전체 서버</Badge>;
          return (
            <span className="inline-flex flex-wrap gap-1">
              {ids.map((id) => (
                <Link
                  key={id}
                  to={`/servers/${encodeURIComponent(id)}`}
                  className="text-primary hover:underline"
                  onClick={(e) => e.stopPropagation()}
                >
                  <Badge variant="outline">{serversById.get(id)?.name ?? id}</Badge>
                </Link>
              ))}
            </span>
          );
        },
      },
      {
        accessorKey: "status",
        header: "상태",
        size: 100,
        cell: ({ row }) => <StatusBadge status={row.original.status} />,
      },
      {
        accessorKey: "expires_at",
        header: "만료",
        size: 180,
        cell: ({ row }) => {
          const v = row.original.expires_at;
          if (!v) return <span className="text-xs text-muted-foreground">무기한</span>;
          const expired = isExpired(v);
          const days = daysUntil(v);
          // within 14 days: D-n countdown; red once ≤3 days or past
          const soon = expired || (days !== null && days <= EXPIRY_WARN_DAYS);
          return (
            <span className="inline-flex items-center gap-1 text-xs">
              {formatDateTime(v)}
              {soon ? (
                <Badge variant={expired || (days !== null && days <= EXPIRY_DANGER_DAYS) ? "danger" : "warning"} dot={expired ? "danger" : "hollow"} data-testid="apikey-expiry">
                  {expired ? "만료됨" : `D-${days}`}
                </Badge>
              ) : null}
            </span>
          );
        },
      },
      {
        accessorKey: "last_used_at",
        header: "최근 사용",
        size: 170,
        cell: ({ row }) => <span className="text-xs">{formatDateTime(row.original.last_used_at)}</span>,
      },
      {
        id: "actions",
        header: "작업",
        enableSorting: false,
        size: 240,
        cell: ({ row }) => {
          const k = row.original;
          const terminal = k.status === "deleted";
          return (
            <span className="inline-flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
              <Button variant="outline" size="icon" title="편집" onClick={() => openEdit(k)} disabled={terminal}>
                <Pencil className="h-3.5 w-3.5" />
              </Button>
              <Button variant="outline" size="icon" title="키 회전 (재발급)" onClick={() => openRotate(k)} disabled={terminal}>
                <RefreshCw className="h-3.5 w-3.5" />
              </Button>
              <Button variant="outline" size="icon" title="접근 로그" onClick={() => openLogs(k)}>
                <FileClock className="h-3.5 w-3.5" />
              </Button>
              {k.expires_at && k.status === "active" ? (
                <Button variant="outline" size="icon" title="30일 연장" data-testid="apikey-extend" onClick={() => openExtend(k)}>
                  <CalendarPlus className="h-3.5 w-3.5" />
                </Button>
              ) : null}
              <StatusActions status={k.status} size="icon" onAction={(t) => requestTransition(k, t)} />
            </span>
          );
        },
      },
    ],
    [usersById, serversById, openEdit, openRotate, openLogs, openExtend, requestTransition],
  );

  const confirmTransition = () => {
    if (!transition) return;
    patch.mutate(
      { keyId: transition.key.key_id, body: { status: TRANSITION_TARGET[transition.transition] } },
      { onSuccess: () => setTransition(null) },
    );
  };

  const confirmExtend = () => {
    if (!extendTarget) return;
    patch.mutate({ keyId: extendTarget.key_id, body: { expires_at: extendedExpiry(extendTarget.expires_at) } }, { onSuccess: () => setExtendTarget(null) });
  };

  const confirmRotate = () => {
    if (!rotateTarget) return;
    rotate.mutate(rotateTarget.key_id, {
      onSuccess: (out) => {
        setRotateTarget(null);
        setIssued({ ...out, kind: "rotated" });
      },
    });
  };

  return (
    <ApiKeysTabPresenter
      companyId={companyId}
      companyDeleted={companyStatus === "deleted"}
      hasSso={!!sso.data}
      keys={keys}
      columns={columns}
      filters={filters}
      onFiltersChange={(patchFilters) => setFilters((f) => ({ ...f, ...patchFilters }))}
      onReset={() => setFilters(EMPTY_FILTERS)}
      isLoading={query.isPending}
      isRefreshing={query.isFetching && !query.isFetchingNextPage}
      error={query.error}
      hasNextPage={query.hasNextPage}
      isFetchingNextPage={query.isFetchingNextPage}
      onLoadMore={() => void query.fetchNextPage()}
      serversById={serversById}
      activeBindings={activeBindings}
      users={usersQuery.data?.items ?? []}
      activeUsers={activeUsers}
      pickersLoading={bindingsQuery.isPending || serversQuery.isPending || usersQuery.isPending}
      createOpen={createOpen}
      onCreateOpenChange={setCreateOpen}
      editTarget={editTarget}
      onEditClose={() => setEditTarget(null)}
      onIssued={(out) => setIssued({ ...out, kind: "created" })}
      issued={issued}
      onIssuedDismiss={() => setIssued(null)}
      rotateTarget={rotateTarget}
      onRotateCancel={() => {
        setRotateTarget(null);
        rotate.reset();
      }}
      onRotateConfirm={confirmRotate}
      rotatePending={rotate.isPending}
      rotateError={rotate.error}
      logsTarget={logsTarget}
      onLogsClose={() => setLogsTarget(null)}
      extendTarget={extendTarget}
      onExtendCancel={() => {
        setExtendTarget(null);
        patch.reset();
      }}
      onExtendConfirm={confirmExtend}
      extendPending={patch.isPending}
      extendError={patch.error}
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
