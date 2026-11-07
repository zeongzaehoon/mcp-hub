from contextvars import ContextVar
from dataclasses import dataclass

from mcpcluster_common.model import AccessRec, ApiKeyRec, TenantSpec


@dataclass(frozen=True)
class TenantContext:
    company_id: str
    user_id: str
    key_id: str
    server_id: str
    server_type: str
    permissions: frozenset[str]
    allowed_tools: frozenset[str]
    tenant: TenantSpec
    binding_version: int
    server_dsn_enc: str | None = None  # datasource of *this* server (multi-datasource per process)
    server_version: int = 0
    key_scope: tuple[str, ...] | None = None  # the key's server_ids; None => all of the user's servers
    key_hash: str = ""  # sha256 of the key (never the raw key); lets scheduled work re-authorize as this key

    @classmethod
    def from_records(cls, key: ApiKeyRec, access: AccessRec, *, server_id: str, tenant: TenantSpec, binding_version: int, server_dsn_enc: str | None = None, server_version: int = 0) -> "TenantContext":
        return cls(
            company_id=key.company_id, user_id=key.user_id, key_id=key.key_id, server_id=server_id,
            server_type=access.server_type, permissions=frozenset(access.permissions),
            allowed_tools=frozenset(access.allowed_tools), tenant=tenant, binding_version=binding_version,
            server_dsn_enc=server_dsn_enc, server_version=server_version,
            key_scope=tuple(key.server_ids) if key.server_ids is not None else None,
        )

    @property
    def connection_version(self) -> tuple[int, int]:
        """Pools are keyed on this: a binding OR server (DSN) change rebuilds the connection."""
        return (self.binding_version, self.server_version)


_current: ContextVar[TenantContext | None] = ContextVar("tenant_ctx", default=None)


def set_tenant(ctx: TenantContext | None):
    return _current.set(ctx)


def reset_tenant(token) -> None:
    _current.reset(token)


def current_tenant() -> TenantContext:
    """Tools call this. Raises if no authenticated tenant is bound — there is no anonymous path."""
    ctx = _current.get()
    if ctx is None:
        raise PermissionError("no tenant context bound to this request")
    return ctx
