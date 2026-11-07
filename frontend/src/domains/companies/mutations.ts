import { useMutation, useQueryClient } from "@tanstack/react-query";
import { createCompany, deleteBranding, patchCompany, putBranding } from "./api";
import { companyKeys } from "./queries";
import type { BrandingIn, CompanyIn, CompanyPatch } from "./types";

export function useCreateCompany() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: CompanyIn) => createCompany(body),
    onSuccess: () => qc.invalidateQueries({ queryKey: companyKeys.lists() }),
  });
}

export function usePatchCompany(companyId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: CompanyPatch) => patchCompany(companyId, body),
    onSuccess: (company) => {
      qc.setQueryData(companyKeys.detail(companyId), company);
      void qc.invalidateQueries({ queryKey: companyKeys.lists() });
    },
  });
}

export function usePutBranding(companyId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: BrandingIn) => putBranding(companyId, body),
    onSuccess: (company) => {
      qc.setQueryData(companyKeys.detail(companyId), company);
      void qc.invalidateQueries({ queryKey: companyKeys.lists() });
    },
  });
}

export function useDeleteBranding(companyId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => deleteBranding(companyId),
    onSuccess: (company) => {
      qc.setQueryData(companyKeys.detail(companyId), company);
      void qc.invalidateQueries({ queryKey: companyKeys.lists() });
    },
  });
}
