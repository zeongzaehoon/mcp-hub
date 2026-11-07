"""Shared Postgres support for mcp-postgres and mcp-hacker: SQL admission guard + per-tenant pool factory."""

from mcpcluster_common.postgres.pool import PoolConfig, make_registry
from mcpcluster_common.postgres.sqlguard import Admitted, SqlRejected, admit

__all__ = ["Admitted", "PoolConfig", "SqlRejected", "admit", "make_registry"]
