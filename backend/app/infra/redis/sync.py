"""Version-guarded projection writes. An older version can never overwrite a newer one, so
concurrent outbox workers / inline sync / reconciler are all safe to run together."""

import logging

import orjson

from app.domain.models import Company, ServerAccess
from app.infra.dynamo.keys import Entity
from app.infra.redis.client import RedisPools
from app.infra.redis.projection import access_index_key, branding_key, dumps, project

log = logging.getLogger(__name__)

# returns 1 if written, 0 if skipped (existing newer/equal version)
_SET_IF_NEWER = """
local cur = redis.call('GET', KEYS[1])
if cur then
  local ok, obj = pcall(cjson.decode, cur)
  if ok and obj.version and tonumber(obj.version) >= tonumber(ARGV[2]) then
    if tonumber(obj.version) > tonumber(ARGV[2]) then return 0 end
    if cur == ARGV[1] then return 0 end
  end
end
redis.call('SET', KEYS[1], ARGV[1])
return 1
"""


class RedisSync:
    def __init__(self, pools: RedisPools):
        self.r = pools.auth
        self._script = self.r.register_script(_SET_IF_NEWER)

    async def write(self, e: Entity) -> bool:
        proj = project(e)
        if proj is None:  # backend-only entity
            return False
        key, value = proj
        written = await self._script(keys=[key], args=[dumps(value), value["version"]])
        if isinstance(e, ServerAccess):
            await self.index_access(e)
        if isinstance(e, Company):
            await self.write_branding(e)
        return bool(written)

    async def write_branding(self, c: Company) -> None:
        if c.branding and (c.branding.primary_color or c.branding.logo_base64 or c.branding.report_footer):
            await self.r.set(branding_key(c.company_id), dumps({**c.branding.model_dump(), "version": c.version}))
        else:
            await self.r.delete(branding_key(c.company_id))

    async def index_access(self, a: ServerAccess) -> None:
        await self.r.sadd(access_index_key(a.company_id, a.user_id), a.server_id)

    async def read(self, key: str) -> dict | None:
        raw = await self.r.get(key)
        return orjson.loads(raw) if raw else None

    async def is_in_sync(self, e: Entity) -> bool:
        proj = project(e)
        if proj is None:
            return True
        key, value = proj
        cur = await self.r.get(key)
        return cur == dumps(value)

    async def delete(self, key: str) -> None:
        await self.r.delete(key)
