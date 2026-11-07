from fastapi import APIRouter, Query

from app.api.deps import C, P, require_company_access
from app.api.schemas import ApiKeyIn, ApiKeyIssued, ApiKeyOut, ApiKeyPatch, Page
from app.core.errors import InvariantViolation
from app.domain.enums import Status

router = APIRouter(prefix="/companies/{company_id}/api-keys", tags=["api-keys"])


@router.get("", response_model=Page[ApiKeyOut])
async def list_keys(company_id: str, p: P, c: C, limit: int = Query(50, le=200), cursor: str | None = None, status: Status | None = None, user_id: str | None = None, q: str | None = None):
    require_company_access(p, company_id)
    items, nxt = await c.apikeys.list_for_company(company_id, limit=limit, cursor=cursor, status=status, user_id=user_id, q=q)
    return Page(items=[ApiKeyOut.of(i) for i in items], next_cursor=nxt)


@router.post("", response_model=ApiKeyIssued, status_code=201)
async def create_key(company_id: str, body: ApiKeyIn, p: P, c: C):
    require_company_access(p, company_id)
    try:
        k, raw = await c.apikeys.create(
            company_id=company_id, user_id=body.user_id, server_ids=body.server_ids, name=body.name,
            expires_at=body.expires_at,
            rate_limit_per_minute=body.rate_limit_per_minute,
        )
    except ValueError as e:
        raise InvariantViolation(str(e)) from e
    return ApiKeyIssued(key=ApiKeyOut.of(k), raw_key=raw)


@router.get("/{key_id}", response_model=ApiKeyOut)
async def get_key(company_id: str, key_id: str, p: P, c: C):
    require_company_access(p, company_id)
    return ApiKeyOut.of(await c.apikeys.get(company_id, key_id))


@router.patch("/{key_id}", response_model=ApiKeyOut)
async def patch_key(company_id: str, key_id: str, body: ApiKeyPatch, p: P, c: C):
    require_company_access(p, company_id)
    return ApiKeyOut.of(await c.apikeys.update(
        company_id, key_id, name=body.name, status=body.status, server_ids=body.server_ids,
        scope_all=body.scope_all, expires_at=body.expires_at, clear_expiry=body.clear_expiry,
        rate_limit_per_minute=body.rate_limit_per_minute, clear_rate_limit=body.clear_rate_limit,
    ))


@router.post("/{key_id}/rotate", response_model=ApiKeyIssued)
async def rotate_key(company_id: str, key_id: str, p: P, c: C):
    require_company_access(p, company_id)
    k, raw = await c.apikeys.rotate(company_id, key_id)
    return ApiKeyIssued(key=ApiKeyOut.of(k), raw_key=raw)
