import { api } from "@/lib/api";
import { compactParams, type Cursor, type Page } from "@/lib/cursor";
import type { Policy, PolicyApplyOut, PolicyFilters, PolicyIn, PolicyPatch } from "./types";

const base = (companyId: string) => `/companies/${encodeURIComponent(companyId)}/policies`;

export async function listPolicies(companyId: string, filters: PolicyFilters, cursor: Cursor): Promise<Page<Policy>> {
  const { data } = await api.get<Page<Policy>>(base(companyId), {
    params: compactParams({ ...filters, cursor, limit: filters.limit ?? 50 }),
  });
  return data;
}

export async function createPolicy(companyId: string, body: PolicyIn): Promise<Policy> {
  const { data } = await api.post<Policy>(base(companyId), body);
  return data;
}

export async function patchPolicy(companyId: string, policyId: string, body: PolicyPatch): Promise<Policy> {
  const { data } = await api.patch<Policy>(`${base(companyId)}/${encodeURIComponent(policyId)}`, body);
  return data;
}

export async function applyPolicies(companyId: string): Promise<PolicyApplyOut> {
  const { data } = await api.post<PolicyApplyOut>(`${base(companyId)}/apply`);
  return data;
}
