import logging
import os
from contextlib import asynccontextmanager
from typing import Any

import polars as pl
import pyarrow as pa
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from mcp_clickhouse import aggregate as agg
from mcp_clickhouse.client import AGG_SETTINGS, MAX_ROWS, QUERY_SETTINGS, SETTINGS, database_for, registry
from mcp_clickhouse.sqlguard import SqlRejected, admit
from mcpcluster_common import TenantAuthMiddleware, current_tenant
from mcpcluster_common.postprocess import redact_rows
from mcpcluster_common.monitor import start_background, stop_background
from mcpcluster_common.datatools import TOOL_PERMISSIONS as DATA_TOOLS, install_data_tools
from mcpcluster_common.serialize import arrow_rows_from_columns, rows_from_columns, to_jsonable

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("mcp-clickhouse")

# Must equal backend/app/domain/tool_catalog.py[ServerType.CLICKHOUSE]
TOOL_PERMISSIONS: dict[str, str] = {
    "list_tables": "read",
    "describe_table": "read",
    "run_select": "read",
    "aggregate": "read",
    **DATA_TOOLS,  # transform (read) + export (export) — engine-agnostic, from mcpcluster_common
}


@asynccontextmanager
async def lifespan(_app):
    start_background()  # monitor scheduler
    try:
        yield
    finally:
        await stop_background()
        await registry.close_all()


INSTRUCTIONS = (
    "Tenant-scoped ClickHouse analytics for business analysts. Every call is authorized by API key and limited to the "
    "caller's business unit. Workflow: list_tables / describe_table to learn the schema -> run_select or aggregate to pull "
    "a tenant-scoped result (prefer SQL GROUP BY / date_trunc so the result is small) -> compute for every figure you report "
    "(describe, group_stats, correlation, time_bucket, linear_trend, growth, pareto, ab_test, seasonality run in Rust over "
    "the full result; never calculate numbers in your own reasoning) -> chart for visuals -> report to assemble artifacts. "
    "State the SQL and the compute op behind each number so the insight is reproducible."
)
mcp = FastMCP("mcp-clickhouse", instructions=INSTRUCTIONS, lifespan=lifespan)


def _out_rows(rows: list[dict[str, Any]], cap: int = MAX_ROWS) -> dict[str, Any]:
    truncated = len(rows) > cap
    return {"rows": redact_rows(rows[:cap]), "row_count": min(len(rows), cap), "truncated": truncated}


def _out(res, cap: int = MAX_ROWS) -> dict[str, Any]:
    """clickhouse-connect QueryResult -> JSON-safe rows, whatever the column types."""
    return _out_rows(rows_from_columns(res.column_names, res.result_rows), cap)


@mcp.tool
async def list_tables() -> dict[str, Any]:
    """Tables visible to this tenant. In column mode only tables carrying the tenant column are listed."""
    ctx = current_tenant()
    client = await registry.get(ctx)
    db = database_for(ctx)
    if ctx.tenant.mode == "column":
        q = "SELECT DISTINCT t.name AS name, t.engine AS engine FROM system.tables t JOIN system.columns c ON c.database = t.database AND c.table = t.name WHERE t.database = {db:String} AND c.name = {col:String} ORDER BY name"
        params = {"db": db, "col": ctx.tenant.column}
    else:
        q = "SELECT name, engine FROM system.tables WHERE database = {db:String} ORDER BY name"
        params = {"db": db}
    res = await client.query(q, parameters=params, settings=QUERY_SETTINGS)
    return _out(res)


@mcp.tool
async def describe_table(table: str) -> dict[str, Any]:
    """Columns of a table within the tenant scope. `table` may be `name` or `db.name` (own db only)."""
    ctx = current_tenant()
    db = database_for(ctx)
    qual, _, name = table.rpartition(".")
    if qual and qual != db:
        raise ToolError(f"database {qual} is outside this tenant")
    client = await registry.get(ctx)
    if ctx.tenant.mode == "column":
        has = await client.query("SELECT count() FROM system.columns WHERE database = {db:String} AND table = {t:String} AND name = {c:String}", parameters={"db": db, "t": name, "c": ctx.tenant.column}, settings=QUERY_SETTINGS)
        if has.result_rows[0][0] == 0:
            raise ToolError(f"table {name} is not tenant-scoped (no {ctx.tenant.column} column)")
    res = await client.query("SELECT name, type, default_expression, comment FROM system.columns WHERE database = {db:String} AND table = {t:String} ORDER BY position", parameters={"db": db, "t": name}, settings=QUERY_SETTINGS)
    if not res.result_rows:
        raise ToolError(f"table {db}.{name} not found in tenant scope")
    return _out(res)


def _admit(sql: str) -> str:
    ctx = current_tenant()
    try:
        return admit(sql, ctx.tenant, database_for(ctx)).sql
    except SqlRejected as e:
        raise ToolError(f"rejected: {e}") from e


async def _fetch_arrow(sql: str, settings: dict[str, Any]):
    """Rows come back as native Python values (same as run_select — Enum names, not codes; Decimal
    kept) and are converted to Arrow for DuckDB. ClickHouse's own Arrow output is not used: it
    changes value semantics for several types (Enum -> code, UUID -> bytes) and fails for others."""
    ctx = current_tenant()
    final_sql = _admit(sql)
    client = await registry.get(ctx)
    res = await client.query(final_sql, settings=settings)
    return final_sql, pa.Table.from_pylist(arrow_rows_from_columns(res.column_names, res.result_rows))


@mcp.tool
async def run_select(sql: str) -> dict[str, Any]:
    """Run a single SELECT within the tenant scope (read-only, row-limited)."""
    ctx = current_tenant()
    final_sql = _admit(sql)
    client = await registry.get(ctx)
    res = await client.query(final_sql, settings=QUERY_SETTINGS)
    return {"sql": final_sql, **_out(res)}


@mcp.tool
async def aggregate(sql: str, group_by: list[str], metrics: dict[str, str], limit: int = 1000) -> dict[str, Any]:
    """Group/aggregate the rows of a tenant-scoped SELECT with DuckDB.
    metrics: {"total": "sum(value)", "n": "count(*)", "users": "count_distinct(user_id)"}"""
    final_sql, table = await _fetch_arrow(sql, AGG_SETTINGS)
    try:
        df = agg.run(table, group_by, metrics, limit=min(int(limit), MAX_ROWS))
    except agg.AggregateRejected as e:
        raise ToolError(f"rejected: {e}") from e
    except Exception as e:  # duckdb binder errors (unknown column etc.)
        raise ToolError(f"aggregation failed: {e}") from e
    return {"sql": final_sql, "base_rows": table.num_rows, **_out_rows([{k: to_jsonable(v) for k, v in r.items()} for r in df.to_dicts()])}


async def _fetch_source(source: dict[str, Any], max_rows: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """`source` = {"sql": "<SELECT>"}; same guard as run_select, result-row cap from settings."""
    sql = source.get("sql") if isinstance(source, dict) else None
    if not isinstance(sql, str) or not sql.strip():
        raise ToolError("rejected: source must be {\"sql\": \"SELECT ...\"}")
    ctx = current_tenant()
    final_sql = _admit(sql)
    client = await registry.get(ctx)
    res = await client.query(final_sql, settings={**QUERY_SETTINGS, "max_result_rows": max_rows + 1})
    rows = res.result_rows
    return arrow_rows_from_columns(res.column_names, rows[:max_rows]), {"sql": final_sql, "source_truncated": len(rows) > max_rows}


install_data_tools(mcp, _fetch_source, SETTINGS, source_doc='{"sql": "SELECT ..."} (tenant-scoped, read-only)')


def install_auth(server: FastMCP) -> FastMCP:
    server.add_middleware(TenantAuthMiddleware(SETTINGS, TOOL_PERMISSIONS))
    return server


def main() -> None:
    SETTINGS.ensure_safe_runtime()
    from mcpcluster_common.tracing import setup_tracing

    setup_tracing("mcp-clickhouse")
    install_auth(mcp)
    mcp.run(transport="http", host="0.0.0.0", port=int(os.environ.get("PORT", "9002")), path="/mcp", stateless_http=True)


if __name__ == "__main__":
    main()
