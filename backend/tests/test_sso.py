"""Company SSO (OIDC) against an in-process mock provider: provider management, MCP-connector session keys via the
gateway handoff, identity linking / auto-provisioning, the platform Google provider, and every refusal path. The
console has no SSO login (operators use email + password): `purpose=console` and `/auth/google/start` are gone."""

from urllib.parse import parse_qs, urlsplit

from tests.conftest import uid
from tests.mock_idp import CLIENT_ID, CLIENT_SECRET, GOOGLE, ISSUER

HANDOFF = {"X-Internal-Secret": "test-handoff-secret-0123456789abcdef"}


async def put_provider(client, hdr, cid, domain, **over):
    body = {"issuer": ISSUER, "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET, "domains": [domain]}
    body.update(over)
    return await client.put(f"/api/v1/companies/{cid}/sso", json=body, headers=hdr)


async def start(http, email, purpose="gateway", **q):
    return await http.get("/api/v1/auth/sso/start", params={"email": email, "purpose": purpose, **q})


async def callback(client, state, code):
    r = await client.get("/api/v1/auth/sso/callback", params={"state": state, "code": code})
    assert r.status_code == 302, r.text
    return r.headers["location"]


def fragment(url: str) -> dict:
    return {k: v[0] for k, v in parse_qs(urlsplit(url).fragment).items()}


def query(url: str) -> dict:
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


async def test_provider_management(client, master, world, idp):
    cid, domain = world["cid"], f"{world['cid']}.example"
    r = await put_provider(client, world["admin_hdr"], cid, domain, hosted_domain=domain)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["has_secret"] and "client_secret" not in out and out["redirect_uri"].endswith("/api/v1/auth/sso/callback")
    assert out["domains"] == [domain] and out["status"] == "active" and out["auto_provision"] is True
    # update without a secret keeps the stored one; issuer normalised
    r = await put_provider(client, world["admin_hdr"], cid, domain, client_secret=None, issuer=ISSUER + "/", auto_provision=False)
    assert r.status_code == 200 and r.json()["has_secret"] and r.json()["issuer"] == ISSUER and r.json()["version"] == 2
    # a domain already owned by another company's provider is refused
    other = uid("globex-")
    await client.post("/api/v1/companies", json={"company_id": other, "name": "Globex"}, headers=master)
    assert (await put_provider(client, master, other, domain)).status_code == 409
    assert (await put_provider(client, master, other, f"{other}.example")).status_code == 200
    # an admin of another company cannot read it
    assert (await client.get(f"/api/v1/companies/{other}/sso", headers=world["admin_hdr"])).status_code == 403
    # connectivity test reaches discovery + JWKS
    r = await client.post(f"/api/v1/companies/{cid}/sso/test", headers=world["admin_hdr"])
    assert r.status_code == 200 and r.json()["jwks_keys"] == 1
    # delete frees the domain and disables login; re-creating needs a secret again
    assert (await client.delete(f"/api/v1/companies/{cid}/sso", headers=world["admin_hdr"])).status_code == 204
    assert (await client.get(f"/api/v1/companies/{cid}/sso", headers=world["admin_hdr"])).status_code == 404
    assert query((await start(client, f"someone@{domain}")).headers["location"]) == {"error": "sso_not_configured"}  # blank req is dropped by the parser
    assert (await put_provider(client, master, other, domain, domains=[f"{other}.example", domain])).status_code == 200
    assert (await put_provider(client, world["admin_hdr"], cid, f"new-{domain}", client_secret=None)).status_code == 422


async def test_gateway_login_issues_session_key_and_handoff(client, master, world, idp, container):
    cid, domain = world["cid"], f"{world['cid']}.example"
    assert (await put_provider(client, world["admin_hdr"], cid, domain)).status_code == 200
    email = f"analyst@{domain}"
    r = await start(client, email, purpose="gateway", req="req-abc", client="ChatGPT")
    assert r.status_code == 302
    state, code = idp.login(r.headers["location"], sub="analyst-sub", email=email, email_verified=True, name="Analyst")
    loc = await callback(client, state, code)
    assert loc.startswith("http://localhost:19000/oauth/sso/return?"), loc
    q = query(loc)
    assert q["req"] == "req-abc" and "error" not in q
    # the handoff needs the shared secret and works once
    assert (await client.post("/api/v1/auth/sso/handoff", json={"code": q["code"]})).status_code == 401
    assert (await client.post("/api/v1/auth/sso/handoff", json={"code": q["code"]}, headers={"X-Internal-Secret": "wrong"})).status_code == 401
    r = await client.post("/api/v1/auth/sso/handoff", json={"code": q["code"]}, headers=HANDOFF)
    assert r.status_code == 200, r.text
    h = r.json()
    assert h["raw_key"].startswith("mcpk_") and h["company_id"] == cid and h["email"] == email
    assert (await client.post("/api/v1/auth/sso/handoff", json={"code": q["code"]}, headers=HANDOFF)).status_code == 401
    # the key is an ordinary key of the auto-provisioned member: all-servers scope, expiring, named after the client
    k = (await client.get(f"/api/v1/companies/{cid}/api-keys/{h['key_id']}", headers=world["admin_hdr"])).json()
    assert k["name"] == "sso:ChatGPT" and k["server_ids"] is None and k["user_id"] == h["user_id"] and k["expires_at"]
    u = (await client.get(f"/api/v1/companies/{cid}/users/{h['user_id']}", headers=world["admin_hdr"])).json()
    assert u["role"] == "member" and u["can_login"] is False and u["identities"][0]["subject"] == "analyst-sub"
    r = await client.post("/api/v1/ops/resolve", json={"raw_key": h["raw_key"]}, headers=master)
    assert r.json()["effective"] == "active" and r.json()["servers"] == {}  # no access granted yet: nothing usable
    # revoking in the console kills it like any key
    await client.patch(f"/api/v1/companies/{cid}/api-keys/{h['key_id']}", json={"status": "suspended"}, headers=world["admin_hdr"])
    assert (await client.post("/api/v1/ops/resolve", json={"raw_key": h["raw_key"]}, headers=master)).json()["effective"].startswith("denied")
    # second login of the same subject resolves to the same user (no duplicate), new session key
    r = await start(client, email, purpose="gateway", req="req-2")
    state, code = idp.login(r.headers["location"], sub="analyst-sub", email=email, email_verified=True)
    q2 = query(await callback(client, state, code))
    h2 = (await client.post("/api/v1/auth/sso/handoff", json={"code": q2["code"]}, headers=HANDOFF)).json()
    assert h2["user_id"] == h["user_id"] and h2["key_id"] != h["key_id"]


async def test_refusals(client, master, world, idp):
    cid, domain = world["cid"], f"{world['cid']}.example"
    assert (await put_provider(client, world["admin_hdr"], cid, domain, hosted_domain=domain)).status_code == 200
    email = f"x@{domain}"

    async def attempt(**claims):
        r = await start(client, email, purpose="gateway", req="r")
        assert r.status_code == 302, r.text
        state, code = idp.login(r.headers["location"], **claims)
        return query(await callback(client, state, code)).get("error")

    assert await attempt(sub="s1", email=email, email_verified=False, hd=domain) == "email_not_verified"
    assert await attempt(sub="s1", email=f"x@other-{domain}", email_verified=True, hd=domain) == "domain_not_allowed"
    assert await attempt(sub="s1", email=email, email_verified=True, hd="elsewhere.example") == "domain_not_allowed"
    assert await attempt(sub="s1", email=email, email_verified=True) == "domain_not_allowed"  # hd missing
    # provider refuses the code (e.g. wrong verifier) -> provider_error, nothing created
    r = await start(client, email, purpose="gateway", req="r")
    state, _ = idp.login(r.headers["location"], sub="s1", email=email, email_verified=True, hd=domain)
    assert query(await callback(client, state, "not-a-code")).get("error") == "provider_error"
    # unknown state / replay: the gateway return page gets the code without a request id
    assert query(await callback(client, "nope", "c")) == {"error": "expired"}
    # auto_provision off: unknown users are refused, known users still log in
    await put_provider(client, world["admin_hdr"], cid, domain, client_secret=None, hosted_domain=domain, auto_provision=False)
    assert await attempt(sub="s2", email=f"new@{domain}", email_verified=True, hd=domain) == "not_registered"
    users = (await client.get(f"/api/v1/companies/{cid}/users", params={"q": "new"}, headers=master)).json()["items"]
    assert not [u for u in users if u["email"] == f"new@{domain}"]
    # an email registered in another company is never linked across companies
    other = uid("globex-")
    await client.post("/api/v1/companies", json={"company_id": other, "name": "Globex"}, headers=master)
    foreign = f"shared@{domain}"
    await client.post(f"/api/v1/companies/{other}/users", json={"email": foreign, "name": "F", "role": "member"}, headers=master)
    await put_provider(client, world["admin_hdr"], cid, domain, client_secret=None, hosted_domain=domain, auto_provision=True)
    r = await start(client, foreign, purpose="gateway", req="r")
    state, code = idp.login(r.headers["location"], sub="s3", email=foreign, email_verified=True, hd=domain)
    assert query(await callback(client, state, code)).get("error") == "not_registered"
    # suspended user, then suspended company (start itself is refused once the company is not active)
    r = await start(client, email, purpose="gateway", req="r")
    state, code = idp.login(r.headers["location"], sub="s4", email=email, email_verified=True, hd=domain)
    q = query(await callback(client, state, code))
    h = (await client.post("/api/v1/auth/sso/handoff", json={"code": q["code"]}, headers=HANDOFF)).json()
    await client.patch(f"/api/v1/companies/{cid}/users/{h['user_id']}", json={"status": "suspended"}, headers=master)
    assert await attempt(sub="s4", email=email, email_verified=True, hd=domain) == "user_suspended"
    await client.patch(f"/api/v1/companies/{cid}", json={"status": "suspended"}, headers=master)
    r = await start(client, email, req="r8")
    assert r.status_code == 302 and query(r.headers["location"]) == {"req": "r8", "error": "sso_not_configured"}
    await client.patch(f"/api/v1/companies/{cid}", json={"status": "active"}, headers=master)
    # provider suspended: no new logins (the gateway purpose is told the same way, on its own return URL)
    await put_provider(client, world["admin_hdr"], cid, domain, client_secret=None, hosted_domain=domain, status="suspended")
    r = await start(client, email, purpose="gateway", req="r9")
    assert r.status_code == 302 and query(r.headers["location"]) == {"req": "r9", "error": "sso_not_configured"}


async def test_http_issuer_only_in_dev_and_secret_length(monkeypatch):
    from app.core.config import Settings

    s = Settings(app_env="prod", jwt_secret="x" * 40, master_password="y" * 16, redis_url="redis://:pw@r:6379", sso_handoff_secret="short")
    assert any("SSO_HANDOFF_SECRET" in p for p in s.runtime_problems())
    s = Settings(app_env="prod", jwt_secret="x" * 40, master_password="y" * 16, redis_url="redis://:pw@r:6379", sso_handoff_secret="change-me-dev-sso-handoff-secret-0123456789")
    assert any("SSO_HANDOFF_SECRET" in p for p in s.runtime_problems())
    assert s.sso_allow_http_issuer is False and Settings(app_env="dev").sso_allow_http_issuer is True


# ---- platform Google provider: the deployment's OAuth client serves a company's connector logins ------------------
async def test_platform_google_provider_serves_connector_login(client, master, world, idp):
    cid, domain = world["cid"], f"{world['cid']}.example"
    assert (await client.get("/api/v1/auth/sso/options")).json() == {"google": True}
    # a company may use the platform client without an OAuth app of its own (no client id/secret stored)
    r = await put_provider(client, world["admin_hdr"], cid, domain, platform="google", issuer=None, client_id=None, client_secret=None)
    assert r.status_code == 200, r.text
    prov = r.json()
    assert prov["platform"] == "google" and prov["client_id"] is None and prov["has_secret"] is False and prov["issuer"] == GOOGLE
    email = f"staff@{domain}"
    r = await start(client, email, req="g1", client="Claude")
    assert r.status_code == 302 and "client_id=platform-google" in r.headers["location"] and "admin.directory" not in r.headers["location"]
    state, code = idp.login(r.headers["location"], sub="staff-sub", email=email, email_verified=True, hd=domain, name="Staff")
    q = query(await callback(client, state, code))
    h = (await client.post("/api/v1/auth/sso/handoff", json={"code": q["code"]}, headers=HANDOFF)).json()
    assert h["company_id"] == cid and h["raw_key"].startswith("mcpk_")
    u = (await client.get(f"/api/v1/companies/{cid}/users/{h['user_id']}", headers=master)).json()
    assert u["role"] == "member" and u["can_login"] is False and u["identities"] == [{"issuer": GOOGLE, "subject": "staff-sub"}]
    # the console has no SSO entrance any more
    assert (await client.get("/api/v1/auth/google/start", params={"mode": "login"})).status_code == 404
    assert (await client.get("/api/v1/auth/sso/start", params={"email": email, "purpose": "console"})).status_code == 422
    # an SSO-linked admin without a password cannot use the console either: only a password does
    r = await client.post(f"/api/v1/companies/{cid}/users", json={"email": f"it@{domain}", "name": "IT", "role": "admin", "password": "adminpassword9"}, headers=master)
    assert r.status_code == 201 and r.json()["can_login"] is True
