/**
 * Access log rows are free-form dicts written by the MCP servers. Known fields are typed loosely;
 * everything else is rendered as JSON in the UI.
 */
export interface LogRecord {
  PK?: string;
  SK?: string;
  gsi1pk?: string;
  gsi1sk?: string;
  ttl?: number;
  ts?: string;
  key_id?: string;
  company_id?: string;
  user_id?: string;
  server_id?: string;
  server_type?: string;
  tool?: string;
  args_summary?: string;
  /** mirrors mcpcluster_common.model.AccessEvent */
  status?: "ok" | "denied" | "error";
  error?: string | null;
  duration_ms?: number;
  [extra: string]: unknown;
}

export interface LogFilters {
  /** ISO 8601 */
  start?: string;
  /** ISO 8601 */
  end?: string;
  limit?: number;
}

export const LOG_INTERNAL_FIELDS: ReadonlySet<string> = new Set([
  "PK",
  "SK",
  "gsi1pk",
  "gsi1sk",
  "ttl",
  "ts",
  "key_id",
  "company_id",
  "user_id",
  "server_id",
  "server_type",
  "tool",
  "args_summary",
  "status",
  "error",
  "duration_ms",
]);
