import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { initialCursor, nextCursor } from "@/lib/cursor";
import { getWorkspace, listTeamMembers, listTeams } from "./api";

export const workspaceKeys = {
  detail: (companyId: string) => ["workspace", "detail", companyId] as const,
  teams: (companyId: string) => ["workspace", "teams", companyId] as const,
  members: (companyId: string, teamId: string) => ["workspace", "teams", companyId, teamId, "members"] as const,
};

export function useWorkspace(companyId: string) {
  return useQuery({
    queryKey: workspaceKeys.detail(companyId),
    queryFn: () => getWorkspace(companyId),
  });
}

export function useTeams(companyId: string) {
  return useInfiniteQuery({
    queryKey: workspaceKeys.teams(companyId),
    queryFn: ({ pageParam }) => listTeams(companyId, pageParam),
    initialPageParam: initialCursor,
    getNextPageParam: nextCursor,
  });
}

export function useTeamMembers(companyId: string, teamId: string | null) {
  return useQuery({
    queryKey: workspaceKeys.members(companyId, teamId ?? ""),
    queryFn: () => listTeamMembers(companyId, teamId as string),
    enabled: teamId !== null,
  });
}
