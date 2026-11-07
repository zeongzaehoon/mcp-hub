"""Counts for the console tiles. Global (master): one entity scan, cached in Redis db2 (`stats:global`) and refreshed
by a background task / on demand. Company (admin): one partition query per request. Read-only; nothing here feeds
authorization."""

import asyncio
import logging
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

import orjson
from redis.asyncio import Redis

from app.domain.enums import Status
from app.domain.models import (
    AccessPolicy,
    ApiKey,
    Company,
    McpServer,
    ServerAccess,
    Team,
    TenantBinding,
    User,
)
from app.infra.dynamo.keys import company_pk
from app.infra.dynamo.repository import Repository

log = logging.getLogger(__name__)
CACHE_KEY = "stats:global"
EXPIRING_DAYS = 30
EXPIRING_LIST_MAX = 20


def _key_row(k: ApiKey) -> dict[str, Any]:
    return {"key_id": k.key_id, "company_id": k.company_id, "user_id": k.user_id, "name": k.name, "key_prefix": k.key_prefix,
            "expires_at": k.expires_at.isoformat() if k.expires_at else None, "status": str(k.status)}


class StatsService:
    def __init__(self, repo: Repository, hot: Redis):
        self.repo = repo
        self.r = hot

    @staticmethod
    def _fold(entities, *, now: datetime, days: int) -> dict[str, Any]:
        by: dict[str, Counter] = {t: Counter() for t in ("companies", "users", "servers", "bindings", "access", "api_keys", "teams", "policies")}
        bindings_per_server: Counter = Counter()
        expiring: list[ApiKey] = []
        expired_active = 0
        horizon = now + timedelta(days=days)
        for e in entities:
            if isinstance(e, Company):
                by["companies"][str(e.status)] += 1
            elif isinstance(e, User):
                by["users"][str(e.status)] += 1
            elif isinstance(e, McpServer):
                by["servers"][str(e.status)] += 1
            elif isinstance(e, TenantBinding):
                by["bindings"][str(e.status)] += 1
                if e.status == Status.ACTIVE:
                    bindings_per_server[e.server_id] += 1
            elif isinstance(e, ServerAccess):
                by["access"][str(e.status)] += 1
            elif isinstance(e, ApiKey):
                by["api_keys"][str(e.status)] += 1
                if e.status == Status.ACTIVE and e.expires_at is not None:
                    if e.expires_at <= now:
                        expired_active += 1
                    elif e.expires_at <= horizon:
                        expiring.append(e)
            elif isinstance(e, Team):
                by["teams"][str(e.status)] += 1
            elif isinstance(e, AccessPolicy):
                by["policies"][str(e.status)] += 1
        expiring.sort(key=lambda k: k.expires_at)  # type: ignore[arg-type,return-value]
        return {
            "computed_at": now.isoformat(),
            "counts": {t: {"active": c.get("active", 0), "suspended": c.get("suspended", 0), "deleted": c.get("deleted", 0), "total": sum(c.values())} for t, c in by.items()},
            "bindings_per_server": dict(bindings_per_server),
            "keys_expiring": {"days": days, "count": len(expiring), "expired_still_active": expired_active, "items": [_key_row(k) for k in expiring[:EXPIRING_LIST_MAX]]},
        }

    async def compute_global(self, *, days: int = EXPIRING_DAYS) -> dict[str, Any]:
        now = datetime.now(UTC)
        ents = [e async for e in self.repo.scan_entities()]
        out = self._fold(ents, now=now, days=days)
        await self.r.set(CACHE_KEY, orjson.dumps(out).decode())
        return out

    async def get_global(self, *, refresh: bool = False) -> dict[str, Any]:
        if not refresh:
            raw = await self.r.get(CACHE_KEY)
            if raw:
                try:
                    return orjson.loads(raw)
                except orjson.JSONDecodeError:
                    pass
        return await self.compute_global()

    async def company(self, company_id: str, *, days: int = EXPIRING_DAYS) -> dict[str, Any]:
        rows, cursor = [], None
        while True:
            page, cursor = await self.repo.query(company_pk(company_id), limit=200, cursor=cursor, consistent=True)
            rows.extend(page)
            if not cursor:
                break
        out = self._fold(rows, now=datetime.now(UTC), days=days)
        out["company_id"] = company_id
        del out["counts"]["companies"], out["counts"]["servers"]
        return out

    async def run_forever(self, interval_s: float) -> None:
        while True:
            try:
                await self.compute_global()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("stats refresh error")
            await asyncio.sleep(interval_s)
