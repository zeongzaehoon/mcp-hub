import { useMutation, useQueryClient } from "@tanstack/react-query";
import { serverKeys } from "@/domains/servers/queries";
import { createBinding, patchBinding } from "./api";
import { bindingKeys } from "./queries";
import type { BindingIn, BindingPatch } from "./types";

export function useCreateBinding(companyId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: BindingIn) => createBinding(companyId, body),
    onSuccess: (binding) => {
      void qc.invalidateQueries({ queryKey: bindingKeys.company(companyId) });
      void qc.invalidateQueries({ queryKey: serverKeys.bindings(binding.server_id) });
    },
  });
}

export function usePatchBinding(companyId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ serverId, body }: { serverId: string; body: BindingPatch }) =>
      patchBinding(companyId, serverId, body),
    onSuccess: (binding) => {
      void qc.invalidateQueries({ queryKey: bindingKeys.company(companyId) });
      void qc.invalidateQueries({ queryKey: serverKeys.bindings(binding.server_id) });
    },
  });
}
