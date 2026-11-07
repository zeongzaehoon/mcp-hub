"""What each entity looks like in Redis db1. MCP servers read exactly these shapes
(mirrored in libs/common/mcpcluster_common/model.py — keep in sync, tests assert it).

Design: no fan-out. Company/user/binding/server state is stored once and the MCP side composes
the effective status from 6 keys in one pipeline, so a company suspend is a single Redis write.
"""

from typing import Any

import orjson

from app.domain.models import (
    AccessPolicy,
    ApiKey,
    Company,
    IdentityProvider,
    McpServer,
    ServerAccess,
    Team,
    TeamMembership,
    TenantBinding,
    User,
    WorkspaceConnection,
)
from app.infra.dynamo.keys import Entity

# Backend-only entities: nothing on the MCP side reads them, so they have no Redis shape (project() -> None).
NOT_PROJECTED = (WorkspaceConnection, Team, TeamMembership, AccessPolicy)

LOG_STREAM = "logs:access"


def company_key(cid: str) -> str:
    return f"company:{cid}"


def user_key(cid: str, uid: str) -> str:
    return f"user:{cid}:{uid}"


def server_key(sid: str) -> str:
    return f"server:{sid}"


def binding_key(cid: str, sid: str) -> str:
    return f"binding:{cid}:{sid}"


def access_key(cid: str, uid: str, sid: str) -> str:
    return f"access:{cid}:{uid}:{sid}"


def branding_key(cid: str) -> str:
    """Report branding, separate from `company:` so the per-request authorization read stays small."""
    return f"branding:{cid}"


def access_index_key(cid: str, uid: str) -> str:
    """SET of server ids a user has (or had) an access record for. Discovery only (the gateway uses it to
    find candidate servers for an all-servers key); authorization always reads the access records."""
    return f"accessidx:{cid}:{uid}"


def apikey_key(key_hash: str) -> str:
    return f"apikey:{key_hash}"


def idp_key(cid: str) -> str:
    """Not read by MCP servers (the backend runs the OIDC flow itself); projected so the outbox/reconciler
    machinery treats the entity like any other. No secret: the client secret stays encrypted in DynamoDB."""
    return f"idp:{cid}"


def project(e: Entity) -> tuple[str, dict[str, Any]] | None:
    if isinstance(e, NOT_PROJECTED):
        return None
    if isinstance(e, Company):
        return company_key(e.company_id), {"status": e.status, "version": e.version}
    if isinstance(e, User):
        return user_key(e.company_id, e.user_id), {"status": e.status, "version": e.version}
    if isinstance(e, McpServer):
        return server_key(e.server_id), {"status": e.status, "server_type": e.server_type, "name": e.name, "dsn_enc": e.connection_enc, "version": e.version}
    if isinstance(e, TenantBinding):
        return binding_key(e.company_id, e.server_id), {
            "status": e.status,
            "server_type": e.server_type,
            "tenant": e.tenant.model_dump(mode="json"),
            "version": e.version,
        }
    if isinstance(e, ServerAccess):
        return access_key(e.company_id, e.user_id, e.server_id), {
            "status": e.status,
            "server_type": e.server_type,
            "permissions": sorted(e.permissions),
            "allowed_tools": sorted(e.allowed_tools),
            "version": e.version,
        }
    if isinstance(e, ApiKey):
        return apikey_key(e.key_hash), {
            "key_id": e.key_id,
            "company_id": e.company_id,
            "user_id": e.user_id,
            "server_ids": e.server_ids,
            "status": e.status,
            "expires_at": e.expires_at.isoformat() if e.expires_at else None,
            "rate_limit_per_minute": e.rate_limit_per_minute,
            "version": e.version,
        }
    if isinstance(e, IdentityProvider):
        return idp_key(e.company_id), {"status": e.status, "issuer": e.issuer, "domains": e.domains, "version": e.version}
    raise TypeError(type(e))


def dumps(d: dict[str, Any]) -> str:
    return orjson.dumps(d, option=orjson.OPT_SORT_KEYS).decode()
