"""mcp-hacker shares the postgres engine: verify the weird fixture and a server-record DSN pointing at
a *different* database than the process default."""

import os
import uuid

import httpx
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

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


async def test_hacker_server_with_own_dsn_reads_the_sample_db_weird_schema():
    with httpx.Client(base_url=BACKEND, timeout=10) as c:
        tok = c.post("/auth/login", json={"email": "master@example.com", "password": "master-dev-password-123"}).json()["access_token"]
        c.headers["Authorization"] = f"Bearer {tok}"
        srv = c.post("/servers", json={"server_type": "hacker", "name": f"hk-dsn-{uuid.uuid4().hex[:4]}", "dsn": "postgresql://mcp:mcp-dev-password@pg-sample:5432/sample"}).json()
        cid = f"gen-{uuid.uuid4().hex[:6]}"
        c.post("/companies", json={"company_id": cid, "name": cid})
        u = c.post(f"/companies/{cid}/users", json={"email": f"m-{cid}@ex.com", "name": "M", "role": "member"}).json()
        assert c.post(f"/companies/{cid}/bindings", json={"server_id": srv["server_id"], "tenant": {"mode": "schema", "schema_name": "Weird-Tenant"}}).status_code == 201
        raw = grant_and_issue(c, cid, u["user_id"], srv["server_id"], "k", ["read"], ["list_tables", "run_select"]).json()["raw_key"]
    assert {t["table_name"] for t in (await _call(raw, "list_tables"))["rows"]} == {"Mixed Case", "V iew", "Empty"}
    res = await _call(raw, "run_select", sql='select amount, "Spaced Col" from "Mixed Case" where "select" = \'kw\'')
    assert res["rows"] == [{"amount": "12345.678901", "Spaced Col": "sp"}]


async def test_compute_on_program_data():
    with httpx.Client(base_url=BACKEND, timeout=10) as c:
        tok = c.post("/auth/login", json={"email": "master@example.com", "password": "master-dev-password-123"}).json()["access_token"]
        c.headers["Authorization"] = f"Bearer {tok}"
        srv = [s for s in c.get("/servers", params={"server_type": "hacker"}).json()["items"] if not s["has_connection"]][0]
        cid = f"gen-{uuid.uuid4().hex[:6]}"
        c.post("/companies", json={"company_id": cid, "name": cid})
        u = c.post(f"/companies/{cid}/users", json={"email": f"m-{cid}@ex.com", "name": "M", "role": "member"}).json()
        c.post(f"/companies/{cid}/bindings", json={"server_id": srv["server_id"], "tenant": {"mode": "schema", "schema_name": "acme"}})
        raw = grant_and_issue(c, cid, u["user_id"], srv["server_id"], "k", ["read"], ["compute"]).json()["raw_key"]
    res = await _call(raw, "compute", source={"sql": "select severity, bounty_usd from reports"}, op={"op": "group_stats", "key": "severity", "value": "bounty_usd"})
    assert res["engine"] == "rust" and {r["key"]: r["sum"] for r in res["result"]} == {"high": 1500.0, "medium": 300.0}
    top = await _call(raw, "compute", source={"sql": "select hacker_handle from reports"}, op={"op": "top_k", "column": "hacker_handle", "k": 1})
    assert top["result"] == [{"key": "h4x0r", "count": 1}] or top["result"][0]["count"] == 1
