"""Fixed-window per-key rate limiter on Redis db2 (hot). One Lua round trip per call.

Window key: rl:{key_id}:{unix_minute}. Fail-closed on Redis errors? No — a Redis outage already denies
every request at the authz step (db1), so the limiter itself is fail-open to avoid a second, redundant
failure mode; it never grants anything the authz step did not."""

import logging
import time
from dataclasses import dataclass

import redis.asyncio as aioredis

from mcpcluster_common.settings import CommonSettings

log = logging.getLogger(__name__)

# returns {count, ttl}
_INCR_WINDOW = """
local c = redis.call('INCR', KEYS[1])
if c == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
return {c, redis.call('TTL', KEYS[1])}
"""


@dataclass(frozen=True)
class RateDecision:
    allowed: bool
    limit: int
    remaining: int
    retry_after_s: int


class RateLimiter:
    def __init__(self, settings: CommonSettings):
        self.s = settings
        self.r = aioredis.from_url(settings.redis_url, db=settings.redis_db_hot, decode_responses=True)
        self._script = self.r.register_script(_INCR_WINDOW)

    async def close(self) -> None:
        await self.r.aclose()

    async def check(self, key_id: str, limit: int | None) -> RateDecision:
        lim = limit if limit is not None else self.s.rate_limit_default_per_minute
        if lim <= 0:
            return RateDecision(True, 0, -1, 0)
        window = int(time.time() // 60)
        try:
            count, ttl = await self._script(keys=[f"rl:{key_id}:{window}"], args=[120])
        except Exception:  # noqa: BLE001
            log.exception("rate limiter unavailable; allowing (authz already passed)")
            return RateDecision(True, lim, -1, 0)
        count, ttl = int(count), int(ttl)
        retry = max(1, 60 - int(time.time()) % 60)
        return RateDecision(count <= lim, lim, max(0, lim - count), 0 if count <= lim else retry)
