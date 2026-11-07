import { api } from "@/lib/api";
import { compactParams, type Cursor, type Page } from "@/lib/cursor";
import type { Access } from "@/domains/access/types";
import type { Binding } from "@/domains/bindings/types";
import type { MetricsOut } from "@/domains/ops/types";
import type { Server, ServerFilters, ServerIn, ServerPatch, ToolCatalog } from "./types";

export async function listServers(filters: ServerFilters, cursor: Cursor): Promise<Page<Server>> {
  const { data } = await api.get<Page<Server>>("/servers", {
    params: compactParams({ ...filters, cursor, limit: filters.limit ?? 50 }),
  });
  return data;
}

export async function createServer(body: ServerIn): Promise<Server> {
  const { data } = await api.post<Server>("/servers", body);
  return data;
}

export async function getServer(serverId: string): Promise<Server> {
  const { data } = await api.get<Server>(`/servers/${encodeURIComponent(serverId)}`);
  return data;
}

export async function patchServer(serverId: string, body: ServerPatch): Promise<Server> {
  const { data } = await api.patch<Server>(`/servers/${encodeURIComponent(serverId)}`, body);
  return data;
}

export async function listServerBindings(serverId: string, cursor: Cursor): Promise<Page<Binding>> {
  const { data } = await api.get<Page<Binding>>(`/servers/${encodeURIComponent(serverId)}/bindings`, {
    params: compactParams({ cursor, limit: 100 }),
  });
  return data;
}

export async function listServerAccess(serverId: string, cursor: Cursor): Promise<Page<Access>> {
  const { data } = await api.get<Page<Access>>(`/servers/${encodeURIComponent(serverId)}/access`, {
    params: compactParams({ cursor, limit: 100 }),
  });
  return data;
}

export async function getToolCatalog(): Promise<ToolCatalog> {
  const { data } = await api.get<ToolCatalog>("/servers/catalog");
  return data;
}

export async function getServerMetrics(serverId: string): Promise<MetricsOut> {
  const { data } = await api.get<MetricsOut>(`/servers/${encodeURIComponent(serverId)}/metrics`);
  return data;
}
