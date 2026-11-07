from fastapi import APIRouter, Query

from app.api.deps import C, M, P
from app.api.schemas import AccessOut, BindingOut, MetricsOut, Page, ServerIn, ServerOut, ServerPatch
from app.domain.enums import ServerType, Status
from app.domain.tool_catalog import TOOL_CATALOG

router = APIRouter(prefix="/servers", tags=["servers"])


@router.get("/catalog")
async def catalog(p: P):
    return {t: {k: v for k, v in tools.items()} for t, tools in TOOL_CATALOG.items()}


@router.get("", response_model=Page[ServerOut])
async def list_servers(p: P, c: C, limit: int = Query(50, le=200), cursor: str | None = None, status: Status | None = None, server_type: ServerType | None = None):
    items, nxt = await c.servers.list(limit=limit, cursor=cursor, status=status, server_type=server_type)
    health = await c.health.snapshot()
    return Page(items=[ServerOut.of(i, health.get(str(i.server_type))) for i in items], next_cursor=nxt)


@router.post("", response_model=ServerOut, status_code=201)
async def create_server(body: ServerIn, p: M, c: C):
    return ServerOut.of(await c.servers.create(server_type=body.server_type, name=body.name, description=body.description, endpoint=body.endpoint, dsn=body.dsn))


@router.get("/{server_id}", response_model=ServerOut)
async def get_server(server_id: str, p: P, c: C):
    s = await c.servers.get(server_id)
    return ServerOut.of(s, (await c.health.snapshot()).get(str(s.server_type)))


@router.get("/{server_id}/metrics", response_model=MetricsOut)
async def server_metrics(server_id: str, p: P, c: C):
    """Last 24h of calls through this server (every company), from the log consumer's hourly buckets."""
    await c.servers.get(server_id)
    return MetricsOut(**(await c.metrics.summary("srv", server_id)))


@router.patch("/{server_id}", response_model=ServerOut)
async def patch_server(server_id: str, body: ServerPatch, p: M, c: C):
    return ServerOut.of(await c.servers.update(server_id, name=body.name, description=body.description, endpoint=body.endpoint, status=body.status, dsn=body.dsn, clear_dsn=body.clear_dsn))


@router.get("/{server_id}/bindings", response_model=Page[BindingOut])
async def server_bindings(server_id: str, p: M, c: C, limit: int = Query(50, le=200), cursor: str | None = None):
    items, nxt = await c.bindings.list_for_server(server_id, limit=limit, cursor=cursor)
    return Page(items=[BindingOut.of(i) for i in items], next_cursor=nxt)


@router.get("/{server_id}/access", response_model=Page[AccessOut])
async def server_access(server_id: str, p: M, c: C, limit: int = Query(50, le=200), cursor: str | None = None):
    items, nxt = await c.access.list_for_server(server_id, limit=limit, cursor=cursor)
    return Page(items=[AccessOut.of(i) for i in items], next_cursor=nxt)
