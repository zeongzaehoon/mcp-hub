import { keepPreviousData, useInfiniteQuery } from "@tanstack/react-query";
import { initialCursor, nextCursor } from "@/lib/cursor";
import { listCompanyLogs, listKeyLogs } from "./api";
import type { LogFilters } from "./types";

export const logKeys = {
  company: (companyId: string, filters: LogFilters) => ["logs", companyId, "company", filters] as const,
  key: (companyId: string, keyId: string, filters: LogFilters) => ["logs", companyId, "key", keyId, filters] as const,
};

export function useCompanyLogsInfinite(companyId: string, filters: LogFilters) {
  return useInfiniteQuery({
    queryKey: logKeys.company(companyId, filters),
    queryFn: ({ pageParam }) => listCompanyLogs(companyId, filters, pageParam),
    initialPageParam: initialCursor,
    getNextPageParam: nextCursor,
    placeholderData: keepPreviousData,
  });
}

export function useKeyLogsInfinite(companyId: string, keyId: string, filters: LogFilters, enabled = true) {
  return useInfiniteQuery({
    queryKey: logKeys.key(companyId, keyId, filters),
    queryFn: ({ pageParam }) => listKeyLogs(companyId, keyId, filters, pageParam),
    initialPageParam: initialCursor,
    getNextPageParam: nextCursor,
    placeholderData: keepPreviousData,
    enabled,
  });
}
