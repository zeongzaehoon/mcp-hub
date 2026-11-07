import { keepPreviousData, useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { initialCursor, nextCursor } from "@/lib/cursor";
import { getCompany, getCompanyStats, listCompanies, listCompanyArtifacts } from "./api";
import type { ArtifactFilters, CompanyFilters } from "./types";

export const companyKeys = {
  all: ["companies"] as const,
  lists: () => ["companies", "list"] as const,
  list: (filters: CompanyFilters) => ["companies", "list", filters] as const,
  detail: (companyId: string) => ["companies", "detail", companyId] as const,
  stats: (companyId: string) => ["companies", "stats", companyId] as const,
  artifacts: (companyId: string, filters: ArtifactFilters) => ["companies", "artifacts", companyId, filters] as const,
};

export function useCompaniesInfinite(filters: CompanyFilters) {
  return useInfiniteQuery({
    queryKey: companyKeys.list(filters),
    queryFn: ({ pageParam }) => listCompanies(filters, pageParam),
    initialPageParam: initialCursor,
    getNextPageParam: nextCursor,
    placeholderData: keepPreviousData,
  });
}

export function useCompany(companyId: string) {
  return useQuery({
    queryKey: companyKeys.detail(companyId),
    queryFn: () => getCompany(companyId),
  });
}

export function useCompanyStats(companyId: string) {
  return useQuery({
    queryKey: companyKeys.stats(companyId),
    queryFn: () => getCompanyStats(companyId),
    staleTime: 60_000,
  });
}

export function useCompanyArtifacts(companyId: string, filters: ArtifactFilters = {}) {
  return useQuery({
    queryKey: companyKeys.artifacts(companyId, filters),
    queryFn: () => listCompanyArtifacts(companyId, filters),
    placeholderData: keepPreviousData,
  });
}
