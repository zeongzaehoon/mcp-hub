import { keepPreviousData, useInfiniteQuery } from "@tanstack/react-query";
import { initialCursor, nextCursor } from "@/lib/cursor";
import { listPolicies } from "./api";
import type { PolicyFilters } from "./types";

export const policyKeys = {
  company: (companyId: string) => ["policies", companyId] as const,
  list: (companyId: string, filters: PolicyFilters) => ["policies", companyId, "list", filters] as const,
};

export function usePoliciesInfinite(companyId: string, filters: PolicyFilters = {}) {
  return useInfiniteQuery({
    queryKey: policyKeys.list(companyId, filters),
    queryFn: ({ pageParam }) => listPolicies(companyId, filters, pageParam),
    initialPageParam: initialCursor,
    getNextPageParam: nextCursor,
    placeholderData: keepPreviousData,
  });
}
