"""Transactional outbox: every DynamoDB mutation adds an OUTBOX row in the same transaction.
After commit we sync inline (fast path); the worker drains leftovers; the reconciler is the net."""

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.infra.dynamo.keys import Entity, entity_keys
from app.infra.dynamo.repository import Repository
from app.infra.redis.sync import RedisSync

log = logging.getLogger(__name__)
OUTBOX_PK = "OUTBOX"
EPOCH_KEY = "meta:epoch"  # present while Redis holds a full projection; vanishes on flush/restart


def outbox_item(e: Entity) -> dict[str, Any]:
    k = entity_keys(e)
    ts = datetime.now(UTC).isoformat()
    return {
        "PK": OUTBOX_PK,
        "SK": f"{ts}#{uuid4().hex[:12]}",
        "target_pk": k["PK"],
        "target_sk": k["SK"],
        "entity_type": k["entity_type"],
        "attempts": 0,
        "created_at": ts,
    }


class Outbox:
    def __init__(self, repo: Repository, sync: RedisSync, reconcile=None):
        self.repo = repo
        self.sync = sync
        self._reconcile = reconcile  # coroutine factory: full DB -> Redis rebuild

    async def mark_epoch(self) -> None:
        await self.sync.r.set(EPOCH_KEY, datetime.now(UTC).isoformat())

    async def heal_if_flushed(self) -> bool:
        """Redis lost its data (restart without persistence, FLUSHDB, failover to an empty replica):
        the epoch marker is gone. Rebuild every projection from DynamoDB right away instead of waiting
        for the periodic reconciler — until then every MCP request is denied (fail closed)."""
        if await self.sync.r.exists(EPOCH_KEY):
            return False
        log.warning("redis projection epoch missing — rebuilding all projections from DynamoDB")
        if self._reconcile is not None:
            await self._reconcile()
        await self.mark_epoch()
        return True

    def op_for(self, e: Entity) -> tuple[dict, dict[str, Any]]:
        """(transact op, outbox item) — include the op in the same transaction as the entity write."""
        item = outbox_item(e)
        return self.repo.tx.put_raw(item), item

    async def flush_inline(self, entities: list[Entity], outbox_items: list[dict[str, Any]]) -> None:
        """Best-effort immediate sync after commit. Always re-reads from DynamoDB so we never
        project an in-memory object that differs from what was committed."""
        for e, item in zip(entities, outbox_items, strict=True):
            try:
                await self._sync_one(item)
            except Exception:
                log.exception("inline sync failed; outbox worker will retry %s", item["SK"])

    async def _sync_one(self, item: dict[str, Any]) -> None:
        entity = await self.repo.get(item["target_pk"], item["target_sk"])
        if entity is not None:
            await self.sync.write(entity)
        await self.repo.delete_raw(OUTBOX_PK, item["SK"])

    async def drain(self) -> int:
        n = 0
        async for item in self.repo.scan_raw(OUTBOX_PK):
            try:
                await self._sync_one(item)
                n += 1
            except Exception:
                log.exception("outbox drain failed for %s", item.get("SK"))
        return n

    async def run_forever(self, interval: float) -> None:
        while True:
            try:
                await self.drain()
            except Exception:
                log.exception("outbox loop error")
            await asyncio.sleep(interval)
