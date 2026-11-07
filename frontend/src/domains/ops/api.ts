import { api } from "@/lib/api";
import type { EventOut, GlobalStatsOut, HealthOut, ReconcileOut, RedisKind, RedisPeekOut, ResolveIn, ResolveOut } from "./types";

export async function resolveKey(body: ResolveIn): Promise<ResolveOut> {
  const { data } = await api.post<ResolveOut>("/ops/resolve", body);
  return data;
}

export async function reconcileNow(): Promise<ReconcileOut> {
  const { data } = await api.post<ReconcileOut>("/ops/reconcile");
  return data;
}

export async function redisPeek(kind: RedisKind, ident: string): Promise<RedisPeekOut> {
  // ident may contain "/" for user and binding kinds ({ident:path} on the backend), so encode per segment.
  const encoded = ident.split("/").map(encodeURIComponent).join("/");
  const { data } = await api.get<RedisPeekOut>(`/ops/redis/${kind}/${encoded}`);
  return data;
}

export async function getGlobalStats(refresh = false): Promise<GlobalStatsOut> {
  const { data } = await api.get<GlobalStatsOut>("/ops/stats", { params: refresh ? { refresh: 1 } : undefined });
  return data;
}

export async function getHealth(): Promise<HealthOut[]> {
  const { data } = await api.get<HealthOut[]>("/ops/health");
  return data;
}

export async function getRecentEvents(limit = 50): Promise<EventOut[]> {
  const { data } = await api.get<EventOut[]>("/ops/events", { params: { limit } });
  return data;
}
