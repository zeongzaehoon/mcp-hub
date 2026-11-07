"""OAuth 2.1 for connectors that cannot send a static header (ChatGPT, Claude.ai custom connectors).

Consent = the person pastes an API key they were issued (decision 2026-09-24): no new identity model, and every
existing invariant keeps applying because each OAuth access token simply stands for that key.

* Authorization server + resource server in the gateway, on FastMCP's `OAuthProvider` (metadata RFC 8414 / 9728,
  dynamic client registration RFC 7591, PKCE S256 verification are the SDK's).
* `authorize()` parks the request (10 min, single use) and sends the browser to `/oauth/consent`, which asks for the
  key. A code is issued only for a key that is active with an active company and user; 5 attempts per request.
* Tokens are opaque random values; Redis stores only their sha256, with the key encrypted (tenant Fernet key).
  Access 1 h, refresh 30 d, refresh rotates. Resource indicators (RFC 8707) must name this gateway.
* Every MCP request resolves token -> key and then takes exactly the key path (gateway check + upstream
  authorization). Suspending, rotating or expiring the key therefore kills its tokens immediately.
* Storage is Redis (`REDIS_DB_OAUTH`); a wipe forces re-consent (fail closed).
* Company SSO (optional, `SsoBridge`): the consent page can instead send the person to their company's OIDC
  provider through the backend (`/api/v1/auth/sso/start?purpose=gateway`). The backend issues a session API key in
  their name and returns a one-time handoff code to `/oauth/sso/return`; the gateway swaps it for the key
  server-to-server (shared secret) and continues exactly as if that key had been pasted."""

import hashlib
import html
import os
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx
import orjson
import redis.asyncio as aioredis
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    RegistrationError,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.routing import Route

from fastmcp.server.auth.auth import ClientRegistrationOptions, OAuthProvider, RevocationOptions
from mcpcluster_common.secrets import fernet
from mcpcluster_common.settings import CommonSettings

KEY_PREFIX = "mcpk_"
PENDING_TTL_S = 600
CODE_TTL_S = 300
ACCESS_TTL_S = 3600
REFRESH_TTL_S = 30 * 86400
CLIENT_TTL_S = 90 * 86400
MAX_CONSENT_ATTEMPTS = 5
SCOPES = ["mcp"]
LOOPBACK = {"localhost", "127.0.0.1", "::1"}
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'",
    "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer", "Cache-Control": "no-store",
}


def _h(v: str) -> str:
    return hashlib.sha256(v.encode()).hexdigest()


@dataclass(frozen=True)
class SsoBridge:
    """How the gateway reaches the backend's SSO: the browser goes to `public_api` (same origin as the gateway),
    the handoff call goes to `backend_url` on the compose/ECS network with the shared secret."""

    public_api: str  # e.g. https://mcp.example.com/api/v1  (browser-facing)
    backend_url: str  # e.g. http://backend:8080           (server-to-server)
    secret: str

    @classmethod
    def from_env(cls, public_url: str) -> "SsoBridge | None":
        backend, secret = os.environ.get("GATEWAY_BACKEND_URL", "").rstrip("/"), os.environ.get("SSO_HANDOFF_SECRET", "")
        if not backend or not secret:
            return None
        return cls(public_api=f"{public_url.rstrip('/')}/api/v1", backend_url=backend, secret=secret)


SSO_ERRORS = {
    "expired": "로그인 세션이 만료되었습니다. 다시 시도하세요.",
    "provider_denied": "회사 로그인이 취소되었습니다.",
    "provider_error": "회사 로그인 공급자에서 오류가 났습니다. 관리자에게 문의하세요.",
    "email_not_verified": "이메일이 확인되지 않은 계정입니다.",
    "domain_not_allowed": "이 회사에 허용된 이메일 도메인이 아닙니다.",
    "not_registered": "등록되지 않은 사용자입니다. 회사 관리자에게 문의하세요.",
    "user_suspended": "정지된 사용자입니다.",
    "sso_not_configured": "이 이메일 도메인에는 회사 로그인이 설정되어 있지 않습니다.",
    "sso_provider_unreachable": "회사 로그인 공급자에 연결할 수 없습니다.",
    "sso_gateway_disabled": "이 배포에서는 회사 로그인으로 MCP 연결을 할 수 없습니다.",
    "sso_google_disabled": "이 배포에는 Google 로그인이 설정되어 있지 않습니다.",
    "not_workspace_account": "Google Workspace 계정이 아닙니다.",
    "not_workspace_admin": "Workspace 관리자만 회사를 연결할 수 있습니다.",
    "use_company_sso": "이 회사는 자체 SSO 를 씁니다. 회사 계정 로그인을 이용하세요.",
}


class ApiKeyConsentProvider(OAuthProvider):
    def __init__(self, *, base_url: str, settings: CommonSettings, key_is_active, sso: SsoBridge | None = None):
        super().__init__(
            base_url=base_url,
            client_registration_options=ClientRegistrationOptions(enabled=True, valid_scopes=SCOPES, default_scopes=SCOPES),
            revocation_options=RevocationOptions(enabled=True),
        )
        self.s = settings
        self._key_is_active = key_is_active  # async (raw_key) -> bool: key, company and user active
        self.r = aioredis.from_url(settings.redis_url, db=int(os.environ.get("REDIS_DB_OAUTH", "4")), decode_responses=True)
        self._resource = str(self._get_resource_url("/mcp") or "").rstrip("/")
        self.sso = sso

    # ---- storage helpers -------------------------------------------------------------------------
    async def _put(self, key: str, value: dict[str, Any], ttl: int) -> None:
        await self.r.set(key, orjson.dumps(value).decode(), ex=ttl)

    async def _get(self, key: str) -> dict[str, Any] | None:
        raw = await self.r.get(key)
        return orjson.loads(raw) if raw else None

    def _check_resource(self, resource: str | None) -> None:
        if resource and resource.rstrip("/") != self._resource:
            raise AuthorizeError(error="invalid_request", error_description="resource must be this MCP gateway")

    # ---- clients (RFC 7591) ----------------------------------------------------------------------
    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        d = await self._get(f"oauth:client:{client_id}")
        return OAuthClientInformationFull.model_validate(d) if d else None

    def _redirect_allowed(self, uri: str) -> bool:
        """Registration is open (RFC 7591), so a phishing client could point the code at itself: only https redirects
        (loopback excepted, for local/native clients) and, when GATEWAY_OAUTH_REDIRECT_HOSTS is set, only those hosts."""
        u = urlsplit(uri)
        host = (u.hostname or "").lower()
        if u.scheme != "https" and not (u.scheme == "http" and host in LOOPBACK):
            return False
        allow = {h.strip().lower() for h in os.environ.get("GATEWAY_OAUTH_REDIRECT_HOSTS", "").split(",") if h.strip()}
        return not allow or host in allow or host in LOOPBACK or any(host.endswith("." + a) for a in allow)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        if client_info.client_id is None:
            raise RegistrationError("invalid_client_metadata", "client_id is required")
        bad = [str(u) for u in (client_info.redirect_uris or []) if not self._redirect_allowed(str(u))]
        if bad or not client_info.redirect_uris:
            raise RegistrationError("invalid_redirect_uri", f"not allowed: {bad or 'none given'} (https only; see GATEWAY_OAUTH_REDIRECT_HOSTS)")
        if client_info.scope and set(client_info.scope.split()) - set(SCOPES):
            raise RegistrationError("invalid_client_metadata", f"unsupported scope; supported: {' '.join(SCOPES)}")
        await self._put(f"oauth:client:{client_info.client_id}", client_info.model_dump(mode="json"), CLIENT_TTL_S)

    # ---- authorization ---------------------------------------------------------------------------
    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        self._check_resource(params.resource)
        req = secrets.token_urlsafe(24)
        await self._put(f"oauth:pending:{req}", {"client_id": client.client_id, "params": params.model_dump(mode="json"), "attempts": 0}, PENDING_TTL_S)
        return f"{str(self.base_url).rstrip('/')}/oauth/consent?req={req}"

    async def _consent_page(self, request: Request) -> Response:
        form = await request.form() if request.method == "POST" else None
        req = str(form.get("req", "")) if form is not None else request.query_params.get("req")
        pending = await self._get(f"oauth:pending:{req}") if req else None
        if not pending:
            return HTMLResponse(self._html("이 인증 요청은 만료되었거나 이미 사용되었습니다. 클라이언트에서 다시 연결하세요."), status_code=400, headers=SECURITY_HEADERS)
        client = await self.get_client(pending["client_id"])
        if client is None:
            return HTMLResponse(self._html("알 수 없는 클라이언트입니다."), status_code=400, headers=SECURITY_HEADERS)
        params = AuthorizationParams.model_validate(pending["params"])
        if request.method == "GET":
            return HTMLResponse(self._form(req, client, params), headers=SECURITY_HEADERS)

        raw = str(form.get("api_key", "")).strip()
        pending["attempts"] += 1
        if not raw.startswith(KEY_PREFIX) or not await self._key_is_active(raw):
            if pending["attempts"] >= MAX_CONSENT_ATTEMPTS:
                await self.r.delete(f"oauth:pending:{req}")
                return HTMLResponse(self._html("시도 횟수를 초과했습니다. 클라이언트에서 다시 연결하세요."), status_code=400, headers=SECURITY_HEADERS)
            await self._put(f"oauth:pending:{req}", pending, PENDING_TTL_S)
            return HTMLResponse(self._form(req, client, params, error="유효하지 않거나 정지된 키입니다."), status_code=400, headers=SECURITY_HEADERS)
        return await self._grant(req, client, params, raw)

    async def _grant(self, req: str, client: OAuthClientInformationFull, params: AuthorizationParams, raw: str) -> Response:
        """An active key was proven (pasted or issued by SSO): consume the pending request and hand the client a code."""
        if not await self.r.delete(f"oauth:pending:{req}"):  # single use, also under concurrent submits
            return HTMLResponse(self._html("이 인증 요청은 이미 사용되었습니다."), status_code=400, headers=SECURITY_HEADERS)
        code = secrets.token_urlsafe(32)
        ac = AuthorizationCode(code=code, client_id=client.client_id, redirect_uri=params.redirect_uri,
                               redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
                               scopes=params.scopes or SCOPES, expires_at=time.time() + CODE_TTL_S,
                               code_challenge=params.code_challenge, resource=params.resource)
        await self._put(f"oauth:code:{_h(code)}", {"code": ac.model_dump(mode="json"), "key_enc": fernet(self.s).encrypt(raw.encode()).decode()}, CODE_TTL_S)
        return RedirectResponse(construct_redirect_uri(str(params.redirect_uri), code=code, state=params.state), status_code=302, headers=SECURITY_HEADERS)

    # ---- company SSO ---------------------------------------------------------------------------------
    async def _consent_sso(self, request: Request) -> Response:
        """Browser -> backend SSO start. The backend returns the person to /oauth/sso/return with a handoff code."""
        if self.sso is None:
            return HTMLResponse(self._html("이 배포에서는 회사 로그인을 지원하지 않습니다."), status_code=404, headers=SECURITY_HEADERS)
        form = await request.form()
        req, email = str(form.get("req", "")), str(form.get("email", "")).strip()
        pending = await self._get(f"oauth:pending:{req}") if req else None
        if not pending:
            return HTMLResponse(self._html("이 인증 요청은 만료되었거나 이미 사용되었습니다. 클라이언트에서 다시 연결하세요."), status_code=400, headers=SECURITY_HEADERS)
        client = await self.get_client(pending["client_id"])
        params = AuthorizationParams.model_validate(pending["params"])
        if client is None or "@" not in email or len(email) > 254:
            return HTMLResponse(self._form(req, client, params, error="이메일을 확인하세요.") if client else self._html("알 수 없는 클라이언트입니다."),
                                status_code=400, headers=SECURITY_HEADERS)
        q = urlencode({"email": email, "purpose": "gateway", "req": req, "client": (client.client_name or client.client_id or "mcp")[:100]})
        return RedirectResponse(f"{self.sso.public_api}/auth/sso/start?{q}", status_code=302, headers=SECURITY_HEADERS)

    async def _sso_return(self, request: Request) -> Response:
        """Backend -> here after the provider login: swap the handoff code for the session key, then grant as usual."""
        req, code, error = request.query_params.get("req", ""), request.query_params.get("code", ""), request.query_params.get("error", "")
        pending = await self._get(f"oauth:pending:{req}") if req else None
        if not pending or self.sso is None:
            return HTMLResponse(self._html("이 인증 요청은 만료되었거나 이미 사용되었습니다. 클라이언트에서 다시 연결하세요."), status_code=400, headers=SECURITY_HEADERS)
        client = await self.get_client(pending["client_id"])
        if client is None:
            return HTMLResponse(self._html("알 수 없는 클라이언트입니다."), status_code=400, headers=SECURITY_HEADERS)
        params = AuthorizationParams.model_validate(pending["params"])
        if error or not code:
            return HTMLResponse(self._form(req, client, params, error=SSO_ERRORS.get(error, "회사 로그인에 실패했습니다.")), status_code=400, headers=SECURITY_HEADERS)
        try:
            async with httpx.AsyncClient(timeout=10) as hc:
                r = await hc.post(f"{self.sso.backend_url}/api/v1/auth/sso/handoff", json={"code": code},
                                  headers={"X-Internal-Secret": self.sso.secret})
            raw = r.json()["raw_key"] if r.status_code == 200 else ""
        except (httpx.HTTPError, ValueError, KeyError):
            raw = ""
        if not raw.startswith(KEY_PREFIX) or not await self._key_is_active(raw):
            return HTMLResponse(self._form(req, client, params, error=SSO_ERRORS["expired"]), status_code=400, headers=SECURITY_HEADERS)
        return await self._grant(req, client, params, raw)

    @staticmethod
    def _html(body: str) -> str:
        return (f"<!doctype html><html lang=ko><meta charset=utf-8><title>FindTheGap MCP</title>"
                f"<body style='font-family:sans-serif;max-width:32rem;margin:4rem auto;padding:0 1rem'>{body}</body></html>")

    def _form(self, req: str, client: OAuthClientInformationFull, params: AuthorizationParams, error: str = "") -> str:
        name = html.escape(client.client_name or client.client_id or "MCP client")
        host = html.escape(urlsplit(str(params.redirect_uri)).netloc or str(params.redirect_uri))
        err = f"<p style='color:#DC2626'>{html.escape(error)}</p>" if error else ""
        return self._html(
            f"<h2>FindTheGap MCP 연결</h2><p><b>{name}</b> 이(가) 이 서버에 연결하려고 합니다.<br>"
            f"<small>돌아갈 주소: {host}</small></p><p>회사 관리자에게 발급받은 API 키(<code>mcpk_…</code>)를 입력하면, 이 연결은 그 키의 권한으로만 동작합니다."
            f" 키를 정지하면 이 연결도 즉시 끊깁니다.</p>{err}"
            f"<form method=post action='/oauth/consent'><input type=hidden name=req value='{html.escape(req)}'>"
            f"<input name=api_key type=password autocomplete=off required style='width:100%;padding:.5rem' placeholder='mcpk_…'>"
            f"<p><button type=submit style='padding:.5rem 1rem;background:#00A758;color:#fff;border:0;border-radius:4px'>연결 허용</button></p></form>"
            + self._sso_section(req))

    def _sso_section(self, req: str) -> str:
        if self.sso is None:
            return ""
        return (f"<hr style='margin:1.5rem 0;border:0;border-top:1px solid #ddd'><h3>회사 계정으로 로그인</h3>"
                f"<p>회사가 SSO(Google Workspace 등)를 연결했다면 API 키 없이 회사 계정으로 로그인할 수 있습니다. "
                f"로그인하면 내 이름으로 만료되는 연결용 키가 발급되고, 권한은 관리자가 부여한 범위 그대로입니다.</p>"
                f"<form method=post action='/oauth/consent/sso'><input type=hidden name=req value='{html.escape(req)}'>"
                f"<input name=email type=email autocomplete=email required style='width:100%;padding:.5rem' placeholder='name@company.com'>"
                f"<p><button type=submit style='padding:.5rem 1rem;background:#1F2937;color:#fff;border:0;border-radius:4px'>회사 계정으로 계속</button></p></form>")

    async def load_authorization_code(self, client: OAuthClientInformationFull, authorization_code: str) -> AuthorizationCode | None:
        d = await self._get(f"oauth:code:{_h(authorization_code)}")
        if not d:
            return None
        ac = AuthorizationCode.model_validate(d["code"])
        if ac.client_id != client.client_id or ac.expires_at < time.time():
            return None
        return ac

    async def _issue(self, client_id: str, scopes: list[str], resource: str | None, key_enc: str) -> OAuthToken:
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        now = int(time.time())
        rec = {"client_id": client_id, "scopes": scopes, "resource": resource, "key_enc": key_enc}
        await self._put(f"oauth:access:{_h(access)}", {**rec, "expires_at": now + ACCESS_TTL_S, "refresh": _h(refresh)}, ACCESS_TTL_S)
        await self._put(f"oauth:refresh:{_h(refresh)}", {**rec, "expires_at": now + REFRESH_TTL_S, "access": _h(access)}, REFRESH_TTL_S)
        return OAuthToken(access_token=access, token_type="Bearer", expires_in=ACCESS_TTL_S, refresh_token=refresh, scope=" ".join(scopes))

    async def exchange_authorization_code(self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode) -> OAuthToken:
        key = f"oauth:code:{_h(authorization_code.code)}"
        d = await self._get(key)
        if not d or not await self.r.delete(key):  # consume once
            raise TokenError("invalid_grant", "authorization code not found or already used")
        self._check_resource(authorization_code.resource)
        return await self._issue(client.client_id, authorization_code.scopes, authorization_code.resource, d["key_enc"])

    # ---- refresh / tokens ------------------------------------------------------------------------
    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str) -> RefreshToken | None:
        d = await self._get(f"oauth:refresh:{_h(refresh_token)}")
        if not d or d["client_id"] != client.client_id:
            return None
        return RefreshToken(token=refresh_token, client_id=d["client_id"], scopes=d["scopes"], expires_at=d["expires_at"], resource=d.get("resource"))

    async def exchange_refresh_token(self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]) -> OAuthToken:
        key = f"oauth:refresh:{_h(refresh_token.token)}"
        d = await self._get(key)
        if not d or not await self.r.delete(key):  # rotation: a refresh token works once
            raise TokenError("invalid_grant", "refresh token not found or already used")
        await self.r.delete(f"oauth:access:{d['access']}")
        if not set(scopes or d["scopes"]) <= set(d["scopes"]):
            raise TokenError("invalid_scope", "requested scopes exceed the original grant")
        raw = fernet(self.s).decrypt(d["key_enc"].encode()).decode()
        if not await self._key_is_active(raw):  # a suspended key cannot mint new tokens
            raise TokenError("invalid_grant", "the API key behind this grant is no longer active")
        return await self._issue(client.client_id, scopes or d["scopes"], d.get("resource"), d["key_enc"])

    async def load_access_token(self, token: str) -> AccessToken | None:  # type: ignore[override]
        d = await self._get(f"oauth:access:{_h(token)}")
        if not d or d["expires_at"] < time.time():
            return None
        return AccessToken(token=token, client_id=d["client_id"], scopes=d["scopes"], expires_at=d["expires_at"], resource=d.get("resource"))

    async def verify_token(self, token: str) -> AccessToken | None:  # type: ignore[override]
        if token.startswith(KEY_PREFIX):  # plain API keys keep working (Claude Code, API use); checked on use
            return AccessToken(token=token, client_id="api-key", scopes=SCOPES)
        return await self.load_access_token(token)

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:  # type: ignore[override]
        for kind in ("access", "refresh"):
            d = await self._get(f"oauth:{kind}:{_h(token.token)}")
            if d:
                await self.r.delete(f"oauth:{kind}:{_h(token.token)}")
                other = d.get("refresh") or d.get("access")
                await self.r.delete(f"oauth:refresh:{other}", f"oauth:access:{other}")

    async def raw_key_for(self, bearer: str) -> str | None:
        """Bearer (API key or our access token) -> the API key the request acts as."""
        if bearer.startswith(KEY_PREFIX):
            return bearer
        d = await self._get(f"oauth:access:{_h(bearer)}")
        if not d or d["expires_at"] < time.time():
            return None
        return fernet(self.s).decrypt(d["key_enc"].encode()).decode()

    def get_routes(self, mcp_path: str | None = None) -> list[Route]:
        return [*super().get_routes(mcp_path), Route("/oauth/consent", self._consent_page, methods=["GET", "POST"]),
                Route("/oauth/consent/sso", self._consent_sso, methods=["POST"]), Route("/oauth/sso/return", self._sso_return, methods=["GET"])]
