"""Google Admin SDK Directory (read-only): users, groups, members of a Workspace, as a service account with
domain-wide delegation impersonating a Workspace admin. Base URL and token URL come from settings so dev/e2e can point
both at a mock; the `token_uri` inside an uploaded key file is never used (an operator-supplied URL is not a place the
backend should POST to).

Reference: https://developers.google.com/admin-sdk/directory/reference/rest/v1 — users.list (`domain=`), groups.list
(`domain=`), members.list (`groups/{groupKey}/members`); all paginated with pageToken/nextPageToken.
Token: https://developers.google.com/identity/protocols/oauth2/service-account#authorizingrequests (JWT bearer grant
with `sub` = the delegated admin)."""

import json
import time
from typing import Any

import httpx
import jwt
from cryptography.hazmat.primitives import serialization

from app.core.config import Settings

SCOPE_USERS = "https://www.googleapis.com/auth/admin.directory.user.readonly"
SCOPE_GROUPS = "https://www.googleapis.com/auth/admin.directory.group.readonly"
SCOPE_MEMBERS = "https://www.googleapis.com/auth/admin.directory.group.member.readonly"
DIRECTORY_SCOPES = (SCOPE_USERS, SCOPE_GROUPS, SCOPE_MEMBERS)
JWT_BEARER = "urn:ietf:params:oauth:grant-type:jwt-bearer"
ASSERTION_TTL_S = 3600  # Google's maximum
HTTP_TIMEOUT_S = 20.0
MAX_PAGES = 200  # 500 users / 200 groups per page: a hard stop against a looping provider
MAX_KEY_BYTES = 20_000


class DirectoryError(Exception):
    def __init__(self, message: str, *, reconnect: bool = False):
        super().__init__(message)
        self.reconnect = reconnect  # the credentials are refused: an operator must fix the key / the delegation


class ServiceAccountKey:
    """The parts of a Google service-account JSON key file the sync needs. Validated on upload so an operator gets an
    immediate answer; the private key is held only Fernet-encrypted at rest."""

    def __init__(self, client_email: str, client_id: str, private_key: str, private_key_id: str):
        self.client_email = client_email
        self.client_id = client_id
        self.private_key = private_key
        self.private_key_id = private_key_id

    @classmethod
    def parse(cls, raw: str) -> "ServiceAccountKey":
        """Raises ValueError with a short, secret-free reason."""
        if not raw or len(raw) > MAX_KEY_BYTES:
            raise ValueError("key file is empty or too large")
        try:
            d = json.loads(raw)
        except ValueError as e:
            raise ValueError("key file is not JSON") from e
        if not isinstance(d, dict) or d.get("type") != "service_account":
            raise ValueError('key file is not a service account key ("type": "service_account")')
        email, key = str(d.get("client_email") or ""), str(d.get("private_key") or "")
        if "@" not in email or not key:
            raise ValueError("key file lacks client_email or private_key")
        try:
            serialization.load_pem_private_key(key.encode(), password=None)
        except (ValueError, TypeError) as e:
            raise ValueError("private_key is not a readable PEM key") from e
        return cls(email.lower(), str(d.get("client_id") or ""), key, str(d.get("private_key_id") or ""))

    def dumps(self) -> str:
        return json.dumps({"client_email": self.client_email, "client_id": self.client_id, "private_key": self.private_key,
                           "private_key_id": self.private_key_id})

    @classmethod
    def loads(cls, raw: str) -> "ServiceAccountKey":
        d = json.loads(raw)
        return cls(d["client_email"], d.get("client_id", ""), d["private_key"], d.get("private_key_id", ""))


class GoogleDirectory:
    def __init__(self, http: httpx.AsyncClient, settings: Settings):
        self.http = http
        self.s = settings

    @property
    def _base(self) -> str:
        return f"{self.s.google_directory_url.rstrip('/')}/admin/directory/v1"

    def assertion(self, key: ServiceAccountKey, subject: str, *, now: int | None = None) -> str:
        """Signed JWT for the bearer grant: the service account asks for the directory scopes as `subject`."""
        iat = now if now is not None else int(time.time())
        claims = {"iss": key.client_email, "sub": subject, "scope": " ".join(DIRECTORY_SCOPES),
                  "aud": self.s.google_token_url, "iat": iat, "exp": iat + ASSERTION_TTL_S}
        headers = {"kid": key.private_key_id} if key.private_key_id else None
        return jwt.encode(claims, key.private_key, algorithm="RS256", headers=headers)

    async def access_token(self, key: ServiceAccountKey, subject: str) -> str:
        try:
            r = await self.http.post(self.s.google_token_url, data={"grant_type": JWT_BEARER, "assertion": self.assertion(key, subject)},
                                     timeout=HTTP_TIMEOUT_S, headers={"Accept": "application/json"})
            body = r.json() if r.content else {}
        except (httpx.HTTPError, ValueError) as e:
            raise DirectoryError(f"token request failed: {type(e).__name__}") from e
        if r.status_code != 200 or not isinstance(body, dict) or not body.get("access_token"):
            err = str(body.get("error", r.status_code)) if isinstance(body, dict) else str(r.status_code)
            # invalid_grant / unauthorized_client: the delegation is not (or no longer) authorised for this client id and
            # scopes, the subject is not a valid admin, or the key was deleted — all need an operator
            raise DirectoryError(f"token refused: {err}", reconnect=err in ("invalid_grant", "unauthorized_client", "invalid_client"))
        return str(body["access_token"])

    async def _pages(self, token: str, path: str, params: dict[str, Any], key: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        page_token = None
        for _ in range(MAX_PAGES):
            q = {**params, **({"pageToken": page_token} if page_token else {})}
            try:
                r = await self.http.get(f"{self._base}/{path}", params=q, headers={"Authorization": f"Bearer {token}"},
                                        timeout=HTTP_TIMEOUT_S)
                body = r.json() if r.content else {}
            except (httpx.HTTPError, ValueError) as e:
                raise DirectoryError(f"directory request failed: {type(e).__name__}") from e
            if r.status_code in (401, 403):
                raise DirectoryError(f"directory refused {path}: {r.status_code}", reconnect=True)
            if r.status_code != 200:
                raise DirectoryError(f"directory error on {path}: {r.status_code}")
            out.extend(i for i in body.get(key) or [] if isinstance(i, dict))
            page_token = body.get("nextPageToken")
            if not page_token:
                return out
        raise DirectoryError(f"directory listing of {path} did not end")

    async def list_users(self, token: str, domain: str) -> list[dict[str, Any]]:
        return await self._pages(token, "users", {"domain": domain, "maxResults": 500, "projection": "basic"}, "users")

    async def list_groups(self, token: str, domain: str) -> list[dict[str, Any]]:
        return await self._pages(token, "groups", {"domain": domain, "maxResults": 200}, "groups")

    async def list_members(self, token: str, group_key: str) -> list[dict[str, Any]]:
        return await self._pages(token, f"groups/{group_key}/members", {"maxResults": 200, "includeDerivedMembership": "true"}, "members")
