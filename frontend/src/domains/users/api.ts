import { api } from "@/lib/api";
import { compactParams, type Cursor, type Page } from "@/lib/cursor";
import type { User, UserFilters, UserIn, UserPatch } from "./types";

export async function listUsers(companyId: string, filters: UserFilters, cursor: Cursor): Promise<Page<User>> {
  const { data } = await api.get<Page<User>>(`/companies/${encodeURIComponent(companyId)}/users`, {
    params: compactParams({ ...filters, cursor, limit: filters.limit ?? 50 }),
  });
  return data;
}

export async function createUser(companyId: string, body: UserIn): Promise<User> {
  const { data } = await api.post<User>(`/companies/${encodeURIComponent(companyId)}/users`, body);
  return data;
}

export async function getUser(companyId: string, userId: string): Promise<User> {
  const { data } = await api.get<User>(
    `/companies/${encodeURIComponent(companyId)}/users/${encodeURIComponent(userId)}`,
  );
  return data;
}

export async function patchUser(companyId: string, userId: string, body: UserPatch): Promise<User> {
  const { data } = await api.patch<User>(
    `/companies/${encodeURIComponent(companyId)}/users/${encodeURIComponent(userId)}`,
    body,
  );
  return data;
}
