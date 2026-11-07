import type { Status } from "@/domains/common/enums";
import type { PolicyApplyOut } from "@/domains/policies/types";

export type WorkspaceSource = "manual" | "workspace";

/** Counts returned by `POST /workspace/sync` and stored as `last_sync`. */
export interface WorkspaceSyncOut {
  users_seen: number;
  users_created: number;
  users_updated: number;
  users_suspended: number;
  users_reactivated: number;
  teams_seen: number;
  teams_created: number;
  teams_updated: number;
  teams_suspended: number;
  members_added: number;
  members_removed: number;
  /** directory entries that were skipped (emails) */
  skipped: string[];
  /** team policies re-applied after the membership change; null when none ran */
  policies?: PolicyApplyOut | null;
}

/** Directory read-only scopes the service account needs (domain-wide delegation). Shown in the setup help before a
 * connection exists; once connected, `WorkspaceOut.scopes` from the API is authoritative. */
export const DIRECTORY_SCOPES: readonly string[] = [
  "https://www.googleapis.com/auth/admin.directory.user.readonly",
  "https://www.googleapis.com/auth/admin.directory.group.readonly",
  "https://www.googleapis.com/auth/admin.directory.group.member.readonly",
];

/** `PUT /companies/{cid}/workspace` — the operator registers a Google service account (no Google login). */
export interface WorkspaceIn {
  domain: string;
  /** Workspace admin the service account impersonates (domain-wide delegation subject) */
  admin_email: string;
  /** WRITE-ONLY pasted service-account JSON. Required on first connect; omit/null on update to keep the stored key. */
  service_account_json?: string | null;
}

export interface WorkspaceOut {
  company_id: string;
  provider: "google";
  domain: string;
  /** delegation subject */
  admin_email: string;
  /** service account email */
  client_email: string;
  /** the service account's numeric OAuth2 client id (what Google Admin's domain-wide delegation takes) */
  client_id: string;
  /** operator who registered the key */
  connected_by: string;
  /** Directory read-only scopes that must be authorised in Google Admin */
  scopes: string[];
  has_credentials: boolean;
  status: Status;
  last_sync_at: string | null;
  last_sync: WorkspaceSyncOut | null;
  sync_error: string | null;
  /** key missing/revoked or delegation refused (invalid_grant / unauthorized_client) */
  needs_reconnect: boolean;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface Team {
  team_id: string;
  company_id: string;
  name: string;
  email: string | null;
  external_id: string | null;
  source: WorkspaceSource;
  status: Status;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface TeamMember {
  team_id: string;
  user_id: string;
  source: WorkspaceSource;
  status: Status;
}
