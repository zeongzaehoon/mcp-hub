import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getGlobalStats, getHealth, getRecentEvents, redisPeek } from "./api";
import type { RedisKind } from "./types";

export const opsKeys = {
  redis: (kind: RedisKind, ident: string) => ["ops", "redis", kind, ident] as const,
  stats: ["ops", "stats"] as const,
  health: ["ops", "health"] as const,
  events: (limit: number) => ["ops", "events", limit] as const,
};

export function useRedisPeek(kind: RedisKind, ident: string, enabled: boolean) {
  return useQuery({
    queryKey: opsKeys.redis(kind, ident),
    queryFn: () => redisPeek(kind, ident),
    enabled: enabled && ident.length > 0,
    staleTime: 0,
    retry: false,
  });
}

/** master only — callers pass `enabled: isMaster` so admins never hit a 403 */
export function useGlobalStats(enabled = true) {
  return useQuery({ queryKey: opsKeys.stats, queryFn: () => getGlobalStats(false), enabled, staleTime: 60_000 });
}

/** `refresh=1`: rescans the counts on the backend and replaces the cached stats. */
export function useRefreshGlobalStats() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => getGlobalStats(true),
    onSuccess: (data) => qc.setQueryData(opsKeys.stats, data),
  });
}

export function useHealth(enabled = true) {
  return useQuery({ queryKey: opsKeys.health, queryFn: getHealth, enabled, staleTime: 30_000, refetchInterval: 30_000 });
}

export function useRecentEvents(limit = 50) {
  return useQuery({ queryKey: opsKeys.events(limit), queryFn: () => getRecentEvents(limit), refetchInterval: 10_000 });
}
