"""Company SSO: a per-company OIDC provider answers *who* the person is; *what* they may do stays on ServerAccess.

One purpose (`start` -> provider -> `callback`): an MCP connector (ChatGPT, Claude) signs in through the gateway's
consent page; the callback issues a session API key in the user's name (name `sso:<client>`, expiring) and hands it to
the gateway through a one-time code. From there everything is the ordinary key path (invariants 1-3 untouched); the key
is visible and revocable in the console like any other. The console itself has no SSO login (decision 2026-11-07):
operators sign in with email + password. A `platform: google` provider uses the deployment's Google OAuth client, so
a company whose Workspace is connected needs no OAuth app of its own.

Identity resolution (fail closed, in this order): linked (issuer, subject) -> same-company user with that verified
email (links it) -> auto-provision a member when the provider allows it -> refuse.

State lives in Redis db2: `sso:txn:<state>` (10 min, single use: nonce, PKCE verifier, purpose) and
`sso:handoff:<sha256 code>` (60 s, single use: the session key, Fernet-encrypted)."""

import hashlib
import logging
import secrets
import time
from datetime import timedelta
from typing import Any, Literal
from urllib.parse import urlencode

import orjson
import redis.asyncio as aioredis

from app.core.config import Settings
from app.core.errors import Conflict, Forbidden, InvariantViolation, NotFound, Unauthorized
from app.core.oidc import OidcClient, OidcError, pkce_pair
from app.core.security import decrypt_secret, encrypt_secret
from app.domain.enums import Role, Status
from app.domain.models import Identity, IdentityProvider, User, now
from app.infra.dynamo.keys import IDP_SK, company_pk, idp_domain_uniq_pk
from app.services.apikeys import ApiKeyService
from app.services.base import ServiceBase
from app.services.companies import CompanyService, assert_transition
from app.services.users import UserService

log = logging.getLogger(__name__)
Purpose = Literal["gateway"]
TXN_TTL_S = 600
HANDOFF_TTL_S = 60


class SsoDenied(Exception):
    """Login outcome the browser is told about with a short code (never provider details)."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class SsoService(ServiceBase):
    def __init__(self, repo, sync, hot: aioredis.Redis, oidc: OidcClient, settings: Settings):
        super().__init__(repo, sync)
        self.hot = hot
        self.oidc = oidc
        self.s = settings
        self.users = UserService(repo, sync)

    @property
    def redirect_uri(self) -> str:
        """What the company registers at its provider; same origin as the console (nginx / ALB front both)."""
        return f"{self.s.console_url.rstrip('/')}/api/v1/auth/sso/callback"

    def _creds(self, idp: IdentityProvider) -> tuple[str, str]:
        """(client_id, client_secret): the company's own, or the platform Google client."""
        if idp.platform == "google":
            if not self.s.google_enabled:
                raise InvariantViolation("platform Google client is not configured", code="sso_google_disabled")
            return self.s.google_client_id, self.s.google_client_secret
        return idp.client_id or "", decrypt_secret(idp.client_secret_enc or "")

    def _platform_google(self, company_id: str, domain: str, *, auto_provision: bool = True) -> IdentityProvider:
        return IdentityProvider(company_id=company_id, platform="google", issuer=self.s.google_issuer, domains=[domain],
                                hosted_domain=domain, auto_provision=auto_provision)

    async def ensure_platform_provider(self, company_id: str, domain: str) -> IdentityProvider | None:
        """After a Workspace is connected: give the company the platform-Google provider when it has none, so its
        employees' connector logins work without more setup. A company with its own provider keeps it; a domain owned
        by another company's provider is left for an operator (logged, not raised)."""
        if not self.s.google_enabled:
            return None
        try:
            return await self.get_provider(company_id)
        except NotFound:
            pass
        try:
            return await self.put_provider(company_id, issuer=None, client_id=None, client_secret=None, domains=[domain],
                                           hosted_domain=domain, auto_provision=True, status=None, platform="google")
        except Conflict:
            log.warning("workspace %s: domain %s already belongs to another company's provider; no provider created", company_id, domain)
            return None

    # ---- provider management ----------------------------------------------------------------------
    async def get_provider(self, company_id: str) -> IdentityProvider:
        e = await self.repo.get(company_pk(company_id), IDP_SK)
        if not isinstance(e, IdentityProvider) or e.status == Status.DELETED:
            raise NotFound(f"company {company_id} has no identity provider")
        return e

    async def put_provider(self, company_id: str, *, issuer: str | None, client_id: str | None, client_secret: str | None,
                           domains: list[str], hosted_domain: str | None, auto_provision: bool,
                           status: Status | None, platform: str | None = None) -> IdentityProvider:
        await CompanyService(self.repo, self.sync).get(company_id)  # 404 before anything else
        cur = await self.repo.get(company_pk(company_id), IDP_SK)
        cur = cur if isinstance(cur, IdentityProvider) and cur.status != Status.DELETED else None
        if platform == "google" and not self.s.google_enabled:
            raise InvariantViolation("platform Google client is not configured on this deployment", code="sso_google_disabled")
        if platform is None:
            if client_secret is None and (cur is None or cur.platform is not None):
                raise InvariantViolation("client_secret is required when creating a provider")
            if not issuer or not client_id:
                raise InvariantViolation("issuer and client_id are required")
        if status is not None and cur is not None and status != cur.status:
            assert_transition(cur.status, status)
        try:
            if platform == "google":
                idp = IdentityProvider(company_id=company_id, platform="google", issuer=self.s.google_issuer, domains=domains,
                                       hosted_domain=hosted_domain, auto_provision=auto_provision,
                                       status=status or (cur.status if cur else Status.ACTIVE))
            else:
                idp = IdentityProvider(
                    company_id=company_id, issuer=issuer, client_id=client_id,
                    client_secret_enc=encrypt_secret(client_secret) if client_secret is not None else cur.client_secret_enc,  # type: ignore[union-attr]
                    domains=domains, hosted_domain=hosted_domain, auto_provision=auto_provision,
                    status=status or (cur.status if cur else Status.ACTIVE),
                )
        except ValueError as e:
            raise InvariantViolation(str(e)) from e
        if not self.s.sso_allow_http_issuer and not idp.issuer.startswith("https://"):
            raise InvariantViolation("issuer must be https")
        old_domains = set(cur.domains) if cur else set()
        ops = [self.repo.tx.check_exists(company_pk(company_id), "META", status=Status.ACTIVE)]
        for d in old_domains - set(idp.domains):
            ops.append(self.repo.tx.delete_raw(idp_domain_uniq_pk(d), "UNIQ"))
        for d in set(idp.domains) - old_domains:
            ops.append(self.repo.tx.put_raw({"PK": idp_domain_uniq_pk(d), "SK": "UNIQ", "company_id": company_id}, must_not_exist=True))
        if cur is not None:
            idp.created_at, idp.version = cur.created_at, cur.version
            ops.append(self.repo.tx.replace(idp, self.bump(idp)))
        else:
            stale = await self.repo.get(company_pk(company_id), IDP_SK)  # a tombstone keeps its row: replace it
            if isinstance(stale, IdentityProvider):
                idp.version = stale.version
                ops.append(self.repo.tx.replace(idp, self.bump(idp)))
            else:
                ops.append(self.repo.tx.create(idp))
        try:
            await self.commit([idp], ops)
        except Conflict as e:
            raise Conflict("company not active, stale version, or an email domain already belongs to another company's provider",
                           code="idp_put_conflict") from e
        return idp

    async def delete_provider(self, company_id: str) -> None:
        idp = await self.get_provider(company_id)
        idp.status = Status.DELETED
        ops = [self.repo.tx.replace(idp, self.bump(idp))]
        ops += [self.repo.tx.delete_raw(idp_domain_uniq_pk(d), "UNIQ") for d in idp.domains]
        await self.commit([idp], ops)

    async def test_provider(self, company_id: str) -> dict[str, Any]:
        """Reach the provider (discovery + JWKS) without a login; proves the issuer URL and network path."""
        idp = await self.get_provider(company_id)
        try:
            disc = await self.oidc.discover(idp.issuer)
            keys = await self.oidc._keys(disc, refresh=True)
        except OidcError as e:
            raise InvariantViolation(f"provider unreachable or invalid: {e}", code="idp_test_failed") from e
        return {"issuer": disc.issuer, "authorization_endpoint": disc.authorization_endpoint, "jwks_keys": len(keys.keys),
                "redirect_uri": self.redirect_uri}

    async def provider_for_domain(self, domain: str) -> IdentityProvider | None:
        """Active provider owning an email domain (company active too); None when the domain is free."""
        ref = await self.repo.get_raw(idp_domain_uniq_pk(domain), "UNIQ")
        if not ref:
            return None
        idp = await self.repo.get(company_pk(ref["company_id"]), IDP_SK)
        if not isinstance(idp, IdentityProvider) or idp.status != Status.ACTIVE or domain not in idp.domains:
            return None
        company = await self.repo.get(company_pk(idp.company_id), "META")
        if company is None or company.status != Status.ACTIVE:
            return None
        return idp

    async def provider_for_email(self, email: str) -> IdentityProvider | None:
        domain = email.rsplit("@", 1)[-1].lower() if "@" in email else ""
        return await self.provider_for_domain(domain) if domain else None

    # ---- login flow -------------------------------------------------------------------------------
    async def start(self, email: str, *, req: str | None = None, client: str | None = None) -> str:
        idp = await self.provider_for_email(email)
        if idp is None:
            raise NotFound("no SSO provider for this email domain", code="sso_not_configured")
        if not self.s.sso_handoff_secret:
            raise InvariantViolation("MCP-connector SSO is not enabled on this deployment", code="sso_gateway_disabled")
        try:
            disc = await self.oidc.discover(idp.issuer)
        except OidcError as e:
            log.warning("sso start: provider %s for %s: %s", idp.issuer, idp.company_id, e)
            raise Conflict("identity provider unreachable", code="sso_provider_unreachable") from e
        client_id, _ = self._creds(idp)
        state, nonce, challenge = await self._new_txn(idp.company_id, req=req, client=client)
        return self.oidc.authorize_url(disc, client_id=client_id, redirect_uri=self.redirect_uri, state=state, nonce=nonce,
                                       code_challenge=challenge, login_hint=email.lower(), hosted_domain=idp.hosted_domain)

    async def _new_txn(self, company_id: str, *, req: str | None = None, client: str | None = None) -> tuple[str, str, str]:
        """Park nonce + PKCE verifier under a fresh state (10 min, single use); returns (state, nonce, challenge)."""
        state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
        verifier, challenge = pkce_pair()
        txn = {"company_id": company_id, "nonce": nonce, "verifier": verifier, "purpose": "gateway",
               "req": req, "client": client, "created_at": int(time.time())}
        await self.hot.set(f"sso:txn:{state}", orjson.dumps(txn).decode(), ex=TXN_TTL_S)
        return state, nonce, challenge

    async def callback(self, state: str | None, code: str | None, error: str | None) -> str:
        """Returns the URL to send the browser to: the gateway's return page with a handoff code or an error code.
        Without a readable transaction the gateway cannot match a pending request either; it shows the error."""
        txn = await self._take_txn(state)
        if txn is None:
            return self.error_url(None, "expired")
        if error or not code:
            return self.error_url(txn.get("req"), "provider_denied")
        try:
            return await self._gateway_success(txn, await self._authenticate(txn, code))
        except SsoDenied as e:
            return self.error_url(txn.get("req"), e.code)
        except OidcError as e:
            log.warning("sso callback for %s: %s", txn["company_id"], e)
            return self.error_url(txn.get("req"), "provider_error")

    async def _take_txn(self, state: str | None) -> dict[str, Any] | None:
        if not state or len(state) > 128:
            return None
        raw = await self.hot.getdel(f"sso:txn:{state}")  # single use
        return orjson.loads(raw) if raw else None

    async def _authenticate(self, txn: dict[str, Any], code: str) -> User:
        try:
            idp = await self.get_provider(txn["company_id"])
        except NotFound as e:
            raise SsoDenied("sso_not_configured") from e
        if idp.status != Status.ACTIVE:
            raise SsoDenied("sso_not_configured")
        disc = await self.oidc.discover(idp.issuer)
        client_id, client_secret = self._creds(idp)
        tokens = await self.oidc.exchange_code(disc, client_id=client_id, client_secret=client_secret,
                                               code=code, redirect_uri=self.redirect_uri, code_verifier=txn["verifier"])
        claims = await self.oidc.verify_id_token(disc, tokens["id_token"], client_id=client_id, nonce=txn["nonce"])
        email = str(claims.get("email") or "").strip().lower()
        if not email or "@" not in email or claims.get("email_verified") is not True:
            raise SsoDenied("email_not_verified")
        if email.rsplit("@", 1)[1] not in idp.domains:
            raise SsoDenied("domain_not_allowed")
        if idp.hosted_domain and claims.get("hd") != idp.hosted_domain:
            raise SsoDenied("domain_not_allowed")
        return await self._resolve_user(idp, Identity(issuer=disc.issuer, subject=str(claims["sub"])), email,
                                        str(claims.get("name") or email.split("@", 1)[0]))

    async def _resolve_user(self, idp: IdentityProvider, ident: Identity, email: str, name: str) -> User:
        user = await self.users.get_by_identity(ident)
        if user is not None:
            if user.company_id != idp.company_id:
                raise SsoDenied("not_registered")  # the subject is linked elsewhere: never cross companies
        else:
            user = await self.users.get_by_email(email)
            if user is not None:
                if user.company_id != idp.company_id or user.status != Status.ACTIVE:
                    raise SsoDenied("not_registered")
                try:
                    user = await self.users.link_identity(user, ident)
                except Conflict as e:
                    raise SsoDenied("not_registered") from e
            elif idp.auto_provision:
                try:
                    user = await self.users.create(company_id=idp.company_id, email=email, name=name[:120] or email,
                                                   role=Role.MEMBER, password=None, identities=[ident])
                except Conflict as e:
                    raise SsoDenied("not_registered") from e
            else:
                raise SsoDenied("not_registered")
        if user.status != Status.ACTIVE:
            raise SsoDenied("user_suspended")
        return user

    async def _gateway_success(self, txn: dict[str, Any], user: User) -> str:
        client = (txn.get("client") or "mcp").strip()[:100] or "mcp"
        try:
            key, raw = await ApiKeyService(self.repo, self.sync).create(
                company_id=user.company_id, user_id=user.user_id, server_ids=None, name=f"sso:{client}",
                expires_at=now() + timedelta(days=self.s.sso_session_key_days),
            )
        except (Conflict, InvariantViolation) as e:
            raise SsoDenied("user_suspended") from e
        code = secrets.token_urlsafe(32)
        rec = {"key_enc": encrypt_secret(raw), "key_id": key.key_id, "company_id": user.company_id, "user_id": user.user_id, "email": user.email}
        await self.hot.set(f"sso:handoff:{_h(code)}", orjson.dumps(rec).decode(), ex=HANDOFF_TTL_S)
        return f"{self.s.gateway_public_url.rstrip('/')}/oauth/sso/return?{urlencode({'req': txn.get('req') or '', 'code': code})}"

    def error_url(self, req: str | None, code: str) -> str:
        return f"{self.s.gateway_public_url.rstrip('/')}/oauth/sso/return?{urlencode({'req': req or '', 'error': code})}"

    async def handoff(self, code: str, presented_secret: str | None) -> dict[str, Any]:
        """Gateway -> backend: swap the one-time code for the session key. Shared-secret authenticated."""
        if not self.s.sso_handoff_secret:
            raise Forbidden("MCP-connector SSO is not enabled", code="sso_gateway_disabled")
        if not presented_secret or not secrets.compare_digest(presented_secret, self.s.sso_handoff_secret):
            raise Unauthorized("bad handoff secret")
        if not code or len(code) > 128:
            raise Unauthorized("invalid handoff code")
        raw = await self.hot.getdel(f"sso:handoff:{_h(code)}")
        if not raw:
            raise Unauthorized("handoff code expired or already used")
        rec = orjson.loads(raw)
        return {**{k: rec[k] for k in ("key_id", "company_id", "user_id", "email")}, "raw_key": decrypt_secret(rec["key_enc"])}


def _h(v: str) -> str:
    return hashlib.sha256(v.encode()).hexdigest()
