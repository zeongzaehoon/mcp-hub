import { useCallback, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import type { ColumnDef } from "@tanstack/react-table";
import { flattenPages } from "@/lib/cursor";
import { formatDateTime } from "@/lib/utils";
import { TENANT_MODE_LABEL, type Status } from "@/domains/common/enums";
import { usePatchBinding } from "@/domains/bindings/mutations";
import { useBindingsInfinite } from "@/domains/bindings/queries";
import { describeTenant, type Binding } from "@/domains/bindings/types";
import { useServersPage } from "@/domains/servers/queries";
import type { Server } from "@/domains/servers/types";
import { Badge } from "@/components/ui/Badge";
import { Code } from "@/components/ui/Code";
import { StatusActions, TRANSITION_TARGET, type StatusTransition } from "@/components/shared/StatusActions";
import { StatusBadge } from "@/components/shared/StatusBadge";
import { BindingsTabPresenter } from "./BindingsTabPresenter";

export interface BindingTransitionRequest {
  binding: Binding;
  transition: StatusTransition;
}

interface BindingsTabContainerProps {
  companyId: string;
  companyStatus: Status;
  isMaster: boolean;
}

export function BindingsTabContainer({ companyId, companyStatus, isMaster }: BindingsTabContainerProps) {
  const [createOpen, setCreateOpen] = useState(false);
  const [transition, setTransition] = useState<BindingTransitionRequest | null>(null);

  const query = useBindingsInfinite(companyId);
  const bindings = useMemo(() => flattenPages(query.data), [query.data]);
  const serversQuery = useServersPage();
  const serversById = useMemo(() => {
    const m = new Map<string, Server>();
    for (const s of serversQuery.data?.items ?? []) m.set(s.server_id, s);
    return m;
  }, [serversQuery.data]);
  const patch = usePatchBinding(companyId);

  const requestTransition = useCallback((binding: Binding, t: StatusTransition) => setTransition({ binding, transition: t }), []);

  const columns = useMemo<ColumnDef<Binding>[]>(() => {
    const cols: ColumnDef<Binding>[] = [
      {
        id: "server",
        header: "서버",
        accessorFn: (b) => serversById.get(b.server_id)?.name ?? b.server_id,
        size: 200,
        cell: ({ row }) => {
          const s = serversById.get(row.original.server_id);
          return (
            <div className="flex flex-col">
              <Link to={`/servers/${encodeURIComponent(row.original.server_id)}`} className="font-medium text-primary hover:underline">
                {s?.name ?? row.original.server_id}
              </Link>
              <Code className="w-fit">{row.original.server_id}</Code>
            </div>
          );
        },
      },
      {
        accessorKey: "server_type",
        header: "타입",
        size: 110,
        cell: ({ row }) => <Badge variant="outline">{row.original.server_type}</Badge>,
      },
      {
        id: "mode",
        header: "격리 방식",
        accessorFn: (b) => b.tenant.mode,
        size: 140,
        cell: ({ row }) => <span className="text-xs">{TENANT_MODE_LABEL[row.original.tenant.mode] ?? row.original.tenant.mode}</span>,
      },
      {
        id: "tenant",
        header: "테넌트",
        accessorFn: (b) => describeTenant(b.tenant),
        cell: ({ row }) => <Code>{describeTenant(row.original.tenant)}</Code>,
      },
      {
        id: "credentials",
        header: "자격증명",
        enableSorting: false,
        size: 140,
        cell: ({ row }) => {
          const c = row.original.tenant.credentials;
          return c ? <span className="text-xs">{c.username} / ***</span> : <span className="text-xs text-muted-foreground">기본</span>;
        },
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
    ];
    if (isMaster) {
      cols.push({
        id: "actions",
        header: "작업",
        enableSorting: false,
        size: 200,
        cell: ({ row }) => (
          <span onClick={(e) => e.stopPropagation()}>
            <StatusActions status={row.original.status} onAction={(t) => requestTransition(row.original, t)} />
          </span>
        ),
      });
    }
    return cols;
  }, [isMaster, serversById, requestTransition]);

  const confirmTransition = () => {
    if (!transition) return;
    patch.mutate(
      { serverId: transition.binding.server_id, body: { status: TRANSITION_TARGET[transition.transition] } },
      { onSuccess: () => setTransition(null) },
    );
  };

  return (
    <BindingsTabPresenter
      companyId={companyId}
      companyDeleted={companyStatus === "deleted"}
      isMaster={isMaster}
      bindings={bindings}
      columns={columns}
      serversById={serversById}
      isLoading={query.isPending}
      error={query.error}
      hasNextPage={query.hasNextPage}
      isFetchingNextPage={query.isFetchingNextPage}
      onLoadMore={() => void query.fetchNextPage()}
      createOpen={createOpen}
      onCreateOpenChange={setCreateOpen}
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
