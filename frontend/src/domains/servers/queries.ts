import { keepPreviousData, useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { initialCursor, nextCursor } from "@/lib/cursor";
import { getServer, getServerMetrics, getToolCatalog, listServerAccess, listServerBindings, listServers } from "./api";
import type { ServerFilters } from "./types";

export const serverKeys = {
  all: ["servers"] as const,
  lists: () => ["servers", "list"] as const,
  list: (filters: ServerFilters) => ["servers", "list", filters] as const,
  page: (filters: ServerFilters) => ["servers", "page", filters] as const,
  detail: (serverId: string) => ["servers", "detail", serverId] as const,
  bindings: (serverId: string) => ["servers", "bindings", serverId] as const,
  access: (serverId: string) => ["servers", "access", serverId] as const,
  catalog: ["servers", "catalog"] as const,
  metrics: (serverId: string) => ["servers", "metrics", serverId] as const,
};

export function useServersInfinite(filters: ServerFilters) {
  return useInfiniteQuery({
    queryKey: serverKeys.list(filters),
    queryFn: ({ pageParam }) => listServers(filters, pageParam),
    initialPageParam: initialCursor,
    getNextPageParam: nextCursor,
    placeholderData: keepPreviousData,
  });
}

/** Single page (up to 200) for pickers. Servers are global and few, so one page is enough. */
export function useServersPage(filters: ServerFilters = {}, enabled = true) {
  const f: ServerFilters = { ...filters, limit: filters.limit ?? 200 };
  return useQuery({
    queryKey: serverKeys.page(f),
    queryFn: () => listServers(f, undefined),
    enabled,
    staleTime: 60_000,
  });
}

export function useServer(serverId: string) {
  return useQuery({
    queryKey: serverKeys.detail(serverId),
    queryFn: () => getServer(serverId),
  });
}

export function useServerBindingsInfinite(serverId: string, enabled = true) {
  return useInfiniteQuery({
    queryKey: serverKeys.bindings(serverId),
    queryFn: ({ pageParam }) => listServerBindings(serverId, pageParam),
    initialPageParam: initialCursor,
    getNextPageParam: nextCursor,
    enabled,
  });
}

export function useServerAccessInfinite(serverId: string, enabled = true) {
  return useInfiniteQuery({
    queryKey: serverKeys.access(serverId),
    queryFn: ({ pageParam }) => listServerAccess(serverId, pageParam),
    initialPageParam: initialCursor,
    getNextPageParam: nextCursor,
    enabled,
  });
}

export function useToolCatalog() {
  return useQuery({ queryKey: serverKeys.catalog, queryFn: getToolCatalog, staleTime: Infinity });
}

export function useServerMetrics(serverId: string) {
  return useQuery({ queryKey: serverKeys.metrics(serverId), queryFn: () => getServerMetrics(serverId), staleTime: 60_000 });
}
