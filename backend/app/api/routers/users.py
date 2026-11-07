from fastapi import APIRouter, Query

from app.api.deps import C, P, require_company_access
from app.api.schemas import AccessIn, AccessOut, AccessPatch, Page, UserIn, UserOut, UserPatch
from app.core.errors import Forbidden
from app.domain.enums import Role, Status

router = APIRouter(prefix="/companies/{company_id}/users", tags=["users"])


@router.get("", response_model=Page[UserOut])
async def list_users(company_id: str, p: P, c: C, limit: int = Query(50, le=200), cursor: str | None = None, status: Status | None = None, q: str | None = None):
    require_company_access(p, company_id)
    items, nxt = await c.users.list(company_id, limit=limit, cursor=cursor, status=status, q=q)
    return Page(items=[UserOut.of(i) for i in items], next_cursor=nxt)


@router.post("", response_model=UserOut, status_code=201)
async def create_user(company_id: str, body: UserIn, p: P, c: C):
    require_company_access(p, company_id)
    if body.role == Role.MASTER and not p.is_master:
        raise Forbidden("only master can create master users")
    return UserOut.of(await c.users.create(company_id=company_id, email=body.email, name=body.name, role=body.role, password=body.password))


@router.get("/{user_id}", response_model=UserOut)
async def get_user(company_id: str, user_id: str, p: P, c: C):
    require_company_access(p, company_id)
    return UserOut.of(await c.users.get(company_id, user_id))


@router.patch("/{user_id}", response_model=UserOut)
async def patch_user(company_id: str, user_id: str, body: UserPatch, p: P, c: C):
    require_company_access(p, company_id)
    return UserOut.of(await c.users.update(company_id, user_id, name=body.name, status=body.status, password=body.password))


@router.get("/{user_id}/access", response_model=Page[AccessOut])
async def list_access(company_id: str, user_id: str, p: P, c: C, limit: int = Query(50, le=200), cursor: str | None = None):
    require_company_access(p, company_id)
    items, nxt = await c.access.list_for_user(company_id, user_id, limit=limit, cursor=cursor)
    return Page(items=[AccessOut.of(i) for i in items], next_cursor=nxt)


@router.post("/{user_id}/access", response_model=AccessOut, status_code=201)
async def grant_access(company_id: str, user_id: str, body: AccessIn, p: P, c: C):
    require_company_access(p, company_id)
    return AccessOut.of(await c.access.create(company_id=company_id, user_id=user_id, server_id=body.server_id, permissions=body.permissions, allowed_tools=body.allowed_tools))


@router.get("/{user_id}/access/{server_id}", response_model=AccessOut)
async def get_access(company_id: str, user_id: str, server_id: str, p: P, c: C):
    require_company_access(p, company_id)
    return AccessOut.of(await c.access.get(company_id, user_id, server_id))


@router.patch("/{user_id}/access/{server_id}", response_model=AccessOut)
async def patch_access(company_id: str, user_id: str, server_id: str, body: AccessPatch, p: P, c: C):
    require_company_access(p, company_id)
    return AccessOut.of(await c.access.update(company_id, user_id, server_id, status=body.status, permissions=body.permissions, allowed_tools=body.allowed_tools, own=True))
