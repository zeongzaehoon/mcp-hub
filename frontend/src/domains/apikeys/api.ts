import { api } from "@/lib/api";
import { compactParams, type Cursor, type Page } from "@/lib/cursor";
import type { ApiKey, ApiKeyFilters, ApiKeyIn, ApiKeyIssued, ApiKeyPatch } from "./types";

const base = (companyId: string) => `/companies/${encodeURIComponent(companyId)}/api-keys`;

export async function listApiKeys(companyId: string, filters: ApiKeyFilters, cursor: Cursor): Promise<Page<ApiKey>> {
  const { data } = await api.get<Page<ApiKey>>(base(companyId), {
    params: compactParams({ ...filters, cursor, limit: filters.limit ?? 50 }),
  });
  return data;
}

export async function createApiKey(companyId: string, body: ApiKeyIn): Promise<ApiKeyIssued> {
  const { data } = await api.post<ApiKeyIssued>(base(companyId), body);
  return data;
}

export async function getApiKey(companyId: string, keyId: string): Promise<ApiKey> {
  const { data } = await api.get<ApiKey>(`${base(companyId)}/${encodeURIComponent(keyId)}`);
  return data;
}

export async function patchApiKey(companyId: string, keyId: string, body: ApiKeyPatch): Promise<ApiKey> {
  const { data } = await api.patch<ApiKey>(`${base(companyId)}/${encodeURIComponent(keyId)}`, body);
  return data;
}

export async function rotateApiKey(companyId: string, keyId: string): Promise<ApiKeyIssued> {
  const { data } = await api.post<ApiKeyIssued>(`${base(companyId)}/${encodeURIComponent(keyId)}/rotate`);
  return data;
}
