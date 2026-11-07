"""End-to-end against the compose stack (gateway :19000 -> backend /api/v1, mcp-clickhouse /mcp-clickhouse). Skips when down."""

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
        srv = c.get("/servers", params={"server_type": "clickhouse"}).json()["items"]
        server = srv[0] if srv else c.post("/servers", json={"server_type": "clickhouse", "name": "mcp-clickhouse", "endpoint": MCP}).json()
        out = {"server": server, "hdr": dict(c.headers)}
        # biz.* (column mode, business units ftg / ftg-academy / ftg-cloud) and two database-mode tenants
        # pinned to different databases (biz vs the weird_db test fixture) to prove database isolation.
        tenants = {
            "ftg_col": {"mode": "column", "column": "company_id", "value": "ftg"},
            "academy_col": {"mode": "column", "column": "company_id", "value": "ftg-academy"},
            "biz_db": {"mode": "database", "database": "biz"},
            "weird_db": {"mode": "database", "database": "weird_db"},
        }
        for name, tenant in tenants.items():
            cid = f"{name.replace('_', '-')}-{uuid.uuid4().hex[:6]}"
            assert c.post("/companies", json={"company_id": cid, "name": name}).status_code == 201
            u = c.post(f"/companies/{cid}/users", json={"email": f"m-{cid}@ex.com", "name": "M", "role": "member"}).json()
            r = c.post(f"/companies/{cid}/bindings", json={"server_id": server["server_id"], "tenant": tenant})
            assert r.status_code == 201, r.text
            k = grant_and_issue(c, cid, u["user_id"], server["server_id"], "ro", ["read"], ["list_tables", "describe_table", "run_select", "aggregate"]).json()
            out[name] = {"cid": cid, "raw": k["raw_key"], "key_id": k["key"]["key_id"]}
        return out


async def test_schema_mode_rejected_for_clickhouse(world):
    with httpx.Client(base_url=BACKEND, headers=world["hdr"], timeout=10) as c:
        cid = f"bad-{uuid.uuid4().hex[:6]}"
        c.post("/companies", json={"company_id": cid, "name": "bad"})
        r = c.post(f"/companies/{cid}/bindings", json={"server_id": world["server"]["server_id"], "tenant": {"mode": "schema", "schema_name": "x"}})
        assert r.status_code == 422


async def test_column_mode_isolation(world):
    q = "SELECT company_id, count() AS n, round(sum(amount)) AS total FROM revenue GROUP BY company_id"
    ftg = await _call(world["ftg_col"]["raw"], "run_select", sql=q)
    academy = await _call(world["academy_col"]["raw"], "run_select", sql=q)
    # each tenant sees exactly its own business unit, never another one's rows
    assert [r["company_id"] for r in ftg["rows"]] == ["ftg"] and ftg["rows"][0]["n"] > 0
    assert [r["company_id"] for r in academy["rows"]] == ["ftg-academy"] and academy["rows"][0]["n"] > 0
    assert ftg["rows"][0]["total"] != academy["rows"][0]["total"]
    # join + subquery + CTE all filtered
    res = await _call(world["ftg_col"]["raw"], "run_select", sql="WITH c AS (SELECT customer_id FROM customers WHERE segment = 'enterprise') SELECT count() AS n FROM revenue r WHERE r.customer_id IN (SELECT customer_id FROM c) OR r.customer_id IN (SELECT customer_id FROM customers WHERE segment = 'public')")
    assert res["rows"][0]["n"] >= 0 and res["sql"].count("\"company_id\" = 'ftg'") == 3


BIZ_TABLES = {"customers", "revenue", "expenses", "campaigns", "marketing_daily", "headcount_monthly", "cash_monthly"}


async def test_column_mode_cannot_see_untenanted_or_system(world):
    raw = world["ftg_col"]["raw"]
    tables = await _call(raw, "list_tables")
    assert {r["name"] for r in tables["rows"]} == BIZ_TABLES  # fx_rates (no company_id) hidden
    with pytest.raises(ToolError):
        await _call(raw, "describe_table", table="fx_rates")
    with pytest.raises(ToolError):  # column injected -> unknown identifier -> fails closed
        await _call(raw, "run_select", sql="SELECT krw_per_unit FROM fx_rates")
    for sql in ("SELECT * FROM system.tables", "SELECT * FROM weird_db.`Mixed Case`", "SELECT * FROM url('http://x', CSV, 'a String')", "SELECT 1 SETTINGS readonly=0"):
        with pytest.raises(ToolError, match="rejected"):
            await _call(raw, "run_select", sql=sql)


async def test_database_mode_isolation(world):
    biz, weird = world["biz_db"]["raw"], world["weird_db"]["raw"]
    # database mode sees every table of its database, including the untenanted fx_rates, and all business units
    assert {r["name"] for r in (await _call(biz, "list_tables"))["rows"]} == BIZ_TABLES | {"fx_rates"}
    units = await _call(biz, "run_select", sql="SELECT DISTINCT company_id FROM revenue ORDER BY company_id")
    assert [r["company_id"] for r in units["rows"]] == ["ftg", "ftg-academy", "ftg-cloud"]
    assert (await _call(biz, "run_select", sql="SELECT count() AS n FROM fx_rates"))["rows"][0]["n"] > 0
    assert {r["name"] for r in (await _call(weird, "list_tables"))["rows"]} == {"Mixed Case", "Empty"}
    for raw, sql in ((biz, "SELECT * FROM weird_db.`Mixed Case`"), (weird, "SELECT * FROM biz.revenue")):
        with pytest.raises(ToolError, match="rejected"):
            await _call(raw, "run_select", sql=sql)


async def test_aggregate_with_duckdb(world):
    raw = world["ftg_col"]["raw"]
    res = await _call(raw, "aggregate", sql="SELECT channel, spend, clicks FROM marketing_daily", group_by=["channel"], metrics={"spend": "sum(spend)", "clicks": "sum(clicks)", "n": "count(*)"})
    direct = await _call(raw, "run_select", sql="SELECT channel, round(sum(spend), 2) AS spend, sum(clicks) AS clicks, count() AS n FROM marketing_daily GROUP BY channel")
    assert res["base_rows"] == sum(r["n"] for r in direct["rows"]) > 0
    # DuckDB aggregation over the tenant rows matches ClickHouse's own aggregation
    assert {r["channel"]: (round(float(r["spend"]), 2), int(r["clicks"]), r["n"]) for r in res["rows"]} == {r["channel"]: (round(float(r["spend"]), 2), int(r["clicks"]), r["n"]) for r in direct["rows"]}
    with pytest.raises(ToolError, match="rejected"):
        await _call(raw, "aggregate", sql="SELECT channel FROM marketing_daily", group_by=["channel"], metrics={"x": "read_csv('/etc/passwd')"})


async def test_key_for_postgres_server_is_refused_here(world):
    with httpx.Client(base_url=BACKEND, headers=world["hdr"], timeout=10) as c:
        pg = c.get("/servers", params={"server_type": "postgres"}).json()["items"]
        if not pg:
            pytest.skip("no postgres server registered")
        cid = world["ftg_col"]["cid"]
        c.post(f"/companies/{cid}/bindings", json={"server_id": pg[0]["server_id"], "tenant": {"mode": "schema", "schema_name": "acme"}})
        u = c.get(f"/companies/{cid}/users").json()["items"][0]
        k = grant_and_issue(c, cid, u["user_id"], pg[0]["server_id"], "pg", ["read"], ["run_select"]).json()
    with pytest.raises(MCPError, match="different server type"):
        await _call(k["raw_key"], "list_tables")


async def test_logs_recorded(world):
    w = world["ftg_col"]
    await _call(w["raw"], "list_tables")
    with httpx.Client(base_url=BACKEND, headers=world["hdr"], timeout=10) as c:
        for _ in range(20):
            items = c.get(f"/companies/{w['cid']}/api-keys/{w['key_id']}/logs").json()["items"]
            if any(i["tool"] == "list_tables" and i["status"] == "ok" and i["server_type"] == "clickhouse" for i in items):
                return
            await asyncio.sleep(0.5)
    raise AssertionError("log not found")


async def test_business_analysis_ops_run_in_rust(world):
    """An analyst's path over biz.*: SQL pulls a tenant-scoped, pre-aggregated result; compute does every figure."""
    with httpx.Client(base_url=BACKEND, headers=world["hdr"], timeout=10) as c:
        cid = world["ftg_col"]["cid"]
        u = c.post(f"/companies/{cid}/users", json={"email": f"an-{uuid.uuid4().hex[:6]}@ex.com", "name": "Analyst", "role": "member"}).json()
        raw = grant_and_issue(c, cid, u["user_id"], world["server"]["server_id"], "an", ["read"], ["run_select", "compute"]).json()["raw_key"]

    monthly = {"sql": "SELECT toStartOfMonth(txn_date) AS month, sum(amount) AS revenue FROM revenue WHERE status = 'paid' GROUP BY month ORDER BY month"}
    trend = await _call(raw, "compute", source=monthly, op={"op": "linear_trend", "x": "month", "y": "revenue", "horizon": 3})
    assert trend["engine"] == "rust" and trend["result"]["n"] == 22 and trend["result"]["slope"] > 0 and len(trend["result"]["forecast"]) == 3

    growth = await _call(raw, "compute", source=monthly, op={"op": "growth", "column": "revenue", "order_by": "month", "lag": 12})
    rows = growth["result"]["rows"]
    assert len(rows) == 22 and rows[0]["pct_change"] is None and rows[12]["pct_change"] is not None  # first 12 months have no YoY base
    assert growth["result"]["total_pct_change"] > 0  # ftg grows over the period

    pareto = await _call(raw, "compute", source={"sql": "SELECT customer_id, amount FROM revenue WHERE status = 'paid'"}, op={"op": "pareto", "key": "customer_id", "value": "amount", "threshold": 0.8})
    r = pareto["result"]
    assert r["rows"][0]["sum"] >= r["rows"][-1]["sum"] and 0 < r["keys_to_threshold"] <= len(r["rows"]) and abs(r["rows"][-1]["cum_share"] - 1) < 1e-6

    ab = await _call(raw, "compute", source={"sql": "SELECT channel, clicks / greatest(impressions, 1) AS ctr FROM marketing_daily WHERE channel IN ('search', 'display')"},
                     op={"op": "ab_test", "group": "channel", "value": "ctr", "a": "search", "b": "display"})
    r = ab["result"]
    assert r["n_a"] > 30 and r["n_b"] > 30 and r["mean_a"] > r["mean_b"] and r["p_value"] < 0.01  # channels differ clearly

    season = await _call(raw, "compute", source={"sql": "SELECT txn_date, amount FROM revenue WHERE status = 'paid'"}, op={"op": "seasonality", "time": "txn_date", "value": None, "period": "weekday"})
    by_day = {b["period"]: b["index"] for b in season["result"]}
    assert set(by_day) == set(range(1, 8)) and by_day[6] < by_day[3] and by_day[7] < by_day[3]  # weekend rows well below weekday

    # the tenant filter is on the SQL, so another unit's figures never feed the kernels
    other = await _call(world["academy_col"]["raw"], "run_select", sql="SELECT round(sum(amount)) AS s FROM revenue WHERE status = 'paid'")
    mine = await _call(raw, "run_select", sql="SELECT round(sum(amount)) AS s FROM revenue WHERE status = 'paid'")
    assert abs(float(pareto["result"]["total"]) - float(mine["rows"][0]["s"])) < 1 and mine["rows"][0]["s"] != other["rows"][0]["s"]
