"""Resolve an API key + target server to an authorization decision using only Redis db1.

Hierarchy company -> user -> server; the key acts on behalf of its user.
Target server = explicit request value (`?server=`), else the process pin (SERVER_ID), else the key's
scope when it names exactly one server. Anything else is ambiguous => deny (never guess).

Effective access = key active & not expired & target in key scope
                 & company, user, server, binding(company, server), access(user, server) all active
                 & server_type consistent across process/server/binding/access.
Permissions and allowed_tools come from the access record only.
Any missing record => deny. Never fall back to "allow" on a Redis error."""

import hashlib
import logging
import re
from dataclasses import dataclass, replace

import orjson
import redis.asyncio as aioredis
from pydantic import ValidationError

from mcpcluster_common.context import TenantContext
from mcpcluster_common.model import AccessRec, ApiKeyRec, BindingRec, CompanyRec, ServerRec, UserRec
from mcpcluster_common.settings import CommonSettings

log = logging.getLogger(__name__)
SERVER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")  # ids are interpolated into Redis key names


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str
    ctx: TenantContext | None = None
    key: ApiKeyRec | None = None  # present even when denied (for logging), if the key record existed
    server_id: str | None = None  # resolved target server (for logging), when known


def _load(model, raw):
    if raw is None:
        return None
    try:
        return model.model_validate(orjson.loads(raw))
    except (ValidationError, orjson.JSONDecodeError) as e:
        log.warning("invalid redis record for %s: %s", model.__name__, e)
        return None


class Resolver:
    def __init__(self, settings: CommonSettings):
        self.s = settings
        self.r = aioredis.from_url(settings.redis_url, db=settings.redis_db_auth, decode_responses=True)

    async def close(self) -> None:
        await self.r.aclose()

    @staticmethod
    def hash_key(raw: str) -> str:
        return hashlib.sha256(raw.encode()).hexdigest()

    def target_server(self, key: ApiKeyRec, requested: str | None) -> str | None:
        if requested:
            return requested
        if self.s.server_id:
            return self.s.server_id
        if key.server_ids is not None and len(key.server_ids) == 1:
            return key.server_ids[0]
        return None

    async def resolve(self, raw_key: str | None, requested_server: str | None = None) -> Decision:
        if not raw_key:
            return Decision(False, "missing api key")
        return await self.resolve_hash(self.hash_key(raw_key), requested_server)

    async def resolve_hash(self, key_hash: str, requested_server: str | None = None) -> Decision:
        """Same decision from a stored key hash (scheduled work such as monitors runs as its creating key)."""
        key: ApiKeyRec | None = _load(ApiKeyRec, await self.r.get(f"apikey:{key_hash}"))
        if key is None:
            return Decision(False, "unknown api key")
        if key.status != "active":
            return Decision(False, f"key {key.status}", key=key)
        if key.is_expired():
            return Decision(False, "key expired", key=key)
        if requested_server is not None and not SERVER_ID_RE.match(requested_server):
            return Decision(False, "malformed server id", key=key)
        if self.s.server_id and requested_server and requested_server != self.s.server_id:
            return Decision(False, "this endpoint serves a different server", key=key)
        sid = self.target_server(key, requested_server)
        if sid is None:
            return Decision(False, "specify the target server with ?server=<server_id> in the MCP URL", key=key)
        if key.server_ids is not None and sid not in key.server_ids:
            return Decision(False, "server is outside this key's scope", key=key, server_id=sid)

        async with self.r.pipeline(transaction=False) as p:
            p.get(f"company:{key.company_id}")
            p.get(f"user:{key.company_id}:{key.user_id}")
            p.get(f"server:{sid}")
            p.get(f"binding:{key.company_id}:{sid}")
            p.get(f"access:{key.company_id}:{key.user_id}:{sid}")
            c_raw, u_raw, s_raw, b_raw, a_raw = await p.execute()
        company, user, server = _load(CompanyRec, c_raw), _load(UserRec, u_raw), _load(ServerRec, s_raw)
        binding, access = _load(BindingRec, b_raw), _load(AccessRec, a_raw)
        for name, rec in (("company", company), ("user", user), ("server", server), ("binding", binding), ("access", access)):
            if rec is None:
                # one message for server/binding/access so a caller cannot probe which server ids exist
                reason = f"{name} record missing" if name in ("company", "user") else "server not available to this key"
                return Decision(False, reason, key=key, server_id=sid)
            if rec.status != "active":
                return Decision(False, f"{name} {rec.status}", key=key, server_id=sid)
        if not (server.server_type == binding.server_type == access.server_type):
            return Decision(False, "server_type mismatch between server/binding/access", key=key, server_id=sid)
        if self.s.server_type and server.server_type != self.s.server_type:
            return Decision(False, f"different server type: this endpoint serves {self.s.server_type}", key=key, server_id=sid)
        ctx = TenantContext.from_records(
            key, access, server_id=sid, tenant=binding.tenant, binding_version=binding.version,
            server_dsn_enc=server.dsn_enc, server_version=server.version,
        )
        return Decision(True, "ok", ctx=replace(ctx, key_hash=key_hash), key=key, server_id=sid)

    async def server_readable(self, ctx: TenantContext, server_id: str, permission: str = "read") -> bool:
        """May the caller of this request read data that originated on `server_id`? Used when a tool
        consumes stored results of another server (artifacts): same rules as a direct call there —
        in the key's scope, server/binding/access active, and `permission` granted (`read` to view it; `export`
        to put it into a file or new stored copy). Company, user and key
        were already verified for this request."""
        if not SERVER_ID_RE.match(server_id):
            return False
        if ctx.key_scope is not None and server_id not in ctx.key_scope:
            return False
        if server_id == ctx.server_id:
            return permission in ctx.permissions
        async with self.r.pipeline(transaction=False) as p:
            p.get(f"server:{server_id}")
            p.get(f"binding:{ctx.company_id}:{server_id}")
            p.get(f"access:{ctx.company_id}:{ctx.user_id}:{server_id}")
            s_raw, b_raw, a_raw = await p.execute()
        server, binding, access = _load(ServerRec, s_raw), _load(BindingRec, b_raw), _load(AccessRec, a_raw)
        if server is None or binding is None or access is None:
            return False
        if not (server.status == binding.status == access.status == "active"):
            return False
        return permission in access.permissions
