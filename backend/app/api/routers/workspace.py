import asyncio

from fastapi import APIRouter, Query

from app.api.deps import C, P, require_company_access
from app.api.schemas import Page, SyncOut, TeamMemberOut, TeamOut, WorkspaceIn, WorkspaceOut
from app.core.config import get_settings

router = APIRouter(prefix="/companies/{company_id}", tags=["workspace"])


@router.get("/workspace", response_model=WorkspaceOut)
async def get_workspace(company_id: str, p: P, c: C):
    require_company_access(p, company_id)
    return WorkspaceOut.of(await c.workspace.get(company_id))


@router.put("/workspace", response_model=WorkspaceOut)
async def put_workspace(company_id: str, body: WorkspaceIn, p: P, c: C):
    """Register (or replace) the service-account key the directory sync runs as, and the Workspace admin it
    impersonates. `service_account_json` is write-only; omit it to keep the stored key. The company gets the platform
    Google provider for its employees' connector logins if it has none. A first sync starts in the background."""
    require_company_access(p, company_id)
    conn = await c.workspace.connect(company_id, domain=body.domain, admin_email=body.admin_email,
                                     service_account_json=body.service_account_json, connected_by=p.email)
    await c.sso.ensure_platform_provider(company_id, conn.domain)
    if get_settings().workspace_sync_on_connect:
        asyncio.get_running_loop().create_task(c.workspace.sync_soon(company_id))
    return WorkspaceOut.of(conn)


@router.post("/workspace/sync", response_model=SyncOut)
async def sync_workspace(company_id: str, p: P, c: C):
    """Pull users and groups from the directory now (also runs on a schedule)."""
    require_company_access(p, company_id)
    return SyncOut(**(await c.workspace.run_sync(company_id)).as_dict())


@router.delete("/workspace", status_code=204)
async def disconnect_workspace(company_id: str, p: P, c: C):
    """Forget the service-account key. Synced users and teams stay as they are."""
    require_company_access(p, company_id)
    await c.workspace.disconnect(company_id)


@router.get("/teams", response_model=Page[TeamOut])
async def list_teams(company_id: str, p: P, c: C, limit: int = Query(50, le=200), cursor: str | None = None):
    require_company_access(p, company_id)
    items, nxt = await c.workspace.list_teams(company_id, limit=limit, cursor=cursor)
    return Page(items=[TeamOut.of(t) for t in items], next_cursor=nxt)


@router.get("/teams/{team_id}", response_model=TeamOut)
async def get_team(company_id: str, team_id: str, p: P, c: C):
    require_company_access(p, company_id)
    return TeamOut.of(await c.workspace.get_team(company_id, team_id))


@router.get("/teams/{team_id}/members", response_model=list[TeamMemberOut])
async def list_team_members(company_id: str, team_id: str, p: P, c: C):
    require_company_access(p, company_id)
    return [TeamMemberOut.of(m) for m in await c.workspace.list_members(company_id, team_id)]
