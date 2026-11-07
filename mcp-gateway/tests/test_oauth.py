"""OAuth 2.1 with API-key consent, end to end against the compose gateway (GATEWAY_PUBLIC_URL=http://localhost:19000).
Walks the flow a connector (ChatGPT / Claude.ai) performs: discovery -> dynamic registration -> PKCE authorize ->
consent with an issued key -> token -> MCP calls; then every refusal path."""

import base64
import hashlib
import json
import os
import secrets
import uuid
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from mcp.shared.exceptions import MCPError

from mcp_gateway.server import assign_aliases

BACKEND = os.environ.get("E2E_BACKEND", "http://localhost:19000/api/v1")
# must equal the gateway's GATEWAY_PUBLIC_URL (the OAuth resource check compares them)
GW = os.environ.get("E2E_GATEWAY", "http://localhost:19000/mcp").removesuffix("/mcp")
RESOURCE = f"{GW}/mcp"
REDIRECT = "http://localhost:9999/callback"
# company SSO: the mock OIDC provider (compose service mock-idp) as the backend reaches it, and the same server from this host
IDP_ISSUER = os.environ.get("E2E_IDP_ISSUER", "http://mock-idp:8080/default")
IDP_PUBLIC_ISSUER = os.environ.get("E2E_IDP_PUBLIC_ISSUER", "http://localhost:28090/default")
pytestmark = pytest.mark.e2e

try:
    _up = httpx.get(f"{GW}/.well-known/oauth-authorization-server", timeout=2).status_code == 200
except Exception:  # noqa: BLE001
    _up = False
if not _up:
    pytest.skip("gateway with OAuth not running", allow_module_level=True)


@pytest.fixture(scope="module")
def world():
    with httpx.Client(base_url=BACKEND, timeout=20) as c:
        c.headers["Authorization"] = "Bearer " + c.post("/auth/login", json={"email": "master@example.com", "password": "master-dev-password-123"}).json()["access_token"]
        tag = uuid.uuid4().hex[:6]
        srv = c.post("/servers", json={"server_type": "postgres", "name": f"OAuth PG {tag}"}).json()
        cid = f"oauth-{tag}"
        c.post("/companies", json={"company_id": cid, "name": "OAuth"})
        c.post(f"/companies/{cid}/bindings", json={"server_id": srv["server_id"], "tenant": {"mode": "schema", "schema_name": "acme"}})
        u = c.post(f"/companies/{cid}/users", json={"email": f"o-{cid}@ex.com", "name": "O", "role": "member"}).json()
        c.post(f"/companies/{cid}/users/{u['user_id']}/access", json={"server_id": srv["server_id"], "permissions": ["read"], "allowed_tools": ["run_select"]})
        k = c.post(f"/companies/{cid}/api-keys", json={"user_id": u["user_id"], "server_ids": None, "name": "oauth"}).json()
        hdr = dict(c.headers)
    alias = assign_aliases([(srv["server_id"], "postgres", srv["name"])])[srv["server_id"]]
    return {"raw": k["raw_key"], "key_id": k["key"]["key_id"], "cid": cid, "sid": srv["server_id"], "alias": alias, "hdr": hdr}


@pytest.fixture(scope="module")
def sso_world(world):
    """A second company whose OIDC provider is the mock IdP, on a domain of its own, bound to the same postgres server."""
    try:
        idp_up = httpx.get(f"{IDP_PUBLIC_ISSUER}/.well-known/openid-configuration", timeout=2).status_code == 200
    except Exception:  # noqa: BLE001
        idp_up = False
    if not idp_up:
        pytest.skip("mock-idp not reachable from the host")
    tag = uuid.uuid4().hex[:6]
    cid, domain = f"sso-{tag}", f"sso-{tag}.example"
    with httpx.Client(base_url=BACKEND, headers=world["hdr"], timeout=20) as c:
        assert c.post("/companies", json={"company_id": cid, "name": "SSO Co"}).status_code == 201
        assert c.post(f"/companies/{cid}/bindings", json={"server_id": world["sid"], "tenant": {"mode": "schema", "schema_name": "acme"}}).status_code == 201
        r = c.put(f"/companies/{cid}/sso", json={"issuer": IDP_ISSUER, "client_id": "e2e-connector", "client_secret": "e2e-connector-secret",
                                                 "domains": [domain], "auto_provision": True})
        assert r.status_code == 200, r.text
    return {"cid": cid, "domain": domain, "tag": tag}


def pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def register(c: httpx.Client, name: str = "<b>Test</b> Connector") -> str:
    r = c.post(f"{GW}/register", json={"redirect_uris": [REDIRECT], "client_name": name, "token_endpoint_auth_method": "none",
                                       "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"]})
    assert r.status_code == 201, r.text
    return r.json()["client_id"]


def start(c: httpx.Client, client_id: str, challenge: str, resource: str = RESOURCE) -> str:
    r = c.get(f"{GW}/authorize", params={"response_type": "code", "client_id": client_id, "redirect_uri": REDIRECT, "code_challenge": challenge,
                                         "code_challenge_method": "S256", "state": "st-1", "resource": resource, "scope": "mcp"})
    assert r.status_code in (302, 307), r.text
    loc = r.headers["location"]
    assert loc.startswith(f"{GW}/oauth/consent?req=")
    return parse_qs(urlsplit(loc).query)["req"][0]


def consent(c: httpx.Client, req: str, key: str) -> httpx.Response:
    return c.post(f"{GW}/oauth/consent", data={"req": req, "api_key": key})


def token(c: httpx.Client, **data) -> httpx.Response:
    return c.post(f"{GW}/token", data={"redirect_uri": REDIRECT, "resource": RESOURCE, **data})


def refused(r: httpx.Response, error: str = "invalid_grant") -> bool:
    # the MCP SDK's token handler answers invalid_grant with 401 (RFC 6749 suggests 400); clients act on `error`
    return r.status_code in (400, 401) and r.json().get("error") == error


def full_flow(c, raw):
    cid = register(c)
    verifier, challenge = pkce()
    req = start(c, cid, challenge)
    r = consent(c, req, raw)
    assert r.status_code == 302
    q = parse_qs(urlsplit(r.headers["location"]).query)
    assert q["state"] == ["st-1"]
    t = token(c, grant_type="authorization_code", code=q["code"][0], code_verifier=verifier, client_id=cid)
    assert t.status_code == 200, t.text
    return cid, t.json()


async def tools(bearer):
    async with Client(StreamableHttpTransport(RESOURCE, headers={"Authorization": f"Bearer {bearer}"})) as cl:
        return [t.name for t in await cl.list_tools()]


async def test_connector_flow_and_mcp_calls_with_the_token(world):
    with httpx.Client(timeout=10, follow_redirects=False) as c:
        cid, tok = full_flow(c, world["raw"])
    assert tok["token_type"].lower() == "bearer" and tok["access_token"] and tok["refresh_token"]
    assert not tok["access_token"].startswith("mcpk_")  # the key itself is never handed to the client
    assert f"{world['alias']}__run_select" in await tools(tok["access_token"])
    async with Client(StreamableHttpTransport(RESOURCE, headers={"Authorization": f"Bearer {tok['access_token']}"})) as cl:
        r = (await cl.call_tool(f"{world['alias']}__run_select", {"sql": "select name from customers order by id"})).structured_content
    assert [x["name"] for x in r["rows"]] == ["Acme Alice", "Acme Bob"]  # runs with the key's tenant and permissions


async def test_consent_page_escapes_client_metadata_and_rejects_bad_keys(world):
    with httpx.Client(timeout=10, follow_redirects=False) as c:
        cid = register(c)
        _, challenge = pkce()
        req = start(c, cid, challenge)
        page = c.get(f"{GW}/oauth/consent", params={"req": req})
        assert page.status_code == 200 and "&lt;b&gt;Test&lt;/b&gt;" in page.text and "<b>Test</b>" not in page.text
        assert "frame-ancestors 'none'" in page.headers["content-security-policy"] and page.headers["cache-control"] == "no-store"
        assert consent(c, req, "mcpk_not_a_real_key").status_code == 400
        assert consent(c, req, "not-even-a-key").status_code == 400
        for _ in range(3):
            consent(c, req, "mcpk_wrong")
        assert consent(c, req, world["raw"]).status_code == 400  # 5 attempts used up: request is gone
        assert c.get(f"{GW}/oauth/consent", params={"req": req}).status_code == 400


async def test_code_and_verifier_rules(world):
    with httpx.Client(timeout=10, follow_redirects=False) as c:
        cid = register(c)
        verifier, challenge = pkce()
        req = start(c, cid, challenge)
        code = parse_qs(urlsplit(consent(c, req, world["raw"]).headers["location"]).query)["code"][0]
        assert refused(token(c, grant_type="authorization_code", code=code, code_verifier="x" * 50, client_id=cid))  # PKCE
        ok = token(c, grant_type="authorization_code", code=code, code_verifier=verifier, client_id=cid)
        assert ok.status_code in (200, 400)  # a failed verifier may already have consumed the code
        # a fresh code can be used exactly once
        verifier, challenge = pkce()
        req = start(c, cid, challenge)
        code = parse_qs(urlsplit(consent(c, req, world["raw"]).headers["location"]).query)["code"][0]
        assert token(c, grant_type="authorization_code", code=code, code_verifier=verifier, client_id=cid).status_code == 200
        assert refused(token(c, grant_type="authorization_code", code=code, code_verifier=verifier, client_id=cid))  # reused
        # tokens are for this gateway only (RFC 8707)
        r = c.get(f"{GW}/authorize", params={"response_type": "code", "client_id": cid, "redirect_uri": REDIRECT, "code_challenge": challenge,
                                             "code_challenge_method": "S256", "state": "s", "resource": "https://evil.example/mcp"})
        assert r.status_code != 302 or "error" in r.headers.get("location", "")


async def test_refresh_rotates_and_key_suspension_kills_tokens(world):
    with httpx.Client(timeout=10, follow_redirects=False) as c:
        cid, tok = full_flow(c, world["raw"])
        new = token(c, grant_type="refresh_token", refresh_token=tok["refresh_token"], client_id=cid)
        assert new.status_code == 200, new.text
        assert refused(token(c, grant_type="refresh_token", refresh_token=tok["refresh_token"], client_id=cid))  # rotated
        new = new.json()
        assert await tools(new["access_token"])
        with pytest.raises(Exception):  # the access token replaced by the refresh no longer works
            await tools(tok["access_token"])
        with httpx.Client(base_url=BACKEND, headers=world["hdr"], timeout=10) as api:
            assert api.patch(f"/companies/{world['cid']}/api-keys/{world['key_id']}", json={"status": "suspended"}).status_code == 200
            try:
                with pytest.raises(MCPError, match="key suspended"):
                    await tools(new["access_token"])
                assert refused(token(c, grant_type="refresh_token", refresh_token=new["refresh_token"], client_id=cid))
            finally:
                assert api.patch(f"/companies/{world['cid']}/api-keys/{world['key_id']}", json={"status": "active"}).status_code == 200


async def test_plain_api_keys_still_work_and_unauthenticated_gets_a_challenge(world):
    assert f"{world['alias']}__run_select" in await tools(world["raw"])
    r = httpx.post(RESOURCE, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, timeout=5)
    assert r.status_code == 401 and "resource_metadata=" in r.headers.get("www-authenticate", "")
    assert httpx.post(RESOURCE, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers={"Authorization": "Bearer random-junk"}, timeout=5).status_code == 401



def test_registration_refuses_unsafe_redirects():
    """Open registration must not let a phishing client receive codes over plain http or javascript: URIs."""
    with httpx.Client(timeout=10) as c:
        for uri in ("http://evil.example/cb", "javascript:alert(1)", "ftp://x/cb"):
            r = c.post(f"{GW}/register", json={"redirect_uris": [uri], "client_name": "evil", "token_endpoint_auth_method": "none"})
            assert r.status_code == 400, (uri, r.text)
        ok = c.post(f"{GW}/register", json={"redirect_uris": ["https://chatgpt.com/connector_platform_oauth_redirect"], "client_name": "cg", "token_endpoint_auth_method": "none"})
        assert ok.status_code == 201, ok.text


# ---- company SSO on the consent page -------------------------------------------------------------------------------

def sso_login(c: httpx.Client, req: str, email: str, subject: str, **claims) -> tuple[httpx.Response, str]:
    """Plays the browser through every hop of "회사 계정으로 로그인": consent -> backend start -> IdP login form ->
    backend callback -> gateway return. Returns the gateway's /oauth/sso/return response (302 to the client with a
    code on success, 400 with the consent form re-rendered otherwise) and the return URL it was given."""
    r = c.post(f"{GW}/oauth/consent/sso", data={"req": req, "email": email})
    assert r.status_code == 302, r.text
    loc = r.headers["location"]
    assert "/api/v1/auth/sso/start?" in loc and f"req={req}" in loc, loc
    r = c.get(loc)  # backend: provider for the domain, PKCE + state, off to the IdP
    assert r.status_code == 302, r.text
    loc = r.headers["location"]
    if loc.startswith(IDP_ISSUER):  # the mock IdP shows a login form: any subject + the claims the id_token will carry
        q = parse_qs(urlsplit(loc).query)
        assert q["code_challenge_method"] == ["S256"] and q["login_hint"] == [email.lower()]
        r = c.post(loc.replace(IDP_ISSUER, IDP_PUBLIC_ISSUER, 1),
                   data={"username": subject, "claims": json.dumps({"email": email, "email_verified": True, **claims})})
        assert r.status_code == 302, r.text
        loc = r.headers["location"]
        assert "/api/v1/auth/sso/callback?" in loc, loc
        r = c.get(loc)  # backend: code -> id_token -> user -> session key -> one-time handoff code
        assert r.status_code == 302, r.text
        loc = r.headers["location"]
    assert loc.startswith(f"{GW}/oauth/sso/return?"), loc
    return c.get(loc), loc


async def test_company_sso_consent_issues_a_session_key(world, sso_world):
    cid, domain, tag = sso_world["cid"], sso_world["domain"], sso_world["tag"]
    email, subject = f"ana-{tag}@{domain}", f"ana-{tag}"
    with httpx.Client(timeout=10, follow_redirects=False) as c:
        client_id = register(c, name="SSO Connector")
        verifier, challenge = pkce()
        req = start(c, client_id, challenge)
        page = c.get(f"{GW}/oauth/consent", params={"req": req})
        assert page.status_code == 200 and "회사 계정으로 로그인" in page.text and "action='/oauth/consent/sso'" in page.text
        r, return_url = sso_login(c, req, email, subject, name="Ana")
        assert r.status_code == 302, r.text
        assert r.headers["location"].startswith(REDIRECT)
        q = parse_qs(urlsplit(r.headers["location"]).query)
        assert q["state"] == ["st-1"] and q["code"]
        # the pending request was consumed with the grant: the return URL cannot be replayed
        assert c.get(return_url).status_code == 400
        t = token(c, grant_type="authorization_code", code=q["code"][0], code_verifier=verifier, client_id=client_id)
        assert t.status_code == 200, t.text
        tok = t.json()
        assert tok["refresh_token"] and not tok["access_token"].startswith("mcpk_")

    # the person was auto-provisioned as a member and got a session key in their name: all servers, expiring, named after the client
    with httpx.Client(base_url=BACKEND, headers=world["hdr"], timeout=10) as api:
        users = [u for u in api.get(f"/companies/{cid}/users").json()["items"] if u["email"] == email]
        assert len(users) == 1, users
        user = users[0]
        assert user["role"] == "member" and user["can_login"] is False and user["name"] == "Ana"
        assert user["identities"] == [{"issuer": IDP_ISSUER, "subject": subject}]
        keys = [k for k in api.get(f"/companies/{cid}/api-keys").json()["items"] if k["user_id"] == user["user_id"]]
        assert len(keys) == 1, keys
        key = keys[0]
        assert key["name"] == "sso:SSO Connector" and key["server_ids"] is None and key["expires_at"] and key["status"] == "active"
        # no server access yet: the connection works but offers nothing (authorization stays on ServerAccess)
        assert await tools(tok["access_token"]) == []
        r = api.post(f"/companies/{cid}/users/{user['user_id']}/access",
                     json={"server_id": world["sid"], "permissions": ["read"], "allowed_tools": ["run_select"]})
        assert r.status_code == 201, r.text
        assert f"{world['alias']}__run_select" in await tools(tok["access_token"])
        async with Client(StreamableHttpTransport(RESOURCE, headers={"Authorization": f"Bearer {tok['access_token']}"})) as cl:
            rows = (await cl.call_tool(f"{world['alias']}__run_select", {"sql": "select name from customers order by id"})).structured_content["rows"]
        assert [x["name"] for x in rows] == ["Acme Alice", "Acme Bob"]  # the SSO company's binding (schema acme)
        # revoking the session key in the console kills the connection like any key
        assert api.patch(f"/companies/{cid}/api-keys/{key['key_id']}", json={"status": "suspended"}).status_code == 200
        with pytest.raises(MCPError, match="key suspended"):
            await tools(tok["access_token"])
        with httpx.Client(timeout=10, follow_redirects=False) as c:
            assert refused(token(c, grant_type="refresh_token", refresh_token=tok["refresh_token"], client_id=client_id))


async def test_company_sso_refusals_keep_the_consent_request(world, sso_world):
    cid, domain, tag = sso_world["cid"], sso_world["domain"], sso_world["tag"]
    with httpx.Client(timeout=10, follow_redirects=False) as c:
        client_id = register(c, name="SSO Connector")
        _, challenge = pkce()
        req = start(c, client_id, challenge)
        # a domain without a provider: the backend sends the person straight back; the form is re-rendered with the reason
        r, _ = sso_login(c, req, f"nobody@no-sso-{tag}.example", subject=f"nobody-{tag}")
        assert r.status_code == 400 and "회사 로그인이 설정되어 있지 않습니다" in r.text, r.text
        assert "action='/oauth/consent'" in r.text and f"value='{req}'" in r.text
        # the IdP vouches for an unverified email: refused on the way back, same form
        r, _ = sso_login(c, req, f"unverified-{tag}@{domain}", subject=f"unverified-{tag}", email_verified=False)
        assert r.status_code == 400 and "이메일이 확인되지 않은 계정입니다" in r.text, r.text
        # a handoff code is swapped once: a copy presented on a fresh request (or the same one) buys nothing
        ok, return_url = sso_login(c, req, f"bob-{tag}@{domain}", subject=f"bob-{tag}")
        assert ok.status_code == 302 and "code=" in ok.headers["location"]
        handoff_code = parse_qs(urlsplit(return_url).query)["code"][0]
        assert c.get(return_url).status_code == 400  # request consumed by the grant
        req2 = start(c, client_id, challenge)
        r = c.get(f"{GW}/oauth/sso/return", params={"req": req2, "code": handoff_code})
        assert r.status_code == 400 and "만료되었습니다" in r.text and f"value='{req2}'" in r.text, r.text
        assert c.get(f"{GW}/oauth/consent", params={"req": req2}).status_code == 200  # the request itself survives the failed handoff
        # unknown / malformed inputs
        assert c.get(f"{GW}/oauth/sso/return", params={"req": "nope", "code": handoff_code}).status_code == 400
        assert c.post(f"{GW}/oauth/consent/sso", data={"req": "nope", "email": f"a@{domain}"}).status_code == 400
        assert c.post(f"{GW}/oauth/consent/sso", data={"req": req2, "email": "not-an-email"}).status_code == 400
        # a failed SSO attempt never burns the request: pasting an issued key still completes it
        assert consent(c, req2, world["raw"]).status_code == 302
    # nothing was provisioned for the refused people
    with httpx.Client(base_url=BACKEND, headers=world["hdr"], timeout=10) as api:
        emails = {u["email"] for u in api.get(f"/companies/{cid}/users").json()["items"]}
        assert f"unverified-{tag}@{domain}" not in emails and f"bob-{tag}@{domain}" in emails
