import re
from typing import Annotated, Literal

from fastapi import APIRouter, Header, Query
from fastapi.responses import RedirectResponse

from app.api.deps import C, P
from app.api.schemas import LoginIn, LoginOut, SsoHandoffIn, SsoHandoffOut, SsoOptionsOut, UserOut
from app.core.config import get_settings
from app.core.errors import DomainError

router = APIRouter(prefix="/auth", tags=["auth"])
_REQ = re.compile(r"^[A-Za-z0-9_-]{1,64}$")  # the gateway's pending-request id, echoed back to it
NO_STORE = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


@router.post("/login", response_model=LoginOut)
async def login(body: LoginIn, c: C):
    token, user = await c.auth.login(body.email, body.password)
    return LoginOut(access_token=token, user=UserOut.of(user))


@router.get("/me", response_model=UserOut)
async def me(p: P, c: C):
    return UserOut.of(await c.users.get(p.company_id, p.user_id))


# ---- company SSO (OIDC) for MCP connectors. Public: the browser arrives here from the gateway's consent page before
# it has any token. The console has no SSO login (operators use email + password).
@router.get("/sso/start", status_code=302)
async def sso_start(
    c: C,
    email: str = Query(min_length=3, max_length=254),
    purpose: Literal["gateway"] = "gateway",
    req: str | None = Query(default=None, max_length=64),
    client: str | None = Query(default=None, max_length=100),
):
    """Find the provider for the email's domain and send the browser there; errors go back to the gateway as codes."""
    if req is not None and not _REQ.match(req):
        req = None
    try:
        url = await c.sso.start(email.strip(), req=req, client=client)
    except DomainError as e:  # the browser is mid-navigation: send it back with the code instead of a JSON body
        url = c.sso.error_url(req, e.code if e.code.startswith("sso_") else "provider_error")
    return RedirectResponse(url, status_code=302, headers=NO_STORE)


@router.get("/sso/callback", status_code=302)
async def sso_callback(c: C, state: str | None = None, code: str | None = None, error: str | None = None):
    """Provider -> here -> gateway (`/oauth/sso/return?code=`). Errors travel the same way as short codes; provider
    details stay in the server log."""
    return RedirectResponse(await c.sso.callback(state, code, error), status_code=302, headers=NO_STORE)


@router.get("/sso/options", response_model=SsoOptionsOut)
async def sso_options():
    """Public: whether the platform Google client exists (the SSO card may offer a `platform: google` provider)."""
    return SsoOptionsOut(google=get_settings().google_enabled)


@router.post("/sso/handoff", response_model=SsoHandoffOut)
async def sso_handoff(body: SsoHandoffIn, c: C, x_internal_secret: Annotated[str | None, Header()] = None):
    """Gateway only (shared secret): one-time code -> the session API key issued by the SSO login."""
    return SsoHandoffOut(**await c.sso.handoff(body.code, x_internal_secret))
