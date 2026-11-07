"""End-to-end against the compose stack (gateway :19000 -> backend /api/v1, mcp-hacker /mcp-hacker). Skips when down."""

import asyncio
import os
import uuid

import httpx
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.exceptions import ToolError

BACKEND = os.environ.get("E2E_BACKEND", "http://localhost:19000/api/v1")
MCP = os.environ.get("E2E_MCP", "http://localhost:19000/mcp-hacker")
pytestmark = pytest.mark.e2e


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



async def _call(raw, tool, **args):
    async with Client(StreamableHttpTransport(MCP, headers={"Authorization": f"Bearer {raw}"})) as cl:
        r = await cl.call_tool(tool, args)
        return r.structured_content or r.data


@pytest.fixture(scope="module")
def world():
    with httpx.Client(base_url=BACKEND, timeout=10) as c:
        tok = c.post("/auth/login", json={"email": "master@example.com", "password": "master-dev-password-123"}).json()["access_token"]
        c.headers["Authorization"] = f"Bearer {tok}"
        srv = c.get("/servers", params={"server_type": "hacker"}).json()["items"]
        server = srv[0] if srv else c.post("/servers", json={"server_type": "hacker", "name": "mcp-hacker", "endpoint": MCP}).json()
        out = {"server": server, "hdr": dict(c.headers)}
        for tenant in ("acme", "globex"):
            cid = f"{tenant}-hk-{uuid.uuid4().hex[:6]}"
            assert c.post("/companies", json={"company_id": cid, "name": tenant}).status_code == 201
            u = c.post(f"/companies/{cid}/users", json={"email": f"m-{cid}@ex.com", "name": "M", "role": "member"}).json()
            assert c.post(f"/companies/{cid}/bindings", json={"server_id": server["server_id"], "tenant": {"mode": "schema", "schema_name": tenant}}).status_code == 201
            k = grant_and_issue(c, cid, u["user_id"], server["server_id"], "ro", ["read"], ["list_tables", "describe_table", "run_select"]).json()
            out[tenant] = {"cid": cid, "raw": k["raw_key"], "key_id": k["key"]["key_id"]}
        return out


async def test_write_permissions_cannot_be_granted_for_hacker(world):
    with httpx.Client(base_url=BACKEND, headers=world["hdr"], timeout=10) as c:
        cid = world["acme"]["cid"]
        u = c.get(f"/companies/{cid}/users").json()["items"][0]
        r = grant_and_issue(c, cid, u["user_id"], world["server"]["server_id"], "w", ["read", "delete"], ["run_delete"])
        assert r.status_code == 422  # run_delete is not in the hacker catalog


async def test_program_isolation(world):
    acme = await _call(world["acme"]["raw"], "run_select", sql="select title from reports order by id")
    globex = await _call(world["globex"]["raw"], "run_select", sql="select title from reports order by id")
    assert [r["title"] for r in acme["rows"]] == ["IDOR on /api/invoices", "Reflected XSS in search"]
    assert [r["title"] for r in globex["rows"]] == ["SSRF via webhook URL"]
    assert {r["table_name"] for r in (await _call(world["acme"]["raw"], "list_tables"))["rows"]} == {"reports", "hackers"}
    with pytest.raises(ToolError, match="rejected"):
        await _call(world["acme"]["raw"], "run_select", sql="select * from globex.reports")


async def test_read_only_even_if_guard_were_bypassed(world):
    # the guard rejects writes outright…
    with pytest.raises(ToolError, match="rejected"):
        await _call(world["acme"]["raw"], "run_select", sql="update reports set status = 'x'")
    # …and a write hidden in a function call is stopped by the read-only connection/transaction
    with pytest.raises(ToolError, match="read-only"):
        await _call(world["acme"]["raw"], "run_select", sql="select setval('reports_id_seq', 999)")  # pg_* helpers are guard-blocked; setval itself is not


async def test_join_scoped_to_program(world):
    res = await _call(world["acme"]["raw"], "run_select", sql="select h.handle, sum(r.bounty_usd) total from reports r join hackers h on h.handle = r.hacker_handle group by h.handle order by total desc")
    assert [r["handle"] for r in res["rows"]] == ["h4x0r", "nullbyte"]


async def test_logs_recorded(world):
    w = world["acme"]
    await _call(w["raw"], "list_tables")
    with httpx.Client(base_url=BACKEND, headers=world["hdr"], timeout=10) as c:
        for _ in range(20):
            items = c.get(f"/companies/{w['cid']}/api-keys/{w['key_id']}/logs").json()["items"]
            if any(i["tool"] == "list_tables" and i["status"] == "ok" and i["server_type"] == "hacker" for i in items):
                return
            await asyncio.sleep(0.5)
    raise AssertionError("log not found")
