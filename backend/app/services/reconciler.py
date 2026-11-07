"""Periodic DB -> Redis reconciliation. DynamoDB is the source of truth:
  * every entity missing/stale in Redis gets rewritten (version-guarded);
  * Redis keys with no DB counterpart are removed (stray = potential unauthorized access)."""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.domain.models import Company, ServerAccess
from app.infra.dynamo.repository import Repository
from app.infra.redis.projection import access_index_key, branding_key, dumps, project
from app.infra.redis.sync import RedisSync

log = logging.getLogger(__name__)
PREFIXES = ("company:", "user:", "server:", "binding:", "access:", "accessidx:", "branding:", "apikey:", "idp:")


@dataclass
class ReconcileReport:
    checked: int = 0
    repaired: int = 0
    removed_stray: int = 0
    errors: list[str] = field(default_factory=list)


class Reconciler:
    def __init__(self, repo: Repository, sync: RedisSync):
        self.repo = repo
        self.sync = sync

    async def run_once(self) -> ReconcileReport:
        rep = ReconcileReport()
        expected: set[str] = set()
        async for e in self.repo.scan_entities():
            proj = project(e)
            if proj is None:  # backend-only entity: no Redis counterpart to check
                continue
            key, _ = proj
            expected.add(key)
            rep.checked += 1
            if isinstance(e, ServerAccess):
                expected.add(access_index_key(e.company_id, e.user_id))
                await self.sync.index_access(e)  # idempotent; rebuilds the discovery index after a wipe
            if isinstance(e, Company) and e.branding:
                expected.add(branding_key(e.company_id))
                await self.sync.write_branding(e)
            try:
                if not await self.sync.is_in_sync(e):
                    if await self.sync.write(e):
                        rep.repaired += 1
                    elif not await self.sync.is_in_sync(e):
                        # redis has a *newer* version than DB — impossible unless someone wrote Redis
                        # directly; force DB truth.
                        k, v = proj
                        await self.sync.r.set(k, dumps(v))
                        rep.repaired += 1
            except Exception as ex:  # noqa: BLE001
                rep.errors.append(f"{key}: {ex}")
        async for key in self.sync.r.scan_iter(match="*", count=500):
            if key.startswith(PREFIXES) and key not in expected:
                await self.sync.delete(key)
                rep.removed_stray += 1
        await self.sync.r.set("meta:epoch", datetime.now(UTC).isoformat())  # projections are complete again
        log.info("reconcile: %s", rep)
        return rep

    async def run_forever(self, interval: float) -> None:
        while True:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("reconciler error")
            await asyncio.sleep(interval)
