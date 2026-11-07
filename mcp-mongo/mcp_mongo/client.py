import os
from urllib.parse import parse_qsl, quote_plus, urlencode, urlsplit, urlunsplit

from pymongo import AsyncMongoClient

from mcpcluster_common import ConnectionRegistry, TenantContext
from mcpcluster_common.secrets import decrypt
from mcpcluster_common.settings import CommonSettings

SETTINGS = CommonSettings()
BASE_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017/")
MAX_ROWS = int(os.environ.get("MAX_ROWS", "1000"))
MAX_INSERT = int(os.environ.get("MAX_INSERT", "1000"))
MAX_TIME_MS = int(os.environ.get("MAX_TIME_MS", "15000"))


def connect_args(ctx: TenantContext) -> tuple[str, dict]:
    """(uri, client kwargs). Per-tenant users authenticate against their own database; `authSource` is passed as a
    keyword (and removed from the URI) so a database name can never smuggle URI options such as `&tls=false`."""
    base = decrypt(SETTINGS, ctx.server_dsn_enc) if ctx.server_dsn_enc else BASE_URI
    if not ctx.tenant.credentials:
        return base, {}
    parts = urlsplit(base)
    hostport = parts.netloc.rsplit("@", 1)[-1]
    pw = decrypt(SETTINGS, ctx.tenant.credentials.password_enc)
    netloc = f"{quote_plus(ctx.tenant.credentials.username)}:{quote_plus(pw)}@{hostport}"
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k.lower() != "authsource"])
    return urlunsplit((parts.scheme, netloc, parts.path, query, parts.fragment)), {"authSource": ctx.tenant.database}


async def _make(ctx: TenantContext) -> AsyncMongoClient:
    uri, extra = connect_args(ctx)
    c = AsyncMongoClient(uri, appname=f"mcp-mongo/{ctx.company_id}", serverSelectionTimeoutMS=5000, maxPoolSize=int(os.environ.get("POOL_MAX", "8")), **extra)
    await c.admin.command("ping")
    return c


async def _close(c: AsyncMongoClient) -> None:
    await c.close()


registry: ConnectionRegistry[AsyncMongoClient] = ConnectionRegistry(_make, _close)
