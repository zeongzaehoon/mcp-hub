import type { ApiKey } from "@/domains/apikeys/types";

export interface ResolveIn {
  raw_key: string;
  /** judge only this server (otherwise every server in the key's scope) */
  server_id?: string;
}

export interface ResolveOut {
  key: ApiKey | null;
  /** "active" | "denied:<reason,...>" | "unknown" */
  effective: string;
  /** server_id -> "active" | "denied:<reason,...>" for each server in the key's scope */
  servers: Record<string, string>;
  redis_in_sync: boolean;
}

export interface ReconcileOut {
  checked: number;
  repaired: number;
  removed_stray: number;
  errors: string[];
}

export type RedisKind = "company" | "server" | "apikey" | "user" | "binding" | "access";
export const REDIS_KIND_VALUES: readonly RedisKind[] = ["company", "server", "apikey", "user", "binding", "access"];
export const REDIS_KIND_HINT: Record<RedisKind, string> = {
  company: "company_id",
  server: "server_id",
  apikey: "key_hash (sha256 of raw key)",
  user: "company_id/user_id",
  binding: "company_id/server_id",
  access: "company_id/user_id/server_id",
};

export interface RedisPeekOut {
  key?: string;
  value?: unknown;
  error?: string;
}

/** Hourly call buckets (last `hours`), oldest first. Percentiles are histogram upper bounds. */
export interface MetricsPoint {
  hour: string;
  n: number;
  ok: number;
  denied: number;
  error: number;
}

export interface MetricsOut {
  hours: number;
  calls: number;
  ok: number;
  denied: number;
  errors: number;
  denied_rate: number;
  avg_ms: number | null;
  max_ms: number | null;
  p50_ms: number | null;
  p99_ms: number | null;
  series: MetricsPoint[];
}

/** Liveness of one MCP service kind (postgres|clickhouse|mongo|hacker|gateway). `ok === null` => configured, not checked yet. */
export interface HealthOut {
  kind: string;
  ok: boolean | null;
  latency_ms: number | null;
  checked_at: string | null;
  error: string | null;
}

export interface StatusCounts {
  active: number;
  suspended: number;
  deleted: number;
  total: number;
}

export interface ExpiringKey {
  key_id: string;
  company_id: string;
  user_id: string;
  name: string;
  key_prefix: string;
  expires_at: string | null;
  status: string;
}

export interface KeysExpiring {
  days: number;
  count: number;
  expired_still_active: number;
  items: ExpiringKey[];
}

/** GET /ops/stats (master). Counts are cached on the backend; `refresh=1` rescans. */
export interface GlobalStatsOut {
  computed_at: string;
  counts: Partial<Record<"companies" | "users" | "servers" | "bindings" | "access" | "api_keys" | "teams" | "policies", StatusCounts>>;
  bindings_per_server: Record<string, number>;
  keys_expiring: KeysExpiring;
  calls_24h: MetricsOut | null;
}

/** GET /ops/events — most recent first; every field may be missing on a malformed row. */
export interface EventOut {
  ts: string | null;
  company_id: string | null;
  user_id: string | null;
  key_id: string | null;
  server_id: string | null;
  server_type: string | null;
  tool: string | null;
  status: "ok" | "denied" | "error" | null;
  duration_ms: number | null;
  error: string | null;
}
