import type { ColumnDef } from "@tanstack/react-table";
import { Plus } from "lucide-react";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/shared/ConfirmDialog";
import { DataTable } from "@/components/shared/DataTable";
import { ErrorAlert } from "@/components/shared/ErrorAlert";
import { LoadMore } from "@/components/shared/LoadMore";
import { TRANSITION_LABEL } from "@/components/shared/StatusActions";
import type { Binding } from "@/domains/bindings/types";
import type { Server } from "@/domains/servers/types";
import { BindingCreateDialog } from "./BindingCreateDialog";
import type { BindingTransitionRequest } from "./BindingsTabContainer";

interface BindingsTabPresenterProps {
  companyId: string;
  companyDeleted: boolean;
  isMaster: boolean;
  bindings: Binding[];
  columns: ColumnDef<Binding>[];
  serversById: Map<string, Server>;
  isLoading: boolean;
  error: unknown;
  hasNextPage: boolean;
  isFetchingNextPage: boolean;
  onLoadMore: () => void;
  createOpen: boolean;
  onCreateOpenChange: (open: boolean) => void;
  transition: BindingTransitionRequest | null;
  onTransitionCancel: () => void;
  onTransitionConfirm: () => void;
  transitionPending: boolean;
  transitionError: unknown;
}

export function BindingsTabPresenter(p: BindingsTabPresenterProps) {
  const serverName = (id: string) => p.serversById.get(id)?.name ?? id;
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-2">
        <p className="text-sm text-muted-foreground">
          회사가 접근할 수 있는 MCP 서버와 데이터 격리(테넌트) 방식입니다. 활성 바인딩이 있는 서버에만 사용자 접근을 부여할 수 있습니다.
        </p>
        {p.isMaster ? (
          <Button onClick={() => p.onCreateOpenChange(true)} disabled={p.companyDeleted}>
            <Plus className="h-4 w-4" /> 바인딩 추가
          </Button>
        ) : null}
      </div>
      {!p.isMaster ? <Alert variant="info">바인딩은 마스터만 생성·변경할 수 있습니다. (읽기 전용)</Alert> : null}
      <ErrorAlert error={p.error} />
      <DataTable
        data={p.bindings}
        columns={p.columns}
        getRowId={(b) => `${b.company_id}:${b.server_id}`}
        isLoading={p.isLoading}
        emptyMessage="바인딩이 없습니다"
        footer={
          <LoadMore loaded={p.bindings.length} hasNextPage={p.hasNextPage} isFetchingNextPage={p.isFetchingNextPage} onLoadMore={p.onLoadMore} />
        }
      />
      {p.isMaster ? (
        <BindingCreateDialog
          companyId={p.companyId}
          open={p.createOpen}
          onClose={() => p.onCreateOpenChange(false)}
          existingServerIds={p.bindings.map((b) => b.server_id)}
        />
      ) : null}
      <ConfirmDialog
        open={p.transition !== null}
        title={p.transition ? `바인딩 ${TRANSITION_LABEL[p.transition.transition]}` : ""}
        description={
          p.transition ? (
            <span>
              <strong>{serverName(p.transition.binding.server_id)}</strong> 서버 바인딩을 {TRANSITION_LABEL[p.transition.transition]}합니다.
              {p.transition.transition !== "reactivate" ? " 이 서버를 대상으로 하는 회사의 모든 API 키가 즉시 거부됩니다." : ""}
              {p.transition.transition === "delete" ? " 삭제는 되돌릴 수 없습니다." : ""}
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
