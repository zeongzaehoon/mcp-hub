"""One-off, idempotent migration of pre-hierarchy API keys (key = one server + its own permissions)
to company -> user -> server: each legacy key yields a ServerAccess(user, server) with the key's
permissions/tools and becomes a key scoped to exactly that server.

Never widens: when a user already has an access to that server with different permissions/tools
(two legacy keys that disagreed), the key is suspended and reported instead of merged."""

import logging
from dataclasses import dataclass, field

from app.core.errors import Conflict
from app.domain.enums import EntityType, Status
from app.domain.models import ApiKey, ServerAccess, now
from app.infra.dynamo.keys import access_sk, company_pk, from_item, is_legacy_apikey
from app.infra.dynamo.repository import Repository, deserialize, serialize
from app.infra.redis.sync import RedisSync
from app.services.access import AccessService
from app.services.base import ServiceBase

log = logging.getLogger(__name__)


@dataclass
class MigrationReport:
    migrated: int = 0
    accesses_created: int = 0
    suspended_conflicts: list[str] = field(default_factory=list)  # key ids
    errors: list[str] = field(default_factory=list)


async def _legacy_items(repo: Repository):
    params = {
        "TableName": repo.table,
        "FilterExpression": "entity_type = :t AND attribute_exists(server_id) AND attribute_not_exists(scope_all)",
        "ExpressionAttributeValues": serialize({":t": EntityType.APIKEY}),
    }
    async with repo.factory.client() as c:
        while True:
            r = await c.scan(**params)
            for i in r.get("Items", []):
                d = deserialize(i)
                if is_legacy_apikey(d):
                    yield d
            lek = r.get("LastEvaluatedKey")
            if not lek:
                return
            params["ExclusiveStartKey"] = lek


async def migrate_legacy_keys(repo: Repository, sync: RedisSync) -> MigrationReport:
    rep = MigrationReport()
    base = ServiceBase(repo, sync)
    access_svc = AccessService(repo, sync)
    # active keys first: when legacy keys of one user x server disagree, the grant follows an active key and
    # the others are held — never a suspended/leaked key's (possibly wider) permissions
    items = [i async for i in _legacy_items(repo)]
    items.sort(key=lambda i: (i.get("status") != "active", i.get("created_at", "")))
    for item in items:
        kid = item.get("key_id", "?")
        try:
            key = from_item(item)
            assert isinstance(key, ApiKey)
            sid = item["server_id"]
            wanted_perms, wanted_tools = set(item.get("permissions") or []), set(item.get("allowed_tools") or [])
            existing = await repo.get(company_pk(key.company_id), access_sk(key.user_id, sid))
            ops: list[dict] = []
            entities: list = []
            if key.status == Status.DELETED:
                pass  # a deleted key grants nothing; do not mint an access for it
            elif existing is None:
                live = key.status == Status.ACTIVE and not key.is_expired(now())
                access = ServerAccess(
                    company_id=key.company_id, user_id=key.user_id, server_id=sid, server_type=item["server_type"],
                    permissions=wanted_perms, allowed_tools=wanted_tools,
                    # a suspended or expired key must not turn into a live user-level grant
                    status=Status.ACTIVE if live else Status.SUSPENDED,
                )
                if live:  # same parent proof as AccessService.create; if a parent is inactive, fall back below
                    ops.extend(access_svc.guard_ops(access))
                ops.append(repo.tx.create(access))
                entities.append(access)
            elif not isinstance(existing, ServerAccess) or existing.permissions != wanted_perms or existing.allowed_tools != wanted_tools:
                # the key would inherit the access's (different) permissions; hold it for an operator.
                # An already-suspended key is reported too: re-activating it is a conscious decision.
                if key.status == Status.ACTIVE:
                    key.status = Status.SUSPENDED
                rep.suspended_conflicts.append(kid)
                log.warning("migration: key %s disagrees with the existing access %s/%s; suspended", kid, key.user_id, sid)
            # rewrite in the new shape (drops server_id/server_type/permissions/allowed_tools/gsi1)
            ops.append(repo.tx.replace(key, base.bump(key)))
            entities.append(key)
            try:
                await base.commit(entities, ops)
            except Conflict:
                held = [e for e in entities if isinstance(e, ServerAccess) and e.status == Status.ACTIVE]
                if not held:
                    raise
                # a parent (company/user/server/binding) is not active: migrate as a held grant instead;
                # re-activating it later goes through AccessService and re-proves the parents
                access = held[0].model_copy(update={"status": Status.SUSPENDED})
                key = from_item(item)  # fresh copy: the failed attempt already bumped the in-memory version
                entities = [access, key]
                await base.commit(entities, [repo.tx.create(access), repo.tx.replace(key, base.bump(key))])
            rep.migrated += 1
            rep.accesses_created += sum(isinstance(e, ServerAccess) for e in entities)
        except Conflict:
            rep.errors.append(f"{kid}: concurrent change; rerun")
        except Exception as e:
            rep.errors.append(f"{kid}: {e}")
            log.exception("migration failed for key %s", kid)
    if rep.migrated or rep.errors:
        log.info("legacy key migration: %s", rep)
    return rep
