import type { Permission, ServerType, Status } from "@/domains/common/enums";

/** A team (or everyone when `team_id` is null) → server grant; the backend materialises it into user server accesses. */
export interface Policy {
  policy_id: string;
  company_id: string;
  team_id: string | null;
  server_id: string;
  server_type: ServerType;
  permissions: Permission[];
  allowed_tools: string[];
  status: Status;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface PolicyIn {
  /** null = everyone in the company */
  team_id: string | null;
  server_id: string;
  permissions: Permission[];
  allowed_tools: string[];
}

export interface PolicyPatch {
  status?: Status;
  permissions?: Permission[];
  allowed_tools?: string[];
}

export interface PolicyFilters {
  server_id?: string;
  team_id?: string;
  limit?: number;
}

/** `POST /policies/apply` result (also embedded in a Workspace sync result). */
export interface PolicyApplyOut {
  users: number;
  created: number;
  updated: number;
  suspended: number;
  reactivated: number;
  kept_manual: number;
  errors: string[];
}
