import type { ColumnDef } from "@tanstack/react-table";
import { Plus } from "lucide-react";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Select } from "@/components/ui/Select";
import { ConfirmDialog } from "@/components/shared/ConfirmDialog";
import { CopyOnceBanner } from "@/components/shared/CopyOnceBanner";
import { DataTable } from "@/components/shared/DataTable";
import { ErrorAlert } from "@/components/shared/ErrorAlert";
import { LoadMore } from "@/components/shared/LoadMore";
import { SearchFilterBar } from "@/components/shared/SearchFilterBar";
import { TRANSITION_LABEL } from "@/components/shared/StatusActions";
import { formatDateTime } from "@/lib/utils";
import { EXTEND_DAYS, extendedExpiry, type ApiKey, type ApiKeyFilters, type ApiKeyIssued } from "@/domains/apikeys/types";
import type { Binding } from "@/domains/bindings/types";
import type { Server } from "@/domains/servers/types";
import type { User } from "@/domains/users/types";
import { ApiKeyFormDialog } from "./ApiKeyFormDialog";
import { KeyConnectionHint } from "./KeyConnectionHint";
import { KeyLogsDialog } from "./KeyLogsDialog";
import type { IssuedKey, KeyTransitionRequest } from "./ApiKeysTabContainer";

interface ApiKeysTabPresenterProps {
  companyId: string;
  companyDeleted: boolean;
  /** company has an SSO provider: connector logins issue `sso:*` keys themselves */
  hasSso: boolean;
  keys: ApiKey[];
  columns: ColumnDef<ApiKey>[];
  filters: ApiKeyFilters;
  onFiltersChange: (patch: Partial<ApiKeyFilters>) => void;
  onReset: () => void;
  isLoading: boolean;
  isRefreshing: boolean;
  error: unknown;
  hasNextPage: boolean;
  isFetchingNextPage: boolean;
  onLoadMore: () => void;
  serversById: Map<string, Server>;
  activeBindings: Binding[];
  users: User[];
  activeUsers: User[];
  pickersLoading: boolean;
  createOpen: boolean;
  onCreateOpenChange: (open: boolean) => void;
  editTarget: ApiKey | null;
  onEditClose: () => void;
  onIssued: (issued: ApiKeyIssued) => void;
  issued: IssuedKey | null;
  onIssuedDismiss: () => void;
  rotateTarget: ApiKey | null;
  onRotateCancel: () => void;
  onRotateConfirm: () => void;
  rotatePending: boolean;
  rotateError: unknown;
  logsTarget: ApiKey | null;
  onLogsClose: () => void;
  extendTarget: ApiKey | null;
  onExtendCancel: () => void;
  onExtendConfirm: () => void;
  extendPending: boolean;
  extendError: unknown;
  transition: KeyTransitionRequest | null;
  onTransitionCancel: () => void;
  onTransitionConfirm: () => void;
  transitionPending: boolean;
  transitionError: unknown;
}

export function ApiKeysTabPresenter(p: ApiKeysTabPresenterProps) {
  return (
    <div className="space-y-4">
      {p.issued ? (
        <CopyOnceBanner
          rawKey={p.issued.raw_key}
          title={p.issued.kind === "rotated" ? `"${p.issued.key.name}" 키가 회전되었습니다 (이전 키는 즉시 무효화됨)` : `"${p.issued.key.name}" 키가 발급되었습니다`}
          onDismiss={p.onIssuedDismiss}
        >
          <KeyConnectionHint companyId={p.companyId} apiKey={p.issued.key} serversById={p.serversById} />
        </CopyOnceBanner>
      ) : null}


      {p.hasSso ? (
        <Alert variant="info" data-testid="apikeys-sso-hint">
          SSO 를 쓰는 회사는 커넥터 로그인이 sso:&lt;클라이언트&gt; 키를 자동 발급합니다. 수동 발급은 서비스 계정·스크립트용으로만 쓰세요.
        </Alert>
      ) : null}
      <ErrorAlert error={p.error} />
      <DataTable
        toolbar={
            <SearchFilterBar
              search={p.filters.q ?? ""}
              onSearchChange={(q) => p.onFiltersChange({ q })}
              searchPlaceholder="키 이름 검색"
              status={p.filters.status ?? ""}
              onStatusChange={(status) => p.onFiltersChange({ status })}
              onReset={p.onReset}
              actions={
                <Button onClick={() => p.onCreateOpenChange(true)} disabled={p.companyDeleted}>
                  <Plus className="h-4 w-4" /> API 키 발급
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
            </SearchFilterBar>

        }
        data={p.keys}
        columns={p.columns}
        getRowId={(k) => k.key_id}
        isLoading={p.isLoading}
        isRefreshing={p.isRefreshing}
        emptyMessage="API 키가 없습니다"
        footer={<LoadMore loaded={p.keys.length} hasNextPage={p.hasNextPage} isFetchingNextPage={p.isFetchingNextPage} onLoadMore={p.onLoadMore} />}
      />

      <ApiKeyFormDialog
        mode="create"
        companyId={p.companyId}
        open={p.createOpen}
        onClose={() => p.onCreateOpenChange(false)}
        onIssued={(issued) => {
          p.onCreateOpenChange(false);
          p.onIssued(issued);
        }}
        activeUsers={p.activeUsers}
        activeBindings={p.activeBindings}
        serversById={p.serversById}
        pickersLoading={p.pickersLoading}
      />
      <ApiKeyFormDialog
        mode="edit"
        companyId={p.companyId}
        open={p.editTarget !== null}
        editing={p.editTarget ?? undefined}
        onClose={p.onEditClose}
        activeUsers={p.activeUsers}
        activeBindings={p.activeBindings}
        serversById={p.serversById}
        pickersLoading={p.pickersLoading}
      />
      <KeyLogsDialog companyId={p.companyId} apiKey={p.logsTarget} onClose={p.onLogsClose} />

      <ConfirmDialog
        open={p.extendTarget !== null}
        title={`만료 ${EXTEND_DAYS}일 연장`}
        description={
          p.extendTarget ? (
            <span>
              <strong>{p.extendTarget.name}</strong> ({p.extendTarget.key_prefix}…) 키의 만료를 <strong>{formatDateTime(extendedExpiry(p.extendTarget.expires_at))}</strong>
              까지 연장합니다.
            </span>
          ) : null
        }
        confirmLabel="연장"
        loading={p.extendPending}
        error={p.extendError}
        onConfirm={p.onExtendConfirm}
        onClose={p.onExtendCancel}
      />

      <ConfirmDialog
        open={p.rotateTarget !== null}
        title="API 키 회전"
        description={
          p.rotateTarget ? (
            <span>
              <strong>{p.rotateTarget.name}</strong> ({p.rotateTarget.key_prefix}…) 키를 재발급합니다. 기존 키는 <strong>즉시 무효화</strong>되며 새 키는 한 번만 표시됩니다.
            </span>
          ) : null
        }
        confirmLabel="회전"
        destructive
        loading={p.rotatePending}
        error={p.rotateError}
        onConfirm={p.onRotateConfirm}
        onClose={p.onRotateCancel}
      />
      <ConfirmDialog
        open={p.transition !== null}
        title={p.transition ? `API 키 ${TRANSITION_LABEL[p.transition.transition]}` : ""}
        description={
          p.transition ? (
            <span>
              <strong>{p.transition.key.name}</strong> ({p.transition.key.key_prefix}…) 키를 {TRANSITION_LABEL[p.transition.transition]}합니다.
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
