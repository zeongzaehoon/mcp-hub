import { useMutation, useQueryClient } from "@tanstack/react-query";
import { createServer, patchServer } from "./api";
import { serverKeys } from "./queries";
import type { ServerIn, ServerPatch } from "./types";

export function useCreateServer() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ServerIn) => createServer(body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: serverKeys.lists() });
      void qc.invalidateQueries({ queryKey: ["servers", "page"] });
    },
  });
}

export function usePatchServer(serverId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ServerPatch) => patchServer(serverId, body),
    onSuccess: (server) => {
      qc.setQueryData(serverKeys.detail(serverId), server);
      void qc.invalidateQueries({ queryKey: serverKeys.lists() });
      void qc.invalidateQueries({ queryKey: ["servers", "page"] });
    },
  });
}
