"""Datasource-agnostic behaviour for MongoDB: BSON types, odd collection names, views, server-record URI."""

import os
import uuid

import httpx
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

BACKEND = os.environ.get("E2E_BACKEND", "http://localhost:19000/api/v1")
MCP = os.environ.get("E2E_MCP", "http://localhost:19000/mcp-mongo")
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
    return grant_and_issue(c, cid, u["user_id"], server["server_id"], "k", ["read"], ["list_collections", "find", "aggregate"]).json()["raw_key"]


@pytest.fixture(scope="module")
def api():
    with httpx.Client(base_url=BACKEND, timeout=10) as c:
        tok = c.post("/auth/login", json={"email": "master@example.com", "password": "master-dev-password-123"}).json()["access_token"]
        c.headers["Authorization"] = f"Bearer {tok}"
        yield c


@pytest.fixture(scope="module")
def default_server(api):
    items = [s for s in api.get("/servers", params={"server_type": "mongo"}).json()["items"] if not s["has_connection"]]
    return items[0] if items else api.post("/servers", json={"server_type": "mongo", "name": "mcp-mongo"}).json()


async def test_bson_types_and_odd_names(api, default_server):
    raw = _issue(api, default_server, {"mode": "database", "database": "weird_mg"})
    cols = await _call(raw, "list_collections")
    assert {c["name"]: c["type"] for c in cols["rows"]} == {"Mixed Case": "collection", "V iew": "view", "Empty": "collection"}
    res = await _call(raw, "find", collection="Mixed Case", sort={"dec": -1})
    assert res["row_count"] == 2
    row = res["rows"][0]
    assert row["dec"] == {"$numberDecimal": "12345.678901"} and row["big"] == 9007199254740993
    assert row["bin"]["$binary"]["base64"] == "3q2+7w==" and row["at"] == {"$date": "2026-09-01T10:00:00Z"}
    assert row["nested"]["arr"] == [1, "two", {"three": None}] and row["nested"]["dotted.key"] == 1
    assert "$regularExpression" in row["re"] and "$uuid" in row["uuid"] or "$binary" in row["uuid"]
    assert (await _call(raw, "find", collection="V iew"))["row_count"] == 2
    assert (await _call(raw, "find", collection="Empty"))["rows"] == []
    agg = await _call(raw, "aggregate", collection="Mixed Case", pipeline=[{"$group": {"_id": None, "n": {"$sum": 1}}}])
    assert agg["rows"] == [{"_id": None, "n": 2}]


async def test_server_record_uri(api):
    srv = api.post("/servers", json={"server_type": "mongo", "name": f"mg-dsn-{uuid.uuid4().hex[:4]}", "dsn": "mongodb://root:mongo-dev-password@mongo:27017/?authSource=admin"}).json()
    assert srv["connection_hint"] == "mongodb://root@mongo:27017/"
    raw = _issue(api, srv, {"mode": "database", "database": "acme_mg"})
    assert (await _call(raw, "find", collection="customers", projection={"_id": 0, "name": 1}, sort={"name": 1}))["row_count"] == 2


async def test_transform_and_export_flatten_documents(api, default_server):
    cid = f"gen-{uuid.uuid4().hex[:6]}"
    api.post("/companies", json={"company_id": cid, "name": cid})
    u = api.post(f"/companies/{cid}/users", json={"email": f"m-{cid}@ex.com", "name": "M", "role": "member"}).json()
    api.post(f"/companies/{cid}/bindings", json={"server_id": default_server["server_id"], "tenant": {"mode": "database", "database": "weird_mg"}})
    raw = grant_and_issue(api, cid, u["user_id"], default_server["server_id"], "k", ["read", "export"], ["transform", "export"]).json()["raw_key"]
    src = {"collection": "Mixed Case", "sort": {"dec": -1}}
    out = await _call(raw, "transform", source=src, pipeline=[{"op": "select", "columns": ["dec", "big", "nested", "at"]}, {"op": "cast", "column": "dec", "to": "float"}])
    assert out["rows"][0]["dec"] == 12345.678901 and out["rows"][0]["big"] == "9007199254740993" and out["rows"][0]["nested"].startswith("{")
    import base64, csv, io
    ex = await _call(raw, "export", source={"collection": "Mixed Case", "pipeline": [{"$match": {"dec": {"$gt": 0}}}]}, format="csv", delivery="inline")
    rows = list(csv.DictReader(io.StringIO(base64.b64decode(ex["content_base64"]).decode("utf-8-sig"))))
    assert len(rows) == 1 and rows[0]["dec"] == "12345.678901"
    from fastmcp.exceptions import ToolError
    with pytest.raises(ToolError, match="rejected"):
        await _call(raw, "export", source={"collection": "Mixed Case", "pipeline": [{"$out": "x"}]})


def test_auth_source_cannot_inject_uri_options(monkeypatch):
    """security review 2 #3: a database name with & / = must not become extra URI options."""
    from pymongo.uri_parser import parse_uri

    from mcp_mongo import client as mc
    from mcpcluster_common.context import TenantContext
    from mcpcluster_common.model import TenantSpec
    from mcpcluster_common.secrets import fernet

    enc = fernet(mc.SETTINGS).encrypt(b"pw").decode()
    ctx = TenantContext(company_id="c", user_id="u", key_id="k", server_id="s", server_type="mongo", permissions=frozenset({"read"}),
                        allowed_tools=frozenset({"find"}), binding_version=1,
                        tenant=TenantSpec(mode="database", database="acme&tls=false&authMechanism=PLAIN", credentials={"username": "u", "password_enc": enc}))
    monkeypatch.setattr(mc, "BASE_URI", "mongodb://root:x@mongo:27017/?authSource=admin&tls=true")
    uri, extra = mc.connect_args(ctx)
    opts = parse_uri(uri)["options"]
    assert "authsource" not in {k.lower() for k in opts} and opts.get("tls") is True and "authmechanism" not in {k.lower() for k in opts}
    assert extra == {"authSource": "acme&tls=false&authMechanism=PLAIN"}  # a literal database name, not options
