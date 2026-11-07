import { api } from "@/lib/api";
import { compactParams, type Cursor, type Page } from "@/lib/cursor";
import type { Artifact, ArtifactFilters, BrandingIn, Company, CompanyFilters, CompanyIn, CompanyPatch, CompanyStats } from "./types";

export async function listCompanies(filters: CompanyFilters, cursor: Cursor): Promise<Page<Company>> {
  const { data } = await api.get<Page<Company>>("/companies", {
    params: compactParams({ ...filters, cursor, limit: filters.limit ?? 50 }),
  });
  return data;
}

export async function createCompany(body: CompanyIn): Promise<Company> {
  const { data } = await api.post<Company>("/companies", body);
  return data;
}

export async function getCompany(companyId: string): Promise<Company> {
  const { data } = await api.get<Company>(`/companies/${encodeURIComponent(companyId)}`);
  return data;
}

export async function patchCompany(companyId: string, body: CompanyPatch): Promise<Company> {
  const { data } = await api.patch<Company>(`/companies/${encodeURIComponent(companyId)}`, body);
  return data;
}

export async function putBranding(companyId: string, body: BrandingIn): Promise<Company> {
  const { data } = await api.put<Company>(`/companies/${encodeURIComponent(companyId)}/branding`, body);
  return data;
}

export async function deleteBranding(companyId: string): Promise<Company> {
  const { data } = await api.delete<Company>(`/companies/${encodeURIComponent(companyId)}/branding`);
  return data;
}

export async function getCompanyStats(companyId: string): Promise<CompanyStats> {
  const { data } = await api.get<CompanyStats>(`/companies/${encodeURIComponent(companyId)}/stats`);
  return data;
}

export async function listCompanyArtifacts(companyId: string, filters: ArtifactFilters): Promise<Artifact[]> {
  const { data } = await api.get<Artifact[]>(`/companies/${encodeURIComponent(companyId)}/artifacts`, {
    params: compactParams({ user_id: filters.user_id, include_expired: filters.include_expired ? 1 : undefined, limit: filters.limit ?? 50 }),
  });
  return data;
}
