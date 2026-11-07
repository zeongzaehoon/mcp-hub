"""End-to-end against the running docker-compose stack (gateway :19000 -> backend /api/v1, mcp-postgres /mcp-postgres).
Skipped automatically when the stack is down. Run: uv run pytest -q tests/test_e2e.py -m e2e"""

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
MCP = os.environ.get("E2E_MCP", "http://localhost:19000/mcp-postgres")
pytestmark = pytest.mark.e2e


def _up() -> bool:
    try:
        return httpx.get(BACKEND.replace("/api/v1", "/healthz"), timeout=2).status_code == 200
    except Exception:  # noqa: BLE001
        return False


if not _up():
    pytest.skip("compose stack not running", allow_module_level=True)


def mcp_client(raw_key: str | None, server: str | None = None) -> Client:
    headers = {"Authorization": f"Bearer {raw_key}"} if raw_key else {}
    return Client(StreamableHttpTransport(f"{MCP}?server={server}" if server else MCP, headers=headers))


def member_with_key(c, cid, server_id, name, perms, tools, **key_over):
    """company -> user -> server: one user per permission set, a key scoped to that server."""
    u = c.post(f"/companies/{cid}/users", json={"email": f"{name}-{cid}@ex.com", "name": name, "role": "member"}).json()
    r = c.post(f"/companies/{cid}/users/{u['user_id']}/access", json={"server_id": server_id, "permissions": perms, "allowed_tools": tools})
    assert r.status_code == 201, r.text
    k = c.post(f"/companies/{cid}/api-keys", json={"user_id": u["user_id"], "server_ids": [server_id], "name": name, **key_over}).json()
    return u, k


@pytest.fixture(scope="module")
def world():
    with httpx.Client(base_url=BACKEND, timeout=10) as c:
        tok = c.post("/auth/login", json={"email": "master@example.com", "password": "master-dev-password-123"}).json()["access_token"]
        c.headers["Authorization"] = f"Bearer {tok}"
        srv = c.get("/servers", params={"server_type": "postgres"}).json()["items"]
        server = srv[0] if srv else c.post("/servers", json={"server_type": "postgres", "name": "mcp-postgres", "endpoint": MCP}).json()
        out = {"server": server, "hdr": dict(c.headers)}
        for tenant in ("acme", "globex"):
            cid = f"{tenant}-{uuid.uuid4().hex[:6]}"
            assert c.post("/companies", json={"company_id": cid, "name": tenant}).status_code == 201
            assert c.post(f"/companies/{cid}/bindings", json={"server_id": server["server_id"], "tenant": {"mode": "schema", "schema_name": tenant}}).status_code == 201
            u, ro = member_with_key(c, cid, server["server_id"], "ro", ["read"], ["list_tables", "run_select"])
            _, rw = member_with_key(c, cid, server["server_id"], "rw", ["read", "create", "update", "delete"], ["list_tables", "describe_table", "run_select", "run_insert", "run_update", "run_delete"])
            out[tenant] = {"cid": cid, "user": u, "ro": ro, "rw": rw}
        return out


async def _tools(raw):
    async with mcp_client(raw) as cl:
        return sorted(t.name for t in await cl.list_tools())


async def _call(raw, tool, **args):
    async with mcp_client(raw) as cl:
        r = await cl.call_tool(tool, args)
        return r.structured_content or r.data


async def test_list_tools_filtered_by_key(world):
    assert await _tools(world["acme"]["ro"]["raw_key"]) == ["list_tables", "run_select"]
    assert len(await _tools(world["acme"]["rw"]["raw_key"])) == 6


async def test_unknown_key_denied():
    with pytest.raises(MCPError, match="unauthorized: unknown api key"):
        await _tools("mcpk_deadbeef_nope")
    with pytest.raises(MCPError, match="unauthorized: missing api key"):
        await _tools(None)
    with pytest.raises(MCPError, match="unauthorized"):
        await _call("mcpk_deadbeef_nope", "list_tables")


async def test_tenant_isolation_on_select(world):
    acme = await _call(world["acme"]["ro"]["raw_key"], "run_select", sql="select name from customers order by id")
    globex = await _call(world["globex"]["ro"]["raw_key"], "run_select", sql="select name from customers order by id")
    assert [r["name"] for r in acme["rows"]] == ["Acme Alice", "Acme Bob"]
    assert [r["name"] for r in globex["rows"]] == ["Globex Gina", "Globex Greg"]
    assert '"acme"' in acme["sql"] and '"globex"' in globex["sql"]


async def test_cross_schema_and_catalog_rejected(world):
    raw = world["acme"]["ro"]["raw_key"]
    for sql in ("select * from globex.customers", "select * from pg_catalog.pg_tables", "select * from public.events", "select set_config('search_path','globex',false)"):
        with pytest.raises(ToolError, match="rejected"):
            await _call(raw, "run_select", sql=sql)
    tables = await _call(raw, "list_tables")
    assert {r["table_schema"] for r in tables["rows"]} == {"acme"}


async def test_permission_enforced_on_call(world):
    with pytest.raises(ToolError, match="not permitted"):
        await _call(world["acme"]["ro"]["raw_key"], "run_insert", sql="insert into customers(name,email) values ('x','x@x')")
    # rw key: insert then verify globex unaffected, then clean up
    rw = world["acme"]["rw"]["raw_key"]
    res = await _call(rw, "run_insert", sql="insert into customers(name,email) values ('Acme Carol','carol@acme.example')")
    assert res["affected_rows"] == 1
    assert len((await _call(world["globex"]["ro"]["raw_key"], "run_select", sql="select 1 from customers"))["rows"]) == 2
    res = await _call(rw, "run_delete", sql="delete from customers where name = 'Acme Carol'")
    assert res["affected_rows"] == 1


async def test_suspend_company_denies_immediately(world):
    w = world["globex"]
    with httpx.Client(base_url=BACKEND, headers=world["hdr"], timeout=10) as c:
        assert c.patch(f"/companies/{w['cid']}", json={"status": "suspended"}).status_code == 200
        try:
            with pytest.raises(MCPError, match="company suspended"):
                await _call(w["ro"]["raw_key"], "list_tables")
        finally:
            assert c.patch(f"/companies/{w['cid']}", json={"status": "active"}).status_code == 200
    assert "rows" in await _call(w["ro"]["raw_key"], "list_tables")


async def test_access_logs_land_in_backend(world):
    w = world["acme"]
    await _call(w["ro"]["raw_key"], "list_tables")
    with httpx.Client(base_url=BACKEND, headers=world["hdr"], timeout=10) as c:
        for _ in range(20):
            items = c.get(f"/companies/{w['cid']}/api-keys/{w['ro']['key']['key_id']}/logs").json()["items"]
            if any(i["tool"] == "list_tables" and i["status"] == "ok" for i in items):
                break
            await asyncio.sleep(0.5)
        else:
            raise AssertionError("access log not found")
        assert any(i["status"] == "denied" for i in items), "denied attempts must be logged too"


async def test_rate_limit_enforced_per_key(world):
    w = world["acme"]
    with httpx.Client(base_url=BACKEND, headers=world["hdr"], timeout=10) as c:
        k = c.post(f"/companies/{w['cid']}/api-keys", json={"user_id": w["user"]["user_id"], "server_ids": [world["server"]["server_id"]], "name": "rl", "rate_limit_per_minute": 2}).json()
    await _call(k["raw_key"], "list_tables")
    await _call(k["raw_key"], "list_tables")
    with pytest.raises(MCPError, match="rate limited: 2 calls/minute"):
        await _call(k["raw_key"], "list_tables")
    # a different key of the same company is unaffected
    assert "rows" in await _call(w["ro"]["raw_key"], "list_tables")
    with httpx.Client(base_url=BACKEND, headers=world["hdr"], timeout=10) as c:
        for _ in range(20):
            items = c.get(f"/companies/{w['cid']}/api-keys/{k['key']['key_id']}/logs").json()["items"]
            if any(i["status"] == "denied" and "rate limited" in (i.get("error") or "") for i in items):
                return
            await asyncio.sleep(0.5)
    raise AssertionError("rate-limit denial was not logged")


async def test_pii_redacted_in_results(world):
    res = await _call(world["acme"]["ro"]["raw_key"], "run_select", sql="select name, email from customers order by id")
    assert [r["email"] for r in res["rows"]] == ["[EMAIL]", "[EMAIL]"]
    assert res["rows"][0]["name"] == "Acme Alice"  # non-PII untouched


async def test_multi_server_key_targets_server_by_url(world):
    """One key, two postgres servers of the same user: the URL picks the server, the scope and the
    per-server access decide. Suspending one access leaves the other server working."""
    w = world["acme"]
    with httpx.Client(base_url=BACKEND, headers=world["hdr"], timeout=10) as c:
        s2 = c.post("/servers", json={"server_type": "postgres", "name": f"pg-multi-{uuid.uuid4().hex[:6]}", "endpoint": MCP}).json()
        assert c.post(f"/companies/{w['cid']}/bindings", json={"server_id": s2["server_id"], "tenant": {"mode": "schema", "schema_name": "globex"}}).status_code == 201
        uid_ = w["user"]["user_id"]
        assert c.post(f"/companies/{w['cid']}/users/{uid_}/access", json={"server_id": s2["server_id"], "permissions": ["read"], "allowed_tools": ["run_select"]}).status_code == 201
        k = c.post(f"/companies/{w['cid']}/api-keys", json={"user_id": uid_, "server_ids": None, "name": "multi"}).json()
    raw, s1 = k["raw_key"], world["server"]["server_id"]
    with pytest.raises(MCPError, match=r"\?server="):
        await _tools(raw)  # ambiguous: never guess the server
    async with mcp_client(raw, s1) as cl:
        assert sorted(t.name for t in await cl.list_tools()) == ["list_tables", "run_select"]
        r = await cl.call_tool("run_select", {"sql": "select name from customers order by id"})
        assert [x["name"] for x in (r.structured_content or r.data)["rows"]] == ["Acme Alice", "Acme Bob"]
    async with mcp_client(raw, s2["server_id"]) as cl:
        assert [t.name for t in await cl.list_tools()] == ["run_select"]  # that server's own access
        r = await cl.call_tool("run_select", {"sql": "select name from customers order by id"})
        assert [x["name"] for x in (r.structured_content or r.data)["rows"]] == ["Globex Gina", "Globex Greg"]  # that binding's tenant
    # another user's key cannot reach s2 through the URL
    with pytest.raises(MCPError, match="scope"):
        async with mcp_client(world["acme"]["rw"]["raw_key"], s2["server_id"]) as cl:
            await cl.list_tools()
    with httpx.Client(base_url=BACKEND, headers=world["hdr"], timeout=10) as c:
        assert c.patch(f"/companies/{w['cid']}/users/{uid_}/access/{s2['server_id']}", json={"status": "suspended"}).status_code == 200
    with pytest.raises(MCPError, match="access suspended"):
        async with mcp_client(raw, s2["server_id"]) as cl:
            await cl.list_tools()
    async with mcp_client(raw, s1) as cl:
        assert await cl.list_tools()


async def test_user_suspend_denies_all_of_that_users_keys(world):
    w = world["globex"]
    with httpx.Client(base_url=BACKEND, headers=world["hdr"], timeout=10) as c:
        assert c.patch(f"/companies/{w['cid']}/users/{w['user']['user_id']}", json={"status": "suspended"}).status_code == 200
        try:
            with pytest.raises(MCPError, match="user suspended"):
                await _call(w["ro"]["raw_key"], "list_tables")
            assert "rows" in await _call(w["rw"]["raw_key"], "list_tables")  # a different user is unaffected
        finally:
            assert c.patch(f"/companies/{w['cid']}/users/{w['user']['user_id']}", json={"status": "active"}).status_code == 200


async def test_artifacts_cross_server_follow_the_hierarchy(world):
    """Save on server A (needs export), read back through server B with a key whose scope covers A;
    a key without A in scope, or after A's access is suspended, sees nothing."""
    w = world["acme"]
    s1 = world["server"]["server_id"]
    with httpx.Client(base_url=BACKEND, headers=world["hdr"], timeout=10) as c:
        u = c.post(f"/companies/{w['cid']}/users", json={"email": f"art-{uuid.uuid4().hex[:6]}@ex.com", "name": "Art", "role": "member"}).json()
        s2 = c.post("/servers", json={"server_type": "postgres", "name": f"pg-art-{uuid.uuid4().hex[:6]}", "endpoint": MCP}).json()["server_id"]
        assert c.post(f"/companies/{w['cid']}/bindings", json={"server_id": s2, "tenant": {"mode": "schema", "schema_name": "acme"}}).status_code == 201
        for sid, perms in ((s1, ["read", "export"]), (s2, ["read"])):
            assert c.post(f"/companies/{w['cid']}/users/{u['user_id']}/access", json={"server_id": sid, "permissions": perms, "allowed_tools": ["compute", "list_artifacts", "get_artifact"]}).status_code == 201
        all_key = c.post(f"/companies/{w['cid']}/api-keys", json={"user_id": u["user_id"], "server_ids": None, "name": "all"}).json()["raw_key"]
        s2_key = c.post(f"/companies/{w['cid']}/api-keys", json={"user_id": u["user_id"], "server_ids": [s2], "name": "s2"}).json()["raw_key"]

    op = {"op": "describe", "column": "n"}
    src = {"sql": "select id as n from customers"}
    async with mcp_client(all_key, s2) as cl:  # s2 access has no export => cannot save
        with pytest.raises(ToolError, match="export"):
            await cl.call_tool("compute", {"source": src, "op": op, "save": True})
    async with mcp_client(all_key, s1) as cl:
        r = (await cl.call_tool("compute", {"source": src, "op": op, "save": True, "title": "customer ids"})).structured_content
    aid, figures = r["artifact"]["artifact_id"], r["result"]
    async with mcp_client(all_key, s2) as cl:  # different server, same user, scope covers s1
        got = (await cl.call_tool("get_artifact", {"artifact_id": aid})).structured_content
        assert got["payload"]["result"] == figures and got["server_id"] == s1
        assert aid in [a["artifact_id"] for a in (await cl.call_tool("list_artifacts", {})).structured_content["artifacts"]]
    async with mcp_client(s2_key) as cl:  # same user, key scoped to s2 only
        with pytest.raises(ToolError, match="artifact not found"):
            await cl.call_tool("get_artifact", {"artifact_id": aid})
        assert aid not in [a["artifact_id"] for a in (await cl.call_tool("list_artifacts", {})).structured_content["artifacts"]]
    async with mcp_client(world["acme"]["ro"]["raw_key"]) as cl:  # another user of the same company
        assert "get_artifact" not in [t.name for t in await cl.list_tools()]
    with httpx.Client(base_url=BACKEND, headers=world["hdr"], timeout=10) as c:
        assert c.patch(f"/companies/{w['cid']}/users/{u['user_id']}/access/{s1}", json={"status": "suspended"}).status_code == 200
    async with mcp_client(all_key, s2) as cl:
        with pytest.raises(ToolError, match="artifact not found"):
            await cl.call_tool("get_artifact", {"artifact_id": aid})


async def test_chart_from_source_histogram_and_artifact(world):
    """chart returns a PNG image block + JSON; histograms are counted by the Rust kernel; a chart can be
    drawn from a compute artifact saved on another server; saving a chart needs export."""
    import base64

    w = world["acme"]
    s1 = world["server"]["server_id"]
    with httpx.Client(base_url=BACKEND, headers=world["hdr"], timeout=10) as c:
        u = c.post(f"/companies/{w['cid']}/users", json={"email": f"ch-{uuid.uuid4().hex[:6]}@ex.com", "name": "Ch", "role": "member"}).json()
        tools = ["chart", "compute", "get_artifact"]
        assert c.post(f"/companies/{w['cid']}/users/{u['user_id']}/access", json={"server_id": s1, "permissions": ["read", "export"], "allowed_tools": tools}).status_code == 201
        raw = c.post(f"/companies/{w['cid']}/api-keys", json={"user_id": u["user_id"], "server_ids": [s1], "name": "ch"}).json()["raw_key"]
    src = {"sql": "select name, id from customers order by id"}
    async with mcp_client(raw) as cl:
        r = await cl.call_tool("chart", {"spec": {"type": "bar", "x": {"field": "name"}, "y": {"field": "id"}, "title": "고객별 ID"}, "source": src})
        img = [b for b in r.content if b.type == "image"][0]
        png = base64.b64decode(img.data)
        assert img.mimeType == "image/png" and png[:4] == b"\x89PNG" and r.structured_content["points"] == 2
        if os.environ.get("E2E_CHART_OUT"):  # eyeball the rendering (fonts) when debugging
            open(os.environ["E2E_CHART_OUT"], "wb").write(png)
        h = (await cl.call_tool("chart", {"spec": {"type": "histogram", "column": "id", "bins": 2}, "source": src})).structured_content
        assert h["type"] == "histogram" and h["points"] == 2
        with pytest.raises(ToolError, match="rejected"):
            await cl.call_tool("chart", {"spec": {"type": "bar", "x": {"field": "name"}, "y": {"field": "id"}, "data": {"url": "file:///etc/passwd"}}, "source": src})
        with pytest.raises(ToolError, match="exactly one"):
            await cl.call_tool("chart", {"spec": {"type": "bar", "x": {"field": "name"}, "y": {"field": "id"}}})
        comp = (await cl.call_tool("compute", {"source": src, "op": {"op": "top_k", "column": "name", "k": 5}, "save": True})).structured_content
        fromart = await cl.call_tool("chart", {"spec": {"type": "bar", "x": {"field": "key"}, "y": {"field": "count"}}, "artifact_id": comp["artifact"]["artifact_id"], "save": True})
        saved = fromart.structured_content["artifact"]
        got = (await cl.call_tool("get_artifact", {"artifact_id": saved["artifact_id"]})).structured_content
        assert got["kind"] == "chart" and base64.b64decode(got["payload"]["png_base64"])[:4] == b"\x89PNG"
        assert got["payload"]["rows"] == comp["result"]  # the chart plots the stored figures verbatim
    async with mcp_client(world["acme"]["ro"]["raw_key"]) as cl:  # ro user has no chart tool at all
        assert "chart" not in [t.name for t in await cl.list_tools()]


async def test_report_combines_postgres_and_clickhouse_artifacts(world):
    """The headline flow: one user, one key, two different engines. Figures are computed and saved on each
    server, then a single report (docx inline, pdf/pptx via presigned link) is built from those artifacts."""
    import base64
    import io

    from docx import Document

    ch_mcp = os.environ.get("E2E_MCP_CH", "http://localhost:19000/mcp-clickhouse")
    w = world["acme"]
    pg = world["server"]["server_id"]
    tools = ["compute", "transform", "chart", "report", "get_artifact"]
    with httpx.Client(base_url=BACKEND, headers=world["hdr"], timeout=10) as c:
        # a fresh (isolated e2e) stack has none yet: register it the way mcp-clickhouse's own e2e does
        chs = c.get("/servers", params={"server_type": "clickhouse", "status": "active"}).json()["items"]
        chs = (chs[0] if chs else c.post("/servers", json={"server_type": "clickhouse", "name": "mcp-clickhouse", "endpoint": ch_mcp}).json())["server_id"]
        assert c.post(f"/companies/{w['cid']}/bindings", json={"server_id": chs, "tenant": {"mode": "column", "column": "company_id", "value": "ftg"}}).status_code in (201, 409)
        u = c.post(f"/companies/{w['cid']}/users", json={"email": f"rep-{uuid.uuid4().hex[:6]}@ex.com", "name": "Rep", "role": "member"}).json()
        for sid in (pg, chs):
            r = c.post(f"/companies/{w['cid']}/users/{u['user_id']}/access", json={"server_id": sid, "permissions": ["read", "export"], "allowed_tools": tools})
            assert r.status_code == 201, r.text
        raw = c.post(f"/companies/{w['cid']}/api-keys", json={"user_id": u["user_id"], "server_ids": None, "name": "report"}).json()["raw_key"]
        ro_raw = world["acme"]["ro"]["raw_key"]

    async with mcp_client(raw, pg) as cl:
        kpi = (await cl.call_tool("compute", {"source": {"sql": "select id from customers"}, "op": {"op": "describe", "column": "id"}, "save": True, "title": "customers"})).structured_content
        tbl = (await cl.call_tool("transform", {"source": {"sql": "select name, email from customers order by id"}, "pipeline": [], "save": True, "title": "customer list"})).structured_content
    async with Client(StreamableHttpTransport(f"{ch_mcp}?server={chs}", headers={"Authorization": f"Bearer {raw}"})) as cl:
        by_product = "SELECT product, round(sum(amount)) AS amount FROM revenue GROUP BY product ORDER BY product"
        orders = (await cl.call_tool("compute", {"source": {"sql": by_product}, "op": {"op": "describe", "column": "amount"}, "save": True, "title": "orders"})).structured_content
        chart = (await cl.call_tool("chart", {"spec": {"type": "bar", "x": {"field": "product", "type": "nominal"}, "y": {"field": "amount"}, "title": "상품별 매출"}, "source": {"sql": by_product}, "save": True})).structured_content

    outline = {"title": "9월 진행 보고서", "subtitle": "고객·주문 현황", "sections": [
        {"heading": "요약", "blocks": [{"type": "text", "text": "- 고객 현황\n- 주문 현황"},
                                      {"type": "kpi", "artifact_id": kpi["artifact"]["artifact_id"], "fields": ["count"], "labels": {"count": "고객 수"}},
                                      {"type": "kpi", "artifact_id": orders["artifact"]["artifact_id"], "fields": ["sum", "mean"], "labels": {"sum": "주문 합계"}}]},
        {"heading": "상세", "blocks": [{"type": "table", "artifact_id": tbl["artifact"]["artifact_id"]},
                                      {"type": "chart", "artifact_id": chart["artifact"]["artifact_id"], "caption": "주문별 금액"}]}]}
    async with mcp_client(raw, pg) as cl:
        docx = (await cl.call_tool("report", {"outline": outline, "format": "docx", "delivery": "inline"})).structured_content
        pdf = (await cl.call_tool("report", {"outline": outline, "format": "pdf", "delivery": "link"})).structured_content
        pptx = (await cl.call_tool("report", {"outline": outline, "format": "pptx", "delivery": "link"})).structured_content
        with pytest.raises(ToolError, match="artifact not found"):
            bad = {**outline, "sections": [{"heading": "x", "blocks": [{"type": "table", "artifact_id": "art_" + "0" * 32}]}]}
            await cl.call_tool("report", {"outline": bad, "format": "docx"})

    d = Document(io.BytesIO(base64.b64decode(docx["content_base64"])))
    cells = [c.text for t in d.tables for row in t.rows for c in row.cells]
    assert str(kpi["result"]["count"]) in cells                    # figures = the postgres artifact
    assert f"{orders['result']['sum']:,.0f}" in cells               # figures = the clickhouse artifact (KRW, thousands-separated)
    assert "[EMAIL]" in cells and not any("@acme.example" in c for c in cells)  # PII redacted before storage
    assert {s["server"].split()[0] for s in docx["sources"]} == {"postgres", "clickhouse"}
    assert len(d.inline_shapes) == 1
    for res, magic in ((pdf, b"%PDF-"), (pptx, b"PK")):
        assert res["delivery"] == "link" and res["object_key"].startswith("mcp/reports/tenant=")
        assert httpx.get(res["url"], timeout=10).content[:len(magic)] == magic

    # a user without export cannot write reports; another user's artifacts are invisible
    async with mcp_client(ro_raw) as cl:
        assert "report" not in [t.name for t in await cl.list_tools()]
    with httpx.Client(base_url=BACKEND, headers=world["hdr"], timeout=10) as c:
        u2 = c.post(f"/companies/{w['cid']}/users", json={"email": f"rep2-{uuid.uuid4().hex[:6]}@ex.com", "name": "R2", "role": "member"}).json()
        assert c.post(f"/companies/{w['cid']}/users/{u2['user_id']}/access", json={"server_id": pg, "permissions": ["read", "export"], "allowed_tools": ["report"]}).status_code == 201
        raw2 = c.post(f"/companies/{w['cid']}/api-keys", json={"user_id": u2["user_id"], "server_ids": [pg], "name": "r2"}).json()["raw_key"]
    async with mcp_client(raw2) as cl:
        with pytest.raises(ToolError, match="artifact not found"):
            await cl.call_tool("report", {"outline": outline, "format": "docx"})


async def test_monitor_runs_on_schedule_and_follows_permissions(world):
    """Created once (first run inline), then re-run by the server's scheduler as the creating key. Suspending the
    access or the key makes runs 'skipped' with the reason; pause stops runs; other users cannot see it."""
    w = world["acme"]
    s1 = world["server"]["server_id"]
    tools = ["monitor_create", "monitor_list", "monitor_status", "monitor_pause", "monitor_delete"]
    with httpx.Client(base_url=BACKEND, headers=world["hdr"], timeout=10) as c:
        u = c.post(f"/companies/{w['cid']}/users", json={"email": f"mon-{uuid.uuid4().hex[:6]}@ex.com", "name": "Mon", "role": "member"}).json()
        assert c.post(f"/companies/{w['cid']}/users/{u['user_id']}/access", json={"server_id": s1, "permissions": ["read"], "allowed_tools": tools}).status_code == 201
        k = c.post(f"/companies/{w['cid']}/api-keys", json={"user_id": u["user_id"], "server_ids": [s1], "name": "mon"}).json()
    raw, kid = k["raw_key"], k["key"]["key_id"]
    access = f"/companies/{w['cid']}/users/{u['user_id']}/access/{s1}"

    async def status(mid, n=50):
        async with mcp_client(raw) as cl:
            return (await cl.call_tool("monitor_status", {"monitor_id": mid, "limit": n})).structured_content

    async def wait_for(mid, pred, timeout=20):
        for _ in range(timeout * 2):
            st = await status(mid)
            if pred(st["events"]):
                return st
            await asyncio.sleep(0.5)
        raise AssertionError(f"condition not reached: {st['events'][:3]}")

    async with mcp_client(raw) as cl:
        with pytest.raises(ToolError, match="path 'nope' not found"):
            await cl.call_tool("monitor_create", {"name": "bad", "source": {"sql": "select id from customers"}, "op": {"op": "describe", "column": "id"},
                                                  "condition": {"path": "nope", "op": "gt", "threshold": 1}, "interval_s": 2})
        with pytest.raises(ToolError, match="not available"):
            await cl.call_tool("monitor_create", {"name": "slack", "source": {"sql": "select id from customers"}, "op": {"op": "describe", "column": "id"},
                                                  "condition": {"path": "count", "op": "gt", "threshold": 1}, "interval_s": 2, "channels": [{"type": "slack"}]})
        m = (await cl.call_tool("monitor_create", {"name": "customers > 1", "source": {"sql": "select id from customers"},
                                                   "op": {"op": "describe", "column": "id"}, "condition": {"path": "count", "op": "gt", "threshold": 1},
                                                   "interval_s": 2})).structured_content
        assert m["first_run"]["status"] == "breach" and m["first_run"]["value"] == 2 and "key_hash" not in m
        assert [x["monitor_id"] for x in (await cl.call_tool("monitor_list", {})).structured_content["monitors"]] == [m["monitor_id"]]
    mid = m["monitor_id"]
    await wait_for(mid, lambda ev: sum(e["status"] == "breach" for e in ev) >= 3)  # scheduler runs

    # while suspended the owner cannot even read the monitor (MCP denies), so observe afterwards
    with httpx.Client(base_url=BACKEND, headers=world["hdr"], timeout=10) as c:
        assert c.patch(access, json={"status": "suspended"}).status_code == 200
        try:
            with pytest.raises(MCPError, match="access suspended"):
                await status(mid)
            await asyncio.sleep(5)
        finally:
            assert c.patch(access, json={"status": "active"}).status_code == 200
    st = await status(mid)
    assert any(e["status"] == "skipped" and e["reason"] == "access suspended" for e in st["events"]), [(e["ts"][11:19], e["status"], e.get("reason")) for e in st["events"][:12]]
    await wait_for(mid, lambda ev: ev[0]["status"] == "breach")  # resumes once access is back

    with httpx.Client(base_url=BACKEND, headers=world["hdr"], timeout=10) as c:  # the key itself
        assert c.patch(f"/companies/{w['cid']}/api-keys/{kid}", json={"status": "suspended"}).status_code == 200
        await asyncio.sleep(5)
        assert c.patch(f"/companies/{w['cid']}/api-keys/{kid}", json={"status": "active"}).status_code == 200
    st = await status(mid)
    assert any(e["status"] == "skipped" and e["reason"] == "key suspended" for e in st["events"])

    async with mcp_client(raw) as cl:
        assert (await cl.call_tool("monitor_pause", {"monitor_id": mid, "paused": True})).structured_content["status"] == "paused"
    await asyncio.sleep(1)
    before = len((await status(mid))["events"])
    await asyncio.sleep(5)
    assert len((await status(mid))["events"]) == before  # paused: no runs

    async with mcp_client(world["acme"]["rw"]["raw_key"]) as cl:  # same company, different user
        assert "monitor_status" not in [t.name for t in await cl.list_tools()]
    async with mcp_client(raw) as cl:
        await cl.call_tool("monitor_delete", {"monitor_id": mid})
        with pytest.raises(ToolError, match="monitor not found"):
            await cl.call_tool("monitor_status", {"monitor_id": mid})


async def test_security_review_regressions(world):
    """#1 compute results are PII-masked before they are returned/saved/reported; #2 a chart of another server's
    artifact keeps that server as a source; #3 files (report / saved copies) need export on every source server;
    #5 scheduled monitor runs appear in the access log."""
    w = world["acme"]
    s1 = world["server"]["server_id"]
    tools = ["compute", "chart", "report", "get_artifact", "monitor_create", "monitor_delete"]
    with httpx.Client(base_url=BACKEND, headers=world["hdr"], timeout=10) as c:
        s2 = c.post("/servers", json={"server_type": "postgres", "name": f"pg-sec-{uuid.uuid4().hex[:6]}", "endpoint": MCP}).json()["server_id"]
        assert c.post(f"/companies/{w['cid']}/bindings", json={"server_id": s2, "tenant": {"mode": "schema", "schema_name": "acme"}}).status_code == 201
        u = c.post(f"/companies/{w['cid']}/users", json={"email": f"sec-{uuid.uuid4().hex[:6]}@ex.com", "name": "Sec", "role": "member"}).json()
        for sid in (s1, s2):
            assert c.post(f"/companies/{w['cid']}/users/{u['user_id']}/access", json={"server_id": sid, "permissions": ["read", "export"], "allowed_tools": tools}).status_code == 201
        raw = c.post(f"/companies/{w['cid']}/api-keys", json={"user_id": u["user_id"], "server_ids": None, "name": "sec"}).json()
        s1_only = c.post(f"/companies/{w['cid']}/api-keys", json={"user_id": u["user_id"], "server_ids": [s1], "name": "s1"}).json()["raw_key"]
    raw, kid = raw["raw_key"], raw["key"]["key_id"]
    acc2 = f"/companies/{w['cid']}/users/{u['user_id']}/access/{s2}"

    async with mcp_client(raw, s2) as cl:  # #1
        top = (await cl.call_tool("compute", {"source": {"sql": "select email from customers"}, "op": {"op": "top_k", "column": "email", "k": 5}, "save": True})).structured_content
        assert {r["key"] for r in top["result"]} == {"[EMAIL]"} and sum(r["count"] for r in top["result"]) == 2  # masked after counting
        got = (await cl.call_tool("get_artifact", {"artifact_id": top["artifact"]["artifact_id"]})).structured_content
        assert "acme.example" not in str(got)
    a2 = top["artifact"]["artifact_id"]

    async with mcp_client(raw, s1) as cl:  # #2 chart on s1 from the s2 artifact
        chart = (await cl.call_tool("chart", {"spec": {"type": "bar", "x": {"field": "key"}, "y": {"field": "count"}}, "artifact_id": a2, "save": True})).structured_content
    assert sorted(chart["artifact"]["sources"]) == sorted([s1, s2])
    c_id = chart["artifact"]["artifact_id"]
    async with mcp_client(s1_only) as cl:  # a key without s2 in scope cannot read s2-derived data through s1
        with pytest.raises(ToolError, match="artifact not found"):
            await cl.call_tool("get_artifact", {"artifact_id": c_id})

    with httpx.Client(base_url=BACKEND, headers=world["hdr"], timeout=10) as c:  # #3 downgrade s2 to read-only
        assert c.patch(acc2, json={"permissions": ["read"], "allowed_tools": ["compute", "get_artifact"]}).status_code == 200
    outline = {"title": "t", "sections": [{"heading": "h", "blocks": [{"type": "table", "artifact_id": a2}]}]}
    async with mcp_client(raw, s1) as cl:
        with pytest.raises(ToolError, match="artifact not found"):
            await cl.call_tool("report", {"outline": outline, "format": "docx"})
        with pytest.raises(ToolError, match="artifact not found"):  # re-saving s2 data is a copy too
            await cl.call_tool("chart", {"spec": {"type": "bar", "x": {"field": "key"}, "y": {"field": "count"}}, "artifact_id": a2, "save": True})
        assert (await cl.call_tool("get_artifact", {"artifact_id": a2})).structured_content["payload"]  # viewing still fine

    async with mcp_client(raw, s1) as cl:  # #5
        m = (await cl.call_tool("monitor_create", {"name": "sec", "source": {"sql": "select id from customers"}, "op": {"op": "describe", "column": "id"},
                                                   "condition": {"path": "count", "op": "gt", "threshold": 100}, "interval_s": 2})).structured_content
    with httpx.Client(base_url=BACKEND, headers=world["hdr"], timeout=10) as c:
        for _ in range(30):
            items = c.get(f"/companies/{w['cid']}/api-keys/{kid}/logs").json()["items"]
            if any(i["tool"] == f"monitor:{m['monitor_id']}" for i in items):
                break
            await asyncio.sleep(0.5)
        else:
            raise AssertionError("scheduled monitor runs are not in the access log")
    async with mcp_client(raw, s1) as cl:
        await cl.call_tool("monitor_delete", {"monitor_id": m["monitor_id"]})


async def test_metrics_count_calls_without_tenant_labels(world):
    import re

    # this server's own metrics, next to its MCP path (nginx: /mcp-<type>/metrics -> mcp-<type>:900x/metrics)
    def count(text):
        m = re.search(r'mcp_tool_calls_total\{server_type="postgres",status="ok",tool="list_tables"\} ([0-9.]+)', text)
        return float(m.group(1)) if m else 0.0

    before = count(httpx.get(f"{MCP}/metrics", timeout=5).text)
    await _call(world["acme"]["ro"]["raw_key"], "list_tables")
    text = httpx.get(f"{MCP}/metrics", timeout=5).text
    assert count(text) == before + 1
    assert world["acme"]["cid"] not in text and world["acme"]["user"]["user_id"] not in text


async def test_report_uses_the_callers_company_branding(world):
    """Branding set through the console API shows up in that company's reports only."""
    import base64
    import io

    from docx import Document
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (120, 40), (26, 115, 232)).save(buf, format="PNG")
    logo = base64.b64encode(buf.getvalue()).decode()
    s1 = world["server"]["server_id"]
    tools = ["compute", "report"]
    keys = {}
    with httpx.Client(base_url=BACKEND, headers=world["hdr"], timeout=10) as c:
        for tenant in ("acme", "globex"):
            w = world[tenant]
            u = c.post(f"/companies/{w['cid']}/users", json={"email": f"br-{uuid.uuid4().hex[:6]}@ex.com", "name": "Br", "role": "member"}).json()
            assert c.post(f"/companies/{w['cid']}/users/{u['user_id']}/access", json={"server_id": s1, "permissions": ["read", "export"], "allowed_tools": tools}).status_code == 201
            keys[tenant] = c.post(f"/companies/{w['cid']}/api-keys", json={"user_id": u["user_id"], "server_ids": [s1], "name": "br"}).json()["raw_key"]
        r = c.put(f"/companies/{world['acme']['cid']}/branding", json={"primary_color": "#1A73E8", "logo_base64": logo, "report_footer": "Acme · Confidential"})
        assert r.status_code == 200, r.text

    async def report_for(raw):
        async with mcp_client(raw) as cl:
            a = (await cl.call_tool("compute", {"source": {"sql": "select id from customers"}, "op": {"op": "describe", "column": "id"}, "save": True})).structured_content
            outline = {"title": "brand", "sections": [{"heading": "h", "blocks": [{"type": "kpi", "artifact_id": a["artifact"]["artifact_id"], "fields": ["count"]}]}]}
            rep = (await cl.call_tool("report", {"outline": outline, "format": "docx", "delivery": "inline"})).structured_content
        return Document(io.BytesIO(base64.b64decode(rep["content_base64"])))

    acme = await report_for(keys["acme"])
    assert len(acme.inline_shapes) == 1 and acme.sections[0].footer.paragraphs[0].text == "Acme · Confidential"
    globex = await report_for(keys["globex"])
    assert len(globex.inline_shapes) == 0 and globex.sections[0].footer.paragraphs[0].text == ""  # never another company's brand
    with httpx.Client(base_url=BACKEND, headers=world["hdr"], timeout=10) as c:
        assert c.delete(f"/companies/{world['acme']['cid']}/branding").status_code == 200
