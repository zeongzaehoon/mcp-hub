"""End-to-end against the compose stack (gateway :19000 -> backend /api/v1, mcp-mongo /mcp-mongo). Skips when down."""

import asyncio
import os
import uuid

import httpx
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.exceptions import ToolError
from mcp.shared.exceptions import MCPError

BACKEND = os.environ.get("E2E_BACKEND", "http://localhost:19000/api/v1")
MCP = os.environ.get("E2E_MCP", "http://localhost:19000/mcp-mongo")
pytestmark = pytest.mark.e2e
ALL_TOOLS = ["list_collections", "find", "aggregate", "insert_many", "update_many", "delete_many"]


def _up() -> bool:
    try:
        return httpx.get(BACKEND.replace("/api/v1", "/healthz"), timeout=2).status_code == 200
    except Exception:  # noqa: BLE001
        return False


if not _up():
    pytest.skip("compose stack not running", allow_module_level=True)


def grant_and_issue(c, cid, user_id, server_id, name, permissions, allowed_tools, **key_over):
    """company -> user -> server: grant the user access to the server, then issue a key scoped to it.
    Returns the failing grant response if the grant is refused (e.g. 422 for tools outside the catalog)."""
    r = c.post(f"/companies/{cid}/users/{user_id}/access", json={"server_id": server_id, "permissions": permissions, "allowed_tools": allowed_tools})
    if r.status_code != 201:
        return r
    return c.post(f"/companies/{cid}/api-keys", json={"user_id": user_id, "server_ids": [server_id], "name": name, **key_over})



def mcp_client(raw_key: str | None) -> Client:
    headers = {"Authorization": f"Bearer {raw_key}"} if raw_key else {}
    return Client(StreamableHttpTransport(MCP, headers=headers))


async def _call(raw, tool, **args):
    async with mcp_client(raw) as cl:
        r = await cl.call_tool(tool, args)
        return r.structured_content or r.data


@pytest.fixture(scope="module")
def world():
    with httpx.Client(base_url=BACKEND, timeout=10) as c:
        tok = c.post("/auth/login", json={"email": "master@example.com", "password": "master-dev-password-123"}).json()["access_token"]
        c.headers["Authorization"] = f"Bearer {tok}"
        srv = c.get("/servers", params={"server_type": "mongo"}).json()["items"]
        server = srv[0] if srv else c.post("/servers", json={"server_type": "mongo", "name": "mcp-mongo", "endpoint": MCP}).json()
        out = {"server": server, "hdr": dict(c.headers)}
        for tenant in ("acme", "globex"):
            cid = f"{tenant}-mg-{uuid.uuid4().hex[:6]}"
            assert c.post("/companies", json={"company_id": cid, "name": tenant}).status_code == 201
            u = c.post(f"/companies/{cid}/users", json={"email": f"m-{cid}@ex.com", "name": "M", "role": "member"}).json()
            r = c.post(f"/companies/{cid}/bindings", json={"server_id": server["server_id"], "tenant": {"mode": "database", "database": f"{tenant}_mg"}})
            assert r.status_code == 201, r.text
            ro = grant_and_issue(c, cid, u["user_id"], server["server_id"], "ro", ["read"], ["list_collections", "find", "aggregate"]).json()
            u_rw = c.post(f"/companies/{cid}/users", json={"email": f"rw-{cid}@ex.com", "name": "RW", "role": "member"}).json()
            rw = grant_and_issue(c, cid, u_rw["user_id"], server["server_id"], "rw", ["read", "create", "update", "delete"], ALL_TOOLS).json()
            out[tenant] = {"cid": cid, "ro": ro["raw_key"], "rw": rw["raw_key"], "key_id": ro["key"]["key_id"]}
        return out


async def test_only_database_mode_accepted(world):
    with httpx.Client(base_url=BACKEND, headers=world["hdr"], timeout=10) as c:
        cid = f"bad-mg-{uuid.uuid4().hex[:6]}"
        c.post("/companies", json={"company_id": cid, "name": "bad"})
        for tenant in ({"mode": "schema", "schema_name": "x"}, {"mode": "column", "column": "c", "value": "v"}):
            assert c.post(f"/companies/{cid}/bindings", json={"server_id": world["server"]["server_id"], "tenant": tenant}).status_code == 422


async def test_isolation_and_tool_filtering(world):
    acme = await _call(world["acme"]["ro"], "find", collection="customers", projection={"_id": 0, "name": 1}, sort={"name": 1})
    globex = await _call(world["globex"]["ro"], "find", collection="customers", projection={"_id": 0, "name": 1})
    assert [r["name"] for r in acme["rows"]] == ["Acme Alice", "Acme Bob"]
    assert [r["name"] for r in globex["rows"]] == ["Globex Gina"]
    assert {r["name"] for r in (await _call(world["acme"]["ro"], "list_collections"))["rows"]} == {"customers", "events"}
    async with mcp_client(world["acme"]["ro"]) as cl:
        assert sorted(t.name for t in await cl.list_tools()) == ["aggregate", "find", "list_collections"]
    with pytest.raises(ToolError, match="not permitted"):
        await _call(world["acme"]["ro"], "delete_many", collection="customers", filter={"plan": "free"})


async def test_dangerous_operations_rejected(world):
    raw = world["acme"]["ro"]
    with pytest.raises(ToolError, match="rejected"):
        await _call(raw, "find", collection="customers", filter={"$where": "this.plan == 'pro'"})
    with pytest.raises(ToolError, match="rejected"):
        await _call(raw, "aggregate", collection="customers", pipeline=[{"$match": {}}, {"$merge": {"into": {"db": "globex_mg", "coll": "customers"}}}])
    with pytest.raises(ToolError, match="rejected"):
        await _call(raw, "find", collection="system.users")
    # $lookup can only reach the tenant's own database — globex's collection is simply not there
    res = await _call(raw, "aggregate", collection="customers", pipeline=[{"$lookup": {"from": "customers", "localField": "plan", "foreignField": "plan", "as": "same"}}, {"$project": {"_id": 0, "n": {"$size": "$same"}}}])
    assert all(r["n"] <= 2 for r in res["rows"])


async def test_write_cycle_and_extended_json(world):
    rw = world["acme"]["rw"]
    ins = await _call(rw, "insert_many", collection="customers", documents=[{"name": "Acme Carol", "email": f"carol-{uuid.uuid4().hex[:4]}@acme.example", "plan": "free", "spend": 0, "joined": {"$date": "2026-09-22T00:00:00Z"}}])
    assert ins["inserted_count"] == 1
    oid = ins["inserted_ids"][0]  # {"$oid": ...}
    upd = await _call(rw, "update_many", collection="customers", filter={"_id": oid}, update={"$set": {"plan": "pro"}})
    assert upd["modified_count"] == 1
    with pytest.raises(ToolError, match="rejected"):
        await _call(rw, "update_many", collection="customers", filter={}, update={"$set": {"plan": "free"}})
    with pytest.raises(ToolError, match="rejected"):
        await _call(rw, "update_many", collection="customers", filter={"_id": oid}, update={"plan": "x"})
    found = await _call(rw, "find", collection="customers", filter={"_id": oid})
    assert found["rows"][0]["plan"] == "pro" and "$date" in found["rows"][0]["joined"]
    assert (await _call(rw, "delete_many", collection="customers", filter={"_id": oid}))["deleted_count"] == 1
    # globex unaffected throughout
    assert (await _call(world["globex"]["ro"], "aggregate", collection="customers", pipeline=[{"$count": "n"}]))["rows"] == [{"n": 1}]


async def test_wrong_server_type_key_refused(world):
    with httpx.Client(base_url=BACKEND, headers=world["hdr"], timeout=10) as c:
        others = [s for s in c.get("/servers").json()["items"] if s["server_type"] != "mongo"]
        if not others:
            pytest.skip("no other server registered")
        cid = world["acme"]["cid"]
        srv = others[0]
        tenant = {"mode": "schema", "schema_name": "acme"} if srv["server_type"] in ("postgres", "hacker") else {"mode": "column", "column": "company_id", "value": "ftg"}
        c.post(f"/companies/{cid}/bindings", json={"server_id": srv["server_id"], "tenant": tenant})
        u = c.get(f"/companies/{cid}/users").json()["items"][0]
        k = grant_and_issue(c, cid, u["user_id"], srv["server_id"], "x", ["read"], ["list_tables"]).json()
    with pytest.raises(MCPError, match="different server type"):
        await _call(k["raw_key"], "list_collections")


async def test_logs_recorded(world):
    w = world["acme"]
    await _call(w["ro"], "list_collections")
    with httpx.Client(base_url=BACKEND, headers=world["hdr"], timeout=10) as c:
        for _ in range(20):
            items = c.get(f"/companies/{w['cid']}/api-keys/{w['key_id']}/logs").json()["items"]
            if any(i["tool"] == "list_collections" and i["status"] == "ok" and i["server_type"] == "mongo" for i in items):
                return
            await asyncio.sleep(0.5)
    raise AssertionError("log not found")
