import { api, isNotFound } from "@/lib/api";
import { compactParams, type Cursor, type Page } from "@/lib/cursor";
import type { Team, TeamMember, WorkspaceIn, WorkspaceOut, WorkspaceSyncOut } from "./types";

const base = (companyId: string) => `/companies/${encodeURIComponent(companyId)}`;

/** null when the company has no directory connection (404). */
export async function getWorkspace(companyId: string): Promise<WorkspaceOut | null> {
  try {
    const { data } = await api.get<WorkspaceOut>(`${base(companyId)}/workspace`);
    return data;
  } catch (e) {
    if (isNotFound(e)) return null;
    throw e;
  }
}

/** Register or replace the service account (create + update). 422 workspace_key_required / workspace_bad_key, 409 workspace_domain_taken. */
export async function putWorkspace(companyId: string, body: WorkspaceIn): Promise<WorkspaceOut> {
  const { data } = await api.put<WorkspaceOut>(`${base(companyId)}/workspace`, body);
  return data;
}

export async function syncWorkspace(companyId: string): Promise<WorkspaceSyncOut> {
  const { data } = await api.post<WorkspaceSyncOut>(`${base(companyId)}/workspace/sync`);
  return data;
}

/** Wipes the service-account key; synced users/teams stay. */
export async function disconnectWorkspace(companyId: string): Promise<void> {
  await api.delete(`${base(companyId)}/workspace`);
}

export async function listTeams(companyId: string, cursor: Cursor, limit = 50): Promise<Page<Team>> {
  const { data } = await api.get<Page<Team>>(`${base(companyId)}/teams`, { params: compactParams({ cursor, limit }) });
  return data;
}

export async function listTeamMembers(companyId: string, teamId: string): Promise<TeamMember[]> {
  const { data } = await api.get<TeamMember[]>(`${base(companyId)}/teams/${encodeURIComponent(teamId)}/members`);
  return data;
}
