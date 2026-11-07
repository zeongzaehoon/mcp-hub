import logging
import os
from contextlib import asynccontextmanager
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from mcp_postgres.pool import SETTINGS, registry
from mcp_postgres.sqlguard import SqlRejected, admit
from mcpcluster_common import TenantAuthMiddleware, current_tenant
from mcpcluster_common.postprocess import redact_rows
from mcpcluster_common.monitor import start_background, stop_background
from mcpcluster_common.datatools import TOOL_PERMISSIONS as DATA_TOOLS, install_data_tools
from mcpcluster_common.serialize import arrow_rows_from_records, rows_from_records

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("mcp-postgres")

# Must equal backend/app/domain/tool_catalog.py[ServerType.POSTGRES]
TOOL_PERMISSIONS: dict[str, str] = {
    "list_tables": "read",
    "describe_table": "read",
    "run_select": "read",
    "run_insert": "create",
    "run_update": "update",
    "run_delete": "delete",
    **DATA_TOOLS,  # transform (read) + export (export) — engine-agnostic, from mcpcluster_common
}
MAX_ROWS = int(os.environ.get("MAX_ROWS", "1000"))


@asynccontextmanager
async def lifespan(_app):
    start_background()  # monitor scheduler
    try:
        yield
    finally:
        await stop_background()
        await registry.close_all()


mcp = FastMCP("mcp-postgres", instructions="Tenant-scoped Postgres access. Every call is authorized by API key.", lifespan=lifespan)


def _rows(records) -> dict[str, Any]:
    """Schema-agnostic: any column type is serialised via to_jsonable, nothing is inferred."""
    rows = rows_from_records(records[:MAX_ROWS])
    return {"rows": redact_rows(rows), "row_count": len(rows), "truncated": len(records) > MAX_ROWS}


def _scope_filter() -> tuple[str, list[Any]]:
    """WHERE fragment restricting catalog queries to the tenant's visible schemas."""
    t = current_tenant().tenant
    if t.mode == "schema":
        return "table_schema = $1", [t.schema_name]
    return "table_schema NOT IN ('pg_catalog', 'information_schema', 'pg_toast')", []


@mcp.tool
async def list_tables() -> dict[str, Any]:
    """List tables visible to this tenant."""
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


async def _execute(sql: str, kind: str) -> dict[str, Any]:
    ctx = current_tenant()
    try:
        adm = admit(sql, kind, ctx.tenant)
    except SqlRejected as e:
        raise ToolError(f"rejected: {e}") from e
    pool = await registry.get(ctx)
    async with pool.acquire() as conn:
        async with conn.transaction(readonly=(kind == "select")):
            if kind == "select":
                rows = await conn.fetch(adm.sql)
                return {"sql": adm.sql, **_rows(rows)}
            status = await conn.execute(adm.sql)
    affected = int(status.split()[-1]) if status and status.split()[-1].isdigit() else None
    return {"sql": adm.sql, "status": status, "affected_rows": affected}


@mcp.tool
async def run_select(sql: str) -> dict[str, Any]:
    """Run a single SELECT within the tenant scope (read-only transaction, row-limited)."""
    return await _execute(sql, "select")


@mcp.tool
async def run_insert(sql: str) -> dict[str, Any]:
    """Run a single INSERT within the tenant scope."""
    return await _execute(sql, "insert")


@mcp.tool
async def run_update(sql: str) -> dict[str, Any]:
    """Run a single UPDATE within the tenant scope."""
    return await _execute(sql, "update")


@mcp.tool
async def run_delete(sql: str) -> dict[str, Any]:
    """Run a single DELETE within the tenant scope."""
    return await _execute(sql, "delete")


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

    setup_tracing("mcp-postgres")
    install_auth(mcp)
    mcp.run(transport="http", host="0.0.0.0", port=int(os.environ.get("PORT", "9001")), path="/mcp", stateless_http=True)


if __name__ == "__main__":
    main()
