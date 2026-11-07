import { keepPreviousData, useInfiniteQuery } from "@tanstack/react-query";
import { initialCursor, nextCursor } from "@/lib/cursor";
import { listApiKeys } from "./api";
import type { ApiKeyFilters } from "./types";

export const apiKeyKeys = {
  company: (companyId: string) => ["apikeys", companyId] as const,
  list: (companyId: string, filters: ApiKeyFilters) => ["apikeys", companyId, "list", filters] as const,
};

export function useApiKeysInfinite(companyId: string, filters: ApiKeyFilters) {
  return useInfiniteQuery({
    queryKey: apiKeyKeys.list(companyId, filters),
    queryFn: ({ pageParam }) => listApiKeys(companyId, filters, pageParam),
    initialPageParam: initialCursor,
    getNextPageParam: nextCursor,
    placeholderData: keepPreviousData,
  });
}
