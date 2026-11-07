import type { ColumnDef } from "@tanstack/react-table";
import { Plus } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/shared/ConfirmDialog";
import { DataTable } from "@/components/shared/DataTable";
import { ErrorAlert } from "@/components/shared/ErrorAlert";
import { LoadMore } from "@/components/shared/LoadMore";
import { SearchFilterBar } from "@/components/shared/SearchFilterBar";
import { TRANSITION_LABEL } from "@/components/shared/StatusActions";
import type { Status } from "@/domains/common/enums";
import type { User, UserFilters } from "@/domains/users/types";
import { UserCreateDialog } from "./UserCreateDialog";
import { UserPasswordDialog } from "./UserPasswordDialog";
import type { UserTransitionRequest } from "./UsersTabContainer";

interface UsersTabPresenterProps {
  companyId: string;
  companyDeleted: boolean;
  users: User[];
  columns: ColumnDef<User>[];
  filters: UserFilters;
  onSearchChange: (q: string) => void;
  onStatusChange: (s: Status | "") => void;
  onReset: () => void;
  isLoading: boolean;
  isRefreshing: boolean;
  error: unknown;
  hasNextPage: boolean;
  isFetchingNextPage: boolean;
  onLoadMore: () => void;
  createOpen: boolean;
  onCreateOpenChange: (open: boolean) => void;
  passwordTarget: User | null;
  onPasswordClose: () => void;
  transition: UserTransitionRequest | null;
  onTransitionCancel: () => void;
  onTransitionConfirm: () => void;
  transitionPending: boolean;
  transitionError: unknown;
  isMasterViewer: boolean;
}

export function UsersTabPresenter(p: UsersTabPresenterProps) {
  return (
    <div className="space-y-4">
      <ErrorAlert error={p.error} />
      <DataTable
        toolbar={
            <SearchFilterBar
              search={p.filters.q ?? ""}
              onSearchChange={p.onSearchChange}
              searchPlaceholder="이메일 / 이름 검색"
              status={p.filters.status ?? ""}
              onStatusChange={p.onStatusChange}
              onReset={p.onReset}
              actions={
                <Button onClick={() => p.onCreateOpenChange(true)} disabled={p.companyDeleted}>
                  <Plus className="h-4 w-4" /> 사용자 추가
                </Button>
              }
            />

        }
        data={p.users}
        columns={p.columns}
        getRowId={(u) => u.user_id}
        isLoading={p.isLoading}
        isRefreshing={p.isRefreshing}
        emptyMessage="사용자가 없습니다"
        footer={
          <LoadMore loaded={p.users.length} hasNextPage={p.hasNextPage} isFetchingNextPage={p.isFetchingNextPage} onLoadMore={p.onLoadMore} />
        }
      />

      <UserCreateDialog
        companyId={p.companyId}
        open={p.createOpen}
        onClose={() => p.onCreateOpenChange(false)}
        allowMasterRole={p.isMasterViewer && p.companyId === "system"}
      />
      <UserPasswordDialog companyId={p.companyId} user={p.passwordTarget} onClose={p.onPasswordClose} />
      <ConfirmDialog
        open={p.transition !== null}
        title={p.transition ? `사용자 ${TRANSITION_LABEL[p.transition.transition]}` : ""}
        description={
          p.transition ? (
            <span>
              <strong>{p.transition.user.email}</strong> 사용자를 {TRANSITION_LABEL[p.transition.transition]}합니다.
              {p.transition.transition === "delete" ? " 삭제는 되돌릴 수 없습니다." : ""}
              {p.transition.transition !== "reactivate" ? " 이 사용자가 소유한 API 키는 즉시 거부됩니다." : ""}
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
