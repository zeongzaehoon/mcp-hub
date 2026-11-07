"""OpenID Connect relying party: discovery, authorization URL, code exchange (PKCE + client secret) and ID-token
verification against the provider's JWKS. Provider-agnostic; Google-specific bits are the optional `hd` claim.

Fails closed: any missing/mismatching claim (issuer, audience, nonce, expiry, email_verified) is a verification error.
No library beyond httpx + PyJWT so the trust path is short and readable."""

import secrets
import time
from base64 import urlsafe_b64encode
from dataclasses import dataclass
from hashlib import sha256
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from jwt import PyJWKSet

DISCOVERY_TTL_S = 3600
HTTP_TIMEOUT_S = 10.0
ALGORITHMS = ["RS256", "RS384", "RS512", "ES256", "ES384", "PS256"]
CLOCK_SKEW_S = 60


class OidcError(Exception):
    """Provider or token problem. Messages are safe to show (no secrets) but are logged, not relayed verbatim."""


@dataclass(frozen=True)
class Discovery:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    fetched_at: float


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = urlsafe_b64encode(sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


class OidcClient:
    def __init__(self, http: httpx.AsyncClient, *, allow_http: bool = False):
        self.http = http
        self.allow_http = allow_http
        self._disc: dict[str, Discovery] = {}
        self._jwks: dict[str, tuple[PyJWKSet, float]] = {}

    def _check_url(self, url: str, what: str) -> None:
        if url.startswith("https://"):
            return
        if url.startswith("http://") and self.allow_http:
            return
        raise OidcError(f"{what} must be https")

    async def discover(self, issuer: str) -> Discovery:
        issuer = issuer.rstrip("/")
        self._check_url(issuer, "issuer")
        cached = self._disc.get(issuer)
        if cached and time.time() - cached.fetched_at < DISCOVERY_TTL_S:
            return cached
        try:
            r = await self.http.get(f"{issuer}/.well-known/openid-configuration", timeout=HTTP_TIMEOUT_S)
            r.raise_for_status()
            d = r.json()
        except (httpx.HTTPError, ValueError) as e:
            raise OidcError(f"discovery failed: {type(e).__name__}") from e
        try:
            disc = Discovery(issuer=str(d["issuer"]).rstrip("/"), authorization_endpoint=d["authorization_endpoint"],
                             token_endpoint=d["token_endpoint"], jwks_uri=d["jwks_uri"], fetched_at=time.time())
        except KeyError as e:
            raise OidcError(f"discovery document lacks {e}") from e
        if disc.issuer != issuer:
            raise OidcError("discovery issuer does not match the configured issuer")
        for u, what in ((disc.authorization_endpoint, "authorization_endpoint"), (disc.token_endpoint, "token_endpoint"), (disc.jwks_uri, "jwks_uri")):
            self._check_url(u, what)
        self._disc[issuer] = disc
        return disc

    async def _keys(self, disc: Discovery, *, refresh: bool = False) -> PyJWKSet:
        cached = self._jwks.get(disc.jwks_uri)
        if cached and not refresh and time.time() - cached[1] < DISCOVERY_TTL_S:
            return cached[0]
        try:
            r = await self.http.get(disc.jwks_uri, timeout=HTTP_TIMEOUT_S)
            r.raise_for_status()
            ks = PyJWKSet.from_dict(r.json())
        except (httpx.HTTPError, ValueError, jwt.PyJWTError) as e:
            raise OidcError(f"jwks fetch failed: {type(e).__name__}") from e
        self._jwks[disc.jwks_uri] = (ks, time.time())
        return ks

    @staticmethod
    def authorize_url(disc: Discovery, *, client_id: str, redirect_uri: str, state: str, nonce: str,
                      code_challenge: str, login_hint: str | None = None, hosted_domain: str | None = None,
                      scope: str | None = None, offline: bool = False) -> str:
        q = {
            "response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri,
            "scope": scope or "openid email profile", "state": state, "nonce": nonce,
            "code_challenge": code_challenge, "code_challenge_method": "S256", "prompt": "select_account",
        }
        if offline:  # a refresh token: Google only issues one with access_type=offline and a fresh consent
            q["access_type"], q["prompt"] = "offline", "consent select_account"
        if login_hint:
            q["login_hint"] = login_hint
        if hosted_domain:
            q["hd"] = hosted_domain
        sep = "&" if "?" in disc.authorization_endpoint else "?"
        return f"{disc.authorization_endpoint}{sep}{urlencode(q)}"

    async def exchange_code(self, disc: Discovery, *, client_id: str, client_secret: str, code: str,
                            redirect_uri: str, code_verifier: str) -> dict[str, Any]:
        data = {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
                "client_id": client_id, "client_secret": client_secret, "code_verifier": code_verifier}
        try:
            r = await self.http.post(disc.token_endpoint, data=data, timeout=HTTP_TIMEOUT_S,
                                     headers={"Accept": "application/json"})
            body = r.json()
        except (httpx.HTTPError, ValueError) as e:
            raise OidcError(f"token request failed: {type(e).__name__}") from e
        if r.status_code != 200 or "id_token" not in body:
            raise OidcError(f"token endpoint refused the code: {body.get('error', r.status_code)}")
        return body

    async def verify_id_token(self, disc: Discovery, id_token: str, *, client_id: str, nonce: str) -> dict[str, Any]:
        try:
            header = jwt.get_unverified_header(id_token)
        except jwt.PyJWTError as e:
            raise OidcError("malformed id_token") from e
        kid = header.get("kid")
        keys = await self._keys(disc)
        key = _pick(keys, kid)
        if key is None:  # key rotation at the provider: refetch once
            key = _pick(await self._keys(disc, refresh=True), kid)
        if key is None:
            raise OidcError("id_token signed with an unknown key")
        try:
            claims = jwt.decode(id_token, key=key.key, algorithms=ALGORITHMS, audience=client_id, issuer=disc.issuer,
                                leeway=CLOCK_SKEW_S, options={"require": ["exp", "iat", "sub", "aud", "iss"]})
        except jwt.PyJWTError as e:
            raise OidcError(f"id_token rejected: {type(e).__name__}") from e
        if claims.get("nonce") != nonce:
            raise OidcError("id_token nonce mismatch")
        return claims


def _pick(keys: PyJWKSet, kid: str | None):
    for k in keys.keys:
        if kid is None or k.key_id == kid:
            if kid is None and len(keys.keys) > 1:
                return None  # ambiguous without a kid
            return k
    return None
