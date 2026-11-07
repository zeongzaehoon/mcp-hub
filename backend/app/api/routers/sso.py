from fastapi import APIRouter

from app.api.deps import C, P, require_company_access
from app.api.schemas import SsoProviderIn, SsoProviderOut, SsoTestOut

router = APIRouter(prefix="/companies/{company_id}/sso", tags=["sso"])


@router.get("", response_model=SsoProviderOut)
async def get_provider(company_id: str, p: P, c: C):
    require_company_access(p, company_id)
    return SsoProviderOut.of(await c.sso.get_provider(company_id), c.sso.redirect_uri)


@router.put("", response_model=SsoProviderOut)
async def put_provider(company_id: str, body: SsoProviderIn, p: P, c: C):
    """Create or replace the company's OIDC provider. `client_secret` is write-only: omit it to keep the stored one."""
    require_company_access(p, company_id)
    idp = await c.sso.put_provider(
        company_id, issuer=body.issuer, client_id=body.client_id, client_secret=body.client_secret, domains=body.domains,
        hosted_domain=body.hosted_domain, auto_provision=body.auto_provision, status=body.status, platform=body.platform,
    )
    return SsoProviderOut.of(idp, c.sso.redirect_uri)


@router.delete("", status_code=204)
async def delete_provider(company_id: str, p: P, c: C):
    require_company_access(p, company_id)
    await c.sso.delete_provider(company_id)


@router.post("/test", response_model=SsoTestOut)
async def test_provider(company_id: str, p: P, c: C):
    require_company_access(p, company_id)
    return SsoTestOut(**await c.sso.test_provider(company_id))
