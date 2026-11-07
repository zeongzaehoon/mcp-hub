import type { Role, Status } from "@/domains/common/enums";

export interface SsoIdentity {
  issuer: string;
  subject: string;
}

export interface User {
  user_id: string;
  company_id: string;
  email: string;
  name: string;
  role: Role;
  status: Status;
  can_login: boolean;
  /** linked SSO logins (OIDC issuer + subject) */
  identities?: SsoIdentity[];
  /** "workspace" = created/managed by the Google Workspace directory sync */
  source: "manual" | "workspace";
  version: number;
  created_at: string;
  updated_at: string;
}

export interface UserIn {
  email: string;
  name: string;
  role: Role;
  password?: string;
}

export interface UserPatch {
  name?: string;
  status?: Status;
  password?: string;
}

export interface UserFilters {
  q?: string;
  status?: Status | "";
  limit?: number;
}
