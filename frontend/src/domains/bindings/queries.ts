import { useInfiniteQuery } from "@tanstack/react-query";
import { initialCursor, nextCursor } from "@/lib/cursor";
import { listBindings } from "./api";

export const bindingKeys = {
  company: (companyId: string) => ["bindings", companyId] as const,
  list: (companyId: string) => ["bindings", companyId, "list"] as const,
};

export function useBindingsInfinite(companyId: string, enabled = true) {
  return useInfiniteQuery({
    queryKey: bindingKeys.list(companyId),
    queryFn: ({ pageParam }) => listBindings(companyId, pageParam),
    initialPageParam: initialCursor,
    getNextPageParam: nextCursor,
    enabled,
  });
}
