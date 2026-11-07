import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import ORJSONResponse

from app.api.deps import Container
from app.api.routers import apikeys, auth, bindings, companies, ops, policies, servers, sso, users, workspace
from app.core.config import get_settings
from app.core.errors import DomainError
from app.core.security import decrypt_secret, encrypt_secret
from app.infra.dynamo.schema import ensure_tables
from app.services.bootstrap import bootstrap
from app.services.migrate import migrate_legacy_keys

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    if problems := s.runtime_problems():
        raise RuntimeError("unsafe configuration: " + "; ".join(problems))
    # TENANT_SECRET_KEY must be usable before we accept any request that stores secrets
    if decrypt_secret(encrypt_secret("probe")) != "probe":
        raise RuntimeError("TENANT_SECRET_KEY is not a valid Fernet key")
    container = Container.build()
    app.state.container = container
    async with container.repo.factory.client() as c:
        await ensure_tables(c)
    await bootstrap(container.repo, container.sync)
    await migrate_legacy_keys(container.repo, container.sync)  # idempotent; no-op once every key is scoped
    await container.outbox.heal_if_flushed()  # cold start against an empty Redis => full rebuild first
    tasks = [
        asyncio.create_task(container.outbox.run_forever(s.outbox_poll_seconds), name="outbox"),
        asyncio.create_task(container.reconciler.run_forever(s.reconcile_interval_seconds), name="reconciler"),
        asyncio.create_task(container.logs.run_forever(), name="log-consumer"),
        asyncio.create_task(container.workspace.run_forever(s.workspace_sync_interval_s), name="workspace-sync"),
        asyncio.create_task(container.stats.run_forever(s.stats_refresh_s), name="stats-refresh"),
        asyncio.create_task(container.health.run_forever(s.health_check_interval_s), name="health-check"),
    ]
    log.info("backend ready")
    try:
        yield
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await container.pools.close()
        await container.oidc.http.aclose()
        await container.health.aclose()


app = FastAPI(title="MCP Cluster Backend", lifespan=lifespan, default_response_class=ORJSONResponse)
app.add_middleware(
    CORSMiddleware, allow_origins=get_settings().cors_origin_list, allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)


@app.exception_handler(DomainError)
async def domain_error_handler(_: Request, exc: DomainError):
    return ORJSONResponse(status_code=exc.status_code, content={"code": exc.code, "message": exc.message})


@app.get("/healthz")
async def healthz():
    return {"ok": True}


for r in (auth, companies, users, servers, bindings, apikeys, sso, workspace, policies, ops):
    app.include_router(r.router, prefix="/api/v1")
