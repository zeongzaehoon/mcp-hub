"""Consumes the access-log stream (Redis db3, XREADGROUP) and batch-writes to the S3 audit lake (>= 1 year) and
DynamoDB (90 days, console queries). A batch is acknowledged only after both writes succeeded.

Log row: PK=KEY#{key_id} SK={ts}#{stream_id}; gsi1pk=COMPANY#{cid} gsi1sk={ts}#{stream_id}; ttl."""

import asyncio
import logging
import time
from typing import Any

import orjson
from redis.exceptions import ResponseError

from app.core.config import get_settings
from app.infra.dynamo.repository import Repository
from app.infra.redis.client import RedisPools
from app.infra.redis.projection import LOG_STREAM
from app.infra.s3.audit import AuditLake
from app.services.metrics import MetricsService

log = logging.getLogger(__name__)
GROUP = "backend"


class AccessLogConsumer:
    def __init__(self, repo: Repository, pools: RedisPools, audit: AuditLake | None = None):
        self.repo = repo
        self.r = pools.logs
        self.settings = get_settings()
        self.audit = audit or AuditLake(self.settings)
        self.metrics = MetricsService(pools.hot)

    async def ensure_group(self) -> None:
        try:
            await self.r.xgroup_create(LOG_STREAM, GROUP, id="0", mkstream=True)
        except ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise

    @staticmethod
    def _event(fields: dict[str, str]) -> dict[str, Any] | None:
        try:
            ev = orjson.loads(fields["event"])
        except (KeyError, orjson.JSONDecodeError):
            return None
        return ev if isinstance(ev, dict) else None

    def _to_row(self, stream_id: str, ev: dict[str, Any]) -> dict[str, Any]:
        ts = ev.get("ts") or time.strftime("%Y-%m-%dT%H:%M:%S%z")
        key_id = ev.get("key_id") or "unknown"
        return {
            "PK": f"KEY#{key_id}",
            "SK": f"{ts}#{stream_id}",
            "gsi1pk": f"COMPANY#{ev.get('company_id', 'unknown')}",
            "gsi1sk": f"{ts}#{stream_id}",
            "ttl": int(time.time()) + self.settings.log_ttl_days * 86400,
            **{k: v for k, v in ev.items() if k not in {"PK", "SK"}},
        }

    async def process_once(self, consumer: str = "c1", block_ms: int = 2000) -> int:
        try:
            res = await self.r.xreadgroup(GROUP, consumer, {LOG_STREAM: ">"}, count=self.settings.log_consumer_batch, block=block_ms)
        except ResponseError as e:
            if "NOGROUP" not in str(e):
                raise
            log.warning("log stream/group vanished (redis restart?) — recreating consumer group")
            await self.ensure_group()
            return 0
        if not res:
            return 0
        events, ids = [], []
        for _stream, entries in res:
            for sid, fields in entries:
                ids.append(sid)
                if (ev := self._event(fields)) is not None:
                    events.append((sid, ev))
        rows = [self._to_row(sid, ev) for sid, ev in events]
        await self.audit.write(events)
        await self.repo.batch_put_logs(rows)
        if ids:
            await self.r.xack(LOG_STREAM, GROUP, *ids)
            await self.r.xdel(LOG_STREAM, *ids)
        try:
            await self.metrics.record([ev for _, ev in events])  # console counters: best-effort, after the durable writes
        except Exception:
            log.exception("metrics record failed")
        return len(rows)

    async def run_forever(self) -> None:
        await self.ensure_group()
        while True:
            try:
                await self.process_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("log consumer error")
                await asyncio.sleep(1)

    async def list_for_key(self, key_id: str, *, start, end, limit, cursor):
        return await self.repo.query_logs(f"KEY#{key_id}", index=None, start=start, end=end, limit=limit, cursor=cursor)

    async def list_for_company(self, company_id: str, *, start, end, limit, cursor):
        return await self.repo.query_logs(f"COMPANY#{company_id}", index="gsi1", start=start, end=end, limit=limit, cursor=cursor)
