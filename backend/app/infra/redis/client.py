import redis.asyncio as aioredis

from app.core.config import get_settings


class RedisPools:
    """One client per logical db. db1 = auth/tenant, db2 = hot cache, db3 = log stream."""

    def __init__(self) -> None:
        s = get_settings()
        self.auth = aioredis.from_url(s.redis_url, db=s.redis_db_auth, decode_responses=True)
        self.hot = aioredis.from_url(s.redis_url, db=s.redis_db_hot, decode_responses=True)
        self.logs = aioredis.from_url(s.redis_url, db=s.redis_db_logs, decode_responses=True)

    async def close(self) -> None:
        for c in (self.auth, self.hot, self.logs):
            await c.aclose()
