import { useMutation, useQueryClient } from "@tanstack/react-query";
import { reconcileNow, resolveKey } from "./api";
import type { ResolveIn } from "./types";

export function useResolveKey() {
  return useMutation({ mutationFn: (body: ResolveIn) => resolveKey(body) });
}

export function useReconcile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => reconcileNow(),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["ops", "redis"] }),
  });
}
