"""Team policies materialised into ServerAccess rows, on top of a synced Workspace: everyone/team grants, union per
server, manual rows untouched, membership changes propagate, operator edits take ownership, suspended policies."""

import uuid

from tests.test_sso import HANDOFF, callback, query, start
from tests.test_workspace import connect, ws_user


async def _server_bound(client, master, cid, tag):
    srv = (await client.post("/api/v1/servers", json={"server_type": "postgres", "name": f"pg-{tag}"}, headers=master)).json()
    r = await client.post(f"/api/v1/companies/{cid}/bindings", json={"server_id": srv["server_id"], "tenant": {"mode": "schema", "schema_name": "acme"}}, headers=master)
    assert r.status_code == 201, r.text
    return srv["server_id"]


async def _access(client, hdr, cid, uid):
    return {a["server_id"]: a for a in (await client.get(f"/api/v1/companies/{cid}/users/{uid}/access", headers=hdr)).json()["items"]}


async def test_policies_follow_teams_and_manual_wins(client, master, idp):
    tag = uuid.uuid4().hex[:6]
    domain = f"ws{tag}.example"
    boss, ana, bob = f"boss@{domain}", f"ana@{domain}", f"bob@{domain}"
    cid, hdr = await connect(client, master, idp, domain, boss)
    sid, sid2 = await _server_bound(client, master, cid, tag), await _server_bound(client, master, cid, tag + "b")
    idp.directory[domain] = {
        "users": [ws_user(boss, f"{tag}-boss"), ws_user(ana, f"{tag}-ana"), ws_user(bob, f"{tag}-bob")],
        "groups": [{"id": "g-data", "email": f"data@{domain}", "name": "Data"}],
        "members": {"g-data": [{"id": f"{tag}-ana", "email": ana, "type": "USER"}]},
    }
    assert (await client.post(f"/api/v1/companies/{cid}/workspace/sync", headers=hdr)).status_code == 200
    users = {u["email"]: u for u in (await client.get(f"/api/v1/companies/{cid}/users", headers=hdr)).json()["items"]}
    team = (await client.get(f"/api/v1/companies/{cid}/teams", headers=hdr)).json()["items"][0]
    # a policy needs an active binding and known tools; a duplicate for the same team/server is refused
    r = await client.post(f"/api/v1/companies/{cid}/policies", json={"team_id": None, "server_id": sid, "permissions": ["read"], "allowed_tools": ["list_tables", "run_select"]}, headers=hdr)
    assert r.status_code == 201, r.text
    everyone = r.json()
    assert (await client.post(f"/api/v1/companies/{cid}/policies", json={"team_id": None, "server_id": sid, "permissions": ["read"], "allowed_tools": ["list_tables"]}, headers=hdr)).status_code == 409
    assert (await client.post(f"/api/v1/companies/{cid}/policies", json={"team_id": None, "server_id": sid, "permissions": ["read"], "allowed_tools": ["export"]}, headers=hdr)).status_code == 422
    assert (await client.post(f"/api/v1/companies/{cid}/policies", json={"team_id": "team_nope", "server_id": sid, "permissions": ["read"], "allowed_tools": ["list_tables"]}, headers=hdr)).status_code == 404
    r = await client.post(f"/api/v1/companies/{cid}/policies", json={"team_id": team["team_id"], "server_id": sid, "permissions": ["read", "export"], "allowed_tools": ["export"]}, headers=hdr)
    assert r.status_code == 201, r.text
    data_pol = r.json()
    # materialised: everyone gets read on sid; ana (Data) additionally export — one row, the union
    acc_ana = await _access(client, hdr, cid, users[ana]["user_id"])
    acc_bob = await _access(client, hdr, cid, users[bob]["user_id"])
    acc_boss = await _access(client, hdr, cid, users[boss]["user_id"])
    assert acc_bob[sid]["source"] == "policy" and acc_bob[sid]["allowed_tools"] == ["list_tables", "run_select"] and acc_bob[sid]["policy_ids"] == [everyone["policy_id"]]
    assert acc_ana[sid]["permissions"] == ["export", "read"] and acc_ana[sid]["allowed_tools"] == ["export", "list_tables", "run_select"]
    assert sorted(acc_ana[sid]["policy_ids"]) == sorted([everyone["policy_id"], data_pol["policy_id"]])
    assert sid in acc_boss and sid2 not in acc_bob
    # the hierarchy still rules: a key of bob only sees what the policy row grants
    k = (await client.post(f"/api/v1/companies/{cid}/api-keys", json={"user_id": users[bob]["user_id"], "server_ids": None, "name": "k"}, headers=hdr)).json()
    res = (await client.post("/api/v1/ops/resolve", json={"raw_key": k["raw_key"]}, headers=master)).json()
    assert res["effective"] == "active" and res["servers"] == {sid: "active"}
    # a manual grant on sid2 for bob stays manual; a policy on sid2 keeps it and counts it
    r = await client.post(f"/api/v1/companies/{cid}/users/{users[bob]['user_id']}/access", json={"server_id": sid2, "permissions": ["read"], "allowed_tools": ["list_tables"]}, headers=hdr)
    assert r.status_code == 201
    await client.post(f"/api/v1/companies/{cid}/policies", json={"team_id": None, "server_id": sid2, "permissions": ["read"], "allowed_tools": ["run_select"]}, headers=hdr)
    rep = (await client.post(f"/api/v1/companies/{cid}/policies/apply", headers=hdr)).json()
    assert rep["kept_manual"] == 1 and rep["errors"] == []
    acc_bob = await _access(client, hdr, cid, users[bob]["user_id"])
    assert acc_bob[sid2]["source"] == "manual" and acc_bob[sid2]["allowed_tools"] == ["list_tables"]
    acc_ana = await _access(client, hdr, cid, users[ana]["user_id"])
    assert acc_ana[sid2]["source"] == "policy" and acc_ana[sid2]["allowed_tools"] == ["run_select"]
    # ana leaves the Data group -> her sid row shrinks to the everyone grant; bob joins -> gets export
    idp.directory[domain]["members"]["g-data"] = [{"id": f"{tag}-bob", "email": bob, "type": "USER"}]
    rep = (await client.post(f"/api/v1/companies/{cid}/workspace/sync", headers=hdr)).json()
    assert rep["policies"]["updated"] == 2 and rep["policies"]["errors"] == []
    assert (await _access(client, hdr, cid, users[ana]["user_id"]))[sid]["allowed_tools"] == ["list_tables", "run_select"]
    assert "export" in (await _access(client, hdr, cid, users[bob]["user_id"]))[sid]["allowed_tools"]
    # an operator edits bob's policy row -> it becomes manual and policies leave it alone from then on
    r = await client.patch(f"/api/v1/companies/{cid}/users/{users[bob]['user_id']}/access/{sid}", json={"allowed_tools": ["list_tables"]}, headers=hdr)
    assert r.status_code == 200 and r.json()["source"] == "manual" and r.json()["policy_ids"] == []
    rep = (await client.post(f"/api/v1/companies/{cid}/policies/apply", headers=hdr)).json()
    assert rep["kept_manual"] == 2
    assert (await _access(client, hdr, cid, users[bob]["user_id"]))[sid]["allowed_tools"] == ["list_tables"]
    # suspending the everyone policy suspends rows it alone produced (boss), ana keeps nothing on sid either
    r = await client.patch(f"/api/v1/companies/{cid}/policies/{everyone['policy_id']}", json={"status": "suspended"}, headers=hdr)
    assert r.status_code == 200
    assert (await _access(client, hdr, cid, users[boss]["user_id"]))[sid]["status"] == "suspended"
    assert (await _access(client, hdr, cid, users[ana]["user_id"]))[sid]["status"] == "suspended"
    res = (await client.post("/api/v1/ops/resolve", json={"raw_key": k["raw_key"]}, headers=master)).json()
    assert res["servers"][sid] == "active"  # bob's row is manual now: untouched
    # reactivating the policy brings the rows back
    await client.patch(f"/api/v1/companies/{cid}/policies/{everyone['policy_id']}", json={"status": "active"}, headers=hdr)
    assert (await _access(client, hdr, cid, users[boss]["user_id"]))[sid]["status"] == "active"
    # listing/filtering and another company's admin is refused
    assert len((await client.get(f"/api/v1/companies/{cid}/policies", params={"server_id": sid}, headers=hdr)).json()["items"]) == 2
    other = (await client.post("/api/v1/companies", json={"company_id": f"o-{tag}", "name": "O"}, headers=master))
    assert other.status_code == 201
    adm = (await client.post(f"/api/v1/companies/o-{tag}/users", json={"email": f"a@o-{tag}.example", "name": "A", "role": "admin", "password": "adminpassword1"}, headers=master)).json()
    tok = (await client.post("/api/v1/auth/login", json={"email": adm["email"], "password": "adminpassword1"})).json()["access_token"]
    assert (await client.get(f"/api/v1/companies/{cid}/policies", headers={"Authorization": f"Bearer {tok}"})).status_code == 403


async def test_employee_login_sees_team_grants_end_to_end(client, master, idp):
    """The whole promise: connect Workspace, set one team policy, and a new employee's first Google login + connector
    session key already works on the right server with the team's tools — no per-user admin action."""
    tag = uuid.uuid4().hex[:6]
    domain = f"ws{tag}.example"
    boss, newbie = f"boss@{domain}", f"new@{domain}"
    cid, hdr = await connect(client, master, idp, domain, boss)
    sid = await _server_bound(client, master, cid, tag)
    idp.directory[domain] = {"users": [ws_user(boss, f"{tag}-boss")], "groups": [{"id": "g-an", "email": f"an@{domain}", "name": "Analysts"}], "members": {"g-an": []}}
    assert (await client.post(f"/api/v1/companies/{cid}/workspace/sync", headers=hdr)).status_code == 200
    team = (await client.get(f"/api/v1/companies/{cid}/teams", headers=hdr)).json()["items"][0]
    assert (await client.post(f"/api/v1/companies/{cid}/policies", json={"team_id": team["team_id"], "server_id": sid, "permissions": ["read"], "allowed_tools": ["run_select"]}, headers=hdr)).status_code == 201
    # HR adds the newbie to the Analysts group; the next sync creates the user, the membership and the access row
    idp.directory[domain]["users"].append(ws_user(newbie, f"{tag}-new", "New Person"))
    idp.directory[domain]["members"]["g-an"] = [{"id": f"{tag}-new", "email": newbie, "type": "USER"}]
    rep = (await client.post(f"/api/v1/companies/{cid}/workspace/sync", headers=hdr)).json()
    assert rep["users_created"] == 1 and rep["members_added"] == 1 and rep["policies"]["created"] == 1
    # the newbie connects a client through the gateway path: identity already linked, session key resolves to the server
    r = await start(client, newbie, req="r1", client="Claude")
    state, code = idp.login(r.headers["location"], sub=f"{tag}-new", email=newbie, email_verified=True, hd=domain)
    q = query(await callback(client, state, code))
    h = (await client.post("/api/v1/auth/sso/handoff", json={"code": q["code"]}, headers=HANDOFF)).json()
    res = (await client.post("/api/v1/ops/resolve", json={"raw_key": h["raw_key"]}, headers=master)).json()
    assert res["effective"] == "active" and res["servers"] == {sid: "active"}
    users = {u["email"]: u for u in (await client.get(f"/api/v1/companies/{cid}/users", headers=hdr)).json()["items"]}
    assert len(users) == 2 and users[newbie]["can_login"] is False  # no duplicate from the login; employees have no console
