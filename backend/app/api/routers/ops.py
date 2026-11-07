"""Operator endpoints: debugging key resolution, forcing reconciliation, access logs."""

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.api.deps import C, M, P, require_company_access
from app.api.schemas import ApiKeyOut, EventOut, GlobalStatsOut, HealthOut, Page, ReconcileOut, ResolveOut
from app.core.errors import NotFound
from app.core.security import hash_api_key
from app.domain.enums import Status
from app.domain.models import now
from app.infra.redis.projection import access_key, apikey_key, binding_key, company_key, server_key, user_key

router = APIRouter(tags=["ops"])


class ResolveIn(BaseModel):
    raw_key: str
    server_id: str | None = None  # evaluate only this server; default: every server in the key's scope


@router.post("/ops/resolve", response_model=ResolveOut)
async def resolve(body: ResolveIn, p: M, c: C):
    """Same decision the MCP servers make, computed from the DB, plus a Redis consistency check."""
    k = await c.apikeys.resolve(body.raw_key)
    if not k:
        return ResolveOut(key=None, effective="unknown", redis_in_sync=await c.sync.read(apikey_key(hash_api_key(body.raw_key))) is None)
    reasons = []
    if k.status != Status.ACTIVE:
        reasons.append(f"key:{k.status}")
    if k.is_expired(now()):
        reasons.append("key:expired")
    comp = await c.companies.get(k.company_id)
    if comp.status != Status.ACTIVE:
        reasons.append(f"company:{comp.status}")
    user = await c.users.get(k.company_id, k.user_id)
    if user.status != Status.ACTIVE:
        reasons.append(f"user:{user.status}")
    in_sync = [await c.sync.is_in_sync(k), await c.sync.is_in_sync(comp), await c.sync.is_in_sync(user)]

    if body.server_id:
        sids = [body.server_id]
    elif k.server_ids is not None:
        sids = list(k.server_ids)
    else:
        sids, cur = [], None
        while True:
            page, cur = await c.access.list_for_user(k.company_id, k.user_id, limit=200, cursor=cur)
            sids += [a.server_id for a in page]
            if not cur:
                break
    servers: dict[str, str] = {}
    for sid in sids:
        r: list[str] = []
        if k.server_ids is not None and sid not in k.server_ids:
            r.append("scope:excluded")
        for label, getter, redis_key in (
            ("server", c.servers.get(sid), server_key(sid)),
            ("binding", c.bindings.get(k.company_id, sid), binding_key(k.company_id, sid)),
            ("access", c.access.get(k.company_id, k.user_id, sid), access_key(k.company_id, k.user_id, sid)),
        ):
            try:
                e = await getter
            except NotFound:
                r.append(f"{label}:missing")
                in_sync.append(await c.sync.read(redis_key) is None)
                continue
            if e.status != Status.ACTIVE:
                r.append(f"{label}:{e.status}")
            in_sync.append(await c.sync.is_in_sync(e))
        servers[sid] = "active" if not r else "denied:" + ",".join(r)
    return ResolveOut(key=ApiKeyOut.of(k), effective="active" if not reasons else "denied:" + ",".join(reasons), servers=servers, redis_in_sync=all(in_sync))


@router.post("/ops/reconcile", response_model=ReconcileOut)
async def reconcile(p: M, c: C):
    await c.outbox.drain()
    rep = await c.reconciler.run_once()
    return ReconcileOut(checked=rep.checked, repaired=rep.repaired, removed_stray=rep.removed_stray, errors=rep.errors)


@router.get("/ops/redis/{kind}/{ident:path}")
async def redis_peek(kind: str, ident: str, p: M, c: C):
    mapping = {"company": company_key, "server": server_key, "apikey": apikey_key}
    if kind in mapping:
        key = mapping[kind](ident)
    elif kind == "user":
        key = user_key(*ident.split("/", 1))
    elif kind == "binding":
        key = binding_key(*ident.split("/", 1))
    elif kind == "access":
        parts = ident.split("/", 2)
        if len(parts) != 3:
            return {"error": "access ident is company/user/server"}
        key = access_key(*parts)
    else:
        return {"error": "unknown kind"}
    return {"key": key, "value": await c.sync.read(key)}


@router.get("/companies/{company_id}/logs", response_model=Page[dict])
async def company_logs(company_id: str, p: P, c: C, start: str | None = None, end: str | None = None, limit: int = Query(100, le=500), cursor: str | None = None):
    require_company_access(p, company_id)
    items, nxt = await c.logs.list_for_company(company_id, start=start, end=end, limit=limit, cursor=cursor)
    return Page(items=items, next_cursor=nxt)


@router.get("/companies/{company_id}/api-keys/{key_id}/logs", response_model=Page[dict])
async def key_logs(company_id: str, key_id: str, p: P, c: C, start: str | None = None, end: str | None = None, limit: int = Query(100, le=500), cursor: str | None = None):
    require_company_access(p, company_id)
    await c.apikeys.get(company_id, key_id)  # 404 + tenant check: key must belong to this company
    items, nxt = await c.logs.list_for_key(key_id, start=start, end=end, limit=limit, cursor=cursor)
    return Page(items=items, next_cursor=nxt)


# ---- dashboards (master): cached global counts, MCP service liveness, the recent-event feed
@router.get("/ops/stats", response_model=GlobalStatsOut)
async def global_stats(p: M, c: C, refresh: bool = False):
    """Counts from the last entity scan (STATS_REFRESH_S) — `refresh=1` rescans now. calls_24h from the hourly buckets."""
    data = await c.stats.get_global(refresh=refresh)
    data["calls_24h"] = await c.metrics.summary("all")
    return GlobalStatsOut(**data)


@router.get("/ops/health", response_model=list[HealthOut])
async def mcp_health(p: M, c: C):
    return [HealthOut(**v) for v in (await c.health.snapshot()).values()]


@router.get("/ops/events", response_model=list[EventOut])
async def recent_events(p: M, c: C, limit: int = Query(50, le=500)):
    """Newest first, every company (the per-company feed is /companies/{cid}/logs)."""
    return [EventOut(**{k: v for k, v in e.items() if k in EventOut.model_fields}) for e in await c.metrics.recent(limit)]
