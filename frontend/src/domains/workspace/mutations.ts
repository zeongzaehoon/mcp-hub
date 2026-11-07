import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ssoKeys } from "@/domains/sso/queries";
import { userKeys } from "@/domains/users/queries";
import { disconnectWorkspace, putWorkspace, syncWorkspace } from "./api";
import { workspaceKeys } from "./queries";
import type { WorkspaceIn, WorkspaceOut } from "./types";

export function usePutWorkspace(companyId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: WorkspaceIn) => putWorkspace(companyId, body),
    onSuccess: (ws) => {
      qc.setQueryData<WorkspaceOut | null>(workspaceKeys.detail(companyId), ws);
      // first connect may auto-create the platform-Google SSO provider; the directory users change on sync only,
      // but a changed admin_email/domain alters what the next sync will do, so refetch the list as well
      void qc.invalidateQueries({ queryKey: ssoKeys.detail(companyId) });
      void qc.invalidateQueries({ queryKey: userKeys.company(companyId) });
    },
  });
}

export function useSyncWorkspace(companyId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => syncWorkspace(companyId),
    // last_sync / sync_error change on success and on a directory failure alike
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: workspaceKeys.detail(companyId) });
      void qc.invalidateQueries({ queryKey: workspaceKeys.teams(companyId) });
      void qc.invalidateQueries({ queryKey: userKeys.company(companyId) });
    },
  });
}

export function useDisconnectWorkspace(companyId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => disconnectWorkspace(companyId),
    onSuccess: () => qc.setQueryData<WorkspaceOut | null>(workspaceKeys.detail(companyId), null),
  });
}
