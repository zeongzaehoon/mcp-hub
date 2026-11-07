"""Per-company singleton connections. Keyed by (company_id, server_id, binding_version): when an
operator changes the tenant spec the version bumps, the old pool is closed and a new one is built."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Generic, TypeVar

from mcpcluster_common.context import TenantContext

log = logging.getLogger(__name__)
T = TypeVar("T")


class ConnectionRegistry(Generic[T]):
    def __init__(self, factory: Callable[[TenantContext], Awaitable[T]], closer: Callable[[T], Awaitable[None]]):
        self._factory = factory
        self._closer = closer
        self._pools: dict[tuple[str, str], tuple[tuple[int, int], T]] = {}
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._global = asyncio.Lock()

    async def get(self, ctx: TenantContext) -> T:
        k = (ctx.company_id, ctx.server_id)
        async with self._global:
            lock = self._locks.setdefault(k, asyncio.Lock())
        async with lock:
            cur = self._pools.get(k)
            if cur and cur[0] == ctx.connection_version:
                return cur[1]
            if cur:
                log.info("binding/server version changed for %s (%s -> %s); rebuilding pool", k, cur[0], ctx.connection_version)
                try:
                    await self._closer(cur[1])
                except Exception:  # noqa: BLE001
                    log.exception("closing stale pool failed")
            pool = await self._factory(ctx)
            self._pools[k] = (ctx.connection_version, pool)
            return pool

    async def close_all(self) -> None:
        for _, (_, p) in list(self._pools.items()):
            try:
                await self._closer(p)
            except Exception:  # noqa: BLE001
                pass
        self._pools.clear()
