import type { Permission, ServerType, Status } from "@/domains/common/enums";
import type { HealthOut } from "@/domains/ops/types";

export interface Server {
  server_id: string;
  server_type: ServerType;
  name: string;
  description: string;
  endpoint: string;
  status: Status;
  /** tool name -> required permission */
  tools: Record<string, Permission>;
  /** datasource connection stored on the server record (encrypted); false => MCP process env default */
  has_connection: boolean;
  /** scheme://user@host:port/db with the password masked, or null */
  connection_hint: string | null;
  /** liveness of the MCP service of this type (backend health checker); null => not configured */
  health: HealthOut | null;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface ServerIn {
  server_type: ServerType;
  name: string;
  description?: string;
  endpoint?: string;
  /** datasource DSN, e.g. postgresql://user:pw@host:5432/db — write-only */
  dsn?: string;
}

export interface ServerPatch {
  name?: string;
  description?: string;
  endpoint?: string;
  status?: Status;
  dsn?: string;
  clear_dsn?: boolean;
}

export const DSN_PLACEHOLDER: Record<ServerType, string> = {
  postgres: "postgresql://user:password@host:5432/database",
  hacker: "postgresql://user:password@host:5432/database",
  clickhouse: "clickhouse://user:password@host:8123/database  (clickhouses:// for TLS)",
  mongo: "mongodb://user:password@host:27017/?authSource=admin",
  ethie: "(DSN 없음 — 실행은 ETHIE_RUNNER_URL 로 프록시)",
};

export interface ServerFilters {
  status?: Status | "";
  server_type?: ServerType | "";
  limit?: number;
}

export type ToolCatalog = Record<ServerType, Record<string, Permission>>;

/** MCP URL a client should use. A key whose scope is not exactly one server must name the server via `?server=`. */
export function mcpConnectUrl(endpoint: string, serverId: string, withServerParam: boolean): string {
  if (!withServerParam) return endpoint;
  const sep = endpoint.includes("?") ? "&" : "?";
  return `${endpoint}${sep}server=${encodeURIComponent(serverId)}`;
}
