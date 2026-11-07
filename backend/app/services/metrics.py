"""24h call metrics and the recent-event feed for the console, kept in Redis db2 by the access-log consumer.

Hourly buckets (UTC): `m:srv:{sid}:{YYYYMMDDHH}`, `m:cmp:{cid}:{hour}`, `m:all:{hour}` — hashes with n/ok/denied/error,
dur_sum/dur_max and a latency histogram (`b:<upper_ms>`), TTL 48h. A summary sums the last 24 buckets; p50/p99 are
read off the histogram, so they are upper-bound estimates. Counters are incremented after the batch is acknowledged:
a crash in between replays the batch and double-counts it — approximate by design, the audit lake is the record.

`events:recent` is a capped list (master feed, every company); per-company feeds use the DynamoDB logs API."""

from datetime import UTC, datetime, timedelta
from typing import Any

import orjson
from redis.asyncio import Redis

BUCKETS_MS = (5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000)
INF = "inf"
TTL_S = 48 * 3600
RECENT_KEY = "events:recent"
RECENT_MAX = 500
EVENT_FIELDS = ("ts", "company_id", "user_id", "key_id", "server_id", "server_type", "tool", "status", "duration_ms", "error")


def hour_key(ts: datetime) -> str:
    return ts.astimezone(UTC).strftime("%Y%m%d%H")


def _bucket(ms: int) -> str:
    for b in BUCKETS_MS:
        if ms <= b:
            return f"b:{b}"
    return f"b:{INF}"


def _parse_ts(ev: dict[str, Any]) -> datetime:
    try:
        return datetime.fromisoformat(str(ev.get("ts")))
    except (TypeError, ValueError):
        return datetime.now(UTC)


def _quantile(hist: dict[str, int], q: float, total: int) -> int | None:
    """Upper bound of the bucket holding the q-th sample; None when empty or when it falls in the open-ended bucket."""
    if total <= 0:
        return None
    need = max(1, int(q * total + 0.999999))
    seen = 0
    for b in BUCKETS_MS:
        seen += hist.get(f"b:{b}", 0)
        if seen >= need:
            return b
    return None


class MetricsService:
    def __init__(self, hot: Redis):
        self.r = hot

    async def record(self, events: list[dict[str, Any]]) -> None:
        if not events:
            return
        pipe = self.r.pipeline(transaction=False)
        touched: set[str] = set()
        maxes: dict[str, int] = {}
        for ev in events:
            h = hour_key(_parse_ts(ev))
            status = str(ev.get("status") or "error")
            status = status if status in ("ok", "denied", "error") else "error"
            try:
                dur = max(0, int(ev.get("duration_ms") or 0))
            except (TypeError, ValueError):
                dur = 0
            keys = [f"m:all:{h}"]
            if sid := ev.get("server_id"):
                keys.append(f"m:srv:{sid}:{h}")
            if cid := ev.get("company_id"):
                keys.append(f"m:cmp:{cid}:{h}")
            for k in keys:
                pipe.hincrby(k, "n", 1)
                pipe.hincrby(k, status, 1)
                pipe.hincrby(k, "dur_sum", dur)
                pipe.hincrby(k, _bucket(dur), 1)
                touched.add(k)
                maxes[k] = max(maxes.get(k, 0), dur)
            pipe.lpush(RECENT_KEY, orjson.dumps({f: ev.get(f) for f in EVENT_FIELDS}).decode())
        for k in touched:
            pipe.expire(k, TTL_S)
        pipe.ltrim(RECENT_KEY, 0, RECENT_MAX - 1)
        await pipe.execute()
        # dur_max: read-then-write per bucket, best-effort (a concurrent consumer may win; the value is a hint)
        pipe = self.r.pipeline(transaction=False)
        for k in maxes:
            pipe.hget(k, "dur_max")
        olds = await pipe.execute()
        pipe = self.r.pipeline(transaction=False)
        for (k, v), old in zip(maxes.items(), olds, strict=True):
            if v > int(old or 0):
                pipe.hset(k, "dur_max", v)
        await pipe.execute()

    async def summary(self, scope: str, ident: str | None = None, *, hours: int = 24, now: datetime | None = None) -> dict[str, Any]:
        """scope: 'all' | 'srv' | 'cmp'. Totals over the last `hours` full-hour buckets plus the hourly series."""
        now = now or datetime.now(UTC)
        start = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=hours - 1)
        hs = [start + timedelta(hours=i) for i in range(hours)]
        prefix = f"m:{scope}:{ident}:" if ident else f"m:{scope}:"
        pipe = self.r.pipeline(transaction=False)
        for h in hs:
            pipe.hgetall(f"{prefix}{hour_key(h)}")
        rows = await pipe.execute()
        tot = {"n": 0, "ok": 0, "denied": 0, "error": 0, "dur_sum": 0, "dur_max": 0}
        hist: dict[str, int] = {}
        series = []
        for h, raw in zip(hs, rows, strict=True):
            d = {k: int(v) for k, v in (raw or {}).items()}
            for k in ("n", "ok", "denied", "error", "dur_sum"):
                tot[k] += d.get(k, 0)
            tot["dur_max"] = max(tot["dur_max"], d.get("dur_max", 0))
            for k, v in d.items():
                if k.startswith("b:"):
                    hist[k] = hist.get(k, 0) + v
            series.append({"hour": h.isoformat(), "n": d.get("n", 0), "ok": d.get("ok", 0), "denied": d.get("denied", 0), "error": d.get("error", 0)})
        n = tot["n"]
        return {
            "hours": hours, "calls": n, "ok": tot["ok"], "denied": tot["denied"], "errors": tot["error"],
            "denied_rate": (tot["denied"] / n) if n else 0.0,
            "avg_ms": (tot["dur_sum"] / n) if n else None, "max_ms": tot["dur_max"] if n else None,
            "p50_ms": _quantile(hist, 0.5, n), "p99_ms": _quantile(hist, 0.99, n),
            "series": series,
        }

    async def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        raw = await self.r.lrange(RECENT_KEY, 0, max(1, min(limit, RECENT_MAX)) - 1)
        out = []
        for x in raw:
            try:
                out.append(orjson.loads(x))
            except orjson.JSONDecodeError:
                continue
        return out
