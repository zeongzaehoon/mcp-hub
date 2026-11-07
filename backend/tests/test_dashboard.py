"""Console dashboards: company/global counts, 24h metric buckets fed by the log consumer, the recent-event feed,
MCP health snapshots (mocked http) and the artifact index (metadata only). All read-only; authz unchanged."""

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import orjson

from app.core.config import get_settings
from app.infra.s3.audit import s3_client
from app.services.health import HealthChecker
from tests.conftest import issue_key, uid


async def _feed(container, events):
    for ev in events:
        await container.pools.logs.xadd("logs:access", {"event": orjson.dumps(ev).decode()})
    await container.logs.ensure_group()
    for _ in range(20):  # the app's own consumer may race us; wait until the stream is drained either way
        await container.logs.process_once(block_ms=100)
        if await container.pools.logs.xlen("logs:access") == 0:
            break
        await asyncio.sleep(0.1)


async def test_company_stats_and_metrics(client, master, world, container):
    w = world
    issued = (await issue_key(client, master, w)).json()
    kid = issued["key"]["key_id"]
    # a key expiring in 3 days shows up in the expiring list; one far away does not
    soon = (datetime.now(UTC) + timedelta(days=3)).isoformat()
    far = (datetime.now(UTC) + timedelta(days=90)).isoformat()
    for name, exp in (("soon", soon), ("far", far)):
        r = await client.post(f"/api/v1/companies/{w['cid']}/api-keys", json={"user_id": w["member"]["user_id"], "server_ids": None, "name": name, "expires_at": exp}, headers=master)
        assert r.status_code == 201, r.text
    base = {"key_id": kid, "company_id": w["cid"], "user_id": w["member"]["user_id"], "server_id": w["server"]["server_id"], "server_type": "postgres", "tool": "run_select", "args_summary": "{}"}
    now = datetime.now(UTC)
    evs = [{**base, "ts": now.isoformat(), "status": "ok", "duration_ms": 12}, {**base, "ts": now.isoformat(), "status": "ok", "duration_ms": 230},
           {**base, "ts": now.isoformat(), "status": "denied", "duration_ms": 1, "error": "no access"},
           {**base, "ts": (now - timedelta(hours=30)).isoformat(), "status": "ok", "duration_ms": 5}]  # outside the 24h window
    await _feed(container, evs)

    r = await client.get(f"/api/v1/companies/{w['cid']}/stats", headers=w["admin_hdr"])
    assert r.status_code == 200, r.text
    st = r.json()
    assert st["counts"]["users"]["active"] == 2 and st["counts"]["bindings"]["active"] == 1 and st["counts"]["api_keys"]["active"] == 3
    assert st["bindings_per_server"] == {w["server"]["server_id"]: 1}
    assert st["keys_expiring"]["count"] == 1 and st["keys_expiring"]["items"][0]["name"] == "soon"
    assert "companies" not in st["counts"]
    m = st["calls_24h"]
    assert m["calls"] == 3 and m["ok"] == 2 and m["denied"] == 1 and abs(m["denied_rate"] - 1 / 3) < 1e-6
    assert m["p50_ms"] == 25 and m["p99_ms"] == 250 and m["max_ms"] == 230 and len(m["series"]) == 24
    assert sum(pt["n"] for pt in m["series"]) == 3

    r = await client.get(f"/api/v1/servers/{w['server']['server_id']}/metrics", headers=w["admin_hdr"])
    assert r.status_code == 200 and r.json()["calls"] == 3
    r = await client.get(f"/api/v1/companies/{w['cid']}/metrics", headers=w["admin_hdr"])
    assert r.status_code == 200 and r.json()["denied"] == 1
    # another company's admin is refused; master sees the global feed with these events
    other = uid("st-")
    assert (await client.post("/api/v1/companies", json={"company_id": other, "name": "O"}, headers=master)).status_code == 201
    adm = (await client.post(f"/api/v1/companies/{other}/users", json={"email": f"a@{other}.example", "name": "A", "role": "admin", "password": "adminpassword1"}, headers=master)).json()
    tok = (await client.post("/api/v1/auth/login", json={"email": adm["email"], "password": "adminpassword1"})).json()["access_token"]
    assert (await client.get(f"/api/v1/companies/{w['cid']}/stats", headers={"Authorization": f"Bearer {tok}"})).status_code == 403
    assert (await client.get("/api/v1/ops/events", headers={"Authorization": f"Bearer {tok}"})).status_code == 403
    r = await client.get("/api/v1/ops/events", headers=master, params={"limit": 10})
    assert r.status_code == 200
    assert any(e["key_id"] == kid and e["status"] == "denied" for e in r.json())
    assert r.json()[0]["key_id"] == kid and r.json()[0]["duration_ms"] == 5  # most recently consumed first


async def test_global_stats_cached_and_refreshed(client, master, world, container):
    r = await client.get("/api/v1/ops/stats", headers=master, params={"refresh": "1"})
    assert r.status_code == 200, r.text
    st = r.json()
    assert st["counts"]["companies"]["active"] >= 1 and st["counts"]["servers"]["active"] >= 1
    assert st["bindings_per_server"].get(world["server"]["server_id"]) == 1
    assert st["calls_24h"]["hours"] == 24
    # cached: a new company is not counted until the next refresh
    other = uid("gs-")
    assert (await client.post("/api/v1/companies", json={"company_id": other, "name": "O"}, headers=master)).status_code == 201
    cached = (await client.get("/api/v1/ops/stats", headers=master)).json()
    assert cached["computed_at"] == st["computed_at"]
    fresh = (await client.get("/api/v1/ops/stats", headers=master, params={"refresh": "1"})).json()
    assert fresh["counts"]["companies"]["active"] == st["counts"]["companies"]["active"] + 1
    assert (await client.get("/api/v1/ops/stats", headers=world["admin_hdr"])).status_code == 403


async def test_health_snapshot_from_internal_urls(client, master, world, container):
    s = get_settings()
    seen = []

    async def handler(req: httpx.Request) -> httpx.Response:
        seen.append(str(req.url))
        if req.url.host == "mcp-postgres":
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(503, text="down")

    s2 = s.model_copy(update={"mcp_internal_postgres": "http://mcp-postgres:9001/", "mcp_internal_gateway": "http://mcp-gateway:9000"})
    hc = HealthChecker(container.pools.hot, s2, httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    res = await hc.run_once()
    assert res["postgres"]["ok"] is True and res["gateway"]["ok"] is False and res["gateway"]["error"] == "http 503"
    assert set(seen) == {"http://mcp-postgres:9001/healthz", "http://mcp-gateway:9000/healthz"}  # never the server's public endpoint
    snap = await hc.snapshot()
    assert snap["postgres"]["ok"] is True and snap["gateway"]["ok"] is False and snap["postgres"]["latency_ms"] >= 0
    # the app's own checker has no targets in tests -> servers carry health=null and /ops/health is empty
    r = await client.get(f"/api/v1/servers/{world['server']['server_id']}", headers=master)
    assert r.status_code == 200 and r.json()["health"] is None
    assert (await client.get("/api/v1/ops/health", headers=master)).json() == []
    await hc.aclose()


async def test_artifact_index_lists_metadata_only(client, master, world, container):
    s = get_settings()
    w = world
    now = datetime.now(UTC)
    rows = [("art1", w["member"]["user_id"], (now + timedelta(days=7)).isoformat(), "table"), ("art2", w["admin"]["user_id"], (now + timedelta(days=7)).isoformat(), "chart"),
            ("old1", w["member"]["user_id"], (now - timedelta(days=1)).isoformat(), "table")]
    async with s3_client(s) as c:
        try:
            await c.create_bucket(Bucket=s.s3_bucket, CreateBucketConfiguration={"LocationConstraint": s.aws_region})
        except Exception:  # noqa: BLE001, S110 - exists
            pass
        for aid, uid_, exp, kind in rows:
            meta = {"artifact_id": aid, "kind": kind, "company_id": w["cid"], "user_id": uid_, "server_id": w["server"]["server_id"], "server_type": "postgres",
                    "key_id": "k", "title": f"T {aid}", "created_at": now.isoformat(), "expires_at": exp, "sha256": "0" * 64, "bytes": "123", "sources": w["server"]["server_id"]}
            await c.put_object(Bucket=s.s3_bucket, Key=f"mcp/artifacts/tenant={w['cid']}/user={uid_}/{aid}.json", Body=b'{"payload":{"secret":1}}', Metadata=meta)
        # another company's artifact must never show up
        await c.put_object(Bucket=s.s3_bucket, Key=f"mcp/artifacts/tenant=other-{w['cid']}/user=u/x.json", Body=b"{}", Metadata={"artifact_id": "x", "kind": "table", "company_id": "other", "user_id": "u", "server_id": "s", "server_type": "postgres", "created_at": now.isoformat(), "expires_at": (now + timedelta(days=1)).isoformat(), "bytes": "1"})
    r = await client.get(f"/api/v1/companies/{w['cid']}/artifacts", headers=w["admin_hdr"])
    assert r.status_code == 200, r.text
    items = r.json()
    assert {i["artifact_id"] for i in items} == {"art1", "art2"} and all(i["expired"] is False for i in items)
    assert all(set(i) == {"artifact_id", "kind", "title", "server_id", "server_type", "sources", "user_id", "created_at", "expires_at", "bytes", "expired"} for i in items)
    r = await client.get(f"/api/v1/companies/{w['cid']}/artifacts", headers=w["admin_hdr"], params={"include_expired": "1", "user_id": w["member"]["user_id"]})
    assert {i["artifact_id"] for i in r.json()} == {"art1", "old1"}
    assert (await client.get(f"/api/v1/companies/{w['cid']}/artifacts", headers=w["admin_hdr"], params={"user_id": "../x"})).json() == []
    other = uid("ar-")
    assert (await client.post("/api/v1/companies", json={"company_id": other, "name": "O"}, headers=master)).status_code == 201
    assert (await client.get(f"/api/v1/companies/{other}/artifacts", headers=master)).json() == []
