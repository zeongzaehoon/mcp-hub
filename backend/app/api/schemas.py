from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, EmailStr, Field

from app.domain.enums import Permission, Role, ServerType, Status
from app.domain.models import (
    AccessPolicy,
    ApiKey,
    Branding,
    Company,
    Identity,
    IdentityProvider,
    McpServer,
    ServerAccess,
    Team,
    TeamMembership,
    TenantBinding,
    User,
    WorkspaceConnection,
)


class Page[T](BaseModel):
    items: list[T]
    next_cursor: str | None = None


class Error(BaseModel):
    code: str
    message: str


# ---- auth
class LoginIn(BaseModel):
    email: EmailStr
    password: str


class LoginOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: "UserOut"


# ---- company
class CompanyIn(BaseModel):
    company_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,62}$")
    name: str


class CompanyPatch(BaseModel):
    name: str | None = None
    status: Status | None = None


class CompanyOut(BaseModel):
    company_id: str
    name: str
    status: Status
    created_by: str
    branding: Branding | None = None
    version: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, c: Company) -> "CompanyOut":
        return cls(**c.model_dump())


# ---- user
class UserIn(BaseModel):
    email: EmailStr
    name: str
    role: Role
    password: str | None = Field(default=None, min_length=10)


class UserPatch(BaseModel):
    name: str | None = None
    status: Status | None = None
    password: str | None = Field(default=None, min_length=10)


class UserOut(BaseModel):
    user_id: str
    company_id: str
    email: str
    name: str
    role: Role
    status: Status
    can_login: bool
    identities: list[Identity] = []  # linked SSO logins (issuer + subject)
    source: str = "manual"  # manual | workspace (created by the directory sync)
    version: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, u: User) -> "UserOut":
        return cls(**u.model_dump(exclude={"password_hash"}), can_login=u.role != Role.MEMBER and bool(u.password_hash))


# ---- company SSO (OIDC provider)
class SsoProviderIn(BaseModel):
    platform: Literal["google"] | None = None  # "google": use the platform's Google client; issuer/client fields ignored
    issuer: str | None = Field(default=None, min_length=8, max_length=500)
    client_id: str | None = Field(default=None, min_length=1, max_length=500)
    client_secret: str | None = Field(default=None, min_length=1, max_length=2000)  # write-only; None keeps the stored one
    domains: list[str] = Field(min_length=1, max_length=20)
    hosted_domain: str | None = None
    auto_provision: bool = True
    status: Status | None = None


class SsoProviderOut(BaseModel):
    company_id: str
    kind: str
    platform: str | None
    issuer: str
    client_id: str | None
    has_secret: bool
    domains: list[str]
    hosted_domain: str | None
    auto_provision: bool
    status: Status
    redirect_uri: str  # register this at the provider
    version: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, i: IdentityProvider, redirect_uri: str) -> "SsoProviderOut":
        return cls(**i.model_dump(exclude={"client_secret_enc"}), has_secret=bool(i.client_secret_enc), redirect_uri=redirect_uri)


class SsoOptionsOut(BaseModel):
    """Public: what the deployment offers."""

    google: bool  # platform Google client configured => a company may use a `platform: google` provider


# ---- workspace directory connection + teams
class WorkspaceIn(BaseModel):
    domain: str = Field(min_length=3, max_length=253)
    admin_email: EmailStr  # delegated subject (a Workspace admin of that domain)
    service_account_json: str | None = Field(default=None, max_length=20_000)  # write-only; None keeps the stored key


class WorkspaceOut(BaseModel):
    company_id: str
    provider: str
    domain: str
    admin_email: str
    client_email: str
    client_id: str
    connected_by: str
    scopes: list[str]
    has_credentials: bool
    status: Status
    last_sync_at: datetime | None
    last_sync: dict[str, Any] | None
    sync_error: str | None
    needs_reconnect: bool
    version: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, w: WorkspaceConnection) -> "WorkspaceOut":
        # a connection from before service accounts (no key) needs an operator just like a refused one
        return cls(**w.model_dump(exclude={"service_account_enc", "needs_reconnect"}), has_credentials=bool(w.service_account_enc),
                   needs_reconnect=w.needs_reconnect or not w.service_account_enc)


class SyncOut(BaseModel):
    users_seen: int
    users_created: int
    users_updated: int
    users_suspended: int
    users_reactivated: int
    teams_seen: int
    teams_created: int
    teams_updated: int
    teams_suspended: int
    members_added: int
    members_removed: int
    skipped: list[str]
    policies: dict[str, Any] | None = None


class TeamOut(BaseModel):
    team_id: str
    company_id: str
    name: str
    email: str | None
    external_id: str | None
    source: str
    status: Status
    version: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, t: Team) -> "TeamOut":
        return cls(**t.model_dump())


class TeamMemberOut(BaseModel):
    team_id: str
    user_id: str
    source: str
    status: Status

    @classmethod
    def of(cls, m: TeamMembership) -> "TeamMemberOut":
        return cls(**m.model_dump(include={"team_id", "user_id", "source", "status"}))


class SsoTestOut(BaseModel):
    issuer: str
    authorization_endpoint: str
    jwks_keys: int
    redirect_uri: str


class SsoHandoffIn(BaseModel):
    code: str = Field(min_length=1, max_length=128)


class SsoHandoffOut(BaseModel):
    raw_key: str
    key_id: str
    company_id: str
    user_id: str
    email: str


# ---- server
class ServerIn(BaseModel):
    server_type: ServerType
    name: str
    description: str = ""
    endpoint: str = ""
    dsn: str | None = Field(default=None, max_length=2000)  # datasource connection; stored encrypted, never returned


class ServerPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    endpoint: str | None = None
    status: Status | None = None
    dsn: str | None = Field(default=None, max_length=2000)
    clear_dsn: bool = False


class ServerOut(BaseModel):
    server_id: str
    server_type: ServerType
    name: str
    description: str
    endpoint: str
    status: Status
    tools: dict[str, Permission]
    has_connection: bool
    connection_hint: str | None  # scheme://user@host:port/db with the password masked
    version: int
    created_at: datetime
    updated_at: datetime
    health: "HealthOut | None" = None  # liveness of the MCP service of this type (backend health checker), when configured

    @classmethod
    def of(cls, s: McpServer, health: dict | None = None) -> "ServerOut":
        from urllib.parse import urlsplit

        from app.core.security import decrypt_secret
        from app.domain.tool_catalog import tools_for

        hint = None
        if s.connection_enc:
            try:
                u = urlsplit(decrypt_secret(s.connection_enc))
                host = u.hostname or ""
                port = f":{u.port}" if u.port else ""
                user = f"{u.username}@" if u.username else ""
                hint = f"{u.scheme}://{user}{host}{port}{u.path or ''}"
            except Exception:  # noqa: BLE001
                hint = "(undecryptable with current TENANT_SECRET_KEY)"
        return cls(**s.model_dump(exclude={"connection_enc"}), tools=tools_for(s.server_type), has_connection=s.connection_enc is not None, connection_hint=hint,
                   health=HealthOut(**health) if health else None)


# ---- binding
class BindingIn(BaseModel):
    server_id: str
    tenant: dict[str, Any]  # validated by TenantSpec in the domain; credentials.password accepted here, stored encrypted


class BindingPatch(BaseModel):
    tenant: dict[str, Any] | None = None
    status: Status | None = None


class BindingOut(BaseModel):
    company_id: str
    server_id: str
    server_type: ServerType
    tenant: dict[str, Any]
    status: Status
    version: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, b: TenantBinding) -> "BindingOut":
        d = b.model_dump(mode="json")
        creds = d["tenant"].get("credentials")
        if creds:
            d["tenant"]["credentials"] = {"username": creds["username"], "password_enc": "***"}
        return cls(**d)


# ---- server access (company -> user -> server)
class AccessIn(BaseModel):
    server_id: str
    permissions: set[Permission]
    allowed_tools: set[str]


class AccessPatch(BaseModel):
    status: Status | None = None
    permissions: set[Permission] | None = None
    allowed_tools: set[str] | None = None


class AccessOut(BaseModel):
    company_id: str
    user_id: str
    server_id: str
    server_type: ServerType
    permissions: list[Permission]
    allowed_tools: list[str]
    status: Status
    source: str = "manual"  # manual | policy (materialised from access policies; an operator edit makes it manual)
    policy_ids: list[str] = []
    version: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, a: ServerAccess) -> "AccessOut":
        d = a.model_dump()
        d["permissions"] = sorted(d["permissions"])
        d["allowed_tools"] = sorted(d["allowed_tools"])
        return cls(**d)


# ---- access policies (team | everyone -> server), materialised into ServerAccess rows
class PolicyIn(BaseModel):
    team_id: str | None = None  # None => everyone in the company
    server_id: str
    permissions: set[Permission]
    allowed_tools: set[str]


class PolicyPatch(BaseModel):
    status: Status | None = None
    permissions: set[Permission] | None = None
    allowed_tools: set[str] | None = None


class PolicyOut(BaseModel):
    policy_id: str
    company_id: str
    team_id: str | None
    server_id: str
    server_type: ServerType
    permissions: list[Permission]
    allowed_tools: list[str]
    status: Status
    version: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, p: AccessPolicy) -> "PolicyOut":
        d = p.model_dump()
        d["permissions"] = sorted(d["permissions"])
        d["allowed_tools"] = sorted(d["allowed_tools"])
        return cls(**d)


class ApplyOut(BaseModel):
    users: int
    created: int
    updated: int
    suspended: int
    reactivated: int
    kept_manual: int
    errors: list[str]


# ---- api key (acts on behalf of its user; server_ids=None => all of the user's servers)
class ApiKeyIn(BaseModel):
    user_id: str
    name: str
    server_ids: list[str] | None = None
    expires_at: datetime | None = None
    rate_limit_per_minute: int | None = Field(default=None, ge=1, le=1_000_000)


class ApiKeyPatch(BaseModel):
    name: str | None = None
    status: Status | None = None
    server_ids: list[str] | None = None
    scope_all: bool = False
    expires_at: datetime | None = None
    clear_expiry: bool = False
    rate_limit_per_minute: int | None = Field(default=None, ge=1, le=1_000_000)
    clear_rate_limit: bool = False


class ApiKeyOut(BaseModel):
    key_id: str
    company_id: str
    user_id: str
    server_ids: list[str] | None
    key_prefix: str
    name: str
    status: Status
    expires_at: datetime | None
    last_used_at: datetime | None
    rate_limit_per_minute: int | None
    version: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, k: ApiKey) -> "ApiKeyOut":
        return cls(**k.model_dump(exclude={"key_hash"}))


class ApiKeyIssued(BaseModel):
    key: ApiKeyOut
    raw_key: str  # shown once


class ResolveOut(BaseModel):
    key: ApiKeyOut | None
    effective: str  # active | denied:<reason> | unknown  (key/company/user level)
    servers: dict[str, str] = {}  # server_id -> active | denied:<reason> for each server in the key's scope
    redis_in_sync: bool


class ReconcileOut(BaseModel):
    checked: int
    repaired: int
    removed_stray: int
    errors: list[str]


# ---- dashboards (read-only)
class HealthOut(BaseModel):
    kind: str  # server type or "gateway"
    ok: bool | None  # None => configured but not checked yet
    latency_ms: int | None
    checked_at: datetime | None
    error: str | None


class MetricsPoint(BaseModel):
    hour: datetime
    n: int
    ok: int
    denied: int
    error: int


class MetricsOut(BaseModel):
    hours: int
    calls: int
    ok: int
    denied: int
    errors: int
    denied_rate: float
    avg_ms: float | None
    max_ms: int | None
    p50_ms: int | None  # histogram upper bound
    p99_ms: int | None
    series: list[MetricsPoint]


class StatusCounts(BaseModel):
    active: int
    suspended: int
    deleted: int
    total: int


class ExpiringKey(BaseModel):
    key_id: str
    company_id: str
    user_id: str
    name: str
    key_prefix: str
    expires_at: datetime | None
    status: Status


class KeysExpiring(BaseModel):
    days: int
    count: int
    expired_still_active: int
    items: list[ExpiringKey]


class GlobalStatsOut(BaseModel):
    computed_at: datetime
    counts: dict[str, StatusCounts]
    bindings_per_server: dict[str, int]
    keys_expiring: KeysExpiring
    calls_24h: MetricsOut | None = None


class CompanyStatsOut(BaseModel):
    company_id: str
    computed_at: datetime
    counts: dict[str, StatusCounts]
    bindings_per_server: dict[str, int]
    keys_expiring: KeysExpiring
    calls_24h: MetricsOut | None = None


class EventOut(BaseModel):
    ts: str | None = None
    company_id: str | None = None
    user_id: str | None = None
    key_id: str | None = None
    server_id: str | None = None
    server_type: str | None = None
    tool: str | None = None
    status: str | None = None
    duration_ms: int | None = None
    error: str | None = None


class ArtifactOut(BaseModel):
    artifact_id: str
    kind: str
    title: str
    server_id: str
    server_type: str
    sources: list[str]
    user_id: str
    created_at: str
    expires_at: str
    bytes: int
    expired: bool
