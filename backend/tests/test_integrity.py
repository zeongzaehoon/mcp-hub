"""Integration tests against dynamodb-local + redis: transactional invariants and DB<->Redis sync."""

import asyncio
from datetime import UTC, datetime, timedelta

import orjson
import pytest

from app.core.errors import Conflict
from app.core.security import hash_api_key
from app.infra.redis.projection import (
    access_index_key,
    access_key,
    apikey_key,
    binding_key,
    company_key,
    dumps,
    server_key,
    user_key,
)
from tests.conftest import grant, issue_key, uid


async def _redis(container, key):
    raw = await container.pools.auth.get(key)
    return orjson.loads(raw) if raw else None


async def test_full_tree_synced_to_redis(client, master, world, container):
    w = world
    r = await issue_key(client, master, w)
    assert r.status_code == 201, r.text
    issued = r.json()
    raw = issued["raw_key"]
    k = issued["key"]
    assert raw.startswith("mcpk_") and "key_hash" not in k

    rk = await _redis(container, apikey_key(hash_api_key(raw)))
    assert rk["status"] == "active" and rk["server_ids"] == [w["server"]["server_id"]] and "permissions" not in rk
    ra = await _redis(container, access_key(w["cid"], w["member"]["user_id"], w["server"]["server_id"]))
    assert ra["status"] == "active" and ra["allowed_tools"] == ["list_tables", "run_select"] and ra["permissions"] == ["read"]
    assert (await _redis(container, company_key(w["cid"])))["status"] == "active"
    assert (await _redis(container, user_key(w["cid"], w["member"]["user_id"])))["status"] == "active"
    assert (await _redis(container, server_key(w["server"]["server_id"])))["server_type"] == "postgres"
    b = await _redis(container, binding_key(w["cid"], w["server"]["server_id"]))
    assert b["tenant"] == {"mode": "schema", "schema_name": "acme", "credentials": None}

    r = await client.post("/api/v1/ops/resolve", json={"raw_key": raw}, headers=master)
    assert r.json()["effective"] == "active" and r.json()["redis_in_sync"] is True
    assert r.json()["servers"] == {w["server"]["server_id"]: "active"}


async def test_access_requires_all_parents_active(client, master, world):
    w = world
    sid = w["server"]["server_id"]
    u2 = (await client.post(f"/api/v1/companies/{w['cid']}/users", json={"email": f"u2-{w['cid']}@ex.com", "name": "U2", "role": "member"}, headers=master)).json()
    # tools not covered by permissions -> 422
    r = await grant(client, master, w["cid"], u2["user_id"], sid, tools=["run_delete"])
    assert r.status_code == 422
    # duplicate grant for the same user x server -> 409
    assert (await grant(client, master, w["cid"], w["member"]["user_id"], sid)).status_code == 409
    # server with no binding for this company -> 409
    s2 = (await client.post("/api/v1/servers", json={"server_type": "postgres", "name": f"pg2-{w['cid']}"}, headers=master)).json()
    assert (await grant(client, master, w["cid"], u2["user_id"], s2["server_id"])).status_code == 409
    # suspended binding -> 409
    await client.patch(f"/api/v1/companies/{w['cid']}/bindings/{sid}", json={"status": "suspended"}, headers=master)
    assert (await grant(client, master, w["cid"], u2["user_id"], sid)).status_code == 409
    await client.patch(f"/api/v1/companies/{w['cid']}/bindings/{sid}", json={"status": "active"}, headers=master)
    # suspended user -> 409
    await client.patch(f"/api/v1/companies/{w['cid']}/users/{u2['user_id']}", json={"status": "suspended"}, headers=master)
    assert (await grant(client, master, w["cid"], u2["user_id"], sid)).status_code == 409
    await client.patch(f"/api/v1/companies/{w['cid']}/users/{u2['user_id']}", json={"status": "active"}, headers=master)
    # user of another company through this company's path -> 404
    other = uid("gx-")
    await client.post("/api/v1/companies", json={"company_id": other, "name": "Gx"}, headers=master)
    fu = (await client.post(f"/api/v1/companies/{other}/users", json={"email": f"f-{other}@ex.com", "name": "F", "role": "member"}, headers=master)).json()
    assert (await grant(client, master, w["cid"], fu["user_id"], sid)).status_code == 404
    # ...and in its own company it has no binding to this server -> 409
    assert (await grant(client, master, other, fu["user_id"], sid)).status_code == 409
    assert (await grant(client, master, w["cid"], u2["user_id"], sid)).status_code == 201


async def test_access_suspend_reactivate_and_permission_change_sync(client, master, world, container):
    w = world
    uidm, sid = w["member"]["user_id"], w["server"]["server_id"]
    base = f"/api/v1/companies/{w['cid']}/users/{uidm}/access/{sid}"
    raw = (await issue_key(client, master, w)).json()["raw_key"]
    r = await client.patch(base, json={"permissions": ["read", "export"], "allowed_tools": ["run_select", "export"]}, headers=master)
    assert r.status_code == 200, r.text
    assert (await _redis(container, access_key(w["cid"], uidm, sid)))["allowed_tools"] == ["export", "run_select"]
    assert (await client.patch(base, json={"allowed_tools": ["run_delete"]}, headers=master)).status_code == 422
    await client.patch(base, json={"status": "suspended"}, headers=master)
    r = await client.post("/api/v1/ops/resolve", json={"raw_key": raw}, headers=master)
    assert r.json()["effective"] == "active" and r.json()["servers"][sid] == "denied:access:suspended"
    # reactivation re-proves parents: with the binding suspended it is refused
    await client.patch(f"/api/v1/companies/{w['cid']}/bindings/{sid}", json={"status": "suspended"}, headers=master)
    assert (await client.patch(base, json={"status": "active"}, headers=master)).status_code == 409
    await client.patch(f"/api/v1/companies/{w['cid']}/bindings/{sid}", json={"status": "active"}, headers=master)
    assert (await client.patch(base, json={"status": "active"}, headers=master)).status_code == 200
    listed = (await client.get(f"/api/v1/companies/{w['cid']}/users/{uidm}/access", headers=w["admin_hdr"])).json()["items"]
    assert [a["server_id"] for a in listed] == [sid]
    assert [a["user_id"] for a in (await client.get(f"/api/v1/servers/{sid}/access", headers=master)).json()["items"]] == [uidm]


async def test_key_requires_all_parents_active(client, master, world):
    w = world
    # user from another company -> 404 (path scoping)
    other = uid("globex-")
    await client.post("/api/v1/companies", json={"company_id": other, "name": "Globex"}, headers=master)
    r = await client.post(f"/api/v1/companies/{other}/users", json={"email": f"m-{other}@ex.com", "name": "M", "role": "member"}, headers=master)
    foreign_user = r.json()["user_id"]
    r = await issue_key(client, master, w, user_id=foreign_user)
    assert r.status_code == 404
    # the foreign user holds no access to that server -> 409 (transaction condition)
    r = await client.post(f"/api/v1/companies/{other}/api-keys", json={"user_id": foreign_user, "server_ids": [w["server"]["server_id"]], "name": "k"}, headers=master)
    assert r.status_code == 409
    # scope naming a server the user has no access to -> 409
    s2 = (await client.post("/api/v1/servers", json={"server_type": "postgres", "name": f"pgx-{w['cid']}"}, headers=master)).json()
    assert (await issue_key(client, master, w, server_ids=[w["server"]["server_id"], s2["server_id"]])).status_code == 409
    # empty scope list -> 422; null scope (all servers) -> OK
    assert (await issue_key(client, master, w, server_ids=[])).status_code == 422
    # suspended user -> 409
    await client.patch(f"/api/v1/companies/{w['cid']}/users/{w['member']['user_id']}", json={"status": "suspended"}, headers=master)
    r = await issue_key(client, master, w)
    assert r.status_code == 409
    await client.patch(f"/api/v1/companies/{w['cid']}/users/{w['member']['user_id']}", json={"status": "active"}, headers=master)
    # suspended access -> 409
    acc = f"/api/v1/companies/{w['cid']}/users/{w['member']['user_id']}/access/{w['server']['server_id']}"
    await client.patch(acc, json={"status": "suspended"}, headers=master)
    r = await issue_key(client, master, w)
    assert r.status_code == 409
    await client.patch(acc, json={"status": "active"}, headers=master)
    # past expiry -> 422
    r = await issue_key(client, master, w, expires_at=(datetime.now(UTC) - timedelta(minutes=1)).isoformat())
    assert r.status_code == 422
    # finally OK
    r = await issue_key(client, master, w)
    assert r.status_code == 201
    r = await issue_key(client, master, w, server_ids=None)
    assert r.status_code == 201 and r.json()["key"]["server_ids"] is None


async def test_company_suspend_propagates_and_reactivation_reproves_parents(client, master, world, container):
    w = world
    raw = (await issue_key(client, master, w)).json()["raw_key"]
    kid = (await client.get(f"/api/v1/companies/{w['cid']}/api-keys", headers=master)).json()["items"][0]["key_id"]

    r = await client.patch(f"/api/v1/companies/{w['cid']}", json={"status": "suspended"}, headers=master)
    assert r.status_code == 200
    assert (await _redis(container, company_key(w["cid"])))["status"] == "suspended"
    r = await client.post("/api/v1/ops/resolve", json={"raw_key": raw}, headers=master)
    assert r.json()["effective"] == "denied:company:suspended"

    # suspend the key, then try to reactivate it while company is suspended -> refused by tx guard
    await client.patch(f"/api/v1/companies/{w['cid']}/api-keys/{kid}", json={"status": "suspended"}, headers=master)
    r = await client.patch(f"/api/v1/companies/{w['cid']}/api-keys/{kid}", json={"status": "active"}, headers=master)
    assert r.status_code == 409

    await client.patch(f"/api/v1/companies/{w['cid']}", json={"status": "active"}, headers=master)
    r = await client.patch(f"/api/v1/companies/{w['cid']}/api-keys/{kid}", json={"status": "active"}, headers=master)
    assert r.status_code == 200
    r = await client.post("/api/v1/ops/resolve", json={"raw_key": raw}, headers=master)
    assert r.json()["effective"] == "active"


async def test_rotate_tombstones_old_hash(client, master, world, container):
    w = world
    issued = (await issue_key(client, master, w)).json()
    old_raw, kid = issued["raw_key"], issued["key"]["key_id"]
    r = await client.post(f"/api/v1/companies/{w['cid']}/api-keys/{kid}/rotate", headers=master)
    assert r.status_code == 200
    new_raw = r.json()["raw_key"]
    assert new_raw != old_raw
    assert (await _redis(container, apikey_key(hash_api_key(old_raw))))["status"] == "deleted"
    assert (await _redis(container, apikey_key(hash_api_key(new_raw))))["status"] == "active"
    r = await client.post("/api/v1/ops/resolve", json={"raw_key": old_raw}, headers=master)
    assert r.json()["effective"] == "unknown"
    r = await client.post("/api/v1/ops/resolve", json={"raw_key": new_raw}, headers=master)
    assert r.json()["effective"] == "active"


async def test_optimistic_locking_rejects_stale_writer(client, master, world, container):
    w = world
    svc = container.companies
    a = await svc.get(w["cid"])
    b = await svc.get(w["cid"])
    a.name = "A"
    await svc.commit([a], [container.repo.tx.replace(a, svc.bump(a))])
    b.name = "B"
    with pytest.raises(Conflict):
        await svc.commit([b], [container.repo.tx.replace(b, svc.bump(b))])
    assert (await svc.get(w["cid"])).name == "A"


async def test_concurrent_key_issuance_all_consistent(client, master, world, container):
    w = world
    rs = await asyncio.gather(*[issue_key(client, master, w, name=f"k{i}") for i in range(15)])
    assert all(r.status_code == 201 for r in rs)
    hashes = {hash_api_key(r.json()["raw_key"]) for r in rs}
    assert len(hashes) == 15
    for h in hashes:
        assert (await _redis(container, apikey_key(h)))["status"] == "active"


async def test_deleted_is_terminal_and_cascades(client, master, world, container):
    w = world
    raw = (await issue_key(client, master, w)).json()["raw_key"]
    r = await client.patch(f"/api/v1/companies/{w['cid']}", json={"status": "deleted"}, headers=master)
    assert r.status_code == 200
    r = await client.patch(f"/api/v1/companies/{w['cid']}", json={"status": "active"}, headers=master)
    assert r.status_code == 422
    keys = (await client.get(f"/api/v1/companies/{w['cid']}/api-keys", headers=master)).json()["items"]
    assert keys and all(k["status"] == "deleted" for k in keys)
    users = (await client.get(f"/api/v1/companies/{w['cid']}/users", headers=master)).json()["items"]
    assert all(u["status"] == "deleted" for u in users)
    assert (await _redis(container, apikey_key(hash_api_key(raw))))["status"] == "deleted"
    # email freed: a new company can register the same email
    other = uid("re-")
    await client.post("/api/v1/companies", json={"company_id": other, "name": "Re"}, headers=master)
    r = await client.post(f"/api/v1/companies/{other}/users", json={"email": w["member"]["email"], "name": "M", "role": "member"}, headers=master)
    assert r.status_code == 201, r.text


async def test_email_unique_across_companies(client, master, world):
    other = uid("dup-")
    await client.post("/api/v1/companies", json={"company_id": other, "name": "Dup"}, headers=master)
    r = await client.post(f"/api/v1/companies/{other}/users", json={"email": world["member"]["email"], "name": "M", "role": "member"}, headers=master)
    assert r.status_code == 409


async def test_admin_scoped_to_own_company(client, master, world):
    w = world
    other = uid("oth-")
    await client.post("/api/v1/companies", json={"company_id": other, "name": "Other"}, headers=master)
    assert (await client.get(f"/api/v1/companies/{other}", headers=w["admin_hdr"])).status_code == 403
    assert (await client.get(f"/api/v1/companies/{other}/api-keys", headers=w["admin_hdr"])).status_code == 403
    assert (await client.post("/api/v1/servers", json={"server_type": "mongo", "name": "x"}, headers=w["admin_hdr"])).status_code == 403
    assert (await client.post(f"/api/v1/companies/{w['cid']}/bindings", json={"server_id": "x", "tenant": {}}, headers=w["admin_hdr"])).status_code == 403
    r = await client.get("/api/v1/companies", headers=w["admin_hdr"])
    assert [c["company_id"] for c in r.json()["items"]] == [w["cid"]]
    assert (await issue_key(client, w["admin_hdr"], w)).status_code == 201


async def test_reconciler_repairs_drift_and_removes_strays(client, master, world, container):
    w = world
    raw = (await issue_key(client, master, w)).json()["raw_key"]
    key = apikey_key(hash_api_key(raw))
    r = container.pools.auth
    good = await r.get(key)
    # 1) missing
    await r.delete(key)
    # 2) tampered with a *higher* version (someone wrote Redis directly)
    tampered = orjson.loads(await r.get(company_key(w["cid"])))
    tampered["status"] = "active"
    tampered["version"] = 999
    await r.set(company_key(w["cid"]), dumps(tampered))
    # 3) stray grant
    await r.set(apikey_key("f" * 64), dumps({"status": "active", "version": 1, "key_id": "x", "company_id": w["cid"], "user_id": "u", "server_ids": None}))
    await r.set(access_key(w["cid"], "u", "s"), dumps({"status": "active", "version": 1, "server_type": "postgres", "permissions": ["read"], "allowed_tools": ["run_select"]}))

    rep = (await client.post("/api/v1/ops/reconcile", headers=master)).json()
    assert rep["errors"] == []
    assert await r.get(key) == good
    assert orjson.loads(await r.get(company_key(w["cid"])))["version"] != 999
    assert await r.get(apikey_key("f" * 64)) is None
    assert await r.get(access_key(w["cid"], "u", "s")) is None


async def test_outbox_drain_recovers_missed_sync(client, master, world, container):
    w = world
    raw = (await issue_key(client, master, w)).json()["raw_key"]
    key = apikey_key(hash_api_key(raw))
    await container.pools.auth.delete(key)
    ent = await container.apikeys.resolve(raw)
    op, _item = container.outbox.op_for(ent)
    await container.repo.transact([op])
    assert await container.outbox.drain() >= 1
    assert (await _redis(container, key))["status"] == "active"


async def test_version_guard_never_downgrades(container, world):
    w = world
    c = await container.companies.get(w["cid"])
    old = c.model_copy(update={"version": c.version - 1, "name": "OLD"}) if c.version > 1 else None
    if old is None:
        c.name = "N"
        await container.companies.commit([c], [container.repo.tx.replace(c, container.companies.bump(c))])
        old = c.model_copy(update={"version": c.version - 1, "name": "OLD"})
    assert await container.sync.write(old) is False
    assert (await _redis(container, company_key(w["cid"])))["version"] == (await container.companies.get(w["cid"])).version


async def test_access_log_pipeline(client, master, world, container):
    w = world
    issued = (await issue_key(client, master, w)).json()
    kid = issued["key"]["key_id"]
    ev = {"ts": datetime.now(UTC).isoformat(), "key_id": kid, "company_id": w["cid"], "user_id": w["member"]["user_id"], "server_id": w["server"]["server_id"], "server_type": "postgres", "tool": "run_select", "args_summary": "{}", "status": "ok", "duration_ms": 3}
    await container.pools.logs.xadd("logs:access", {"event": orjson.dumps(ev).decode()})
    await container.logs.ensure_group()
    await container.logs.process_once(block_ms=100)  # the app's background consumer may already have taken it
    for _ in range(20):
        r = await client.get(f"/api/v1/companies/{w['cid']}/api-keys/{kid}/logs", headers=master)
        if r.status_code == 200 and r.json()["items"]:
            break
        await asyncio.sleep(0.2)
    assert r.json()["items"][0]["tool"] == "run_select"
    # the same event is in the >= 1 year S3 audit lake, under the caller's company partition only
    lake = container.logs.audit
    async with lake._client() as c:
        listed = await c.list_objects_v2(Bucket=lake.s.s3_bucket, Prefix=f"audit/access_logs/tenant={w['cid']}/")
    assert any(o["Key"].endswith(".parquet") for o in listed.get("Contents", []))
    r = await client.get(f"/api/v1/companies/{w['cid']}/logs", headers=master)
    assert any(i["key_id"] == kid for i in r.json()["items"])
    # another company's admin cannot read these logs
    assert (await client.get(f"/api/v1/companies/{w['cid']}/api-keys/{kid}/logs", headers=master)).status_code == 200
    other = uid("lg-")
    await client.post("/api/v1/companies", json={"company_id": other, "name": "Lg"}, headers=master)
    r = await client.get(f"/api/v1/companies/{other}/api-keys/{kid}/logs", headers=master)
    assert r.status_code == 404


async def test_suspended_admin_or_company_loses_console_access_immediately(client, master, world):
    w = world
    assert (await client.get("/api/v1/auth/me", headers=w["admin_hdr"])).status_code == 200
    await client.patch(f"/api/v1/companies/{w['cid']}/users/{w['admin']['user_id']}", json={"status": "suspended"}, headers=master)
    assert (await client.get("/api/v1/auth/me", headers=w["admin_hdr"])).status_code == 401
    await client.patch(f"/api/v1/companies/{w['cid']}/users/{w['admin']['user_id']}", json={"status": "active"}, headers=master)
    assert (await client.get("/api/v1/auth/me", headers=w["admin_hdr"])).status_code == 200
    await client.patch(f"/api/v1/companies/{w['cid']}", json={"status": "suspended"}, headers=master)
    assert (await client.get("/api/v1/auth/me", headers=w["admin_hdr"])).status_code == 401
    await client.patch(f"/api/v1/companies/{w['cid']}", json={"status": "active"}, headers=master)
    assert (await client.get("/api/v1/auth/me", headers=w["admin_hdr"])).status_code == 200


async def test_rate_limit_field_roundtrips_to_redis(client, master, world, container):
    w = world
    issued = (await issue_key(client, master, w, rate_limit_per_minute=5)).json()
    kid, raw = issued["key"]["key_id"], issued["raw_key"]
    assert issued["key"]["rate_limit_per_minute"] == 5
    assert (await _redis(container, apikey_key(hash_api_key(raw))))["rate_limit_per_minute"] == 5
    r = await client.patch(f"/api/v1/companies/{w['cid']}/api-keys/{kid}", json={"rate_limit_per_minute": 0}, headers=master)
    assert r.status_code == 422  # ge=1
    r = await client.patch(f"/api/v1/companies/{w['cid']}/api-keys/{kid}", json={"clear_rate_limit": True}, headers=master)
    assert r.status_code == 200 and r.json()["rate_limit_per_minute"] is None
    assert (await _redis(container, apikey_key(hash_api_key(raw))))["rate_limit_per_minute"] is None


async def test_server_connection_encrypted_and_masked(client, master, container):
    dsn = "postgresql://ro_user:s3cret@pg-sample:5432/hacker"
    r = await client.post("/api/v1/servers", json={"server_type": "postgres", "name": "pg-hacker", "dsn": dsn}, headers=master)
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["has_connection"] is True and out["connection_hint"] == "postgresql://ro_user@pg-sample:5432/hacker"
    assert "s3cret" not in r.text
    rec = await _redis(container, server_key(out["server_id"]))
    assert rec["dsn_enc"] and "s3cret" not in rec["dsn_enc"]
    from app.core.security import decrypt_secret

    assert decrypt_secret(rec["dsn_enc"]) == dsn
    # wrong scheme for the type
    r = await client.post("/api/v1/servers", json={"server_type": "mongo", "name": "x", "dsn": dsn}, headers=master)
    assert r.status_code == 422
    # clear
    r = await client.patch(f"/api/v1/servers/{out['server_id']}", json={"clear_dsn": True}, headers=master)
    assert r.json()["has_connection"] is False and (await _redis(container, server_key(out["server_id"])))["dsn_enc"] is None


async def test_tenant_identifiers_are_engine_agnostic(client, master, world):
    w = world
    # quoted-identifier friendly names are fine for postgres
    r = await client.patch(f"/api/v1/companies/{w['cid']}/bindings/{w['server']['server_id']}", json={"tenant": {"mode": "schema", "schema_name": "Weird-Tenant 2"}}, headers=master)
    assert r.status_code == 200, r.text
    for bad in ('a"b', "a;b", "a`b", "a\\b", ""):
        r = await client.patch(f"/api/v1/companies/{w['cid']}/bindings/{w['server']['server_id']}", json={"tenant": {"mode": "schema", "schema_name": bad}}, headers=master)
        assert r.status_code == 422, bad
    # mongo database-name rules
    ms = (await client.post("/api/v1/servers", json={"server_type": "mongo", "name": "m"}, headers=master)).json()
    other = uid("mg-")
    await client.post("/api/v1/companies", json={"company_id": other, "name": "m"}, headers=master)
    assert (await client.post(f"/api/v1/companies/{other}/bindings", json={"server_id": ms["server_id"], "tenant": {"mode": "database", "database": "bad.name"}}, headers=master)).status_code == 422
    assert (await client.post(f"/api/v1/companies/{other}/bindings", json={"server_id": ms["server_id"], "tenant": {"mode": "database", "database": "Good-Name_1"}}, headers=master)).status_code == 201
    # clickhouse column mode may pin the database
    cs = (await client.post("/api/v1/servers", json={"server_type": "clickhouse", "name": "c"}, headers=master)).json()
    r = await client.post(f"/api/v1/companies/{other}/bindings", json={"server_id": cs["server_id"], "tenant": {"mode": "column", "column": "company_id", "value": "x", "database": "weird_db"}}, headers=master)
    assert r.status_code == 201 and r.json()["tenant"]["database"] == "weird_db"


async def test_redis_flush_is_self_healed(client, master, world, container):
    """A Redis restart without persistence must not leave MCP servers denying everything until the
    periodic reconciler runs: the outbox loop notices the missing epoch marker and rebuilds at once."""
    w = world
    raw = (await issue_key(client, master, w)).json()["raw_key"]
    key = apikey_key(hash_api_key(raw))
    assert await container.pools.auth.exists("meta:epoch")
    await container.pools.auth.flushdb()  # simulate restart / failover to an empty node
    assert await _redis(container, key) is None
    assert await container.outbox.heal_if_flushed() is True
    assert (await _redis(container, key))["status"] == "active"
    assert (await _redis(container, company_key(w["cid"])))["status"] == "active"
    assert await container.outbox.heal_if_flushed() is False  # marker restored: no rebuild on the next tick


async def test_key_scope_change_reproves_access(client, master, world, container):
    w = world
    issued = (await issue_key(client, master, w)).json()
    kid, raw = issued["key"]["key_id"], issued["raw_key"]
    s2 = (await client.post("/api/v1/servers", json={"server_type": "postgres", "name": f"pgs-{w['cid']}"}, headers=master)).json()
    path = f"/api/v1/companies/{w['cid']}/api-keys/{kid}"
    # widening to a server without access is refused by the transaction guard
    assert (await client.patch(path, json={"server_ids": [w["server"]["server_id"], s2["server_id"]]}, headers=master)).status_code == 409
    await client.post(f"/api/v1/companies/{w['cid']}/bindings", json={"server_id": s2["server_id"], "tenant": {"mode": "schema", "schema_name": "acme"}}, headers=master)
    assert (await grant(client, master, w["cid"], w["member"]["user_id"], s2["server_id"])).status_code == 201
    r = await client.patch(path, json={"server_ids": [w["server"]["server_id"], s2["server_id"]]}, headers=master)
    assert r.status_code == 200 and len(r.json()["server_ids"]) == 2
    r = await client.patch(path, json={"scope_all": True}, headers=master)
    assert r.status_code == 200 and r.json()["server_ids"] is None
    assert (await _redis(container, apikey_key(hash_api_key(raw))))["server_ids"] is None
    res = (await client.post("/api/v1/ops/resolve", json={"raw_key": raw}, headers=master)).json()
    assert res["servers"] == {w["server"]["server_id"]: "active", s2["server_id"]: "active"}


async def test_user_suspend_denies_every_server(client, master, world):
    w = world
    raw = (await issue_key(client, master, w, server_ids=None)).json()["raw_key"]
    await client.patch(f"/api/v1/companies/{w['cid']}/users/{w['member']['user_id']}", json={"status": "suspended"}, headers=master)
    assert (await client.post("/api/v1/ops/resolve", json={"raw_key": raw}, headers=master)).json()["effective"] == "denied:user:suspended"
    await client.patch(f"/api/v1/companies/{w['cid']}/users/{w['member']['user_id']}", json={"status": "active"}, headers=master)


async def test_legacy_keys_migrate_without_widening(client, master, world, container):
    """Pre-hierarchy key items become an access + a key scoped to their one server; a second legacy key
    that disagrees with the resulting access is suspended, never merged. Re-running is a no-op."""
    from app.infra.dynamo.repository import serialize
    from app.services.migrate import migrate_legacy_keys

    w = world
    sid = w["server"]["server_id"]
    u = (await client.post(f"/api/v1/companies/{w['cid']}/users", json={"email": f"lg-{w['cid']}@ex.com", "name": "L", "role": "member"}, headers=master)).json()

    def legacy(kid, h, perms, tools):
        return {"PK": f"COMPANY#{w['cid']}", "SK": f"APIKEY#{kid}", "gsi1pk": f"SERVER#{sid}", "gsi1sk": f"APIKEY#{w['cid']}#{kid}",
                "gsi2pk": f"USER#{u['user_id']}", "gsi2sk": f"APIKEY#{kid}", "entity_type": "APIKEY", "key_id": kid,
                "company_id": w["cid"], "user_id": u["user_id"], "server_id": sid, "server_type": "postgres", "key_hash": h,
                "key_prefix": "mcpk_legacy", "name": kid, "permissions": perms, "allowed_tools": tools, "status": "active",
                "version": 1, "created_at": "2026-01-01T00:00:00+00:00", "updated_at": "2026-01-01T00:00:00+00:00"}

    k1, k2 = uid("key_a"), uid("key_b")
    async with container.repo.factory.client() as c:
        await c.put_item(TableName=container.repo.table, Item=serialize(legacy(k1, "1" * 64, ["read"], ["run_select"])))
        await c.put_item(TableName=container.repo.table, Item=serialize(legacy(k2, "2" * 64, ["read", "delete"], ["run_select", "run_delete"])))
    rep = await migrate_legacy_keys(container.repo, container.sync)
    assert rep.errors == [] and rep.migrated >= 2
    a = await container.access.get(w["cid"], u["user_id"], sid)
    keys = {k: await container.apikeys.get(w["cid"], k) for k in (k1, k2)}
    first, second = sorted(keys.values(), key=lambda k: k.key_id in rep.suspended_conflicts)
    assert second.status == "suspended" and second.key_id in rep.suspended_conflicts
    assert first.status == "active" and first.server_ids == [sid]
    # the access equals exactly one of the legacy permission sets — never their union
    assert a.permissions in ({"read"}, {"read", "delete"})
    assert (await _redis(container, apikey_key("1" * 64)))["server_ids"] == [sid]
    again = await migrate_legacy_keys(container.repo, container.sync)
    assert again.migrated == 0


async def test_access_index_for_gateway_discovery(client, master, world, container):
    w = world
    r = container.pools.auth
    key = access_index_key(w["cid"], w["member"]["user_id"])
    assert await r.smembers(key) == {w["server"]["server_id"]}
    assert (await _redis(container, server_key(w["server"]["server_id"])))["name"] == w["server"]["name"]
    await r.delete(key)
    await r.sadd(access_index_key(w["cid"], "ghost"), "srv_x")  # stray index with no access behind it
    rep = (await client.post("/api/v1/ops/reconcile", headers=master)).json()
    assert rep["errors"] == []
    assert await r.smembers(key) == {w["server"]["server_id"]}
    assert not await r.exists(access_index_key(w["cid"], "ghost"))


async def test_migration_never_activates_grants_from_inactive_keys(client, master, world, container):
    """security review #4: a suspended (e.g. leaked) or expired legacy key yields a SUSPENDED access; with two
    disagreeing keys the active one sets the grant regardless of scan order; inactive parents -> held grant."""
    from app.infra.dynamo.repository import serialize
    from app.services.migrate import migrate_legacy_keys

    w = world
    sid = w["server"]["server_id"]

    def legacy(uid_, kid, h, perms, tools, status="active", created="2026-01-01T00:00:00+00:00", expires=None):
        it = {"PK": f"COMPANY#{w['cid']}", "SK": f"APIKEY#{kid}", "gsi2pk": f"USER#{uid_}", "gsi2sk": f"APIKEY#{kid}",
              "entity_type": "APIKEY", "key_id": kid, "company_id": w["cid"], "user_id": uid_, "server_id": sid,
              "server_type": "postgres", "key_hash": h, "key_prefix": "mcpk_legacy", "name": kid, "permissions": perms,
              "allowed_tools": tools, "status": status, "version": 1, "created_at": created, "updated_at": created}
        if expires:
            it["expires_at"] = expires
        return it

    async def user(tag):
        return (await client.post(f"/api/v1/companies/{w['cid']}/users", json={"email": f"{tag}-{uid()}@ex.com", "name": tag, "role": "member"}, headers=master)).json()["user_id"]

    u_susp, u_exp, u_pair = await user("susp"), await user("exp"), await user("pair")
    wide, narrow = (["read", "export"], ["run_select", "export"]), (["read"], ["run_select"])
    items = [
        legacy(u_susp, uid("key_s"), uid("h").ljust(64, "a")[:64], *wide, status="suspended"),
        legacy(u_exp, uid("key_e"), uid("h").ljust(64, "b")[:64], *wide, expires="2026-01-02T00:00:00+00:00"),
        # the suspended, wider key is OLDER (would be scanned first by created_at) — the active one must still win
        legacy(u_pair, uid("key_p"), uid("h").ljust(64, "c")[:64], *wide, status="suspended", created="2025-01-01T00:00:00+00:00"),
        legacy(u_pair, uid("key_q"), uid("h").ljust(64, "d")[:64], *narrow),
    ]
    async with container.repo.factory.client() as c:
        for it in items:
            await c.put_item(TableName=container.repo.table, Item=serialize(it))
    rep = await migrate_legacy_keys(container.repo, container.sync)
    assert rep.errors == []
    assert (await container.access.get(w["cid"], u_susp, sid)).status == "suspended"
    assert (await container.access.get(w["cid"], u_exp, sid)).status == "suspended"
    pair = await container.access.get(w["cid"], u_pair, sid)
    assert pair.status == "active" and pair.permissions == {"read"}  # the active key's (narrow) set
    assert (await container.apikeys.get(w["cid"], items[3]["key_id"])).status == "active"

    # parents inactive: suspended user -> the grant is created held, the migration still completes
    u_held = await user("held")
    await client.patch(f"/api/v1/companies/{w['cid']}/users/{u_held}", json={"status": "suspended"}, headers=master)
    kid = uid("key_h")
    async with container.repo.factory.client() as c:
        await c.put_item(TableName=container.repo.table, Item=serialize(legacy(u_held, kid, uid("h").ljust(64, "e")[:64], *narrow)))
    rep = await migrate_legacy_keys(container.repo, container.sync)
    assert rep.errors == [] and (await container.access.get(w["cid"], u_held, sid)).status == "suspended"
    assert (await container.apikeys.get(w["cid"], kid)).server_ids == [sid]


async def test_company_branding_roundtrip_and_projection(client, master, world, container):
    import base64 as b64

    w = world
    png = b64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 100).decode()
    path = f"/api/v1/companies/{w['cid']}/branding"
    r = await client.put(path, json={"primary_color": "#1a73e8", "logo_base64": png, "report_footer": "Confidential"}, headers=w["admin_hdr"])
    assert r.status_code == 200, r.text  # company admins may brand their own reports
    assert r.json()["branding"]["primary_color"] == "#1A73E8"
    rec = orjson.loads(await container.pools.auth.get(f"branding:{w['cid']}"))
    assert rec["logo_base64"] == png and rec["report_footer"] == "Confidential"
    assert "branding" not in (await _redis(container, company_key(w["cid"])))  # the authz record stays small
    for bad in ({"primary_color": "red"}, {"logo_base64": b64.b64encode(b"GIF89a....").decode()}, {"logo_base64": "@@notb64@@"},
                {"logo_base64": b64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 200_001).decode()}):
        assert (await client.put(path, json=bad, headers=w["admin_hdr"])).status_code == 422, bad
    other = uid("br-")
    await client.post("/api/v1/companies", json={"company_id": other, "name": "Other"}, headers=master)
    assert (await client.put(f"/api/v1/companies/{other}/branding", json={"primary_color": "#000000"}, headers=w["admin_hdr"])).status_code == 403
    await container.pools.auth.delete(f"branding:{w['cid']}")
    assert (await client.post("/api/v1/ops/reconcile", headers=master)).json()["errors"] == []
    assert await container.pools.auth.exists(f"branding:{w['cid']}")  # reconciler rebuilds it
    assert (await client.delete(path, headers=w["admin_hdr"])).status_code == 200
    assert not await container.pools.auth.exists(f"branding:{w['cid']}")
