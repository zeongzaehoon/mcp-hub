import { api } from "@/lib/api";
import { compactParams, type Cursor, type Page } from "@/lib/cursor";
import type { Access, AccessFilters, AccessIn, AccessPatch } from "./types";

const userBase = (companyId: string, userId: string) =>
  `/companies/${encodeURIComponent(companyId)}/users/${encodeURIComponent(userId)}/access`;

export async function listUserAccess(companyId: string, userId: string, cursor: Cursor, limit = 200): Promise<Page<Access>> {
  const { data } = await api.get<Page<Access>>(userBase(companyId, userId), { params: compactParams({ cursor, limit }) });
  return data;
}

/** Company-wide list; the backend filters by server_id only, so a user filter goes through the per-user endpoint. */
export async function listAccess(companyId: string, filters: AccessFilters, cursor: Cursor): Promise<Page<Access>> {
  if (filters.user_id) {
    const page = await listUserAccess(companyId, filters.user_id, cursor, filters.limit ?? 200);
    return filters.server_id ? { ...page, items: page.items.filter((a) => a.server_id === filters.server_id) } : page;
  }
  const { data } = await api.get<Page<Access>>(`/companies/${encodeURIComponent(companyId)}/access`, {
    params: compactParams({ server_id: filters.server_id, cursor, limit: filters.limit ?? 50 }),
  });
  return data;
}

export async function createAccess(companyId: string, userId: string, body: AccessIn): Promise<Access> {
  const { data } = await api.post<Access>(userBase(companyId, userId), body);
  return data;
}

export async function patchAccess(companyId: string, userId: string, serverId: string, body: AccessPatch): Promise<Access> {
  const { data } = await api.patch<Access>(`${userBase(companyId, userId)}/${encodeURIComponent(serverId)}`, body);
  return data;
}
