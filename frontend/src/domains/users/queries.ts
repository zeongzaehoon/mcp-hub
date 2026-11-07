import { keepPreviousData, useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { initialCursor, nextCursor } from "@/lib/cursor";
import { listUsers } from "./api";
import type { UserFilters } from "./types";

export const userKeys = {
  company: (companyId: string) => ["users", companyId] as const,
  list: (companyId: string, filters: UserFilters) => ["users", companyId, "list", filters] as const,
  page: (companyId: string, filters: UserFilters) => ["users", companyId, "page", filters] as const,
};

export function useUsersInfinite(companyId: string, filters: UserFilters) {
  return useInfiniteQuery({
    queryKey: userKeys.list(companyId, filters),
    queryFn: ({ pageParam }) => listUsers(companyId, filters, pageParam),
    initialPageParam: initialCursor,
    getNextPageParam: nextCursor,
    placeholderData: keepPreviousData,
  });
}

/** Single page (up to 200) — used by pickers such as the API key create dialog. */
export function useUsersPage(companyId: string, filters: UserFilters, enabled = true) {
  const f: UserFilters = { ...filters, limit: filters.limit ?? 200 };
  return useQuery({
    queryKey: userKeys.page(companyId, f),
    queryFn: () => listUsers(companyId, f, undefined),
    enabled,
    staleTime: 30_000,
  });
}
