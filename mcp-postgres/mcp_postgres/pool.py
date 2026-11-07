import os

from mcpcluster_common.postgres import PoolConfig, make_registry
from mcpcluster_common.settings import CommonSettings

SETTINGS = CommonSettings()
registry = make_registry(
    PoolConfig(
        base_dsn=os.environ.get("BASE_DSN", "postgresql://mcp:mcp@localhost:5432/sample"),
        app_name="mcp-postgres",
        statement_timeout_ms=int(os.environ.get("STATEMENT_TIMEOUT_MS", "15000")),
        pool_max=int(os.environ.get("POOL_MAX", "8")),
    ),
    SETTINGS,
)
