from fastapi import APIRouter, Query

from app.api.deps import C, P, require_company_access
from app.api.schemas import ApplyOut, Page, PolicyIn, PolicyOut, PolicyPatch

router = APIRouter(prefix="/companies/{company_id}/policies", tags=["policies"])


@router.get("", response_model=Page[PolicyOut])
async def list_policies(company_id: str, p: P, c: C, limit: int = Query(50, le=200), cursor: str | None = None,
                        server_id: str | None = None, team_id: str | None = None):
    require_company_access(p, company_id)
    items, nxt = await c.policies.list(company_id, limit=limit, cursor=cursor, server_id=server_id, team_id=team_id)
    return Page(items=[PolicyOut.of(i) for i in items], next_cursor=nxt)


@router.post("", response_model=PolicyOut, status_code=201)
async def create_policy(company_id: str, body: PolicyIn, p: P, c: C):
    """Grant a team (team_id) or everyone (team_id null) a server; materialised into ServerAccess rows right away."""
    require_company_access(p, company_id)
    pol = await c.policies.create(company_id=company_id, team_id=body.team_id, server_id=body.server_id,
                                  permissions=body.permissions, allowed_tools=body.allowed_tools)
    await c.policies.apply(company_id)
    return PolicyOut.of(pol)


@router.get("/{policy_id}", response_model=PolicyOut)
async def get_policy(company_id: str, policy_id: str, p: P, c: C):
    require_company_access(p, company_id)
    return PolicyOut.of(await c.policies.get(company_id, policy_id))


@router.patch("/{policy_id}", response_model=PolicyOut)
async def patch_policy(company_id: str, policy_id: str, body: PolicyPatch, p: P, c: C):
    require_company_access(p, company_id)
    pol = await c.policies.update(company_id, policy_id, permissions=body.permissions, allowed_tools=body.allowed_tools, status=body.status)
    await c.policies.apply(company_id)
    return PolicyOut.of(pol)


@router.post("/apply", response_model=ApplyOut)
async def apply_policies(company_id: str, p: P, c: C):
    """Re-materialise every active policy (also runs after each directory sync and policy change)."""
    require_company_access(p, company_id)
    return ApplyOut(**(await c.policies.apply(company_id)).as_dict())
