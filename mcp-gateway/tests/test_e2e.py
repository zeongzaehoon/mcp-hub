"""Gateway end-to-end against the compose stack (nginx :19000 -> backend /api/v1, mcp-gateway /mcp). Skips when down."""

import os
import uuid

import httpx
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.exceptions import ToolError
from mcp.shared.exceptions import MCPError

BACKEND = os.environ.get("E2E_BACKEND", "http://localhost:19000/api/v1")
GW = os.environ.get("E2E_GATEWAY", "http://localhost:19000/mcp")
pytestmark = pytest.mark.e2e


def _up() -> bool:
    try:
        return httpx.get(BACKEND.replace("/api/v1", "/healthz"), timeout=2).status_code == 200 and httpx.get(GW, timeout=2).status_code < 500
    except Exception:  # noqa: BLE001
        return False


if not _up():
    pytest.skip("compose stack not running", allow_module_level=True)


def gw(raw: str | None) -> Client:
    return Client(StreamableHttpTransport(GW, headers={"Authorization": f"Bearer {raw}"} if raw else {}))


async def names(raw) -> list[str]:
    async with gw(raw) as cl:
        return sorted(t.name for t in await cl.list_tools())


@pytest.fixture(scope="module")
def world():
    with httpx.Client(base_url=BACKEND, timeout=20) as c:
        tok = c.post("/auth/login", json={"email": "master@example.com", "password": "master-dev-password-123"}).json()["access_token"]
        c.headers["Authorization"] = f"Bearer {tok}"
        tag = uuid.uuid4().hex[:6]
        pg = c.post("/servers", json={"server_type": "postgres", "name": f"Sales PG {tag}"}).json()
        chs = c.get("/servers", params={"server_type": "clickhouse", "status": "active"}).json()["items"]
        ch = chs[0] if chs else c.post("/servers", json={"server_type": "clickhouse", "name": "mcp-clickhouse"}).json()
        c.post("/ops/reconcile")  # older server records gain `name` in their projection
        out = {"hdr": dict(c.headers), "pg": pg, "ch": ch}
        for tenant, pg_schema, ch_unit in (("acme", "acme", "ftg"), ("globex", "globex", "ftg-academy")):
            cid = f"gw-{tenant}-{tag}"
            assert c.post("/companies", json={"company_id": cid, "name": tenant}).status_code == 201
            assert c.post(f"/companies/{cid}/bindings", json={"server_id": pg["server_id"], "tenant": {"mode": "schema", "schema_name": pg_schema}}).status_code == 201
            assert c.post(f"/companies/{cid}/bindings", json={"server_id": ch["server_id"], "tenant": {"mode": "column", "column": "company_id", "value": ch_unit}}).status_code == 201
            u = c.post(f"/companies/{cid}/users", json={"email": f"gw-{cid}@ex.com", "name": "G", "role": "member"}).json()
            for sid, tools in ((pg["server_id"], ["run_select", "list_tables", "list_artifacts"]), (ch["server_id"], ["run_select", "list_artifacts"])):
                r = c.post(f"/companies/{cid}/users/{u['user_id']}/access", json={"server_id": sid, "permissions": ["read"], "allowed_tools": tools})
                assert r.status_code == 201, r.text
            all_key = c.post(f"/companies/{cid}/api-keys", json={"user_id": u["user_id"], "server_ids": None, "name": "all"}).json()["raw_key"]
            pg_key = c.post(f"/companies/{cid}/api-keys", json={"user_id": u["user_id"], "server_ids": [pg["server_id"]], "name": "pg"}).json()["raw_key"]
            out[tenant] = {"cid": cid, "user": u, "all": all_key, "pg": pg_key}
        return out


def _alias(world, which):
    from mcp_gateway.server import assign_aliases

    s = world[which]
    return assign_aliases([(s["server_id"], s["server_type"], s["name"])])[s["server_id"]]


async def test_one_key_sees_both_servers_namespaced(world):
    pa, ca = _alias(world, "pg"), _alias(world, "ch")
    async with gw(world["acme"]["all"]) as cl:  # the analyst rule reaches the client through initialize
        assert "never calculate" in (cl.instructions or "")
    got = await names(world["acme"]["all"])
    assert f"{pa}__run_select" in got and f"{pa}__list_tables" in got and f"{ca}__run_select" in got
    assert got.count("list_artifacts") == 1  # shared tool exposed once, unprefixed
    assert not any(n.startswith(f"{ca}__list_tables") for n in got)  # only what that server's access allows


async def test_calls_run_on_the_right_server_and_tenant(world):
    pa, ca = _alias(world, "pg"), _alias(world, "ch")
    async with gw(world["acme"]["all"]) as cl:
        r = (await cl.call_tool(f"{pa}__run_select", {"sql": "select name from customers order by id"})).structured_content
        assert [x["name"] for x in r["rows"]] == ["Acme Alice", "Acme Bob"]
        r = (await cl.call_tool(f"{ca}__run_select", {"sql": "SELECT DISTINCT company_id FROM revenue"})).structured_content
        assert [x["company_id"] for x in r["rows"]] == ["ftg"]
    async with gw(world["globex"]["all"]) as cl:
        r = (await cl.call_tool(f"{pa}__run_select", {"sql": "select name from customers order by id"})).structured_content
        assert [x["name"] for x in r["rows"]] == ["Globex Gina", "Globex Greg"]


async def test_scope_narrows_what_the_gateway_offers(world):
    pa, ca = _alias(world, "pg"), _alias(world, "ch")
    got = await names(world["acme"]["pg"])
    assert f"{pa}__run_select" in got and not any(n.startswith(f"{ca}__") for n in got)
    async with gw(world["acme"]["pg"]) as cl:
        with pytest.raises(ToolError, match="Unknown tool|not found"):
            await cl.call_tool(f"{ca}__run_select", {"sql": "SELECT 1"})


async def test_suspended_access_disappears_and_cannot_be_called(world):
    ca = _alias(world, "ch")
    w = world["globex"]
    with httpx.Client(base_url=BACKEND, headers=world["hdr"], timeout=10) as c:
        path = f"/companies/{w['cid']}/users/{w['user']['user_id']}/access/{world['ch']['server_id']}"
        assert c.patch(path, json={"status": "suspended"}).status_code == 200
        try:
            assert not any(n.startswith(f"{ca}__") for n in await names(w["all"]))
            async with gw(w["all"]) as cl:
                with pytest.raises(ToolError):
                    await cl.call_tool(f"{ca}__run_select", {"sql": "SELECT 1"})
        finally:
            assert c.patch(path, json={"status": "active"}).status_code == 200


async def test_unknown_or_missing_key_is_unauthorized():
    with pytest.raises(MCPError, match="unauthorized: unknown api key"):
        await names("mcpk_nope")
    # no credentials at all: an OAuth challenge (HTTP 401 + WWW-Authenticate) so connectors can start the flow
    r = httpx.post(GW, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, timeout=5)
    assert r.status_code == 401 and "resource_metadata=" in r.headers.get("www-authenticate", "")


async def test_calls_are_logged_by_the_upstream(world):
    pa = _alias(world, "pg")
    async with gw(world["acme"]["all"]) as cl:
        await cl.call_tool(f"{pa}__list_tables", {})
    with httpx.Client(base_url=BACKEND, headers=world["hdr"], timeout=10) as c:
        import asyncio

        for _ in range(20):
            items = c.get(f"/companies/{world['acme']['cid']}/logs").json()["items"]
            if any(i["tool"] == "list_tables" and i["server_id"] == world["pg"]["server_id"] for i in items):
                return
            await asyncio.sleep(0.5)
    raise AssertionError("gateway call not visible in the upstream access log")


async def test_pooled_sessions_survive_an_upstream_restart(world):
    import asyncio
    import subprocess

    pa = _alias(world, "pg")
    async with gw(world["acme"]["all"]) as cl:
        await cl.call_tool(f"{pa}__run_select", {"sql": "select 1 as x"})
    subprocess.run(["docker", "compose", "restart", "mcp-postgres"], check=True, capture_output=True)
    for _ in range(40):
        try:
            async with gw(world["acme"]["all"]) as cl:
                r = (await cl.call_tool(f"{pa}__run_select", {"sql": "select 1 as x"})).structured_content
            assert r["rows"] == [{"x": 1}]
            return
        except Exception:  # noqa: BLE001 — upstream still starting
            await asyncio.sleep(0.5)
    raise AssertionError("gateway did not recover after the upstream restarted")
