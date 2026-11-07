import { useMutation, useQueryClient } from "@tanstack/react-query";
import { createApiKey, patchApiKey, rotateApiKey } from "./api";
import { apiKeyKeys } from "./queries";
import type { ApiKeyIn, ApiKeyPatch } from "./types";

export function useCreateApiKey(companyId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ApiKeyIn) => createApiKey(companyId, body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: apiKeyKeys.company(companyId) });
    },
  });
}

export function usePatchApiKey(companyId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ keyId, body }: { keyId: string; body: ApiKeyPatch }) => patchApiKey(companyId, keyId, body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: apiKeyKeys.company(companyId) });
    },
  });
}

export function useRotateApiKey(companyId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (keyId: string) => rotateApiKey(companyId, keyId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: apiKeyKeys.company(companyId) });
    },
  });
}
