"""Datasource-agnostic behaviour for ClickHouse: exotic types, quoted identifiers, column-mode
database pinning, and a server-record DSN."""

import os
import uuid

import httpx
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.exceptions import ToolError

BACKEND = os.environ.get("E2E_BACKEND", "http://localhost:19000/api/v1")
MCP = os.environ.get("E2E_MCP", "http://localhost:19000/mcp-clickhouse")
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


def _issue(c, server, tenant):
    cid = f"gen-{uuid.uuid4().hex[:6]}"
    assert c.post("/companies", json={"company_id": cid, "name": cid}).status_code == 201
    u = c.post(f"/companies/{cid}/users", json={"email": f"m-{cid}@ex.com", "name": "M", "role": "member"}).json()
    r = c.post(f"/companies/{cid}/bindings", json={"server_id": server["server_id"], "tenant": tenant})
    assert r.status_code == 201, r.text
    return grant_and_issue(c, cid, u["user_id"], server["server_id"], "k", ["read"], ["list_tables", "describe_table", "run_select", "aggregate"]).json()["raw_key"]


@pytest.fixture(scope="module")
def api():
    with httpx.Client(base_url=BACKEND, timeout=10) as c:
        tok = c.post("/auth/login", json={"email": "master@example.com", "password": "master-dev-password-123"}).json()["access_token"]
        c.headers["Authorization"] = f"Bearer {tok}"
        yield c


@pytest.fixture(scope="module")
def default_server(api):
    items = [s for s in api.get("/servers", params={"server_type": "clickhouse"}).json()["items"] if not s["has_connection"]]
    return items[0] if items else api.post("/servers", json={"server_type": "clickhouse", "name": "mcp-clickhouse"}).json()


async def test_exotic_types_and_quoted_identifiers(api, default_server):
    raw = _issue(api, default_server, {"mode": "database", "database": "weird_db"})
    assert {t["name"] for t in (await _call(raw, "list_tables"))["rows"]} == {"Mixed Case", "Empty"}
    res = await _call(raw, "run_select", sql="SELECT * FROM `Mixed Case` ORDER BY dec DESC")
    assert res["row_count"] == 3
    row = res["rows"][0]
    assert len(row["id"]) == 36 and row["ip"] in ("10.0.0.1", "[IP]") and row["m"] == {"a": 1, "b": 2}  # [IP] when REDACT_PII=1
    assert row["t"] == ["t", 1] and row["arr"] == ["x", None] and row["dec"] == "1234.5678"
    assert row["ts"].startswith("2026-09-01T10:00:00.123") and row["e"] == "x" and row["f"] == 1.5 and row["d"] == "2026-09-01"
    assert row["select"] == "kw"
    assert res["rows"][2]["f"] is None and res["rows"][2]["m"] == {} and res["rows"][2]["dec"] == "-0.0001"  # dec DESC: 1234.5678, 99, -0.0001
    assert (await _call(raw, "run_select", sql="SELECT * FROM `Empty`"))["rows"] == []
    assert (await _call(raw, "run_select", sql="SELECT 'a;b' AS s"))["rows"] == [{"s": "a;b"}]
    agg = await _call(raw, "aggregate", sql="SELECT e, dec FROM `Mixed Case`", group_by=["e"], metrics={"n": "count(*)"})
    assert {r["e"]: r["n"] for r in agg["rows"]} == {"x": 2, "y": 1}


async def test_column_mode_with_pinned_database(api, default_server):
    acme = _issue(api, default_server, {"mode": "column", "column": "company_id", "value": "acme", "database": "weird_db"})
    globex = _issue(api, default_server, {"mode": "column", "column": "company_id", "value": "globex", "database": "weird_db"})
    assert (await _call(acme, "run_select", sql="SELECT count() AS n FROM `Mixed Case`"))["rows"] == [{"n": 2}]
    assert (await _call(globex, "run_select", sql="SELECT count() AS n FROM `Mixed Case`"))["rows"] == [{"n": 1}]
    assert {t["name"] for t in (await _call(acme, "list_tables"))["rows"]} == {"Mixed Case", "Empty"}
    with pytest.raises(ToolError, match="rejected"):
        await _call(acme, "run_select", sql="SELECT * FROM biz.revenue")  # the env default db is now foreign


async def test_server_record_dsn(api):
    srv = api.post("/servers", json={"server_type": "clickhouse", "name": f"ch-dsn-{uuid.uuid4().hex[:4]}", "dsn": "clickhouse://default:ch-dev-password@clickhouse:8123/weird_db"}).json()
    assert srv["connection_hint"] == "clickhouse://default@clickhouse:8123/weird_db"
    raw = _issue(api, srv, {"mode": "column", "column": "company_id", "value": "globex"})  # db comes from the DSN
    assert (await _call(raw, "run_select", sql="SELECT count() AS n FROM `Mixed Case`"))["rows"] == [{"n": 1}]


async def test_transform_and_export(api, default_server):
    cid = f"gen-{uuid.uuid4().hex[:6]}"
    api.post("/companies", json={"company_id": cid, "name": cid})
    u = api.post(f"/companies/{cid}/users", json={"email": f"m-{cid}@ex.com", "name": "M", "role": "member"}).json()
    api.post(f"/companies/{cid}/bindings", json={"server_id": default_server["server_id"], "tenant": {"mode": "column", "column": "company_id", "value": "acme", "database": "weird_db"}})
    raw = grant_and_issue(api, cid, u["user_id"], default_server["server_id"], "k", ["read", "export"], ["transform", "export"]).json()["raw_key"]
    src = {"sql": "SELECT e, dec, m, ts FROM `Mixed Case`"}
    out = await _call(raw, "transform", source=src, pipeline=[{"op": "aggregate", "group_by": ["e"], "metrics": {"n": "count(*)", "total": "sum(dec)"}}])
    assert {r["e"]: r["n"] for r in out["rows"]} == {"x": 1, "y": 1}  # column mode: only acme rows reach the pipeline
    import base64, csv, io
    ex = await _call(raw, "export", source=src, format="csv", delivery="inline")
    rows = list(csv.DictReader(io.StringIO(base64.b64decode(ex["content_base64"]).decode("utf-8-sig"))))
    assert len(rows) == 2 and rows[0]["m"].startswith("{")
