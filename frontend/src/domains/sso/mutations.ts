import { useMutation, useQueryClient } from "@tanstack/react-query";
import { deleteSso, putSso, testSso } from "./api";
import { ssoKeys } from "./queries";
import type { SsoProviderIn, SsoProviderOut } from "./types";

export function usePutSso(companyId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: SsoProviderIn) => putSso(companyId, body),
    onSuccess: (sso) => qc.setQueryData<SsoProviderOut | null>(ssoKeys.detail(companyId), sso),
  });
}

export function useDeleteSso(companyId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => deleteSso(companyId),
    onSuccess: () => qc.setQueryData<SsoProviderOut | null>(ssoKeys.detail(companyId), null),
  });
}

export function useTestSso(companyId: string) {
  return useMutation({ mutationFn: () => testSso(companyId) });
}
