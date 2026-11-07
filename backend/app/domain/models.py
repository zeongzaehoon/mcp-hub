"""Domain entities. Pure data + local invariants; cross-entity rules live in services and are
enforced transactionally against DynamoDB."""

import base64
import re
from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import BaseModel, EmailStr, Field, StringConstraints, field_validator, model_validator

from app.domain.enums import SYSTEM_COMPANY_ID, Permission, Role, ServerType, Status
from app.domain.tenant import ColumnTenant, DatabaseTenant, SchemaTenant, TenantSpec, assert_mode_supported
from app.domain.tool_catalog import tools_for

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Slug = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]{1,62}$")]


def now() -> datetime:
    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:20]}"


class Versioned(BaseModel):
    version: int = 1
    created_at: datetime = Field(default_factory=now)
    updated_at: datetime = Field(default_factory=now)


_HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")
LOGO_MAX_BYTES = 200_000


class Branding(BaseModel):
    """Per-company report branding. The logo is a small PNG/JPEG (checked by magic bytes here; the renderer re-opens it
    with Pillow and a pixel cap). Colours are #RRGGBB."""

    primary_color: str | None = None
    logo_base64: str | None = None
    report_footer: str | None = Field(default=None, max_length=200)

    @field_validator("primary_color")
    @classmethod
    def _color(cls, v: str | None) -> str | None:
        if v is not None and not _HEX_COLOR.match(v):
            raise ValueError("primary_color must be #RRGGBB")
        return v.upper() if v else v

    @field_validator("logo_base64")
    @classmethod
    def _logo(cls, v: str | None) -> str | None:
        if v is None:
            return v
        try:
            raw = base64.b64decode(v, validate=True)
        except Exception as e:
            raise ValueError("logo_base64 is not valid base64") from e
        if len(raw) > LOGO_MAX_BYTES:
            raise ValueError(f"logo must be at most {LOGO_MAX_BYTES // 1000} KB")
        if not raw.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff")):
            raise ValueError("logo must be PNG or JPEG")
        return v


class Company(Versioned):
    company_id: Slug
    name: Name
    status: Status = Status.ACTIVE
    created_by: str
    branding: Branding | None = None

    @property
    def is_system(self) -> bool:
        return self.company_id == SYSTEM_COMPANY_ID


class Identity(BaseModel):
    """An external identity linked to a user: the OIDC issuer and its stable subject. Never the email, which can
    be reassigned at the provider."""

    issuer: str = Field(min_length=1, max_length=500)
    subject: str = Field(min_length=1, max_length=500)


Source = Literal["manual", "workspace"]  # who owns a record: an operator, or the directory sync (manual always wins)


class User(Versioned):
    user_id: str = Field(default_factory=lambda: new_id("usr"))
    company_id: Slug
    email: EmailStr
    name: Name
    role: Role
    status: Status = Status.ACTIVE
    password_hash: str | None = None  # only master/admin can log in
    identities: list[Identity] = Field(default_factory=list)  # external logins (SSO); one per issuer
    source: Source = "manual"
    external_id: str | None = None  # directory id (Google: equals the OIDC `sub`)
    suspended_by_sync: bool = False  # the sync suspended this user; only then may the sync reactivate them

    @field_validator("email")
    @classmethod
    def _lower(cls, v: str) -> str:
        return v.lower()

    @model_validator(mode="after")
    def _role_company_consistency(self) -> "User":
        if self.role == Role.MASTER and self.company_id != SYSTEM_COMPANY_ID:
            raise ValueError("master users must belong to the system company")
        if self.role != Role.MASTER and self.company_id == SYSTEM_COMPANY_ID:
            raise ValueError("system company may only contain master users")
        return self


_DOMAIN = re.compile(r"^(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$")


class IdentityProvider(Versioned):
    """A company's OpenID Connect provider (Google Workspace, Okta, Entra ID, Keycloak, ...). Authentication only:
    who the person is. What they may do stays on ServerAccess. One per company; email domains are unique across
    companies so an email maps to exactly one provider."""

    company_id: Slug
    kind: str = "oidc"
    # "google": the platform's own Google OAuth client (settings) — no client id/secret stored here, issuer = Google.
    platform: Literal["google"] | None = None
    issuer: str = Field(min_length=8, max_length=500)  # discovery at {issuer}/.well-known/openid-configuration
    client_id: str | None = Field(default=None, max_length=500)
    client_secret_enc: str | None = None  # Fernet(client_secret)
    domains: list[str] = Field(min_length=1, max_length=20)  # verified-email domains this provider vouches for
    hosted_domain: str | None = None  # Google Workspace `hd` claim; checked when set
    auto_provision: bool = True  # unknown but domain-matching users are created as members
    status: Status = Status.ACTIVE

    @field_validator("issuer")
    @classmethod
    def _issuer(cls, v: str) -> str:
        v = v.strip().rstrip("/")
        if not v.startswith(("https://", "http://")) or any(ch.isspace() for ch in v) or "#" in v or "?" in v:
            raise ValueError("issuer must be a plain https URL")
        return v

    @field_validator("domains")
    @classmethod
    def _domains(cls, v: list[str]) -> list[str]:
        out = sorted({d.strip().lower() for d in v})
        bad = [d for d in out if not _DOMAIN.match(d)]
        if bad:
            raise ValueError(f"invalid email domain: {bad}")
        return out

    @field_validator("hosted_domain")
    @classmethod
    def _hd(cls, v: str | None) -> str | None:
        if v is None or not v.strip():
            return None
        v = v.strip().lower()
        if not _DOMAIN.match(v):
            raise ValueError("hosted_domain must be a domain")
        return v

    @model_validator(mode="after")
    def _not_system(self) -> "IdentityProvider":
        if self.company_id == SYSTEM_COMPANY_ID:
            raise ValueError("system company has no identity provider")
        if self.kind != "oidc":
            raise ValueError("only oidc providers are supported")
        if self.platform is None and not (self.client_id and self.client_secret_enc):
            raise ValueError("client_id and client_secret are required unless a platform provider is used")
        return self


class WorkspaceConnection(Versioned):
    """A company's directory connection (Google Workspace): a service-account key with domain-wide delegation
    (Fernet-encrypted, registered by an operator), the admin it impersonates, and the state of the last sync. One per
    company; the domain is unique across companies (`UNIQ#WSDOMAIN#`)."""

    company_id: Slug
    provider: Literal["google"] = "google"
    domain: str
    admin_email: str = ""  # delegated subject: a Workspace admin with directory read rights
    client_email: str = ""  # service account email
    client_id: str = ""  # service account OAuth2 client id: what the Workspace admin authorises in Google Admin
    connected_by: str  # operator (email) who registered the key
    service_account_enc: str | None = None  # Fernet(ServiceAccountKey.dumps())
    scopes: list[str] = Field(default_factory=list)  # directory scopes the delegation must allow
    status: Status = Status.ACTIVE
    last_sync_at: datetime | None = None
    last_sync: dict[str, Any] | None = None  # counts of the last run
    sync_error: str | None = None
    needs_reconnect: bool = False  # key missing or refused (delegation not authorised, key deleted): an operator acts


def workspace_team_id(company_id: str, external_id: str) -> str:
    """Deterministic id for a synced team, so re-syncs and concurrent runs converge on one row."""
    import hashlib

    return "team_" + hashlib.sha256(f"google:{company_id}:{external_id}".encode()).hexdigest()[:20]


class Team(Versioned):
    """A group of users of one company. Synced teams mirror directory groups; manual teams are an operator's."""

    team_id: str = Field(default_factory=lambda: new_id("team"))
    company_id: Slug
    name: Name
    email: str | None = None  # group address (directory)
    external_id: str | None = None
    source: Source = "manual"
    status: Status = Status.ACTIVE


class TeamMembership(Versioned):
    company_id: Slug
    team_id: str
    user_id: str
    source: Source = "manual"
    status: Status = Status.ACTIVE


class McpServer(Versioned):
    server_id: str = Field(default_factory=lambda: new_id("srv"))
    server_type: ServerType
    name: Name
    description: str = ""
    status: Status = Status.ACTIVE
    endpoint: str = ""  # informational; where the MCP server is reachable
    connection_enc: str | None = None  # Fernet(DSN) of the datasource; None => MCP process env default

    @property
    def tool_names(self) -> set[str]:
        return set(tools_for(self.server_type))


class TenantBinding(Versioned):
    """Company <-> McpServer edge holding the tenant discriminator."""

    company_id: Slug
    server_id: str
    server_type: ServerType
    tenant: TenantSpec
    status: Status = Status.ACTIVE

    @model_validator(mode="after")
    def _mode_supported(self) -> "TenantBinding":
        assert_mode_supported(self.server_type, self.tenant)
        return self


class ServerAccess(Versioned):
    """User <-> McpServer edge: what this user may do on that server. The hierarchy is
    company -> user -> server; API keys only act on behalf of the user and never widen this."""

    company_id: Slug
    user_id: str
    server_id: str
    server_type: ServerType
    permissions: set[Permission]
    allowed_tools: set[str]
    status: Status = Status.ACTIVE
    source: Literal["manual", "policy"] = "manual"  # policy rows are owned by AccessPolicy materialisation
    policy_ids: list[str] = Field(default_factory=list)  # the policies that produced this row (source=policy)

    @model_validator(mode="after")
    def _tools_consistent(self) -> "ServerAccess":
        catalog = tools_for(self.server_type)
        unknown = self.allowed_tools - set(catalog)
        if unknown:
            raise ValueError(f"unknown tools for {self.server_type}: {sorted(unknown)}")
        if not self.allowed_tools:
            raise ValueError("allowed_tools must not be empty")
        if not self.permissions:
            raise ValueError("permissions must not be empty")
        uncovered = {t for t in self.allowed_tools if catalog[t] not in self.permissions}
        if uncovered:
            raise ValueError(
                f"tools {sorted(uncovered)} require permissions not granted; "
                "grant the permission or remove the tool"
            )
        return self


def policy_id_for(company_id: str, team_id: str | None, server_id: str) -> str:
    """One policy per (team | everyone, server); the id encodes the pair so duplicates collide on create."""
    import hashlib

    return "pol_" + hashlib.sha256(f"{company_id}:{team_id or '*'}:{server_id}".encode()).hexdigest()[:20]


class AccessPolicy(Versioned):
    """What a team (or everyone in the company, team_id=None) may do on a server. Materialised into ServerAccess rows
    (`source=policy`) for the users who are members; a manual ServerAccess on the same (user, server) wins and is
    never touched. Same tool/permission rules as ServerAccess."""

    policy_id: str
    company_id: Slug
    team_id: str | None = None
    server_id: str
    server_type: ServerType
    permissions: set[Permission]
    allowed_tools: set[str]
    status: Status = Status.ACTIVE

    @model_validator(mode="after")
    def _tools_consistent(self) -> "AccessPolicy":
        catalog = tools_for(self.server_type)
        unknown = self.allowed_tools - set(catalog)
        if unknown:
            raise ValueError(f"unknown tools for {self.server_type}: {sorted(unknown)}")
        if not self.allowed_tools or not self.permissions:
            raise ValueError("allowed_tools and permissions must not be empty")
        uncovered = {t for t in self.allowed_tools if catalog[t] not in self.permissions}
        if uncovered:
            raise ValueError(f"tools {sorted(uncovered)} require permissions not granted")
        if self.policy_id != policy_id_for(self.company_id, self.team_id, self.server_id):
            raise ValueError("policy_id does not match (company, team, server)")
        return self


MAX_KEY_SCOPE = 50  # each scoped server adds a ConditionCheck; TransactWriteItems caps at 100


class ApiKey(Versioned):
    """Acts on behalf of its user. `server_ids` narrows which of the user's server accesses the key
    may use; None means every access the user holds (now or later). Permissions live on ServerAccess."""

    key_id: str = Field(default_factory=lambda: new_id("key"))
    company_id: Slug
    user_id: str
    server_ids: list[str] | None
    key_hash: str = Field(min_length=64, max_length=64)
    key_prefix: str
    name: Name
    status: Status = Status.ACTIVE
    expires_at: datetime | None = None
    last_used_at: datetime | None = None
    rate_limit_per_minute: int | None = Field(default=None, ge=1, le=1_000_000)  # None => server default

    @model_validator(mode="after")
    def _scope_valid(self) -> "ApiKey":
        if self.server_ids is not None:
            if not self.server_ids:
                raise ValueError("server_ids must not be empty; use null for all of the user's servers")
            if len(set(self.server_ids)) != len(self.server_ids):
                raise ValueError("server_ids must be unique")
            if len(self.server_ids) > MAX_KEY_SCOPE:
                raise ValueError(f"at most {MAX_KEY_SCOPE} servers per key")
            self.server_ids = sorted(self.server_ids)
        if self.expires_at is not None and self.expires_at.tzinfo is None:
            raise ValueError("expires_at must be timezone-aware")
        return self

    def is_expired(self, at: datetime | None = None) -> bool:
        return self.expires_at is not None and self.expires_at <= (at or now())


__all__ = [
    "ApiKey",
    "Branding",
    "ColumnTenant",
    "Company",
    "DatabaseTenant",
    "McpServer",
    "SchemaTenant",
    "ServerAccess",
    "TenantBinding",
    "User",
    "new_id",
    "now",
]
