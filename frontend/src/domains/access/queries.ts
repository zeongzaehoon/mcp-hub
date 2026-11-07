import { keepPreviousData, useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { initialCursor, nextCursor } from "@/lib/cursor";
import { listAccess, listUserAccess } from "./api";
import type { AccessFilters } from "./types";

export const accessKeys = {
  company: (companyId: string) => ["access", companyId] as const,
  list: (companyId: string, filters: AccessFilters) => ["access", companyId, "list", filters] as const,
  user: (companyId: string, userId: string) => ["access", companyId, "user", userId] as const,
};

export function useAccessInfinite(companyId: string, filters: AccessFilters) {
  return useInfiniteQuery({
    queryKey: accessKeys.list(companyId, filters),
    queryFn: ({ pageParam }) => listAccess(companyId, filters, pageParam),
    initialPageParam: initialCursor,
    getNextPageParam: nextCursor,
    placeholderData: keepPreviousData,
  });
}

/** One page (up to 200) of a user's accesses — used by pickers such as the API key scope selector. */
export function useUserAccessPage(companyId: string, userId: string, enabled = true) {
  return useQuery({
    queryKey: accessKeys.user(companyId, userId),
    queryFn: () => listUserAccess(companyId, userId, undefined),
    enabled: enabled && userId !== "",
    staleTime: 10_000,
  });
}
