import type { Status } from "@/domains/common/enums";
import type { KeysExpiring, MetricsOut, StatusCounts } from "@/domains/ops/types";

export interface Company {
  company_id: string;
  name: string;
  status: Status;
  created_by: string;
  branding: Branding | null;
  version: number;
  created_at: string;
  updated_at: string;
}

/** Report branding (PDF/DOCX/PPTX). `logo_base64` is raw base64 of a PNG/JPEG, without a `data:` prefix. */
export interface Branding {
  primary_color: string | null;
  logo_base64: string | null;
  report_footer: string | null;
}

export interface BrandingIn {
  primary_color?: string;
  logo_base64?: string;
  report_footer?: string;
}

export interface CompanyIn {
  company_id: string;
  name: string;
}

export interface CompanyPatch {
  name?: string;
  status?: Status;
}

export interface CompanyFilters {
  q?: string;
  status?: Status | "";
  limit?: number;
}

/** Backend regex for company_id: ^[a-z0-9][a-z0-9-]{1,62}$ */
export const COMPANY_ID_PATTERN = /^[a-z0-9][a-z0-9-]{1,62}$/;

/** Backend regex for Branding.primary_color. */
export const HEX_COLOR_PATTERN = /^#[0-9A-Fa-f]{6}$/;
/** Backend LOGO_MAX_BYTES (decoded size). */
export const LOGO_MAX_BYTES = 200_000;
export const REPORT_FOOTER_MAX = 200;

/** `<img src>` for a stored logo. The backend only accepts PNG/JPEG (magic bytes), so the MIME is picked from the
 * base64 prefix; anything that is not plain base64 yields null instead of being put in a URL. */
export function logoDataUri(b64: string | null | undefined): string | null {
  if (!b64 || !/^[A-Za-z0-9+/]+={0,2}$/.test(b64)) return null;
  if (b64.startsWith("iVBORw0KGgo")) return `data:image/png;base64,${b64}`;
  if (b64.startsWith("/9j/")) return `data:image/jpeg;base64,${b64}`;
  return null;
}

/** GET /companies/{cid}/stats — same shape as the global stats minus companies/servers counts. */
export interface CompanyStats {
  company_id: string;
  computed_at: string;
  counts: Partial<Record<"users" | "bindings" | "access" | "api_keys" | "teams" | "policies", StatusCounts>>;
  bindings_per_server: Record<string, number>;
  keys_expiring: KeysExpiring;
  calls_24h: MetricsOut | null;
}

/** S3 artifact metadata only — the payload leaves only through the MCP `export` path. */
export interface Artifact {
  artifact_id: string;
  kind: string;
  title: string;
  server_id: string;
  server_type: string;
  sources: string[];
  user_id: string;
  created_at: string;
  expires_at: string | null;
  bytes: number;
  expired: boolean;
}

export interface ArtifactFilters {
  user_id?: string;
  include_expired?: boolean;
  limit?: number;
}
