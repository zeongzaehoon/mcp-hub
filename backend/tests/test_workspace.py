"""Google Workspace directory sync against the mock provider, with the operator path: master creates the company and
registers a service-account key (domain-wide delegation) — no Google login anywhere. The sync mirrors users and groups
as members and teams, re-runs are idempotent, manual records are never overwritten, directory suspensions propagate one
way, and a refused delegation flags the connection for an operator."""

import json
import re
import uuid

from tests.mock_idp import service_account_json
from tests.test_sso import HANDOFF, callback, query, start


def ws_user(email, uid, name="", suspended=False):
    return {"id": uid, "primaryEmail": email, "name": {"fullName": name or email.split("@")[0].title()}, "suspended": suspended}


async def connect(client, master, idp, domain, admin_email, *, sa=None):
    """Operator path: the company exists (master-made) and master registers the key; the mock authorises the key's
    client for `admin_email`. Returns (company_id, headers to use)."""
    cid = re.sub(r"[^a-z0-9-]+", "-", domain.lower()).strip("-")[:40]
    r = await client.post("/api/v1/companies", json={"company_id": cid, "name": domain}, headers=master)
    assert r.status_code in (201, 409), r.text
    sa_json, pub = sa or service_account_json()
    idp.delegations[json.loads(sa_json)["client_email"]] = pub
    idp.workspace_admins.add(admin_email)
    r = await client.put(f"/api/v1/companies/{cid}/workspace", json={"domain": domain, "admin_email": admin_email, "service_account_json": sa_json}, headers=master)
    assert r.status_code == 200, r.text
    return cid, master


async def test_sync_mirrors_directory_and_is_idempotent(client, master, idp):
    domain = f"ws{uuid.uuid4().hex[:6]}.example"
    boss, ana, bob = f"boss@{domain}", f"ana@{domain}", f"bob@{domain}"
    cid, hdr = await connect(client, master, idp, domain, boss)
    w = (await client.get(f"/api/v1/companies/{cid}/workspace", headers=hdr)).json()
    assert w["domain"] == domain and w["admin_email"] == boss and w["client_id"] == "1234567890" and w["client_email"].endswith(".iam.gserviceaccount.com")
    assert w["has_credentials"] is True and w["needs_reconnect"] is False and w["last_sync_at"] is None and len(w["scopes"]) == 3
    assert "private_key" not in str(w) and "service_account" not in str(w)
    # connecting gave the company the platform Google provider for its employees' connector logins
    prov = (await client.get(f"/api/v1/companies/{cid}/sso", headers=hdr)).json()
    assert prov["platform"] == "google" and prov["domains"] == [domain] and prov["hosted_domain"] == domain
    idp.directory[domain] = {
        "users": [ws_user(boss, "100-boss", "Boss"), ws_user(ana, "101-ana", "Ana"), ws_user(bob, "102-bob", "Bob", suspended=True),
                  ws_user(f"ext@other-{domain}", "999", "Ext")],
        "groups": [{"id": "g-data", "email": f"data@{domain}", "name": "Data Team"}, {"id": "g-all", "email": f"all@{domain}", "name": "Everyone"}],
        "members": {"g-data": [{"id": "101-ana", "email": ana, "type": "USER"}, {"id": "102-bob", "email": bob, "type": "USER"}],
                    "g-all": [{"id": "100-boss", "email": boss, "type": "USER"}, {"id": "101-ana", "email": ana, "type": "USER"},
                              {"id": "g-data", "email": f"data@{domain}", "type": "GROUP"}]},
    }
    r = await client.post(f"/api/v1/companies/{cid}/workspace/sync", headers=hdr)
    assert r.status_code == 200, r.text
    rep = r.json()
    # boss + ana created as members; bob is suspended in the directory and never existed here -> not created; ext domain ignored
    assert rep["users_seen"] == 3 and rep["users_created"] == 2 and rep["users_suspended"] == 0 and rep["skipped"] == []
    assert rep["teams_seen"] == 2 and rep["teams_created"] == 2 and rep["members_added"] == 3
    users = {u["email"]: u for u in (await client.get(f"/api/v1/companies/{cid}/users", headers=hdr)).json()["items"]}
    assert set(users) == {boss, ana}
    assert users[ana]["source"] == "workspace" and users[ana]["role"] == "member" and users[ana]["identities"][0]["subject"] == "101-ana"
    assert users[boss]["role"] == "member" and users[boss]["can_login"] is False
    teams = {t["email"]: t for t in (await client.get(f"/api/v1/companies/{cid}/teams", headers=hdr)).json()["items"]}
    assert set(teams) == {f"data@{domain}", f"all@{domain}"} and teams[f"data@{domain}"]["source"] == "workspace"
    data_members = {m["user_id"] for m in (await client.get(f"/api/v1/companies/{cid}/teams/{teams[f'data@{domain}']['team_id']}/members", headers=hdr)).json()}
    assert data_members == {users[ana]["user_id"]}
    all_members = {m["user_id"] for m in (await client.get(f"/api/v1/companies/{cid}/teams/{teams[f'all@{domain}']['team_id']}/members", headers=hdr)).json()}
    assert all_members == {users[ana]["user_id"], users[boss]["user_id"]}
    w = (await client.get(f"/api/v1/companies/{cid}/workspace", headers=hdr)).json()
    assert w["last_sync_at"] and w["last_sync"]["users_created"] == 2 and w["sync_error"] is None
    # idempotent
    rep2 = (await client.post(f"/api/v1/companies/{cid}/workspace/sync", headers=hdr)).json()
    assert {k: v for k, v in rep2.items() if k not in ("users_seen", "teams_seen", "skipped", "policies")} == dict.fromkeys(
        ["users_created", "users_updated", "users_suspended", "users_reactivated", "teams_created", "teams_updated", "teams_suspended", "members_added", "members_removed"], 0)
    # ana connects a client: the connector login resolves to the synced identity (no duplicate) and issues her key
    r = await start(client, ana, req="c1", client="Claude")
    assert r.status_code == 302 and "client_id=platform-google" in r.headers["location"]
    state, code = idp.login(r.headers["location"], sub="101-ana", email=ana, email_verified=True, hd=domain)
    h = (await client.post("/api/v1/auth/sso/handoff", json={"code": query(await callback(client, state, code))["code"]}, headers=HANDOFF)).json()
    assert h["user_id"] == users[ana]["user_id"]
    assert len((await client.get(f"/api/v1/companies/{cid}/users", headers=hdr)).json()["items"]) == 2


async def test_sync_status_rules_and_manual_wins(client, master, idp):
    domain = f"ws{uuid.uuid4().hex[:6]}.example"
    boss, ana, carl, manual = f"boss@{domain}", f"ana@{domain}", f"carl@{domain}", f"manual@{domain}"
    cid, hdr = await connect(client, master, idp, domain, boss)
    # an operator-created user whose email the directory also knows: it gains the id/identity but stays manual
    mu = (await client.post(f"/api/v1/companies/{cid}/users", json={"email": manual, "name": "Manual Person", "role": "member"}, headers=master)).json()
    d = idp.directory[domain] = {
        "users": [ws_user(boss, "200-boss"), ws_user(ana, "201-ana"), ws_user(carl, "202-carl"), ws_user(manual, "203-man", "Dir Name")],
        "groups": [{"id": "g1", "email": f"g1@{domain}", "name": "G1"}],
        "members": {"g1": [{"id": "201-ana", "email": ana, "type": "USER"}, {"id": "203-man", "email": manual, "type": "USER"}]},
    }
    rep = (await client.post(f"/api/v1/companies/{cid}/workspace/sync", headers=hdr)).json()
    assert rep["users_created"] == 3 and rep["users_updated"] == 1  # manual user updated (external id + identity)
    users = {u["email"]: u for u in (await client.get(f"/api/v1/companies/{cid}/users", headers=hdr)).json()["items"]}
    assert users[manual]["source"] == "manual" and users[manual]["name"] == "Manual Person" and users[manual]["identities"][0]["subject"] == "203-man"
    tid = (await client.get(f"/api/v1/companies/{cid}/teams", headers=hdr)).json()["items"][0]["team_id"]
    # directory suspends ana, carl leaves, the group drops manual -> ana + carl suspended here, membership removed
    d["users"] = [ws_user(boss, "200-boss"), ws_user(ana, "201-ana", suspended=True), ws_user(manual, "203-man", "Dir Name")]
    d["members"]["g1"] = [{"id": "201-ana", "email": ana, "type": "USER"}]
    rep = (await client.post(f"/api/v1/companies/{cid}/workspace/sync", headers=hdr)).json()
    assert rep["users_suspended"] == 2 and rep["members_removed"] == 1
    users = {u["email"]: u for u in (await client.get(f"/api/v1/companies/{cid}/users", headers=hdr)).json()["items"]}
    assert users[ana]["status"] == "suspended" and users[carl]["status"] == "suspended"
    assert {m["user_id"] for m in (await client.get(f"/api/v1/companies/{cid}/teams/{tid}/members", headers=hdr)).json()} == {users[ana]["user_id"]}
    # the directory reactivates ana -> reactivated here (the sync had suspended her)
    d["users"] = [ws_user(boss, "200-boss"), ws_user(ana, "201-ana"), ws_user(manual, "203-man", "Dir Name")]
    rep = (await client.post(f"/api/v1/companies/{cid}/workspace/sync", headers=hdr)).json()
    assert rep["users_reactivated"] == 1
    # an operator suspends the manual user: the directory says active, the sync leaves it suspended
    await client.patch(f"/api/v1/companies/{cid}/users/{mu['user_id']}", json={"status": "suspended"}, headers=master)
    rep = (await client.post(f"/api/v1/companies/{cid}/workspace/sync", headers=hdr)).json()
    assert rep["users_reactivated"] == 0
    users = {u["email"]: u for u in (await client.get(f"/api/v1/companies/{cid}/users", headers=hdr)).json()["items"]}
    assert users[manual]["status"] == "suspended"
    # group removed from the directory -> team suspended, not deleted
    d["groups"] = []
    rep = (await client.post(f"/api/v1/companies/{cid}/workspace/sync", headers=hdr)).json()
    assert rep["teams_suspended"] == 1
    assert (await client.get(f"/api/v1/companies/{cid}/teams/{tid}", headers=hdr)).json()["status"] == "suspended"


async def test_registration_rules_refused_delegation_and_disconnect(client, master, world, idp):
    domain = f"ws{uuid.uuid4().hex[:6]}.example"
    boss = f"boss@{domain}"
    cid = re.sub(r"[^a-z0-9-]+", "-", domain).strip("-")
    assert (await client.post("/api/v1/companies", json={"company_id": cid, "name": domain}, headers=master)).status_code == 201
    url = f"/api/v1/companies/{cid}/workspace"
    sa_json, pub = service_account_json()
    # what an operator can get wrong: no key on first connect, not a service-account file, admin outside the domain
    r = await client.put(url, json={"domain": domain, "admin_email": boss}, headers=master)
    assert r.status_code == 422 and r.json()["code"] == "workspace_key_required", r.text
    r = await client.put(url, json={"domain": domain, "admin_email": boss, "service_account_json": json.dumps({"type": "authorized_user"})}, headers=master)
    assert r.status_code == 422 and r.json()["code"] == "workspace_bad_key"
    bad = json.loads(sa_json)
    bad["private_key"] = "-----BEGIN PRIVATE KEY-----\nnope\n-----END PRIVATE KEY-----\n"
    r = await client.put(url, json={"domain": domain, "admin_email": boss, "service_account_json": json.dumps(bad)}, headers=master)
    assert r.status_code == 422 and r.json()["code"] == "workspace_bad_key"
    r = await client.put(url, json={"domain": domain, "admin_email": "boss@elsewhere.example", "service_account_json": sa_json}, headers=master)
    assert r.status_code == 422 and r.json()["code"] == "workspace_admin_domain"
    # registered, but the Workspace admin has not authorised the delegation yet: the grant is refused -> reconnect flag
    cid, hdr = await connect(client, master, idp, domain, boss, sa=(sa_json, pub))
    idp.directory[domain] = {"users": [ws_user(boss, "300-boss")], "groups": [], "members": {}}
    idp.workspace_admins.discard(boss)
    r = await client.post(f"{url}/sync", headers=hdr)
    assert r.status_code == 409 and r.json()["code"] == "workspace_reconnect", r.text
    w = (await client.get(url, headers=hdr)).json()
    assert w["needs_reconnect"] is True and "refused" in w["sync_error"]
    assert (await client.post(f"{url}/sync", headers=hdr)).status_code == 409  # the operator may retry after fixing Google Admin
    # the admin authorises the client id; the same key keeps working (re-PUT without a key keeps the stored one)
    idp.workspace_admins.add(boss)
    r = await client.put(url, json={"domain": domain, "admin_email": boss}, headers=master)
    assert r.status_code == 200 and r.json()["needs_reconnect"] is False and r.json()["has_credentials"] is True
    assert (await client.post(f"{url}/sync", headers=hdr)).status_code == 200
    # the domain belongs to this company now: another company cannot connect it; another company's admin sees nothing
    other = world["cid"]
    r = await client.put(f"/api/v1/companies/{other}/workspace", json={"domain": domain, "admin_email": boss, "service_account_json": sa_json}, headers=master)
    assert r.status_code == 409 and r.json()["code"] == "workspace_domain_taken"
    assert (await client.get(url, headers=world["admin_hdr"])).status_code == 403
    # disconnect wipes the key and frees the domain; records stay
    assert (await client.delete(url, headers=hdr)).status_code == 204
    assert (await client.get(url, headers=hdr)).status_code == 404
    assert (await client.post(f"{url}/sync", headers=hdr)).status_code == 404
    assert len((await client.get(f"/api/v1/companies/{cid}/users", headers=hdr)).json()["items"]) == 1
    r = await client.put(f"/api/v1/companies/{other}/workspace", json={"domain": domain, "admin_email": boss, "service_account_json": sa_json}, headers=master)
    assert r.status_code == 200, r.text
    assert (await client.delete(f"/api/v1/companies/{other}/workspace", headers=master)).status_code == 204
