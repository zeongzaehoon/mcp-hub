import type { Status } from "@/domains/common/enums";

/** Acts on behalf of its user; permissions/tools come from the user's server access, the key only narrows servers. */
export interface ApiKey {
  key_id: string;
  company_id: string;
  user_id: string;
  /** null => every server the user has access to, now or later */
  server_ids: string[] | null;
  key_prefix: string;
  name: string;
  status: Status;
  expires_at: string | null;
  last_used_at: string | null;
  /** null => server default */
  rate_limit_per_minute: number | null;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface ApiKeyIn {
  user_id: string;
  name: string;
  /** omit / null => all of the user's servers */
  server_ids?: string[] | null;
  expires_at?: string | null;
  rate_limit_per_minute?: number | null;
}

export interface ApiKeyPatch {
  name?: string;
  status?: Status;
  server_ids?: string[];
  /** widen the scope back to all of the user's servers */
  scope_all?: boolean;
  expires_at?: string | null;
  clear_expiry?: boolean;
  rate_limit_per_minute?: number | null;
  clear_rate_limit?: boolean;
}

export interface ApiKeyIssued {
  key: ApiKey;
  /** Shown exactly once. */
  raw_key: string;
}

export interface ApiKeyFilters {
  q?: string;
  status?: Status | "";
  user_id?: string;
  limit?: number;
}

export const EXTEND_DAYS = 30;

/** "30일 연장": max(now, expires_at) + 30 days — an already-expired key gets 30 days from now, not from its old expiry. */
export function extendedExpiry(expiresAt: string | null): string {
  const base = Math.max(Date.now(), expiresAt ? new Date(expiresAt).getTime() : 0);
  return new Date(base + EXTEND_DAYS * 86_400_000).toISOString();
}
