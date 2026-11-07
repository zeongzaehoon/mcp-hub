from fastapi import APIRouter, Query

from app.api.deps import C, M, P, require_company_access
from app.api.schemas import (
    AccessOut,
    ArtifactOut,
    CompanyIn,
    CompanyOut,
    CompanyPatch,
    CompanyStatsOut,
    MetricsOut,
    Page,
)
from app.domain.enums import Status
from app.domain.models import Branding

router = APIRouter(prefix="/companies", tags=["companies"])


@router.get("", response_model=Page[CompanyOut])
async def list_companies(p: P, c: C, limit: int = Query(50, le=200), cursor: str | None = None, status: Status | None = None, q: str | None = None):
    if not p.is_master:
        return Page(items=[CompanyOut.of(await c.companies.get(p.company_id))])
    items, nxt = await c.companies.list(limit=limit, cursor=cursor, status=status, q=q)
    return Page(items=[CompanyOut.of(i) for i in items], next_cursor=nxt)


@router.post("", response_model=CompanyOut, status_code=201)
async def create_company(body: CompanyIn, p: M, c: C):
    return CompanyOut.of(await c.companies.create(company_id=body.company_id, name=body.name, created_by=p.user_id))


@router.get("/{company_id}", response_model=CompanyOut)
async def get_company(company_id: str, p: P, c: C):
    require_company_access(p, company_id)
    return CompanyOut.of(await c.companies.get(company_id))


@router.patch("/{company_id}", response_model=CompanyOut)
async def patch_company(company_id: str, body: CompanyPatch, p: M, c: C):
    return CompanyOut.of(await c.companies.update(company_id, name=body.name, status=body.status))


@router.get("/{company_id}/access", response_model=Page[AccessOut])
async def list_company_access(company_id: str, p: P, c: C, limit: int = Query(50, le=200), cursor: str | None = None, server_id: str | None = None):
    require_company_access(p, company_id)
    items, nxt = await c.access.list_for_company(company_id, limit=limit, cursor=cursor, server_id=server_id)
    return Page(items=[AccessOut.of(i) for i in items], next_cursor=nxt)


@router.put("/{company_id}/branding", response_model=CompanyOut)
async def put_branding(company_id: str, body: Branding, p: P, c: C):
    """Report branding (logo, brand colour, footer); company admins may set their own."""
    require_company_access(p, company_id)
    return CompanyOut.of(await c.companies.set_branding(company_id, body))


@router.delete("/{company_id}/branding", response_model=CompanyOut)
async def delete_branding(company_id: str, p: P, c: C):
    require_company_access(p, company_id)
    return CompanyOut.of(await c.companies.set_branding(company_id, None))


# ---- dashboards (company scope, read-only)
@router.get("/{company_id}/stats", response_model=CompanyStatsOut)
async def company_stats(company_id: str, p: P, c: C):
    require_company_access(p, company_id)
    await c.companies.get(company_id)  # 404 for unknown
    data = await c.stats.company(company_id)
    data["calls_24h"] = await c.metrics.summary("cmp", company_id)
    return CompanyStatsOut(**data)


@router.get("/{company_id}/metrics", response_model=MetricsOut)
async def company_metrics(company_id: str, p: P, c: C):
    require_company_access(p, company_id)
    return MetricsOut(**(await c.metrics.summary("cmp", company_id)))


@router.get("/{company_id}/artifacts", response_model=list[ArtifactOut])
async def company_artifacts(company_id: str, p: P, c: C, user_id: str | None = None, limit: int = Query(50, le=100), include_expired: bool = False):
    """Metadata of the company's S3 artifacts (newest first). No payload, no download link: data leaves only via MCP export."""
    require_company_access(p, company_id)
    await c.companies.get(company_id)
    return [ArtifactOut(**a) for a in await c.artifacts.list_for_company(company_id, user_id=user_id, limit=limit, include_expired=include_expired)]
