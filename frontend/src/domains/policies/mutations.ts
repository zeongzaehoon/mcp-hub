import { useMutation, useQueryClient } from "@tanstack/react-query";
import { accessKeys } from "@/domains/access/queries";
import { applyPolicies, createPolicy, patchPolicy } from "./api";
import { policyKeys } from "./queries";
import type { PolicyIn, PolicyPatch } from "./types";

/** Every policy write is applied by the backend immediately, so user accesses change too. */
function useInvalidatePolicies(companyId: string) {
  const qc = useQueryClient();
  return () => {
    void qc.invalidateQueries({ queryKey: policyKeys.company(companyId) });
    void qc.invalidateQueries({ queryKey: accessKeys.company(companyId) });
  };
}

export function useCreatePolicy(companyId: string) {
  const invalidate = useInvalidatePolicies(companyId);
  return useMutation({ mutationFn: (body: PolicyIn) => createPolicy(companyId, body), onSuccess: invalidate });
}

export function usePatchPolicy(companyId: string) {
  const invalidate = useInvalidatePolicies(companyId);
  return useMutation({
    mutationFn: ({ policyId, body }: { policyId: string; body: PolicyPatch }) => patchPolicy(companyId, policyId, body),
    onSuccess: invalidate,
  });
}

export function useApplyPolicies(companyId: string) {
  const invalidate = useInvalidatePolicies(companyId);
  return useMutation({ mutationFn: () => applyPolicies(companyId), onSettled: invalidate });
}
