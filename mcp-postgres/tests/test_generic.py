"""Datasource-agnostic behaviour: unknown identifiers/types, and one process serving several
datasources through server-record DSNs. Skips when the stack is down."""

import os
import uuid

import httpx
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.exceptions import ToolError

BACKEND = os.environ.get("E2E_BACKEND", "http://localhost:19000/api/v1")
MCP = os.environ.get("E2E_MCP", "http://localhost:19000/mcp-postgres")
pytestmark = pytest.mark.e2e


def _up() -> bool:
    try:
        return httpx.get(BACKEND.replace("/api/v1", "/healthz"), timeout=2).status_code == 200
    except Exception:  # noqa: BLE001
        return False


if not _up():
    pytest.skip("compose stack not running", allow_module_level=True)


async def _call(raw, tool, **args):
    async with Client(StreamableHttpTransport(MCP, headers={"Authorization": f"Bearer {raw}"})) as cl:
        r = await cl.call_tool(tool, args)
        return r.structured_content or r.data


def _issue(c, server, tenant, tools=("list_tables", "describe_table", "run_select"), perms=("read",)):
    cid = f"gen-{uuid.uuid4().hex[:6]}"
    assert c.post("/companies", json={"company_id": cid, "name": cid}).status_code == 201
    u = c.post(f"/companies/{cid}/users", json={"email": f"m-{cid}@ex.com", "name": "M", "role": "member"}).json()
    r = c.post(f"/companies/{cid}/bindings", json={"server_id": server["server_id"], "tenant": tenant})
    assert r.status_code == 201, r.text
    r = c.post(f"/companies/{cid}/users/{u['user_id']}/access", json={"server_id": server["server_id"], "permissions": list(perms), "allowed_tools": list(tools)})
    assert r.status_code == 201, r.text
    k = c.post(f"/companies/{cid}/api-keys", json={"user_id": u["user_id"], "server_ids": [server["server_id"]], "name": "k"}).json()
    return k["raw_key"]


@pytest.fixture(scope="module")
def api():
    with httpx.Client(base_url=BACKEND, timeout=10) as c:
        tok = c.post("/auth/login", json={"email": "master@example.com", "password": "master-dev-password-123"}).json()["access_token"]
        c.headers["Authorization"] = f"Bearer {tok}"
        yield c


@pytest.fixture(scope="module")
def default_server(api):
    items = api.get("/servers", params={"server_type": "postgres"}).json()["items"]
    plain = [s for s in items if not s["has_connection"]]
    return plain[0] if plain else api.post("/servers", json={"server_type": "postgres", "name": "mcp-postgres"}).json()


async def test_unknown_identifiers_and_types_are_handled(api, default_server):
    raw = _issue(api, default_server, {"mode": "schema", "schema_name": "Weird-Tenant"})
    tables = await _call(raw, "list_tables")
    assert {t["table_name"] for t in tables["rows"]} == {"Mixed Case", "V iew", "Empty"}
    cols = await _call(raw, "describe_table", table="Mixed Case")
    assert {c["column_name"] for c in cols["rows"]} >= {"id", "amount", "tags", "meta", "span", "raw", "dur", "ip", "feeling", "select", "Spaced Col"}
    res = await _call(raw, "run_select", sql='select * from "Mixed Case" order by amount desc')
    assert res["row_count"] == 2 and '"Weird-Tenant"."Mixed Case"' in res["sql"]
    row = res["rows"][0]
    assert isinstance(row["id"], str) and len(row["id"]) == 36          # uuid -> str
    assert row["amount"] == "12345.678901"                               # non-integral numeric keeps precision
    assert row["big"] == "9007199254740993"                              # > 2^53 -> str, never rounded
    assert row["tags"] == ["a", "b"] and row["meta"] == {"k": [1, {"n": None}]}
    assert row["span"] == {"lower": 1, "upper": 10, "lower_inc": True, "upper_inc": False}
    assert row["raw"] == {"$base64": "3q2+7w=="} and row["ip"] in ("10.1.2.3", "[IP]") and row["feeling"] == "happy"  # [IP] when REDACT_PII=1
    assert row["select"] == "kw" and row["Spaced Col"] == "sp" and row["nothing"] is None
    assert res["rows"][1]["span"] == {"empty": True} and res["rows"][1]["amount"] == "0.500000"  # numeric(20,6) scale is preserved verbatim
    assert (await _call(raw, "run_select", sql='select * from "Empty"'))["rows"] == []
    assert (await _call(raw, "run_select", sql="select 'a;b' as s"))["rows"] == [{"s": "a;b"}]
    with pytest.raises(ToolError, match="rejected"):
        await _call(raw, "run_select", sql='select * from acme.customers')  # other schema still off-limits


async def test_one_process_serves_many_datasources_via_server_dsn(api):
    # a second postgres *server* pointing at a different database, registered purely through the API
    srv = api.post("/servers", json={"server_type": "postgres", "name": f"pg-hacker-{uuid.uuid4().hex[:4]}",
                                     "dsn": "postgresql://mcp:mcp-dev-password@pg-sample:5432/hacker"}).json()
    assert srv["has_connection"] and srv["connection_hint"] == "postgresql://mcp@pg-sample:5432/hacker"
    raw = _issue(api, srv, {"mode": "schema", "schema_name": "acme"})
    tables = await _call(raw, "list_tables")
    assert {t["table_name"] for t in tables["rows"]} == {"reports", "hackers"}   # hacker db, not sample db
    res = await _call(raw, "run_select", sql="select count(*) as n from reports")
    assert res["rows"] == [{"n": 2}]
    # rotating the DSN to an unreachable host rebuilds the pool and fails closed
    api.patch(f"/servers/{srv['server_id']}", json={"dsn": "postgresql://mcp:mcp-dev-password@nowhere.invalid:5432/hacker"})
    with pytest.raises(Exception):  # noqa: B017 - driver error surfaces as tool error
        await _call(raw, "list_tables")


async def test_dsn_scheme_must_match_server_type(api):
    r = api.post("/servers", json={"server_type": "postgres", "name": "bad", "dsn": "mongodb://x:y@host/db"})
    assert r.status_code == 422


async def test_transform_and_export_are_engine_agnostic(api, default_server):
    raw = _issue(api, default_server, {"mode": "schema", "schema_name": "Weird-Tenant"},
                 tools=("run_select", "transform", "export"), perms=("read", "export"))
    src = {"sql": 'select id, amount, big, tags, meta, at, "Spaced Col", "select" from "Mixed Case"'}
    out = await _call(raw, "transform", source=src, pipeline=[
        {"op": "rename", "mapping": {"Spaced Col": "spaced", "select": "keyword"}},
        {"op": "cast", "column": "amount", "to": "float"},
        {"op": "filter", "column": "amount", "cmp": "gt", "value": 1},
        {"op": "select", "columns": ["amount", "big", "tags", "spaced", "keyword"]},
    ])
    assert out["row_count"] == 1 and out["rows"][0]["amount"] == 12345.678901 and out["rows"][0]["big"] == "9007199254740993"
    assert '"Weird-Tenant"."Mixed Case"' in out["sql"]
    # export inline csv
    import base64, csv, io
    ex = await _call(raw, "export", source=src, format="csv", delivery="inline", filename="weird")
    assert ex["delivery"] == "inline" and ex["filename"] == "weird.csv" and ex["rows"] == 2
    text = base64.b64decode(ex["content_base64"]).decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(text)))
    assert rows[0]["amount"] == "12345.678901" and rows[0]["meta"].startswith("{") and rows[0]["big"] == "9007199254740993"
    # export xlsx via link (MinIO) — downloadable from the host with the presigned URL
    ex = await _call(raw, "export", source=src, format="xlsx", delivery="link", pipeline=[{"op": "select", "columns": ["amount", "Spaced Col"]}])
    assert ex["delivery"] == "link" and ex["url"].startswith(os.environ.get("E2E_S3_PUBLIC", "http://localhost:9000") + "/") and ex["object_key"].startswith("mcp/exports/tenant=")
    body = httpx.get(ex["url"], timeout=10)
    assert body.status_code == 200 and len(body.content) == ex["bytes"] and body.content[:2] == b"PK"
    # read-only key cannot export (permission), and writes cannot hide in the source
    ro = _issue(api, default_server, {"mode": "schema", "schema_name": "Weird-Tenant"}, tools=("run_select", "transform"))
    with pytest.raises(ToolError, match="not permitted"):
        await _call(ro, "export", source=src)
    with pytest.raises(ToolError, match="rejected"):
        await _call(raw, "export", source={"sql": 'delete from "Mixed Case"'})


async def test_compute_runs_rust_kernels_over_any_source(api, default_server):
    raw = _issue(api, default_server, {"mode": "schema", "schema_name": "Weird-Tenant"}, tools=("compute",))
    src = {"sql": 'select amount, big, feeling, at from "Mixed Case"'}
    d = await _call(raw, "compute", source=src, op={"op": "describe", "column": "amount"})
    assert d["engine"] == "rust" and d["transfer"] == "copy" and d["result"]["count"] == 2 and d["result"]["max"] == 12345.678901 and d["result"]["min"] == 0.5
    d2 = await _call(raw, "compute", source=src, op={"op": "describe", "column": "amount"}, transfer="arrow")
    assert d2["transfer"] == "arrow" and d2["result"] == d["result"]  # zero-copy hand-off, identical numbers
    g = await _call(raw, "compute", source=src, op={"op": "group_stats", "key": "feeling", "value": "amount"})
    assert {r["key"]: r["count"] for r in g["result"]} == {"happy": 1, "<null>": 1}
    tb = await _call(raw, "compute", source=src, op={"op": "time_bucket", "time": "at", "bucket": "1d"})
    assert sum(r["count"] for r in tb["result"]) == 2 and tb["result"][0]["bucket"].endswith("T00:00:00+00:00")
    with pytest.raises(ToolError, match="rejected"):
        await _call(raw, "compute", source=src, op={"op": "eval", "expr": "1+1"})
    with pytest.raises(ToolError, match="rejected"):
        await _call(raw, "compute", source={"sql": 'update "Mixed Case" set amount = 0'}, op={"op": "describe", "column": "amount"})


async def test_per_company_database_role_via_binding_credentials(api, default_server):
    """A binding may carry its own DB role: queries run as that role (defence in depth below the SQL guard), a
    password full of URL metacharacters works, and a wrong password fails closed — never the server's own login."""
    import asyncpg

    role = f"t_ro_{uuid.uuid4().hex[:8]}"
    pw = "p@ss:w/rd#?%&x"
    admin = await asyncpg.connect(os.environ.get("E2E_PG_ADMIN_DSN", "postgresql://mcp:mcp-dev-password@localhost:55432/sample"))
    try:
        await admin.execute(f"CREATE ROLE {role} LOGIN PASSWORD $$" + pw + "$$")
        await admin.execute(f"GRANT USAGE ON SCHEMA acme TO {role}; GRANT SELECT ON ALL TABLES IN SCHEMA acme TO {role}")
        cid = f"role-{uuid.uuid4().hex[:6]}"
        assert api.post("/companies", json={"company_id": cid, "name": cid}).status_code == 201
        u = api.post(f"/companies/{cid}/users", json={"email": f"r-{cid}@ex.com", "name": "R", "role": "member"}).json()
        tenant = {"mode": "schema", "schema_name": "acme", "credentials": {"username": role, "password": pw}}
        r = api.post(f"/companies/{cid}/bindings", json={"server_id": default_server["server_id"], "tenant": tenant})
        assert r.status_code == 201, r.text
        assert r.json()["tenant"]["credentials"]["password_enc"] == "***"  # never echoed
        assert api.post(f"/companies/{cid}/users/{u['user_id']}/access", json={"server_id": default_server["server_id"], "permissions": ["read"], "allowed_tools": ["run_select"]}).status_code == 201
        raw = api.post(f"/companies/{cid}/api-keys", json={"user_id": u["user_id"], "server_ids": [default_server["server_id"]], "name": "k"}).json()["raw_key"]

        res = await _call(raw, "run_select", sql="select current_user as who, count(*) as n from customers")
        assert res["rows"] == [{"who": role, "n": 2}]

        tenant["credentials"]["password"] = "wrong"
        assert api.patch(f"/companies/{cid}/bindings/{default_server['server_id']}", json={"tenant": tenant}).status_code == 200
        with pytest.raises(ToolError):
            await _call(raw, "run_select", sql="select current_user as who")
    finally:
        await admin.execute(f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename = '{role}'")
        await admin.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA acme FROM {role}; REVOKE USAGE ON SCHEMA acme FROM {role}; DROP ROLE IF EXISTS {role}")
        await admin.close()
