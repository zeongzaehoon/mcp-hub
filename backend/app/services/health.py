"""Liveness of the MCP services as seen from the backend. Targets are the internal URLs from settings (env), one per
server type plus the gateway; the public `endpoint` on a server record is never fetched. Results live in Redis db2
(`health:<type>`) so every backend replica reads the same snapshot."""

import asyncio
import logging
import time
from datetime import UTC, datetime
from typing import Any

import httpx
from redis.asyncio import Redis

from app.core.config import Settings

log = logging.getLogger(__name__)
TTL_S = 600


class HealthChecker:
    def __init__(self, hot: Redis, settings: Settings, http: httpx.AsyncClient | None = None):
        self.r = hot
        self.s = settings
        self.http = http or httpx.AsyncClient(timeout=settings.health_check_timeout_s, follow_redirects=False)

    @property
    def targets(self) -> dict[str, str]:
        return self.s.internal_urls()

    async def check_one(self, kind: str, base: str) -> dict[str, Any]:
        started = time.perf_counter()
        ok, error = False, None
        try:
            resp = await self.http.get(f"{base}/healthz")
            ok = resp.status_code == 200
            if not ok:
                error = f"http {resp.status_code}"
        except httpx.HTTPError as e:
            error = type(e).__name__
        latency = int((time.perf_counter() - started) * 1000)
        rec = {"kind": kind, "ok": ok, "latency_ms": latency, "checked_at": datetime.now(UTC).isoformat(), "error": error}
        await self.r.hset(f"health:{kind}", mapping={k: ("" if v is None else str(v)) for k, v in rec.items()})
        await self.r.expire(f"health:{kind}", TTL_S)
        return rec

    async def run_once(self) -> dict[str, dict[str, Any]]:
        results = await asyncio.gather(*(self.check_one(k, u) for k, u in self.targets.items()), return_exceptions=True)
        out = {}
        for (k, _), r in zip(self.targets.items(), results, strict=True):
            if isinstance(r, Exception):
                log.warning("health check %s failed: %s", k, r)
                continue
            out[k] = r
        return out

    async def run_forever(self, interval_s: float) -> None:
        if not self.targets:
            log.info("health checker: no MCP_INTERNAL_* targets configured")
            return
        while True:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("health checker error")
            await asyncio.sleep(interval_s)

    async def snapshot(self) -> dict[str, dict[str, Any]]:
        """Last known result per configured target; a configured target never checked yet is reported as unknown."""
        kinds = list(self.targets)
        if not kinds:
            return {}
        pipe = self.r.pipeline(transaction=False)
        for k in kinds:
            pipe.hgetall(f"health:{k}")
        rows = await pipe.execute()
        out = {}
        for k, raw in zip(kinds, rows, strict=True):
            if raw:
                out[k] = {"kind": k, "ok": raw.get("ok") == "True", "latency_ms": int(raw.get("latency_ms") or 0),
                          "checked_at": raw.get("checked_at") or None, "error": raw.get("error") or None}
            else:
                out[k] = {"kind": k, "ok": None, "latency_ms": None, "checked_at": None, "error": None}
        return out

    async def aclose(self) -> None:
        await self.http.aclose()
