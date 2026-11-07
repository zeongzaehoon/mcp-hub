import type { ColumnDef } from "@tanstack/react-table";
import { Play, Plus } from "lucide-react";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/shared/ConfirmDialog";
import { DataTable } from "@/components/shared/DataTable";
import { ErrorAlert } from "@/components/shared/ErrorAlert";
import { KeyValueList } from "@/components/shared/KeyValueList";
import { LoadMore } from "@/components/shared/LoadMore";
import { SearchFilterBar } from "@/components/shared/SearchFilterBar";
import { TRANSITION_LABEL } from "@/components/shared/StatusActions";
import type { Binding } from "@/domains/bindings/types";
import type { Policy, PolicyApplyOut } from "@/domains/policies/types";
import type { Server } from "@/domains/servers/types";
import type { Team } from "@/domains/workspace/types";
import { PolicyFormDialog } from "./PolicyFormDialog";
import type { PolicyTransitionRequest } from "./PoliciesTabContainer";

/** Compact view of a `POST /policies/apply` result (also shown inside the Workspace sync result). */
export function PolicyApplySummary({ result }: { result: PolicyApplyOut }) {
  return (
    <div className="space-y-2">
      <KeyValueList
        items={[
          { label: "대상 사용자", value: result.users },
          { label: "접근 (생성/수정)", value: `${result.created} / ${result.updated}` },
          { label: "접근 (정지/재개)", value: `${result.suspended} / ${result.reactivated}` },
          { label: "수동 접근 유지", value: result.kept_manual },
        ]}
      />
      {result.errors.length > 0 ? (
        <ul className="list-disc space-y-0.5 pl-5 text-xs text-destructive">
          {result.errors.map((e, i) => (
            <li key={i}>{e}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

interface PoliciesTabPresenterProps {
  companyId: string;
  canEdit: boolean;
  readOnlyHint: string;
  policies: Policy[];
  columns: ColumnDef<Policy>[];
  isLoading: boolean;
  error: unknown;
  hasNextPage: boolean;
  isFetchingNextPage: boolean;
  onLoadMore: () => void;
  serversById: Map<string, Server>;
  teamsById: Map<string, Team>;
  activeTeams: Team[];
  activeBindings: Binding[];
  pickersLoading: boolean;
  createOpen: boolean;
  onCreateOpenChange: (open: boolean) => void;
  editTarget: Policy | null;
  onEditClose: () => void;
  transition: PolicyTransitionRequest | null;
  onTransitionCancel: () => void;
  onTransitionConfirm: () => void;
  transitionPending: boolean;
  transitionError: unknown;
  onApply: () => void;
  applyPending: boolean;
  applyResult: PolicyApplyOut | undefined;
  applyError: unknown;
}

export function PoliciesTabPresenter(p: PoliciesTabPresenterProps) {
  const serverLabel = (id: string) => p.serversById.get(id)?.name ?? id;
  const targetLabel = (id: string | null) => (id === null ? "전원" : (p.teamsById.get(id)?.name ?? id));
  const noBinding = p.activeBindings.length === 0;
  return (
    <div className="space-y-3" data-testid="policies-tab">
      <p className="text-sm text-muted-foreground">
        팀(또는 전원) 단위로 MCP 서버 권한·도구를 정합니다. 정책은 팀 멤버마다 사용자 접근으로 적용되며, Workspace 동기화로 멤버가 바뀌면 자동으로
        다시 적용됩니다. 정책이 만든 접근을 직접 수정하면 수동 접근으로 전환됩니다.
      </p>

      {p.applyResult ? (
        <Alert variant={p.applyResult.errors.length > 0 ? "warning" : "success"} data-testid="policies-apply-result">
          <div className="space-y-2">
            <div>정책 적용 완료</div>
            <PolicyApplySummary result={p.applyResult} />
          </div>
        </Alert>
      ) : null}
      <ErrorAlert error={p.applyError} />
      <ErrorAlert error={p.error} />
      <DataTable
        toolbar={
            <SearchFilterBar
              actions={
                p.canEdit ? (
                  <>
                    <Button variant="outline" onClick={p.onApply} loading={p.applyPending} data-testid="policies-apply-button" title="모든 활성 정책을 현재 팀 멤버에게 다시 적용">
                      <Play className="h-4 w-4" /> 지금 적용
                    </Button>
                    <Button onClick={() => p.onCreateOpenChange(true)} disabled={noBinding} title={noBinding ? "활성 서버 바인딩이 필요합니다" : undefined}>
                      <Plus className="h-4 w-4" /> 정책 추가
                    </Button>
                  </>
                ) : (
                  <span className="text-xs text-muted-foreground">{p.readOnlyHint}</span>
                )
              }
            />

        }
        data={p.policies}
        columns={p.columns}
        getRowId={(x) => x.policy_id}
        isLoading={p.isLoading}
        emptyMessage="팀 정책이 없습니다"
        footer={<LoadMore loaded={p.policies.length} hasNextPage={p.hasNextPage} isFetchingNextPage={p.isFetchingNextPage} onLoadMore={p.onLoadMore} />}
      />

      <PolicyFormDialog
        mode="create"
        companyId={p.companyId}
        open={p.createOpen}
        onClose={() => p.onCreateOpenChange(false)}
        policies={p.policies}
        activeTeams={p.activeTeams}
        activeBindings={p.activeBindings}
        serversById={p.serversById}
        teamsById={p.teamsById}
        pickersLoading={p.pickersLoading}
      />
      <PolicyFormDialog
        mode="edit"
        companyId={p.companyId}
        open={p.editTarget !== null}
        editing={p.editTarget ?? undefined}
        onClose={p.onEditClose}
        policies={p.policies}
        activeTeams={p.activeTeams}
        activeBindings={p.activeBindings}
        serversById={p.serversById}
        teamsById={p.teamsById}
        pickersLoading={p.pickersLoading}
      />

      <ConfirmDialog
        open={p.transition !== null}
        title={p.transition ? `팀 정책 ${TRANSITION_LABEL[p.transition.transition]}` : ""}
        description={
          p.transition ? (
            <span>
              <strong>{targetLabel(p.transition.policy.team_id)}</strong> → <strong>{serverLabel(p.transition.policy.server_id)}</strong> 정책을{" "}
              {TRANSITION_LABEL[p.transition.transition]}합니다.
              {p.transition.transition !== "reactivate" ? " 이 정책이 만든 사용자 접근은 즉시 정지됩니다." : " 이 정책이 만든 사용자 접근이 다시 활성화됩니다."}
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
