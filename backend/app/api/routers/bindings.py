from fastapi import APIRouter, Query

from app.api.deps import C, M, P, require_company_access
from app.api.schemas import BindingIn, BindingOut, BindingPatch, Page
from app.core.errors import InvariantViolation

router = APIRouter(prefix="/companies/{company_id}/bindings", tags=["bindings"])


@router.get("", response_model=Page[BindingOut])
async def list_bindings(company_id: str, p: P, c: C, limit: int = Query(50, le=200), cursor: str | None = None):
    require_company_access(p, company_id)
    items, nxt = await c.bindings.list_for_company(company_id, limit=limit, cursor=cursor)
    return Page(items=[BindingOut.of(i) for i in items], next_cursor=nxt)


@router.post("", response_model=BindingOut, status_code=201)
async def create_binding(company_id: str, body: BindingIn, p: M, c: C):
    try:
        return BindingOut.of(await c.bindings.create(company_id=company_id, server_id=body.server_id, tenant=body.tenant))
    except ValueError as e:
        raise InvariantViolation(str(e)) from e


@router.get("/{server_id}", response_model=BindingOut)
async def get_binding(company_id: str, server_id: str, p: P, c: C):
    require_company_access(p, company_id)
    return BindingOut.of(await c.bindings.get(company_id, server_id))


@router.patch("/{server_id}", response_model=BindingOut)
async def patch_binding(company_id: str, server_id: str, body: BindingPatch, p: M, c: C):
    try:
        return BindingOut.of(await c.bindings.update(company_id, server_id, tenant=body.tenant, status=body.status))
    except ValueError as e:
        raise InvariantViolation(str(e)) from e
