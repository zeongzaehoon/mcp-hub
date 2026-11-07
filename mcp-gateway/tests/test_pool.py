"""Upstream session pool: reuse per (key, server, permission versions); permission change / idle / dead session
-> a fresh session; bounded size."""

import mcp_gateway.server as gw
from mcp_gateway.server import Target, UpstreamPool


class FakeClient:
    instances: list["FakeClient"] = []

    def __init__(self, *_a, **_k):
        self.connected = False
        self.closed = False
        FakeClient.instances.append(self)

    async def __aenter__(self):
        self.connected = True
        return self

    async def __aexit__(self, *exc):
        self.connected = False
        self.closed = True

    def is_connected(self):
        return self.connected


def _t(versions=(1, 1, 1, 1, 1), sid="srv_a"):
    return Target(sid, "postgres", "n", "n-srva", "http://u/mcp", versions)


async def test_reuse_and_invalidation(monkeypatch):
    monkeypatch.setattr(gw, "_new_client", lambda target, raw: FakeClient())
    p = UpstreamPool(idle_s=60, max_sessions=10)
    a = await p.get(_t(), "k1")
    assert await p.get(_t(), "k1") is a and p.opened == 1            # reused: no second handshake
    assert await p.get(_t(), "k2") is not a                           # other key -> own session
    assert await p.get(_t(versions=(1, 1, 1, 1, 2)), "k1") is not a         # access changed -> fresh session
    a.connected = False                                               # upstream went away
    b = await p.get(_t(), "k1")
    assert b is not a and b.connected


async def test_idle_eviction_and_bound(monkeypatch):
    monkeypatch.setattr(gw, "_new_client", lambda target, raw: FakeClient())
    p = UpstreamPool(idle_s=0.0, max_sessions=2)
    a = await p.get(_t(), "k1")
    await p.get(_t(sid="srv_b"), "k1")  # a is now idle beyond 0 s -> closed on the next open
    assert a.closed
    p2 = UpstreamPool(idle_s=60, max_sessions=2)
    x = await p2.get(_t(sid="s1"), "k")
    await p2.get(_t(sid="s2"), "k")
    await p2.get(_t(sid="s3"), "k")     # over the bound: least recently used goes
    assert x.closed and len(p2._open) == 2
    await p2.close()
    assert not p2._open


async def test_one_hung_upstream_does_not_block_others(monkeypatch):
    """security review 2 #1: connecting to a stuck upstream must not hold a lock other requests need."""
    import asyncio

    class Hung(FakeClient):
        async def __aenter__(self):
            await asyncio.sleep(3600)

    monkeypatch.setattr(gw, "UPSTREAM_CONNECT_TIMEOUT_S", 0.5)
    monkeypatch.setattr(gw, "_new_client", lambda target, raw: Hung() if target.server_id == "stuck" else FakeClient())
    p = UpstreamPool()
    stuck = asyncio.create_task(p.get(_t(sid="stuck"), "k1"))
    await asyncio.sleep(0.05)
    ok = await asyncio.wait_for(p.get(_t(sid="fine"), "k2"), 0.3)  # served while the other connect is pending
    assert ok.connected
    try:
        await stuck
        raise AssertionError("stuck connect should time out")
    except TimeoutError:
        pass
    assert (await p.get(_t(sid="fine"), "k2")) is ok  # still pooled


async def test_concurrent_requests_share_one_connect(monkeypatch):
    import asyncio

    class Slow(FakeClient):
        async def __aenter__(self):
            await asyncio.sleep(0.05)
            return await super().__aenter__()

    monkeypatch.setattr(gw, "_new_client", lambda target, raw: Slow())
    p = UpstreamPool()
    got = await asyncio.gather(*(p.get(_t(), "k1") for _ in range(10)))
    assert len({id(c) for c in got}) == 1 and p.opened == 1


async def test_sweep_closes_idle_sessions_without_traffic(monkeypatch):
    monkeypatch.setattr(gw, "_new_client", lambda target, raw: FakeClient())
    p = UpstreamPool(idle_s=0.0)
    c = await p.get(_t(), "k1")
    await p.sweep()
    assert c.closed and not p._open
