"""mcp-gateway: one MCP endpoint for every server a key may use.

The gateway never decides permissions and never touches a datasource. Per request it
  1. rejects unknown / inactive / expired keys early,
  2. finds the candidate servers (key scope, or the user's access index for an all-servers key) and keeps the
     ones whose server, binding and access records are active,
  3. lists each upstream server's tools *with the caller's own key* (`<upstream>/mcp?server=<id>`), so the list
     is exactly what that server would allow, and exposes them as `<alias>__<tool>`,
  4. forwards calls the same way. Authorization, tenant guards, rate limits, redaction and access logs all
     happen on the upstream server, unchanged.

Server-independent tools (report, list_artifacts, get_artifact) are exposed once, unprefixed, and routed to the
first server (by alias) that offers them to this caller."""

import asyncio
import logging
import os
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import orjson
import redis.asyncio as aioredis
from fastmcp import Client, FastMCP
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.server.dependencies import get_http_headers
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.server.providers import Provider
from fastmcp.server.providers.proxy import ProxyTool
from fastmcp.tools import Tool

from mcpcluster_common import metrics
from mcpcluster_common.authz import Resolver, _load
from mcpcluster_common.middleware import extract_api_key, unauthorized
from mcpcluster_common.model import AccessRec, ApiKeyRec, BindingRec, CompanyRec, ServerRec, UserRec
from mcpcluster_common.settings import CommonSettings

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("mcp-gateway")

SETTINGS = CommonSettings()
SHARED_TOOLS = ("report", "list_artifacts", "get_artifact")
SEP = "__"
MAX_ALIAS = 24
LIST_CACHE_TTL_S = float(os.environ.get("GATEWAY_LIST_CACHE_TTL_S", "30"))
SESSION_IDLE_S = float(os.environ.get("GATEWAY_SESSION_IDLE_S", "60"))
SESSION_MAX = int(os.environ.get("GATEWAY_SESSION_MAX", "1000"))
UPSTREAM_CONNECT_TIMEOUT_S = float(os.environ.get("GATEWAY_UPSTREAM_CONNECT_TIMEOUT_S", "5"))
UPSTREAM_CALL_TIMEOUT_S = float(os.environ.get("GATEWAY_UPSTREAM_CALL_TIMEOUT_S", "120"))


def parse_upstreams(raw: str) -> dict[str, str]:
    """`postgres=http://mcp-postgres:9001/mcp,clickhouse=...` -> {server_type: url}"""
    out = {}
    for part in filter(None, (p.strip() for p in raw.split(","))):
        t, _, url = part.partition("=")
        if t and url:
            out[t.strip()] = url.strip()
    return out


UPSTREAMS = parse_upstreams(os.environ.get("GATEWAY_UPSTREAMS", ""))


@dataclass(frozen=True)
class Target:
    server_id: str
    server_type: str
    name: str
    alias: str
    url: str
    versions: tuple[int, ...] = ()  # company, user, server, binding, access — any change invalidates cached lists/sessions


def slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s[:MAX_ALIAS].strip("-") or "server"


def assign_aliases(servers: list[tuple[str, str, str]]) -> dict[str, str]:
    """(server_id, server_type, name) -> alias. Always `<name-slug>-<last 4 of id>`, so an alias names one server
    for good: granting access to another server with the same name can never take over a name a client already
    listed (a call to `sales-pg-1a2b__run_delete` cannot silently land on a different database)."""
    out: dict[str, str] = {}
    taken: set[str] = set()
    for sid, stype, name in sorted(servers):
        alias = f"{slug(name or stype)[:MAX_ALIAS - 5].strip('-') or 'server'}-{sid[-4:].lower()}"
        if alias in taken:  # same name and same id suffix: extend with more of the id (still derived from the id)
            alias = f"{slug(name or stype)[:MAX_ALIAS - 9].strip('-') or 'server'}-{sid[-8:].lower()}"
        taken.add(alias)
        out[sid] = alias
    return out


class Discovery:
    def __init__(self, settings: CommonSettings, upstreams: dict[str, str]):
        self.s = settings
        self.upstreams = upstreams
        self.r = aioredis.from_url(settings.redis_url, db=settings.redis_db_auth, decode_responses=True)

    async def key(self, raw: str | None) -> tuple[ApiKeyRec | None, str]:
        if not raw:
            return None, "missing api key"
        key = _load(ApiKeyRec, await self.r.get(f"apikey:{Resolver.hash_key(raw)}"))
        if key is None:
            return None, "unknown api key"
        if key.status != "active":
            return None, f"key {key.status}"
        if key.is_expired():
            return None, "key expired"
        return key, "ok"

    async def parents_active(self, key: ApiKeyRec) -> bool:
        async with self.r.pipeline(transaction=False) as p:
            p.get(f"company:{key.company_id}")
            p.get(f"user:{key.company_id}:{key.user_id}")
            c_raw, u_raw = await p.execute()
        company, user = _load(CompanyRec, c_raw), _load(UserRec, u_raw)
        return bool(company and user and company.status == user.status == "active")

    async def targets(self, key: ApiKeyRec) -> list[Target]:
        sids = list(key.server_ids) if key.server_ids is not None else sorted(await self.r.smembers(f"accessidx:{key.company_id}:{key.user_id}"))
        if not sids:
            return []
        async with self.r.pipeline(transaction=False) as p:
            p.get(f"company:{key.company_id}")
            p.get(f"user:{key.company_id}:{key.user_id}")
            for sid in sids:
                p.get(f"server:{sid}")
                p.get(f"binding:{key.company_id}:{sid}")
                p.get(f"access:{key.company_id}:{key.user_id}:{sid}")
            raw = await p.execute()
        company, user = _load(CompanyRec, raw[0]), _load(UserRec, raw[1])
        if not (company and user and company.status == user.status == "active"):
            return []  # a company/user suspend hides everything at once (not only after the list-cache TTL)
        parent = (company.version, user.version)
        raw = raw[2:]
        live: list[tuple[str, str, str]] = []
        versions: dict[str, tuple[int, int, int]] = {}
        for i, sid in enumerate(sids):
            server, binding, access = _load(ServerRec, raw[3 * i]), _load(BindingRec, raw[3 * i + 1]), _load(AccessRec, raw[3 * i + 2])
            if not (server and binding and access):
                continue
            if server.status == binding.status == access.status == "active" and server.server_type in self.upstreams:
                live.append((sid, server.server_type, server.name))
                versions[sid] = (*parent, server.version, binding.version, access.version)
        aliases = assign_aliases(live)
        return [Target(sid, t, n, aliases[sid], self.upstreams[t], versions[sid]) for sid, t, n in sorted(live, key=lambda x: aliases[x[0]])]


OAUTH = None  # ApiKeyConsentProvider when GATEWAY_PUBLIC_URL is set


async def _caller_key() -> str | None:
    """The API key this request acts as: a plain `mcpk_…` bearer, or the key behind an OAuth access token."""
    try:
        bearer = extract_api_key(get_http_headers(include={"authorization", "x-api-key"}))
    except Exception:  # noqa: BLE001
        return None
    if not bearer or OAUTH is None:
        return bearer
    return await OAUTH.raw_key_for(bearer)


def _new_client(target: Target, raw_key: str) -> Client:
    return Client(StreamableHttpTransport(f"{target.url}?server={target.server_id}", headers={"Authorization": f"Bearer {raw_key}"}),
                  init_timeout=UPSTREAM_CONNECT_TIMEOUT_S, timeout=UPSTREAM_CALL_TIMEOUT_S)


class UpstreamPool:
    """Open upstream MCP sessions reused across calls, keyed by (key hash, server, server/binding/access versions):
    a permission change yields a new key -> a fresh session. FastMCP clients are reference-counted, so ProxyTool's own
    `async with client` reuses the open session instead of re-initializing (the per-call handshake was most of the
    gateway's overhead). Sessions idle for SESSION_IDLE_S are closed; at most SESSION_MAX are kept. Reuse never
    widens access: the gateway re-checks the key on every request and the upstream authorizes every call."""

    def __init__(self, idle_s: float = SESSION_IDLE_S, max_sessions: int = SESSION_MAX):
        self.idle_s = idle_s
        self.max = max_sessions
        self._open: dict[tuple, tuple[Client, float]] = {}
        self._pending: dict[tuple, asyncio.Future] = {}
        self._lock = asyncio.Lock()  # guards the dicts only — never held across network I/O
        self.opened = 0  # observability / tests

    async def get(self, target: Target, raw_key: str) -> Client:
        ck = (Resolver.hash_key(raw_key), target.server_id, target.versions)
        now = time.monotonic()
        async with self._lock:
            hit = self._open.get(ck)
            if hit and hit[0].is_connected():
                self._open[ck] = (hit[0], now)
                return hit[0]
            if hit:  # the session died (upstream restart / network): replace it
                self._open.pop(ck)
            fut = self._pending.get(ck)
            owner = fut is None
            if owner:  # one opener per key; concurrent requests for the same key wait on it
                fut = asyncio.get_running_loop().create_future()
                self._pending[ck] = fut
            doomed = self._pick_evictions(now)
        await self._close(doomed)
        if not owner:
            return await asyncio.shield(fut)
        try:
            cl = _new_client(target, raw_key)
            await asyncio.wait_for(cl.__aenter__(), UPSTREAM_CONNECT_TIMEOUT_S)  # one slow upstream only delays itself
        except BaseException as e:
            async with self._lock:
                self._pending.pop(ck, None)
            fut.set_exception(e if isinstance(e, Exception) else RuntimeError("upstream connect cancelled"))
            fut.exception()  # mark retrieved: waiters (if any) get it via shield
            raise
        async with self._lock:
            self._pending.pop(ck, None)
            self._open[ck] = (cl, time.monotonic())
            self.opened += 1
        fut.set_result(cl)
        return cl

    def _pick_evictions(self, now: float) -> list[Client]:
        """Call with the lock held; returns clients to close after releasing it."""
        stale = [k for k, (_, t) in self._open.items() if now - t > self.idle_s]
        if len(self._open) - len(stale) >= self.max:  # still full: drop the least recently used
            stale += sorted((k for k in self._open if k not in stale), key=lambda k: self._open[k][1])[: len(self._open) - self.max + 1]
        return [self._open.pop(k)[0] for k in stale]

    @staticmethod
    async def _close(clients: list[Client]) -> None:
        for cl in clients:  # reference-counted: an in-flight call keeps its session until it finishes
            try:
                await asyncio.wait_for(cl.__aexit__(None, None, None), UPSTREAM_CONNECT_TIMEOUT_S)
            except BaseException:  # noqa: BLE001
                log.debug("closing upstream session failed", exc_info=True)

    async def sweep(self) -> None:
        """Close idle sessions (and drop the raw keys they hold) even when no request misses the pool."""
        async with self._lock:
            doomed = self._pick_evictions(time.monotonic())
        await self._close(doomed)

    async def close(self) -> None:
        async with self._lock:
            doomed = [c for c, _ in self._open.values()]
            self._open.clear()
        await self._close(doomed)


POOL = UpstreamPool()


def _client_factory(target: Target, raw_key: str):
    async def make() -> Client:
        return await POOL.get(target, raw_key)
    return make


class GatewayProvider(Provider):
    """Upstream tool lists are cached per (key hash, server, server/binding/access versions) for a short TTL:
    a permission change bumps a version and misses the cache at once. The cache only affects what is *listed*;
    every call is still authorized by the upstream server."""

    def __init__(self, discovery: Discovery, ttl_s: float = LIST_CACHE_TTL_S):
        super().__init__()
        self.d = discovery
        self.ttl_s = ttl_s
        self._cache: dict[tuple, tuple[float, list[Any]]] = {}
        self.upstream_list_calls = 0  # observability / tests

    async def _fetch_upstream(self, target: Target, raw_key: str) -> list[Any]:
        self.upstream_list_calls += 1
        cl = await POOL.get(target, raw_key)
        async with cl:
            return list(await cl.list_tools())

    async def _upstream_tools(self, target: Target, raw_key: str) -> list[Any]:
        ck = (Resolver.hash_key(raw_key), target.server_id, target.versions)
        hit = self._cache.get(ck)
        now = time.monotonic()
        if hit and now - hit[0] < self.ttl_s:
            metrics.GATEWAY_LIST_CACHE.labels("hit").inc()
            return hit[1]
        metrics.GATEWAY_LIST_CACHE.labels("miss").inc()
        try:
            tools = await self._fetch_upstream(target, raw_key)
        except Exception as e:  # noqa: BLE001 — a denied or unreachable upstream simply contributes nothing
            log.info("upstream %s (%s) contributed no tools: %s", target.alias, target.server_id, str(e)[:200])
            return []  # failures are not cached
        if len(self._cache) > 10_000:  # bounded: drop expired, then everything if still large
            self._cache = {k: v for k, v in self._cache.items() if now - v[0] < self.ttl_s}
            if len(self._cache) > 10_000:
                self._cache.clear()
        self._cache[ck] = (now, tools)
        return tools

    def _wrap(self, target: Target, raw_key: str, mcp_tool, *, shared: bool) -> Tool:
        t = ProxyTool.from_mcp_tool(_client_factory(target, raw_key), mcp_tool)
        name = mcp_tool.name if shared else f"{target.alias}{SEP}{mcp_tool.name}"
        label = f"[{target.name or target.alias} · {target.server_type}] " if not shared else ""
        return t.model_copy(update={"name": name, "description": label + (mcp_tool.description or "")})

    async def _catalog(self, only_alias: str | None = None, want_shared: str | None = None, *, raw: str | None = None) -> list[Tool]:
        raw = raw if raw is not None else await _caller_key()
        key, _ = await self.d.key(raw)
        if key is None:
            return []
        targets = await self.d.targets(key)
        if only_alias is not None:
            targets = [t for t in targets if t.alias == only_alias]
        lists = await asyncio.gather(*(self._upstream_tools(t, raw) for t in targets))
        tools: list[Tool] = []
        shared_done: set[str] = set()
        for target, mcp_tools in zip(targets, lists, strict=True):
            for mt in mcp_tools:
                if mt.name in SHARED_TOOLS:
                    if mt.name not in shared_done and (want_shared is None or want_shared == mt.name):
                        shared_done.add(mt.name)
                        tools.append(self._wrap(target, raw, mt, shared=True))
                elif want_shared is None:
                    tools.append(self._wrap(target, raw, mt, shared=False))
        return tools

    async def _list_tools(self) -> Sequence[Tool]:
        return await self._catalog()

    async def _get_tool(self, name: str, version=None) -> Tool | None:  # noqa: ARG002
        if name in SHARED_TOOLS:
            found = await self._catalog(want_shared=name)
        elif SEP in name:
            found = await self._catalog(only_alias=name.split(SEP, 1)[0])
        else:
            return None
        return next((t for t in found if t.name == name), None)


class GatewayAuthMiddleware(Middleware):
    """Fail fast with the same errors as the servers. Not the security boundary — upstream is."""

    def __init__(self, discovery: Discovery):
        self.d = discovery

    async def _check(self) -> None:
        key, reason = await self.d.key(await _caller_key())
        if key is None:
            raise unauthorized(reason)

    async def on_list_tools(self, context: MiddlewareContext, call_next):
        await self._check()
        return await call_next(context)

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        await self._check()
        return await call_next(context)


async def _sweeper() -> None:
    while True:
        await asyncio.sleep(max(5.0, SESSION_IDLE_S / 2))
        try:
            await POOL.sweep()
        except Exception:  # noqa: BLE001
            log.exception("session sweep failed")


def _lifespan():
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(_app):
        task = asyncio.create_task(_sweeper(), name="upstream-session-sweeper")
        try:
            yield
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await POOL.close()
    return lifespan


# MCP `initialize` carries only this server's instructions: the upstream servers' own instructions never reach the
# client, so the shared analyst rule (numbers come from compute, not from the model) has to be stated here.
INSTRUCTIONS = (
    "FindTheGap MCP gateway. Tools are named <server>__<tool>; each call runs on that server with your key's permissions. "
    "report / list_artifacts / get_artifact are shared across your servers. "
    "Workflow for any analysis: discover the schema (list_tables / describe_table or the server's equivalent) -> pull a "
    "small, pre-aggregated result (SQL GROUP BY, aggregate) -> compute for every figure you report (statistics run in "
    "Rust over the full result; never calculate, sum or compare numbers in your own reasoning) -> chart for visuals -> "
    "report, where kpi/table/chart blocks must reference saved artifacts and text blocks carry interpretation only. "
    "State the query and the compute op behind each number so the insight is reproducible."
)


def build(settings: CommonSettings = SETTINGS, upstreams: dict[str, str] | None = None, public_url: str | None = None) -> FastMCP:
    global OAUTH
    d = Discovery(settings, upstreams if upstreams is not None else UPSTREAMS)
    public_url = public_url if public_url is not None else os.environ.get("GATEWAY_PUBLIC_URL", "")
    if public_url:  # OAuth 2.1 for connectors (ChatGPT, Claude.ai); plain API keys keep working
        from mcp_gateway.oauth import ApiKeyConsentProvider, SsoBridge

        async def key_is_active(raw: str) -> bool:
            key, _ = await d.key(raw)
            return key is not None and bool(await d.parents_active(key))

        OAUTH = ApiKeyConsentProvider(base_url=public_url, settings=settings, key_is_active=key_is_active, sso=SsoBridge.from_env(public_url))
    mcp = FastMCP(
        name="mcp-gateway",
        auth=OAUTH,
        lifespan=_lifespan(),
        instructions=INSTRUCTIONS,
        providers=[GatewayProvider(d)],
    )
    mcp.add_middleware(GatewayAuthMiddleware(d))
    metrics.install_metrics(mcp)
    return mcp


def main() -> None:
    SETTINGS.ensure_safe_runtime()
    if not UPSTREAMS:
        raise SystemExit("GATEWAY_UPSTREAMS is empty")
    from mcpcluster_common.tracing import setup_tracing

    setup_tracing("mcp-gateway")
    log.info("upstreams: %s", orjson.dumps(UPSTREAMS).decode())
    build().run(transport="http", host="0.0.0.0", port=int(os.environ.get("PORT", "9000")), path="/mcp", stateless_http=True)


if __name__ == "__main__":
    main()
