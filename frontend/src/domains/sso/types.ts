export type SsoStatus = "active" | "suspended" | "deleted";

export type SsoPlatform = "google";

export interface SsoProviderIn {
  /** "google" = the platform Google client; issuer/client_id/client_secret are then ignored (send null/omit). */
  platform?: SsoPlatform | null;
  /** required unless `platform` is set */
  issuer?: string | null;
  /** required unless `platform` is set */
  client_id?: string | null;
  /** WRITE-ONLY. Omit/null keeps the stored secret; required on first create (not for platform providers). */
  client_secret?: string | null;
  /** email domains, 1..20 */
  domains: string[];
  /** Google Workspace `hd` */
  hosted_domain?: string | null;
  auto_provision: boolean;
  status?: SsoStatus | null;
}

export interface SsoProviderOut {
  company_id: string;
  kind: "oidc";
  /** platform providers have no own client: `client_id` is null and `has_secret` is false */
  platform: SsoPlatform | null;
  issuer: string;
  client_id: string | null;
  has_secret: boolean;
  domains: string[];
  hosted_domain: string | null;
  auto_provision: boolean;
  status: SsoStatus;
  /** must be registered at the IdP as an authorized redirect URI */
  redirect_uri: string;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface SsoTestOut {
  issuer: string;
  authorization_endpoint: string;
  jwks_keys: number;
  redirect_uri: string;
}

export const SSO_DOMAINS_MAX = 20;

/** "a.com, B.com" -> ["a.com", "b.com"] (trimmed, lower-cased, de-duplicated, empties dropped). */
export function parseDomains(text: string): string[] {
  const out: string[] = [];
  for (const raw of text.split(/[,\s]+/)) {
    const d = raw.trim().toLowerCase();
    if (d && !out.includes(d)) out.push(d);
  }
  return out;
}
