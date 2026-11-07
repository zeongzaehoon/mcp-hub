import logging

import orjson
import redis.asyncio as aioredis

from mcpcluster_common.model import AccessEvent
from mcpcluster_common.settings import CommonSettings

log = logging.getLogger(__name__)


class AccessLogPublisher:
    def __init__(self, settings: CommonSettings):
        self.s = settings
        self.r = aioredis.from_url(settings.redis_url, db=settings.redis_db_logs, decode_responses=True)

    async def close(self) -> None:
        await self.r.aclose()

    async def publish(self, ev: AccessEvent) -> None:
        try:
            await self.r.xadd(self.s.log_stream, {"event": orjson.dumps(ev.model_dump(mode="json")).decode()}, maxlen=self.s.log_stream_maxlen, approximate=True)
        except Exception:  # noqa: BLE001 — logging must never break the tool call
            log.exception("failed to publish access event")
