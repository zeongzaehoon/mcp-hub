"""Per-tenant ClickHouse async client. Database mode connects with the tenant database as default;
column mode connects to the shared default database. Optional per-tenant credentials override."""

import os
from urllib.parse import parse_qs, urlsplit

import clickhouse_connect
from clickhouse_connect.driver.asyncclient import AsyncClient

from mcpcluster_common import ConnectionRegistry, TenantContext
from mcpcluster_common.secrets import decrypt
from mcpcluster_common.settings import CommonSettings

SETTINGS = CommonSettings()
HOST = os.environ.get("CLICKHOUSE_HOST", "localhost")
PORT = int(os.environ.get("CLICKHOUSE_PORT", "8123"))
USER = os.environ.get("CLICKHOUSE_USER", "default")
PASSWORD = os.environ.get("CLICKHOUSE_PASSWORD", "")
DEFAULT_DB = os.environ.get("CLICKHOUSE_DEFAULT_DB", "default")
MAX_ROWS = int(os.environ.get("MAX_ROWS", "1000"))
AGG_MAX_ROWS = int(os.environ.get("AGG_MAX_ROWS", "500000"))
MAX_EXECUTION_SECONDS = int(os.environ.get("MAX_EXECUTION_SECONDS", "30"))

# applied to every user query; readonly=1 also blocks SET / DDL at the server
QUERY_SETTINGS = {
    "readonly": 1,
    "max_execution_time": MAX_EXECUTION_SECONDS,
    "max_result_rows": MAX_ROWS + 1,
    "result_overflow_mode": "break",
    "allow_ddl": 0,
}
AGG_SETTINGS = {**QUERY_SETTINGS, "max_result_rows": AGG_MAX_ROWS + 1}


def _conn_params(ctx: TenantContext) -> dict:
    """Connection parameters from the server record's DSN (clickhouse[s]://user:pw@host:port/db?secure=1),
    falling back to the process env. Keeps servers of the same type on one process."""
    params = {"host": HOST, "port": PORT, "username": USER, "password": PASSWORD, "database": DEFAULT_DB, "secure": False}
    if ctx.server_dsn_enc:
        u = urlsplit(decrypt(SETTINGS, ctx.server_dsn_enc))
        q = parse_qs(u.query)
        secure = u.scheme in ("clickhouses", "https") or q.get("secure", ["0"])[0] in ("1", "true", "True")
        params.update({
            "host": u.hostname or HOST,
            "port": u.port or (8443 if secure else 8123),
            "username": u.username or "default",
            "password": u.password or "",
            "database": (u.path or "/").lstrip("/") or "default",
            "secure": secure,
        })
    return params


def database_for(ctx: TenantContext) -> str:
    """Database every query is pinned to: tenant database (database mode) or the shared database
    holding the tenant column (column mode: binding.database, else DSN/env default)."""
    if ctx.tenant.mode == "database":
        return ctx.tenant.database
    return ctx.tenant.database or _conn_params(ctx)["database"]


async def _make_client(ctx: TenantContext) -> AsyncClient:
    params = _conn_params(ctx)
    if ctx.tenant.credentials:
        params["username"] = ctx.tenant.credentials.username
        params["password"] = decrypt(SETTINGS, ctx.tenant.credentials.password_enc)
    params["database"] = database_for(ctx)
    return await clickhouse_connect.get_async_client(**params, client_name=f"mcp-clickhouse/{ctx.company_id}")


async def _close_client(c: AsyncClient) -> None:
    await c.close()


registry: ConnectionRegistry[AsyncClient] = ConnectionRegistry(_make_client, _close_client)
