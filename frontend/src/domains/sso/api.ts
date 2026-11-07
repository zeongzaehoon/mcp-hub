import { api, isNotFound } from "@/lib/api";
import type { SsoProviderIn, SsoProviderOut, SsoTestOut } from "./types";

const path = (companyId: string) => `/companies/${encodeURIComponent(companyId)}/sso`;

/** null when the company has no provider (404). */
export async function getSso(companyId: string): Promise<SsoProviderOut | null> {
  try {
    const { data } = await api.get<SsoProviderOut>(path(companyId));
    return data;
  } catch (e) {
    if (isNotFound(e)) return null;
    throw e;
  }
}

export async function putSso(companyId: string, body: SsoProviderIn): Promise<SsoProviderOut> {
  const { data } = await api.put<SsoProviderOut>(path(companyId), body);
  return data;
}

export async function deleteSso(companyId: string): Promise<void> {
  await api.delete(path(companyId));
}

export async function testSso(companyId: string): Promise<SsoTestOut> {
  const { data } = await api.post<SsoTestOut>(`${path(companyId)}/test`);
  return data;
}
