"""Upstream tool-list cache: hits skip the upstream, any permission change (version bump) misses at once."""

import os
from types import SimpleNamespace

import orjson
import pytest
import redis.asyncio as aioredis

from mcp_gateway.server import Discovery, GatewayProvider
from mcpcluster_common.authz import Resolver
from mcpcluster_common.settings import CommonSettings

REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379")
DB = 14
RAW = "mcpk_gateway_cache_test"


@pytest.fixture
async def r():
    cl = aioredis.from_url(REDIS_URL, db=DB, decode_responses=True)
    try:
        await cl.ping()
    except Exception:  # noqa: BLE001
        pytest.skip("redis not reachable")
    await cl.flushdb()
    recs = {
        "company:c": {"status": "active", "version": 1},
        "user:c:u": {"status": "active", "version": 1},
        f"apikey:{Resolver.hash_key(RAW)}": {"key_id": "k", "company_id": "c", "user_id": "u", "server_ids": None, "status": "active", "version": 1},
        "server:srv_aaaa0001": {"status": "active", "server_type": "postgres", "name": "Sales", "version": 1},
        "binding:c:srv_aaaa0001": {"status": "active", "server_type": "postgres", "tenant": {"mode": "schema", "schema_name": "a"}, "version": 1},
        "access:c:u:srv_aaaa0001": {"status": "active", "server_type": "postgres", "permissions": ["read"], "allowed_tools": ["run_select"], "version": 1},
    }
    for k, v in recs.items():
        await cl.set(k, orjson.dumps(v).decode())
    await cl.sadd("accessidx:c:u", "srv_aaaa0001")
    yield cl
    await cl.flushdb()
    await cl.aclose()


class Counting(GatewayProvider):
    async def _fetch_upstream(self, target, raw_key):
        self.upstream_list_calls += 1
        return [SimpleNamespace(name="run_select", title=None, description="d", input_schema={"type": "object"}, annotations=None,
                                output_schema=None, icons=None, meta=None, execution=None)]


def _provider(ttl=30.0):
    d = Discovery(CommonSettings(redis_url=REDIS_URL, redis_db_auth=DB), {"postgres": "http://upstream/mcp"})
    return Counting(d, ttl_s=ttl)


async def test_hits_skip_upstream_and_version_bump_misses(r):
    p = _provider()
    names = [t.name for t in await p._catalog(raw=RAW)]
    assert names == ["sales-0001__run_select"] and p.upstream_list_calls == 1
    for _ in range(5):
        await p._catalog(raw=RAW)
        await p._catalog(only_alias="sales-0001", raw=RAW)  # the call path (_get_tool) too
    assert p.upstream_list_calls == 1
    # access changed (e.g. a tool removed) -> version bump -> immediate miss
    await r.set("access:c:u:srv_aaaa0001", orjson.dumps({"status": "active", "server_type": "postgres", "permissions": ["read"], "allowed_tools": ["run_select"], "version": 2}).decode())
    await p._catalog(raw=RAW)
    assert p.upstream_list_calls == 2


async def test_suspension_removes_server_without_waiting_for_ttl(r):
    p = _provider()
    assert await p._catalog(raw=RAW)
    await r.set("access:c:u:srv_aaaa0001", orjson.dumps({"status": "suspended", "server_type": "postgres", "permissions": ["read"], "allowed_tools": ["run_select"], "version": 3}).decode())
    assert await p._catalog(raw=RAW) == []  # discovery reads the live records every time


async def test_other_keys_do_not_share_entries_and_ttl_expires(r):
    p = _provider(ttl=0.0)
    await p._catalog(raw=RAW)
    await p._catalog(raw=RAW)
    assert p.upstream_list_calls == 2  # ttl 0: no reuse
    assert await _provider()._catalog(raw="mcpk_unknown") == []


async def test_company_or_user_suspension_hides_tools_immediately(r):
    # security review 2 #5: discovery also requires company and user to be active (not only after the cache TTL)
    p = _provider()
    assert await p._catalog(raw=RAW)
    await r.set("user:c:u", orjson.dumps({"status": "suspended", "version": 2}).decode())
    assert await p._catalog(raw=RAW) == []
    await r.set("user:c:u", orjson.dumps({"status": "active", "version": 3}).decode())
    await r.set("company:c", orjson.dumps({"status": "suspended", "version": 2}).decode())
    assert await p._catalog(raw=RAW) == []
