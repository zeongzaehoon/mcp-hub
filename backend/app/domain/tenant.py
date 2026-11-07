"""Tenant isolation spec: how a company's data is separated inside a datasource.

Each server type accepts a subset of modes. Validation happens at binding creation time so a
binding can never reach Redis with a mode the MCP server cannot enforce.
"""

import re
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

from app.domain.enums import ServerType

# Identifiers are ALWAYS quoted where they are interpolated (search_path, sqlglot identifiers, backticks),
# so we only forbid what quoting cannot carry: control chars, quote characters, backslash, semicolon.
# Per-engine rules (e.g. MongoDB database names) are checked in validate_tenant().
Ident = Annotated[str, StringConstraints(min_length=1, max_length=63, pattern=r'^[^\x00-\x1f"`\\;]+$')]
_MONGO_DB = re.compile(r'^[^/\\. "$*<>:|?&=#%\x00]{1,63}$')  # also no URI metacharacters (authSource)


class TenantCredentials(BaseModel):
    """Optional per-company datasource credentials. Stored encrypted (Fernet) at rest and in Redis."""

    username: str
    password_enc: str  # ciphertext; never plaintext beyond the request boundary


class SchemaTenant(BaseModel):
    mode: Literal["schema"] = "schema"
    schema_name: Ident
    credentials: TenantCredentials | None = None


class DatabaseTenant(BaseModel):
    mode: Literal["database"] = "database"
    database: Ident
    credentials: TenantCredentials | None = None


class ColumnTenant(BaseModel):
    mode: Literal["column"] = "column"
    column: Ident
    value: str = Field(min_length=1, max_length=128)
    database: Ident | None = None  # which database holds the shared tables; None => server default
    credentials: TenantCredentials | None = None


TenantSpec = Annotated[SchemaTenant | DatabaseTenant | ColumnTenant, Field(discriminator="mode")]

ALLOWED_MODES: dict[ServerType, frozenset[str]] = {
    # column mode for postgres requires SQL rewriting; deferred until the rust/sqlglot path lands
    ServerType.POSTGRES: frozenset({"schema", "database"}),
    ServerType.HACKER: frozenset({"schema", "database"}),
    ServerType.CLICKHOUSE: frozenset({"database", "column"}),
    ServerType.MONGO: frozenset({"database"}),
}


def assert_mode_supported(server_type: ServerType, spec: SchemaTenant | DatabaseTenant | ColumnTenant) -> None:
    if spec.mode not in ALLOWED_MODES[server_type]:
        raise ValueError(
            f"tenant mode '{spec.mode}' is not supported for server type '{server_type}'; "
            f"allowed: {sorted(ALLOWED_MODES[server_type])}"
        )
    if server_type == ServerType.MONGO and isinstance(spec, DatabaseTenant) and not _MONGO_DB.match(spec.database):
        raise ValueError("MongoDB database names may not contain / \\ . \" $ * < > : | ? & = # % or spaces")
