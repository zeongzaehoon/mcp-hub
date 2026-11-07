import type { Permission, ServerType, Status } from "@/domains/common/enums";

/** A user's access to one MCP server (company → user → server). Permissions and tools live here, not on API keys. */
export interface Access {
  company_id: string;
  user_id: string;
  server_id: string;
  server_type: ServerType;
  permissions: Permission[];
  allowed_tools: string[];
  status: Status;
  /** "policy" = materialised by team policies; editing it via PATCH turns it into a manual row */
  source: "manual" | "policy";
  policy_ids: string[];
  version: number;
  created_at: string;
  updated_at: string;
}

export interface AccessIn {
  server_id: string;
  permissions: Permission[];
  allowed_tools: string[];
}

export interface AccessPatch {
  status?: Status;
  permissions?: Permission[];
  allowed_tools?: string[];
}

export interface AccessFilters {
  user_id?: string;
  server_id?: string;
  limit?: number;
}
