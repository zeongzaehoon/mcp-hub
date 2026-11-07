"""In-process stand-in for an OIDC provider (two issuers: a company realm and the platform Google one), Google's
service-account token grant (JWT bearer, domain-wide delegation) and the Admin SDK Directory endpoints the backend
uses. Shared by test_sso.py, test_workspace.py and test_policies.py via the `idp` fixture in conftest."""

import json
import time
import uuid
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

ISSUER = "http://idp.test/realm"
GOOGLE = "http://idp.test/google"  # platform Google client (conftest: GOOGLE_* env)
CLIENT_ID = "console-client"
CLIENT_SECRET = "console-secret"
SECRETS = {CLIENT_ID: CLIENT_SECRET, "platform-google": "platform-google-secret"}
JWT_BEARER = "urn:ietf:params:oauth:grant-type:jwt-bearer"


def service_account_json(client_email="sync@proj-test.iam.gserviceaccount.com", client_id="1234567890") -> tuple[str, str]:
    """A throwaway service-account key file like the one an operator pastes: (json text, public PEM)."""
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = priv.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    pub = priv.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    d = {"type": "service_account", "project_id": "proj-test", "private_key_id": "kid1", "private_key": pem, "client_email": client_email,
         "client_id": client_id, "token_uri": "https://oauth2.googleapis.com/token"}
    return json.dumps(d), pub


class MockIdp:
    """Enough OIDC to drive the relying party: discovery, JWKS, token endpoint. `login()` plays the user: it reads the
    nonce the backend sent in the authorize redirect and returns a one-time code bound to the chosen claims."""

    def __init__(self):
        self.priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.jwk = {**json.loads(RSAAlgorithm.to_jwk(self.priv.public_key())), "kid": "k1", "use": "sig", "alg": "RS256"}
        self.codes: dict[str, dict] = {}
        self.token_calls: list[dict] = []
        self.delegations: dict[str, str] = {}  # service account client_email -> public PEM authorised (domain-wide delegation)
        self.workspace_admins: set[str] = set()  # delegated subjects the token grant accepts
        self.directory: dict[str, dict] = {}  # domain -> {"users": [...], "groups": [...], "members": {group_id: [...]}}
        self.transport = httpx.MockTransport(self.handle)

    def handle(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path
        realm = path.split("/")[1] if path.count("/") >= 2 else ""
        issuer = f"http://idp.test/{realm}"
        if path.endswith("/.well-known/openid-configuration") and realm in ("realm", "google"):
            return httpx.Response(200, json={"issuer": issuer, "authorization_endpoint": f"{issuer}/authorize",
                                             "token_endpoint": f"{issuer}/token", "jwks_uri": f"{issuer}/jwks"})
        if path.endswith("/jwks") and realm in ("realm", "google"):
            return httpx.Response(200, json={"keys": [self.jwk]})
        if path.endswith("/token") and realm in ("realm", "google"):
            form = parse_qs(req.content.decode())
            self.token_calls.append(form)
            if form.get("grant_type") == [JWT_BEARER]:  # service account with domain-wide delegation
                assertion = form.get("assertion", [""])[0]
                try:
                    iss = jwt.decode(assertion, options={"verify_signature": False})["iss"]
                    claims = jwt.decode(assertion, self.delegations[iss], algorithms=["RS256"], audience=f"{issuer}/token")
                except (KeyError, jwt.PyJWTError):
                    return httpx.Response(400, json={"error": "invalid_grant"})
                if claims.get("sub") not in self.workspace_admins or "admin.directory.user.readonly" not in str(claims.get("scope")):
                    return httpx.Response(401, json={"error": "unauthorized_client"})
                return httpx.Response(200, json={"access_token": f"at:{claims['sub']}", "token_type": "Bearer", "expires_in": 3600})
            rec = self.codes.pop(form.get("code", [""])[0], None)
            client_id = form.get("client_id", [""])[0]
            if rec is None or form.get("client_secret") != [SECRETS.get(client_id)] or _s256(form.get("code_verifier", [""])[0]) != rec["challenge"]:
                return httpx.Response(400, json={"error": "invalid_grant"})
            now = int(time.time())
            claims = {"iss": issuer, "aud": client_id, "iat": now, "exp": now + 300, "nonce": rec["nonce"], **rec["claims"]}
            tok = jwt.encode(claims, self.priv, algorithm="RS256", headers={"kid": "k1"})
            body = {"access_token": f"at:{rec['claims'].get('email', '')}", "token_type": "Bearer", "id_token": tok, "scope": rec["scope"]}
            return httpx.Response(200, json=body)
        if path.startswith("/directory/admin/directory/v1/"):  # Google Admin SDK stand-in
            auth = req.headers.get("Authorization", "")
            if not auth.startswith("Bearer at:"):
                return httpx.Response(401, json={"error": "bad token"})
            rest = path.removeprefix("/directory/admin/directory/v1/")
            q = dict(req.url.params)
            if rest == "users":
                return httpx.Response(200, json={"users": self.directory.get(q.get("domain", ""), {}).get("users", [])})
            if rest == "groups":
                return httpx.Response(200, json={"groups": self.directory.get(q.get("domain", ""), {}).get("groups", [])})
            if rest.startswith("groups/") and rest.endswith("/members"):
                gid = rest.split("/")[1]
                for d in self.directory.values():
                    if gid in d.get("members", {}):
                        return httpx.Response(200, json={"members": d["members"][gid]})
                return httpx.Response(404, json={"error": "no group"})
        return httpx.Response(404)

    def login(self, authorize_url: str, **claims) -> tuple[str, str]:
        """Simulates the user authenticating at the provider: returns (state, code) for the callback."""
        q = parse_qs(urlsplit(authorize_url).query)
        assert q["response_type"] == ["code"] and q["code_challenge_method"] == ["S256"] and q["client_id"][0] in SECRETS
        code = uuid.uuid4().hex
        self.codes[code] = {"nonce": q["nonce"][0], "claims": claims, "challenge": q["code_challenge"][0], "scope": q["scope"][0]}
        return q["state"][0], code


def _s256(verifier: str) -> str:
    import base64
    import hashlib

    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


