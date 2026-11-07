import type { ColumnDef } from "@tanstack/react-table";
import { Plus } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Select } from "@/components/ui/Select";
import { ConfirmDialog } from "@/components/shared/ConfirmDialog";
import { DataTable } from "@/components/shared/DataTable";
import { ErrorAlert } from "@/components/shared/ErrorAlert";
import { LoadMore } from "@/components/shared/LoadMore";
import { SearchFilterBar } from "@/components/shared/SearchFilterBar";
import { TRANSITION_LABEL } from "@/components/shared/StatusActions";
import type { Access, AccessFilters } from "@/domains/access/types";
import type { Binding } from "@/domains/bindings/types";
import type { Server } from "@/domains/servers/types";
import type { User } from "@/domains/users/types";
import { AccessFormDialog } from "./AccessFormDialog";
import type { AccessTransitionRequest } from "./AccessTabContainer";

interface AccessTabPresenterProps {
  companyId: string;
  companyDeleted: boolean;
  accesses: Access[];
  columns: ColumnDef<Access>[];
  filters: AccessFilters;
  onFiltersChange: (patch: Partial<AccessFilters>) => void;
  onReset: () => void;
  isLoading: boolean;
  isRefreshing: boolean;
  error: unknown;
  hasNextPage: boolean;
  isFetchingNextPage: boolean;
  onLoadMore: () => void;
  serversById: Map<string, Server>;
  usersById: Map<string, User>;
  bindings: Binding[];
  activeBindings: Binding[];
  users: User[];
  activeUsers: User[];
  pickersLoading: boolean;
  createOpen: boolean;
  onCreateOpenChange: (open: boolean) => void;
  editTarget: Access | null;
  onEditClose: () => void;
  editConfirm: Access | null;
  onEditConfirmCancel: () => void;
  onEditConfirm: () => void;
  transition: AccessTransitionRequest | null;
  onTransitionCancel: () => void;
  onTransitionConfirm: () => void;
  transitionPending: boolean;
  transitionError: unknown;
}

export function AccessTabPresenter(p: AccessTabPresenterProps) {
  const serverLabel = (id: string) => p.serversById.get(id)?.name ?? id;
  const userLabel = (id: string) => p.usersById.get(id)?.email ?? id;
  const noBinding = p.activeBindings.length === 0;
  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">
        사용자별로 어떤 MCP 서버에서 어떤 권한·도구를 쓸 수 있는지 정합니다. API 키는 소유 사용자를 대리하며, 여기서 부여된 범위를 넘을 수 없습니다.
      </p>

      <ErrorAlert error={p.error} />
      <DataTable
        toolbar={
            <SearchFilterBar
              onReset={p.onReset}
              actions={
                <Button onClick={() => p.onCreateOpenChange(true)} disabled={p.companyDeleted || noBinding} title={noBinding ? "활성 서버 바인딩이 필요합니다" : undefined}>
                  <Plus className="h-4 w-4" /> 접근 부여
                </Button>
              }
            >
              <Select value={p.filters.user_id ?? ""} onChange={(e) => p.onFiltersChange({ user_id: e.target.value })} className="h-9 w-56" aria-label="사용자 필터">
                <option value="">전체 사용자</option>
                {p.users.map((u) => (
                  <option key={u.user_id} value={u.user_id}>
                    {u.email}
                  </option>
                ))}
              </Select>
              <Select value={p.filters.server_id ?? ""} onChange={(e) => p.onFiltersChange({ server_id: e.target.value })} className="h-9 w-48" aria-label="서버 필터">
                <option value="">전체 서버</option>
                {p.bindings.map((b) => (
                  <option key={b.server_id} value={b.server_id}>
                    {serverLabel(b.server_id)}
                  </option>
                ))}
              </Select>
            </SearchFilterBar>

        }
        data={p.accesses}
        columns={p.columns}
        getRowId={(a) => `${a.user_id}:${a.server_id}`}
        isLoading={p.isLoading}
        isRefreshing={p.isRefreshing}
        emptyMessage="부여된 서버 접근이 없습니다"
        footer={<LoadMore loaded={p.accesses.length} hasNextPage={p.hasNextPage} isFetchingNextPage={p.isFetchingNextPage} onLoadMore={p.onLoadMore} />}
      />

      <AccessFormDialog
        mode="create"
        companyId={p.companyId}
        open={p.createOpen}
        onClose={() => p.onCreateOpenChange(false)}
        presetUserId={p.filters.user_id || undefined}
        activeUsers={p.activeUsers}
        activeBindings={p.activeBindings}
        serversById={p.serversById}
        usersById={p.usersById}
        pickersLoading={p.pickersLoading}
      />
      <AccessFormDialog
        mode="edit"
        companyId={p.companyId}
        open={p.editTarget !== null}
        editing={p.editTarget ?? undefined}
        onClose={p.onEditClose}
        activeUsers={p.activeUsers}
        activeBindings={p.activeBindings}
        serversById={p.serversById}
        usersById={p.usersById}
        pickersLoading={p.pickersLoading}
      />

      <ConfirmDialog
        open={p.editConfirm !== null}
        title="정책이 만든 접근 수정"
        description={
          p.editConfirm ? (
            <span>
              <strong>{userLabel(p.editConfirm.user_id)}</strong>의 <strong>{serverLabel(p.editConfirm.server_id)}</strong> 접근은 팀 정책이 만든 것입니다.
              수정하면 수동 접근으로 전환되어 정책이 더 이상 갱신하지 않습니다.
            </span>
          ) : null
        }
        confirmLabel="계속 수정"
        onConfirm={p.onEditConfirm}
        onClose={p.onEditConfirmCancel}
      />

      <ConfirmDialog
        open={p.transition !== null}
        title={p.transition ? `서버 접근 ${TRANSITION_LABEL[p.transition.transition]}` : ""}
        description={
          p.transition ? (
            <span>
              <strong>{userLabel(p.transition.access.user_id)}</strong>의 <strong>{serverLabel(p.transition.access.server_id)}</strong> 서버 접근을{" "}
              {TRANSITION_LABEL[p.transition.transition]}합니다.
              {p.transition.transition !== "reactivate" ? " 이 사용자의 API 키로 이 서버를 호출하면 즉시 거부됩니다." : ""}
              {p.transition.transition === "delete" ? " 삭제는 되돌릴 수 없습니다." : ""}
              {/* any operator PATCH (status included) turns a policy-made row into a manual one */}
              {p.transition.access.source === "policy"
                ? " 이 접근은 팀 정책이 만든 것입니다. 수정하면 수동 접근으로 전환되어 정책이 더 이상 갱신하지 않습니다."
                : ""}
            </span>
          ) : null
        }
        confirmLabel={p.transition ? TRANSITION_LABEL[p.transition.transition] : "확인"}
        destructive={p.transition?.transition === "delete"}
        loading={p.transitionPending}
        error={p.transitionError}
        onConfirm={p.onTransitionConfirm}
        onClose={p.onTransitionCancel}
      />
    </div>
  );
}
