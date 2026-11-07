"""mcp-hacker: read-only, tenant-scoped access to the bug-bounty (hacker) Postgres datasource.
Same guard as mcp-postgres plus connection-level default_transaction_read_only=on."""

import logging
import os
from contextlib import asynccontextmanager
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from mcpcluster_common import TenantAuthMiddleware, current_tenant
from mcpcluster_common.postgres import PoolConfig, SqlRejected, admit, make_registry
from mcpcluster_common.postprocess import redact_rows
from mcpcluster_common.monitor import start_background, stop_background
from mcpcluster_common.datatools import TOOL_PERMISSIONS as DATA_TOOLS, install_data_tools
from mcpcluster_common.serialize import arrow_rows_from_records, rows_from_records
from mcpcluster_common.settings import CommonSettings

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("mcp-hacker")

# Must equal backend/app/domain/tool_catalog.py[ServerType.HACKER]
TOOL_PERMISSIONS: dict[str, str] = {
    "list_tables": "read",
    "describe_table": "read",
    "run_select": "read",
    **DATA_TOOLS,  # transform (read) + export (export) — engine-agnostic, from mcpcluster_common
}
SETTINGS = CommonSettings()
MAX_ROWS = int(os.environ.get("MAX_ROWS", "1000"))
registry = make_registry(
    PoolConfig(
        base_dsn=os.environ.get("BASE_DSN", "postgresql://mcp:mcp@localhost:5432/hacker"),
        app_name="mcp-hacker",
        statement_timeout_ms=int(os.environ.get("STATEMENT_TIMEOUT_MS", "15000")),
        pool_max=int(os.environ.get("POOL_MAX", "8")),
        read_only=True,
    ),
    SETTINGS,
)


@asynccontextmanager
async def lifespan(_app):
    start_background()  # monitor scheduler
    try:
        yield
    finally:
        await stop_background()
        await registry.close_all()


mcp = FastMCP("mcp-hacker", instructions="Read-only, tenant-scoped access to bug-bounty program data. Every call is authorized by API key.", lifespan=lifespan)


def _rows(records) -> dict[str, Any]:
    """Schema-agnostic: any column type is serialised via to_jsonable, nothing is inferred."""
    rows = rows_from_records(records[:MAX_ROWS])
    return {"rows": redact_rows(rows), "row_count": len(rows), "truncated": len(records) > MAX_ROWS}


def _scope_filter() -> tuple[str, list[Any]]:
    t = current_tenant().tenant
    if t.mode == "schema":
        return "table_schema = $1", [t.schema_name]
    return "table_schema NOT IN ('pg_catalog', 'information_schema', 'pg_toast')", []


@mcp.tool
async def list_tables() -> dict[str, Any]:
    """List tables visible to this program (tenant)."""
    ctx = current_tenant()
    pool = await registry.get(ctx)
    where, params = _scope_filter()
    async with pool.acquire() as conn:
        rows = await conn.fetch(f"SELECT table_schema, table_name, table_type FROM information_schema.tables WHERE {where} ORDER BY 1, 2", *params)
    return _rows(rows)


@mcp.tool
async def describe_table(table: str) -> dict[str, Any]:
    """Column definitions of a table in the tenant scope. `table` may be `name` or `schema.name`."""
    ctx = current_tenant()
    t = ctx.tenant
    schema, _, name = table.rpartition(".")
    if t.mode == "schema":
        if schema and schema != t.schema_name:
            raise ToolError(f"schema {schema} is outside this tenant")
        schema = t.schema_name
    elif not schema:
        schema = "public"
    if schema in ("pg_catalog", "information_schema", "pg_toast"):
        raise ToolError("system schemas are not accessible")
    pool = await registry.get(ctx)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT column_name, data_type, is_nullable, column_default FROM information_schema.columns "
            "WHERE table_schema = $1 AND table_name = $2 ORDER BY ordinal_position", schema, name,
        )
    if not rows:
        raise ToolError(f"table {schema}.{name} not found in tenant scope")
    return _rows(rows)


@mcp.tool
async def run_select(sql: str) -> dict[str, Any]:
    """Run a single SELECT within the tenant scope (read-only connection + read-only transaction, row-limited)."""
    ctx = current_tenant()
    try:
        adm = admit(sql, "select", ctx.tenant)
    except SqlRejected as e:
        raise ToolError(f"rejected: {e}") from e
    pool = await registry.get(ctx)
    async with pool.acquire() as conn, conn.transaction(readonly=True):
        rows = await conn.fetch(adm.sql)
    return {"sql": adm.sql, **_rows(rows)}


async def _fetch_source(source: dict[str, Any], max_rows: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """`source` = {"sql": "<SELECT>"}; same guard and read-only transaction as run_select."""
    sql = source.get("sql") if isinstance(source, dict) else None
    if not isinstance(sql, str) or not sql.strip():
        raise ToolError("rejected: source must be {\"sql\": \"SELECT ...\"}")
    ctx = current_tenant()
    try:
        adm = admit(sql, "select", ctx.tenant)
    except SqlRejected as e:
        raise ToolError(f"rejected: {e}") from e
    pool = await registry.get(ctx)
    async with pool.acquire() as conn, conn.transaction(readonly=True):
        cur = await conn.cursor(adm.sql)
        records = await cur.fetch(max_rows + 1)
    truncated = len(records) > max_rows
    return arrow_rows_from_records(records[:max_rows]), {"sql": adm.sql, "source_truncated": truncated}


install_data_tools(mcp, _fetch_source, SETTINGS, source_doc='{"sql": "SELECT ..."} (tenant-scoped, read-only)')


def install_auth(server: FastMCP) -> FastMCP:
    server.add_middleware(TenantAuthMiddleware(SETTINGS, TOOL_PERMISSIONS))
    return server


def main() -> None:
    SETTINGS.ensure_safe_runtime()
    from mcpcluster_common.tracing import setup_tracing

    setup_tracing("mcp-hacker")
    install_auth(mcp)
    mcp.run(transport="http", host="0.0.0.0", port=int(os.environ.get("PORT", "9004")), path="/mcp", stateless_http=True)


if __name__ == "__main__":
    main()
