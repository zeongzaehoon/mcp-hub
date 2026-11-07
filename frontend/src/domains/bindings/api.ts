import { api } from "@/lib/api";
import { compactParams, type Cursor, type Page } from "@/lib/cursor";
import type { Binding, BindingIn, BindingPatch } from "./types";

export async function listBindings(companyId: string, cursor: Cursor): Promise<Page<Binding>> {
  const { data } = await api.get<Page<Binding>>(`/companies/${encodeURIComponent(companyId)}/bindings`, {
    params: compactParams({ cursor, limit: 100 }),
  });
  return data;
}

export async function createBinding(companyId: string, body: BindingIn): Promise<Binding> {
  const { data } = await api.post<Binding>(`/companies/${encodeURIComponent(companyId)}/bindings`, body);
  return data;
}

export async function getBinding(companyId: string, serverId: string): Promise<Binding> {
  const { data } = await api.get<Binding>(
    `/companies/${encodeURIComponent(companyId)}/bindings/${encodeURIComponent(serverId)}`,
  );
  return data;
}

export async function patchBinding(companyId: string, serverId: string, body: BindingPatch): Promise<Binding> {
  const { data } = await api.patch<Binding>(
    `/companies/${encodeURIComponent(companyId)}/bindings/${encodeURIComponent(serverId)}`,
    body,
  );
  return data;
}
