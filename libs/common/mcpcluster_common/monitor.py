"""Monitors: a saved source + compute + threshold that the server re-runs on a schedule. Read through MCP
(monitor_status); outbound channels plug in via `notifiers` later.

Definitions and recent events live on S3 under the owner's company/user (Redis is only a cache and may be
wiped; MCP servers do not talk to DynamoDB). A monitor runs **as the key that created it**: every run
re-resolves that key (stored as its sha256) against the monitor's server — key, company, user, server,
binding and access must all be active, `read` granted and `monitor_create` still allowed — otherwise the
run is skipped and the reason recorded. Nothing runs on stale permissions."""

import asyncio
import logging
import operator
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import uuid4

import orjson
import redis.asyncio as aioredis
from botocore.exceptions import ClientError
from pydantic import BaseModel, ConfigDict, Field, field_validator

from mcpcluster_common import layout, metrics, notifiers
from mcpcluster_common.authz import Resolver
from mcpcluster_common.context import TenantContext, reset_tenant, set_tenant
from mcpcluster_common.logs import AccessLogPublisher
from mcpcluster_common.model import AccessEvent
from mcpcluster_common.ratelimit import RateLimiter
from mcpcluster_common.settings import CommonSettings
from mcpcluster_common.storage import ObjectStorage

log = logging.getLogger(__name__)
MONITOR_ID_RE = re.compile(r"^mon_[0-9a-f]{32}$")
KEY_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_EVENTS = 50
OPS: dict[str, Callable[[float, float], bool]] = {"gt": operator.gt, "gte": operator.ge, "lt": operator.lt, "lte": operator.le, "eq": operator.eq, "ne": operator.ne}

# (ctx, source, pipeline, op) -> (compute result, source meta); injected by datatools
Runner = Callable[[dict[str, Any], list[dict[str, Any]] | None, dict[str, Any]], Awaitable[tuple[Any, dict[str, Any]]]]


class MonitorRejected(ValueError):
    pass


class MonitorNotFound(LookupError):
    def __init__(self) -> None:
        super().__init__("monitor not found")


class Condition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=120, description="key into the compute result, dotted; list index allowed (e.g. 'mean', '0.count')")
    op: Literal["gt", "gte", "lt", "lte", "eq", "ne"]
    threshold: float


class Channel(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: str

    @field_validator("type")
    @classmethod
    def _registered(cls, v: str) -> str:
        if v not in notifiers.available():
            raise ValueError(f"channel '{v}' is not available (available: {sorted(notifiers.available()) or 'none — read monitors via monitor_status'})")
        return v


class MonitorSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    source: dict[str, Any]
    pipeline: list[dict[str, Any]] | None = None
    op: dict[str, Any]
    condition: Condition
    interval_s: int = Field(ge=1, le=86_400)
    channels: list[Channel] = Field(default_factory=list, max_length=5)


def extract(result: Any, path: str) -> float:
    cur = result
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            raise MonitorRejected(f"path '{path}' not found in the compute result")
    if isinstance(cur, bool) or not isinstance(cur, (int, float)):
        raise MonitorRejected(f"path '{path}' is not a number")
    return float(cur)


def evaluate(result: Any, cond: Condition) -> tuple[float, bool]:
    v = extract(result, cond.path)
    return v, OPS[cond.op](v, cond.threshold)


class MonitorStore:
    def __init__(self, settings: CommonSettings, storage: ObjectStorage):
        self.s = settings
        self.storage = storage

    # control/ objects are settings, not lake data: overwritten in place (last write wins), see layout.py
    def_key = staticmethod(layout.monitor_key)
    state_key = staticmethod(layout.monitor_state_key)

    async def _get(self, c, key: str) -> dict[str, Any] | None:
        try:
            obj = await c.get_object(Bucket=self.s.s3_bucket, Key=key)
        except ClientError:
            return None
        async with obj["Body"] as b:
            return orjson.loads(await b.read())

    async def _put(self, c, key: str, doc: dict[str, Any]) -> None:
        await c.put_object(Bucket=self.s.s3_bucket, Key=key, Body=orjson.dumps(doc, default=str), ContentType="application/json")

    async def put_def(self, d: dict[str, Any]) -> None:
        async with self.storage.client() as c:
            await self.storage.ensure_bucket(c)
            await self._put(c, self.def_key(d["server_type"], d["company_id"], d["user_id"], d["monitor_id"]), d)

    async def get_def(self, ctx: TenantContext, mid: str) -> dict[str, Any]:
        if not isinstance(mid, str) or not MONITOR_ID_RE.match(mid):
            raise MonitorNotFound
        async with self.storage.client() as c:
            d = await self._get(c, self.def_key(ctx.server_type, ctx.company_id, ctx.user_id, mid))
        if not d or d.get("company_id") != ctx.company_id or d.get("user_id") != ctx.user_id or d.get("server_id") != ctx.server_id:
            raise MonitorNotFound
        return d

    async def read_def(self, d: dict[str, Any]) -> dict[str, Any] | None:
        async with self.storage.client() as c:
            return await self._get(c, self.def_key(d["server_type"], d["company_id"], d["user_id"], d["monitor_id"]))

    async def delete(self, d: dict[str, Any]) -> None:
        async with self.storage.client() as c:
            await c.delete_object(Bucket=self.s.s3_bucket, Key=self.def_key(d["server_type"], d["company_id"], d["user_id"], d["monitor_id"]))
            await c.delete_object(Bucket=self.s.s3_bucket, Key=self.state_key(d["company_id"], d["user_id"], d["monitor_id"]))

    async def list_defs(self, prefix: str) -> list[dict[str, Any]]:
        out = []
        async with self.storage.client() as c:
            await self.storage.ensure_bucket(c)
            async for page in c.get_paginator("list_objects_v2").paginate(Bucket=self.s.s3_bucket, Prefix=prefix):
                for o in page.get("Contents", []):
                    d = await self._get(c, o["Key"])
                    if d:
                        out.append(d)
        return out

    async def events(self, d: dict[str, Any]) -> list[dict[str, Any]]:
        async with self.storage.client() as c:
            st = await self._get(c, self.state_key(d["company_id"], d["user_id"], d["monitor_id"]))
        return (st or {}).get("events", [])

    async def append_event(self, d: dict[str, Any], ev: dict[str, Any]) -> None:
        async with self.storage.client() as c:
            key = self.state_key(d["company_id"], d["user_id"], d["monitor_id"])
            st = await self._get(c, key) or {"events": []}
            st["events"] = [ev, *st["events"]][:MAX_EVENTS]
            await self._put(c, key, st)


class Monitors:
    def __init__(self, settings: CommonSettings, resolver: Resolver, storage: ObjectStorage, runner: Runner):
        self.s = settings
        self.resolver = resolver
        self.store = MonitorStore(settings, storage)
        self.run_compute = runner
        self._logs = aioredis.from_url(settings.redis_url, db=settings.redis_db_logs, decode_responses=True)
        self.access_log = AccessLogPublisher(settings)  # scheduled runs appear in the console access log
        self.limiter = RateLimiter(settings)  # and count against the creating key's rate limit
        self._task: asyncio.Task | None = None

    # ---- tool-facing ---------------------------------------------------------------------------
    def parse(self, raw: dict[str, Any]) -> MonitorSpec:
        try:
            spec = MonitorSpec.model_validate(raw)
        except Exception as e:  # noqa: BLE001
            raise MonitorRejected(f"invalid monitor: {e}") from e
        if spec.interval_s < self.s.monitor_min_interval_s:
            raise MonitorRejected(f"interval_s must be at least {self.s.monitor_min_interval_s}")
        return spec

    async def create(self, ctx: TenantContext, spec: MonitorSpec) -> tuple[dict[str, Any], dict[str, Any]]:
        if not KEY_HASH_RE.match(ctx.key_hash):
            raise MonitorRejected("monitors need an API-key authenticated caller")
        # count -> first run -> save must be one step per user, or concurrent creates all pass the cap
        lock = f"monitor:create:{ctx.server_type}:{ctx.company_id}:{ctx.user_id}"
        if not await self.resolver.r.set(lock, "1", nx=True, ex=int(self.s.monitor_run_timeout_s) + 30):
            raise MonitorRejected("another monitor_create for this user is in progress; retry shortly")
        try:
            return await self._create_locked(ctx, spec)
        finally:
            await self.resolver.r.delete(lock)

    async def _create_locked(self, ctx: TenantContext, spec: MonitorSpec) -> tuple[dict[str, Any], dict[str, Any]]:
        mine = await self.store.list_defs(layout.monitor_prefix(ctx.server_type, ctx.company_id, ctx.user_id))
        if len(mine) >= self.s.monitor_max_per_user:
            raise MonitorRejected(f"at most {self.s.monitor_max_per_user} monitors per user on this server type; delete one first")
        now = datetime.now(UTC)
        d = {"monitor_id": f"mon_{uuid4().hex}", "company_id": ctx.company_id, "user_id": ctx.user_id, "server_id": ctx.server_id,
             "server_type": ctx.server_type, "key_id": ctx.key_id, "key_hash": ctx.key_hash, "status": "active",
             "created_at": now.isoformat(), "next_run_at": (now + timedelta(seconds=spec.interval_s)).isoformat(),
             **spec.model_dump()}
        ev = await self._execute(d, ctx)  # first run now: validates source/op/path against real data
        if ev["status"] == "error":
            raise MonitorRejected(f"first run failed: {ev['error']}")
        await self.store.put_def(d)
        await self._record(d, ev)
        return d, ev

    async def list_for(self, ctx: TenantContext) -> list[dict[str, Any]]:
        prefix = layout.monitor_prefix(ctx.server_type, ctx.company_id, ctx.user_id)
        return [public(d) for d in await self.store.list_defs(prefix) if d.get("server_id") == ctx.server_id]

    async def status(self, ctx: TenantContext, mid: str, limit: int = 10) -> dict[str, Any]:
        d = await self.store.get_def(ctx, mid)
        events = [{k: v for k, v in e.items() if k != "company_id"} for e in (await self.store.events(d))[: max(1, min(limit, MAX_EVENTS))]]
        return {**public(d), "events": events, "breaching": bool(events) and events[0]["status"] == "breach"}

    async def set_paused(self, ctx: TenantContext, mid: str, paused: bool) -> dict[str, Any]:
        d = await self.store.get_def(ctx, mid)
        d["status"] = "paused" if paused else "active"
        if not paused:
            d["next_run_at"] = datetime.now(UTC).isoformat()
        await self.store.put_def(d)
        return public(d)

    async def delete(self, ctx: TenantContext, mid: str) -> None:
        await self.store.delete(await self.store.get_def(ctx, mid))

    # ---- execution -----------------------------------------------------------------------------
    async def _authorize(self, d: dict[str, Any]) -> tuple[TenantContext | None, str]:
        if not KEY_HASH_RE.match(d.get("key_hash", "")):
            return None, "monitor has no valid key reference"
        dec = await self.resolver.resolve_hash(d["key_hash"], d["server_id"])
        if not dec.allowed or dec.ctx is None:
            return None, dec.reason
        rd = await self.limiter.check(dec.ctx.key_id, dec.key.rate_limit_per_minute if dec.key else None)
        if not rd.allowed:
            return None, f"rate limited: {rd.limit} calls/minute for this key"
        ctx = dec.ctx
        if ctx.company_id != d["company_id"] or ctx.user_id != d["user_id"] or ctx.server_id != d["server_id"]:
            return None, "key no longer belongs to the monitor owner"
        if "read" not in ctx.permissions or "monitor_create" not in ctx.allowed_tools:
            return None, "owner no longer may run monitors on this server"
        return ctx, "ok"

    async def _execute(self, d: dict[str, Any], ctx: TenantContext) -> dict[str, Any]:
        started = datetime.now(UTC)
        ev: dict[str, Any] = {"ts": started.isoformat(), "monitor_id": d["monitor_id"], "company_id": d["company_id"], "server_id": d["server_id"]}
        token = set_tenant(ctx)
        try:
            result, _meta = await asyncio.wait_for(self.run_compute(d["source"], d.get("pipeline"), d["op"]), self.s.monitor_run_timeout_s)
            value, breach = evaluate(result, Condition.model_validate(d["condition"]))
            ev.update(status="breach" if breach else "ok", value=value, threshold=d["condition"]["threshold"], op=d["condition"]["op"])
        except TimeoutError:
            ev.update(status="error", error=f"timed out after {self.s.monitor_run_timeout_s:g}s")
        except Exception as e:  # noqa: BLE001 — recorded, never raised out of the scheduler
            ev.update(status="error", error=str(e)[:300])
        finally:
            reset_tenant(token)
        ev["duration_ms"] = int((datetime.now(UTC) - started).total_seconds() * 1000)
        return ev

    async def _record(self, d: dict[str, Any], ev: dict[str, Any]) -> None:
        await self.store.append_event(d, ev)
        metrics.MONITOR_RUNS.labels(d.get("server_type", "unknown"), ev["status"]).inc()
        status = {"ok": "ok", "breach": "ok", "skipped": "denied", "error": "error"}[ev["status"]]
        await self.access_log.publish(AccessEvent(
            key_id=d.get("key_id", "unknown"), company_id=d["company_id"], user_id=d["user_id"], server_id=d["server_id"],
            server_type=d["server_type"], tool=f"monitor:{d['monitor_id']}", args_summary=d.get("name", "")[:200],
            status=status, error=ev.get("reason") or ev.get("error"), duration_ms=int(ev.get("duration_ms", 0)),
            extra={"monitor_status": ev["status"]},
        ))
        try:
            await self._logs.xadd("monitor:events", {"event": orjson.dumps(ev, default=str).decode()}, maxlen=100_000, approximate=True)
        except Exception:  # noqa: BLE001
            log.warning("monitor event stream unavailable")
        if ev["status"] == "breach" and d.get("channels"):
            await notifiers.dispatch(ev, d["channels"])

    async def run_once(self, d: dict[str, Any], now: datetime) -> dict[str, Any] | None:
        """Run one due monitor under a per-monitor lock (safe with several replicas)."""
        if d.get("status") != "active" or datetime.fromisoformat(d["next_run_at"]) > now:
            return None
        lock = f"monitor:lock:{d['monitor_id']}"
        if not await self.resolver.r.set(lock, "1", nx=True, ex=300):
            return None
        try:
            # the listing may be stale: a delete / pause / resume can have landed since — never write it back
            fresh = await self.store.read_def(d)
            if fresh is None or fresh.get("status") != "active" or datetime.fromisoformat(fresh["next_run_at"]) > now:
                return None
            d = fresh
            d["next_run_at"] = (now + timedelta(seconds=int(d["interval_s"]))).isoformat()
            await self.store.put_def(d)
            ctx, reason = await self._authorize(d)
            if ctx is None:
                ev = {"ts": now.isoformat(), "monitor_id": d["monitor_id"], "company_id": d["company_id"], "server_id": d["server_id"], "status": "skipped", "reason": reason}
            else:
                ev = await self._execute(d, ctx)
            await self._record(d, ev)
            return ev
        finally:
            await self.resolver.r.delete(lock)  # TTL only covers a crashed runner

    async def tick(self, now: datetime | None = None) -> int:
        """Due monitors run with bounded concurrency, each bounded in time, in round-robin order across companies
        so one tenant with many monitors cannot push everyone else's runs back."""
        now = now or datetime.now(UTC)
        defs = [d for d in await self.store.list_defs(layout.monitor_prefix(self.s.server_type))
                if not (self.s.server_id and d.get("server_id") != self.s.server_id)]  # pinned: own server only
        sem = asyncio.Semaphore(max(1, self.s.monitor_concurrency))
        ran = 0

        async def one(d: dict[str, Any]) -> None:
            nonlocal ran
            async with sem:
                try:
                    if await self.run_once(d, now):
                        ran += 1
                except Exception:  # noqa: BLE001
                    log.exception("monitor %s failed", d.get("monitor_id"))

        await asyncio.gather(*(one(d) for d in fair_order(defs)))
        return ran

    async def run_forever(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("monitor scheduler tick failed")
            await asyncio.sleep(self.s.monitor_tick_s)

    def start(self) -> None:
        if self._task is None and self.s.monitor_tick_s > 0 and self.s.server_type:
            self._task = asyncio.create_task(self.run_forever(), name="monitors")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None


def fair_order(defs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Interleave companies: c1#1, c2#1, c3#1, c1#2, … (stable within a company)."""
    by: dict[str, list[dict[str, Any]]] = {}
    for d in defs:
        by.setdefault(d.get("company_id", ""), []).append(d)
    queues = [by[k] for k in sorted(by)]
    out: list[dict[str, Any]] = []
    i = 0
    while any(queues):
        for q in queues:
            if i < len(q):
                out.append(q[i])
        i += 1
        if all(i >= len(q) for q in queues):
            break
    return out


def public(d: dict[str, Any]) -> dict[str, Any]:
    hidden = {"key_hash", "company_id", "user_id"}
    return {k: v for k, v in d.items() if k not in hidden}


_ACTIVE: list[Monitors] = []


def start_background() -> None:
    """Called from each server's lifespan."""
    for m in _ACTIVE:
        m.start()


async def stop_background() -> None:
    for m in _ACTIVE:
        await m.stop()
