"""Resolver decisions for the company -> user -> server hierarchy, against a real Redis (db 14).
Skipped when Redis is not reachable."""

import os

import orjson
import pytest
import redis.asyncio as aioredis

from mcpcluster_common.authz import Resolver
from mcpcluster_common.settings import CommonSettings

REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379")
DB = 14


def _settings(**over) -> CommonSettings:
    base = {"redis_url": REDIS_URL, "redis_db_auth": DB, "server_type": "postgres", "server_id": ""}
    base.update(over)
    return CommonSettings(**base)


@pytest.fixture
async def r():
    cl = aioredis.from_url(REDIS_URL, db=DB, decode_responses=True)
    try:
        await cl.ping()
    except Exception:  # noqa: BLE001
        pytest.skip("redis not reachable")
    await cl.flushdb()
    yield cl
    await cl.flushdb()
    await cl.aclose()


RAW = "mcpk_test_raw_key"
H = Resolver.hash_key(RAW)


async def _world(r, *, server_ids=("s1",), access=None, extra=None):
    recs = {
        "company:c": {"status": "active", "version": 1},
        "user:c:u": {"status": "active", "version": 1},
        "server:s1": {"status": "active", "server_type": "postgres", "version": 1},
        "server:s2": {"status": "active", "server_type": "postgres", "version": 1},
        "server:m1": {"status": "active", "server_type": "mongo", "version": 1},
        "binding:c:s1": {"status": "active", "server_type": "postgres", "tenant": {"mode": "schema", "schema_name": "a"}, "version": 3},
        "binding:c:s2": {"status": "active", "server_type": "postgres", "tenant": {"mode": "schema", "schema_name": "b"}, "version": 1},
        "binding:c:m1": {"status": "active", "server_type": "mongo", "tenant": {"mode": "database", "database": "a"}, "version": 1},
        "access:c:u:s1": access or {"status": "active", "server_type": "postgres", "permissions": ["read"], "allowed_tools": ["run_select"], "version": 1},
        "access:c:u:s2": {"status": "active", "server_type": "postgres", "permissions": ["read", "delete"], "allowed_tools": ["run_select", "run_delete"], "version": 1},
        "access:c:u:m1": {"status": "active", "server_type": "mongo", "permissions": ["read"], "allowed_tools": ["find"], "version": 1},
        f"apikey:{H}": {"key_id": "k", "company_id": "c", "user_id": "u", "server_ids": list(server_ids) if server_ids is not None else None, "status": "active", "version": 1},
    }
    recs.update(extra or {})
    for k, v in recs.items():
        if v is None:
            await r.delete(k)
        else:
            await r.set(k, orjson.dumps(v).decode())


async def _resolve(requested=None, **settings):
    res = Resolver(_settings(**settings))
    try:
        return await res.resolve(RAW, requested)
    finally:
        await res.close()


async def test_single_scope_is_implicit_and_permissions_come_from_access(r):
    await _world(r)
    d = await _resolve()
    assert d.allowed and d.ctx.server_id == "s1" and d.ctx.permissions == {"read"} and d.ctx.allowed_tools == {"run_select"}
    assert d.ctx.tenant.schema_name == "a" and d.ctx.binding_version == 3


async def test_multi_scope_requires_explicit_server_and_uses_that_access(r):
    await _world(r, server_ids=("s1", "s2"))
    d = await _resolve()
    assert not d.allowed and "?server=" in d.reason
    d = await _resolve("s2")
    assert d.allowed and d.ctx.server_id == "s2" and d.ctx.allowed_tools == {"run_select", "run_delete"}


async def test_all_scope_needs_server_and_is_bounded_by_accesses(r):
    await _world(r, server_ids=None)
    assert not (await _resolve()).allowed
    assert (await _resolve("s1")).allowed
    await r.delete("access:c:u:s2")
    d = await _resolve("s2")
    assert not d.allowed and d.reason == "server not available to this key"


async def test_server_outside_scope_denied(r):
    await _world(r, server_ids=("s1",))
    d = await _resolve("s2")
    assert not d.allowed and "scope" in d.reason


async def test_access_suspended_denies_only_that_server(r):
    await _world(r, server_ids=("s1", "s2"), access={"status": "suspended", "server_type": "postgres", "permissions": ["read"], "allowed_tools": ["run_select"], "version": 2})
    assert (await _resolve("s1")).reason == "access suspended"
    assert (await _resolve("s2")).allowed


@pytest.mark.parametrize("dead", ["company:c", "user:c:u", "server:s1", "binding:c:s1"])
async def test_every_parent_is_required(r, dead):
    await _world(r, extra={dead: None})
    d = await _resolve()
    assert not d.allowed and (d.reason.endswith("record missing") or d.reason == "server not available to this key")
    await _world(r, extra={dead: {"status": "suspended", "version": 9, "server_type": "postgres", "tenant": {"mode": "schema", "schema_name": "a"}}})
    assert not (await _resolve()).allowed


async def test_other_server_type_denied_on_this_endpoint(r):
    await _world(r, server_ids=("s1", "m1"))
    d = await _resolve("m1")
    assert not d.allowed and "different server type" in d.reason


async def test_pinned_endpoint(r):
    await _world(r, server_ids=None)
    assert (await _resolve(server_id="s2")).ctx.server_id == "s2"
    d = await _resolve("s1", server_id="s2")
    assert not d.allowed and "different server" in d.reason


@pytest.mark.parametrize("bad", ["s1:x", "../s1", "a" * 65, "s 1", "*"])
async def test_malformed_server_id_denied(r, bad):
    await _world(r)
    d = await _resolve(bad)
    assert not d.allowed and d.reason == "malformed server id"


async def test_record_without_scope_field_is_invalid(r):
    # a pre-hierarchy projection (server_id, no server_ids) must never be read as "all servers"
    await _world(r, extra={f"apikey:{H}": {"key_id": "k", "company_id": "c", "user_id": "u", "server_id": "s1", "server_type": "postgres", "permissions": ["read"], "allowed_tools": ["run_select"], "status": "active", "version": 1}})
    d = await _resolve("s1")
    assert not d.allowed and d.reason == "unknown api key"


async def test_server_readable_for_cross_server_artifacts(r):
    await _world(r, server_ids=("s1", "s2"))
    res = Resolver(_settings())
    try:
        ctx = (await res.resolve(RAW, "s1")).ctx
        assert await res.server_readable(ctx, "s1") and await res.server_readable(ctx, "s2")
        assert not await res.server_readable(ctx, "m1")  # outside the key scope
        assert not await res.server_readable(ctx, "bad:id")
        await r.set("access:c:u:s2", orjson.dumps({"status": "suspended", "server_type": "postgres", "permissions": ["read"], "allowed_tools": ["run_select"], "version": 2}).decode())
        assert not await res.server_readable(ctx, "s2")
        await r.set("access:c:u:s2", orjson.dumps({"status": "active", "server_type": "postgres", "permissions": ["delete"], "allowed_tools": ["run_delete"], "version": 3}).decode())
        assert not await res.server_readable(ctx, "s2")  # access without read
        await r.delete("binding:c:s2")
        assert not await res.server_readable(ctx, "s2")
    finally:
        await res.close()


async def test_missing_server_binding_access_are_indistinguishable(r):
    # security review #8: an all-servers key must not learn whether a server id exists
    await _world(r, server_ids=None)
    reasons = {(await _resolve(sid)).reason for sid in ("srv_does_not_exist", "m1x", "s9")}
    await r.delete("binding:c:s2")
    reasons.add((await _resolve("s2")).reason)
    assert reasons == {"server not available to this key"}
