import { api } from "@/lib/api";
import { compactParams, type Cursor, type Page } from "@/lib/cursor";
import type { LogFilters, LogRecord } from "./types";

export async function listCompanyLogs(companyId: string, filters: LogFilters, cursor: Cursor): Promise<Page<LogRecord>> {
  const { data } = await api.get<Page<LogRecord>>(`/companies/${encodeURIComponent(companyId)}/logs`, {
    params: compactParams({ ...filters, cursor, limit: filters.limit ?? 100 }),
  });
  return data;
}

export async function listKeyLogs(
  companyId: string,
  keyId: string,
  filters: LogFilters,
  cursor: Cursor,
): Promise<Page<LogRecord>> {
  const { data } = await api.get<Page<LogRecord>>(
    `/companies/${encodeURIComponent(companyId)}/api-keys/${encodeURIComponent(keyId)}/logs`,
    { params: compactParams({ ...filters, cursor, limit: filters.limit ?? 100 }) },
  );
  return data;
}
