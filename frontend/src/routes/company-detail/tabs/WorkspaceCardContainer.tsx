import { useState } from "react";
import { ConfirmDialog } from "@/components/shared/ConfirmDialog";
import type { Company } from "@/domains/companies/types";
import { useDisconnectWorkspace, useSyncWorkspace } from "@/domains/workspace/mutations";
import { useWorkspace } from "@/domains/workspace/queries";
import { WorkspaceCard } from "./WorkspaceCard";
import { WorkspaceFormDialog } from "./WorkspaceFormDialog";

interface WorkspaceCardContainerProps {
  company: Company;
  canEdit: boolean;
  readOnlyHint: string;
}

export function WorkspaceCardContainer({ company, canEdit, readOnlyHint }: WorkspaceCardContainerProps) {
  const companyId = company.company_id;
  const query = useWorkspace(companyId);
  const [formOpen, setFormOpen] = useState(false);
  const [disconnectOpen, setDisconnectOpen] = useState(false);
  const sync = useSyncWorkspace(companyId);
  const disconnect = useDisconnectWorkspace(companyId);

  return (
    <>
      <WorkspaceCard
        workspace={query.data}
        isLoading={query.isPending}
        loadError={query.error}
        canEdit={canEdit}
        readOnlyHint={readOnlyHint}
        onConnect={() => {
          sync.reset();
          setFormOpen(true);
        }}
        onSync={() => sync.mutate()}
        syncPending={sync.isPending}
        syncResult={sync.data}
        syncError={sync.error}
        onRequestDisconnect={() => {
          disconnect.reset();
          setDisconnectOpen(true);
        }}
      />
      <WorkspaceFormDialog companyId={companyId} current={query.data ?? null} open={formOpen} onClose={() => setFormOpen(false)} />
      <ConfirmDialog
        open={disconnectOpen}
        title="Google Workspace 연결 해제"
        description={
          <span>
            <strong>{company.name}</strong>의 서비스 계정 키를 삭제합니다. 이후 동기화는 중단되며, 이미 동기화된 사용자·팀은 그대로 남습니다. 다시
            연결하려면 서비스 계정 JSON 키를 다시 등록해야 합니다.
          </span>
        }
        confirmLabel="연결 해제"
        destructive
        loading={disconnect.isPending}
        error={disconnect.error}
        onConfirm={() => disconnect.mutate(undefined, { onSuccess: () => setDisconnectOpen(false) })}
        onClose={() => {
          setDisconnectOpen(false);
          disconnect.reset();
        }}
      />
    </>
  );
}
