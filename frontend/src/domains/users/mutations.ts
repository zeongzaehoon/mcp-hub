import { useMutation, useQueryClient } from "@tanstack/react-query";
import { createUser, patchUser } from "./api";
import { userKeys } from "./queries";
import type { UserIn, UserPatch } from "./types";

export function useCreateUser(companyId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: UserIn) => createUser(companyId, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: userKeys.company(companyId) }),
  });
}

export function usePatchUser(companyId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ userId, body }: { userId: string; body: UserPatch }) => patchUser(companyId, userId, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: userKeys.company(companyId) }),
  });
}
