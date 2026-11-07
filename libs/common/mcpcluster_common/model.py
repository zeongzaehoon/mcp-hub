"""Redis db1 record shapes — mirror of backend/app/infra/redis/projection.py. Unknown fields are
ignored; missing required fields make the record invalid (=> deny)."""

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Status = Literal["active", "suspended", "deleted"]


class _Rec(BaseModel):
    model_config = ConfigDict(extra="ignore")
    status: Status
    version: int


class CompanyRec(_Rec):
    pass


class UserRec(_Rec):
    pass


class ServerRec(_Rec):
    server_type: str
    name: str = ""  # display name; the gateway derives tool namespaces from it
    dsn_enc: str | None = None  # encrypted datasource DSN; None => process env default


class Credentials(BaseModel):
    username: str
    password_enc: str


class TenantSpec(BaseModel):
    model_config = ConfigDict(extra="ignore")
    mode: Literal["schema", "database", "column"]
    schema_name: str | None = None
    database: str | None = None
    column: str | None = None
    value: str | None = None
    credentials: Credentials | None = None


class BindingRec(_Rec):
    server_type: str
    tenant: TenantSpec


class AccessRec(_Rec):
    """user -> server grant; the only place permissions/allowed_tools live."""

    server_type: str
    permissions: list[str]
    allowed_tools: list[str]


class ApiKeyRec(_Rec):
    """Acts on behalf of its user. server_ids None => every server the user has access to.
    The field is required (no default): a record without it is invalid => deny."""

    key_id: str
    company_id: str
    user_id: str
    server_ids: list[str] | None
    expires_at: datetime | None = None
    rate_limit_per_minute: int | None = None  # None => server default

    def is_expired(self, at: datetime | None = None) -> bool:
        return self.expires_at is not None and self.expires_at <= (at or datetime.now(UTC))


class AccessEvent(BaseModel):
    ts: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    key_id: str
    company_id: str
    user_id: str
    server_id: str
    server_type: str
    tool: str
    args_summary: str
    status: Literal["ok", "denied", "error"]
    error: str | None = None
    duration_ms: int = 0
    extra: dict[str, Any] = Field(default_factory=dict)
