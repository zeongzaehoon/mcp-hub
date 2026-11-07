import { useMutation, useQueryClient } from "@tanstack/react-query";
import { serverKeys } from "@/domains/servers/queries";
import { createAccess, patchAccess } from "./api";
import { accessKeys } from "./queries";
import type { AccessIn, AccessPatch } from "./types";

export function useCreateAccess(companyId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ userId, body }: { userId: string; body: AccessIn }) => createAccess(companyId, userId, body),
    onSuccess: (access) => {
      void qc.invalidateQueries({ queryKey: accessKeys.company(companyId) });
      void qc.invalidateQueries({ queryKey: serverKeys.access(access.server_id) });
    },
  });
}

export function usePatchAccess(companyId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ userId, serverId, body }: { userId: string; serverId: string; body: AccessPatch }) =>
      patchAccess(companyId, userId, serverId, body),
    onSuccess: (access) => {
      void qc.invalidateQueries({ queryKey: accessKeys.company(companyId) });
      void qc.invalidateQueries({ queryKey: serverKeys.access(access.server_id) });
    },
  });
}
