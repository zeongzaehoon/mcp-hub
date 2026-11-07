"""asyncpg pool per tenant. Schema mode: base DSN + search_path pinned; database mode: DSN with the
database swapped; optional per-tenant credentials override user/password. `read_only=True` sets
default_transaction_read_only=on for the whole connection — a second wall behind the SQL guard."""

import json
from dataclasses import dataclass
from urllib.parse import quote, urlsplit, urlunsplit

import asyncpg

from mcpcluster_common.connections import ConnectionRegistry
from mcpcluster_common.context import TenantContext
from mcpcluster_common.secrets import decrypt
from mcpcluster_common.settings import CommonSettings


@dataclass(frozen=True)
class PoolConfig:
    base_dsn: str
    app_name: str
    statement_timeout_ms: int = 15000
    pool_max: int = 8
    read_only: bool = False


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def dsn_for(cfg: PoolConfig, settings: CommonSettings, ctx: TenantContext) -> str:
    # the server record's encrypted DSN wins; the process env DSN is only a fallback
    base = decrypt(settings, ctx.server_dsn_enc) if ctx.server_dsn_enc else cfg.base_dsn
    parts = urlsplit(base)
    netloc, path = parts.netloc, parts.path
    t = ctx.tenant
    if t.mode == "database":
        path = "/" + quote(t.database, safe="")
    if t.credentials:
        # percent-encode: a password with @ : / # ? must not be able to change the host or the database
        pw = decrypt(settings, t.credentials.password_enc)
        hostport = netloc.rsplit("@", 1)[-1]
        netloc = f"{quote(t.credentials.username, safe='')}:{quote(pw, safe='')}@{hostport}"
    return urlunsplit((parts.scheme, netloc, path, parts.query, parts.fragment))


async def _init_conn(conn: asyncpg.Connection) -> None:
    """Engine-agnostic decoding: json/jsonb become Python objects (asyncpg hands back text by default)."""
    for typ in ("json", "jsonb"):
        await conn.set_type_codec(typ, encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


def make_registry(cfg: PoolConfig, settings: CommonSettings) -> ConnectionRegistry[asyncpg.Pool]:
    async def _make(ctx: TenantContext) -> asyncpg.Pool:
        server_settings = {
            "statement_timeout": str(cfg.statement_timeout_ms),
            "application_name": f"{cfg.app_name}:{ctx.company_id}",
        }
        if cfg.read_only:
            server_settings["default_transaction_read_only"] = "on"
        if ctx.tenant.mode == "schema":
            server_settings["search_path"] = quote_ident(ctx.tenant.schema_name)  # mixed case / hyphens survive
        return await asyncpg.create_pool(dsn_for(cfg, settings, ctx), min_size=1, max_size=cfg.pool_max, server_settings=server_settings, init=_init_conn)

    async def _close(p: asyncpg.Pool) -> None:
        await p.close()

    return ConnectionRegistry(_make, _close)
