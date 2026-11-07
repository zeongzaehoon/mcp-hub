import type { ServerType, Status, TenantMode } from "@/domains/common/enums";

export interface TenantCredentialsOut {
  username: string;
  /** Always "***" in responses. */
  password_enc: string;
}

export interface TenantCredentialsIn {
  username: string;
  password: string;
}

interface TenantBase {
  mode: TenantMode;
  credentials?: TenantCredentialsOut | null;
}

export interface SchemaTenant extends TenantBase {
  mode: "schema";
  schema_name: string;
}

export interface DatabaseTenant extends TenantBase {
  mode: "database";
  database: string;
}

export interface ColumnTenant extends TenantBase {
  mode: "column";
  column: string;
  value: string;
  /** database holding the shared tables (clickhouse); null => server default */
  database?: string | null;
}

export type TenantSpec = SchemaTenant | DatabaseTenant | ColumnTenant;

/** Request payload; credentials carry a plaintext password only across the request boundary. */
export type TenantSpecIn =
  | { mode: "schema"; schema_name: string; credentials?: TenantCredentialsIn }
  | { mode: "database"; database: string; credentials?: TenantCredentialsIn }
  | { mode: "column"; column: string; value: string; database?: string; credentials?: TenantCredentialsIn };

export interface Binding {
  company_id: string;
  server_id: string;
  server_type: ServerType;
  tenant: TenantSpec;
  status: Status;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface BindingIn {
  server_id: string;
  tenant: TenantSpecIn;
}

export interface BindingPatch {
  tenant?: TenantSpecIn;
  status?: Status;
}

/** Mirrors the backend Ident rule: identifiers are always quoted server-side, so only characters that
 *  quoting cannot carry are forbidden (control chars, quotes, backslash, semicolon); 1–63 chars. */
// eslint-disable-next-line no-control-regex
export const IDENT_PATTERN = /^[^\u0000-\u001f"`\\;]{1,63}$/;

export function describeTenant(t: TenantSpec): string {
  switch (t.mode) {
    case "schema":
      return `schema=${t.schema_name}`;
    case "database":
      return `database=${t.database}`;
    case "column":
      return `${t.database ? `${t.database}.` : ""}${t.column}=${t.value}`;
    default:
      return "";
  }
}
