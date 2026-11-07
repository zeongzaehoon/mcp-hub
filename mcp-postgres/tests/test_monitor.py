"""Monitor spec validation and condition evaluation (pure)."""

import os

import pytest

from mcpcluster_common import monitor as mon
from mcpcluster_common import notifiers
from mcpcluster_common.settings import CommonSettings


def _m(**over):
    return mon.Monitors(CommonSettings(monitor_min_interval_s=60), resolver=None, storage=None, runner=None)  # type: ignore[arg-type]


BASE = {"name": "n", "source": {"sql": "select 1"}, "op": {"op": "describe", "column": "x"},
        "condition": {"path": "mean", "op": "gt", "threshold": 10}, "interval_s": 300}


def test_extract_and_evaluate():
    r = {"mean": 12.5, "nested": {"p": [1, {"count": 3}]}, "flag": True, "s": "x"}
    assert mon.extract(r, "mean") == 12.5 and mon.extract(r, "nested.p.1.count") == 3.0
    assert mon.extract([{"count": 7}], "0.count") == 7.0
    for bad in ("nope", "nested.p.9", "flag", "s"):
        with pytest.raises(mon.MonitorRejected):
            mon.extract(r, bad)
    assert mon.evaluate(r, mon.Condition(path="mean", op="gt", threshold=10)) == (12.5, True)
    assert mon.evaluate(r, mon.Condition(path="mean", op="lte", threshold=10)) == (12.5, False)


def test_spec_validation():
    m = _m()
    assert m.parse(BASE).interval_s == 300
    for over in ({"interval_s": 5}, {"condition": {"path": "mean", "op": "like", "threshold": 1}}, {"extra": 1},
                 {"channels": [{"type": "slack", "webhook": "https://hooks.example/x"}]}):
        with pytest.raises(mon.MonitorRejected):
            m.parse({**BASE, **over})


def test_channels_accepted_once_a_notifier_is_registered():
    class Fake:
        name = "fake"
        sent: list = []

        async def send(self, event, target):
            self.sent.append((event["monitor_id"], target["to"]))

    notifiers.register(Fake())
    try:
        spec = _m().parse({**BASE, "channels": [{"type": "fake", "to": "#ops"}]})
        assert spec.channels[0].type == "fake"
    finally:
        notifiers._REGISTRY.pop("fake", None)


async def test_dispatch_isolates_failures_and_skips_unknown():
    class Boom:
        name = "boom"

        async def send(self, event, target):
            raise RuntimeError("down")

    class Ok:
        name = "ok"

        async def send(self, event, target):
            return None

    notifiers.register(Boom())
    notifiers.register(Ok())
    try:
        sent = await notifiers.dispatch({"monitor_id": "m"}, [{"type": "boom"}, {"type": "ok"}, {"type": "gone"}])
        assert sent == ["ok"]
    finally:
        notifiers._REGISTRY.pop("boom", None)
        notifiers._REGISTRY.pop("ok", None)


class _FakeStore:
    """In-memory stand-in for MonitorStore (S3)."""

    def __init__(self):
        self.defs: dict[str, dict] = {}
        self.events: list[dict] = []
        self.writes = 0

    async def list_defs(self, prefix):
        return [dict(d) for d in self.defs.values()]

    async def read_def(self, d):
        cur = self.defs.get(d["monitor_id"])
        return dict(cur) if cur else None

    async def put_def(self, d):
        self.writes += 1
        self.defs[d["monitor_id"]] = dict(d)

    async def append_event(self, d, ev):
        self.events.append(ev)


@pytest.fixture
async def redis14():
    import redis.asyncio as aioredis

    r = aioredis.from_url(os.environ.get("TEST_REDIS_URL", "redis://localhost:6379"), db=14, decode_responses=True)
    try:
        await r.ping()
    except Exception:  # noqa: BLE001
        pytest.skip("redis not reachable")
    await r.flushdb()
    yield r
    await r.flushdb()
    await r.aclose()


def _monitors(redis14, store):
    from types import SimpleNamespace

    m = mon.Monitors(CommonSettings(redis_db_auth=14, redis_db_logs=14, monitor_max_per_user=2), resolver=SimpleNamespace(r=redis14), storage=None, runner=None)  # type: ignore[arg-type]
    m.store = store
    m.runs = 0

    async def fake_authorize(d):
        m.runs += 1
        return None, "stub"  # records a skipped event; enough to prove the run happened

    m._authorize = fake_authorize
    return m


def _def(mid="mon_" + "a" * 32, **over):
    from datetime import UTC, datetime, timedelta

    d = {"monitor_id": mid, "company_id": "c", "user_id": "u", "server_id": "s", "server_type": "postgres", "key_id": "k",
         "key_hash": "0" * 64, "status": "active", "interval_s": 60, "name": "n",
         "next_run_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat()}
    d.update(over)
    return d


async def test_scheduler_never_resurrects_a_deleted_or_paused_monitor(redis14):
    # security review #6: the tick's listing is stale by the time a monitor runs
    from datetime import UTC, datetime

    store = _FakeStore()
    m = _monitors(redis14, store)
    stale = _def()
    assert await m.run_once(stale, datetime.now(UTC)) is None and m.runs == 0 and store.writes == 0  # deleted meanwhile
    store.defs[stale["monitor_id"]] = {**stale, "status": "paused"}
    assert await m.run_once(stale, datetime.now(UTC)) is None and m.runs == 0
    assert store.defs[stale["monitor_id"]]["status"] == "paused" and store.writes == 0  # pause not overwritten
    store.defs[stale["monitor_id"]] = dict(stale)
    assert (await m.run_once(stale, datetime.now(UTC)))["status"] == "skipped" and m.runs == 1
    assert not await redis14.exists(f"monitor:lock:{stale['monitor_id']}")  # lock released after the run


async def test_per_user_monitor_cap(redis14):
    store = _FakeStore()
    m = _monitors(redis14, store)
    for i in range(2):
        store.defs[f"mon_{i:032d}"] = _def(f"mon_{i:032d}")
    from mcpcluster_common.context import TenantContext
    from mcpcluster_common.model import TenantSpec

    ctx = TenantContext(company_id="c", user_id="u", key_id="k", server_id="s", server_type="postgres", permissions=frozenset({"read"}),
                        allowed_tools=frozenset({"monitor_create"}), tenant=TenantSpec(mode="schema", schema_name="a"), binding_version=1, key_hash="0" * 64)
    with pytest.raises(mon.MonitorRejected, match="at most 2 monitors"):
        await m.create(ctx, m.parse({**BASE, "interval_s": 60}))


def _ctx():
    from mcpcluster_common.context import TenantContext
    from mcpcluster_common.model import TenantSpec

    return TenantContext(company_id="c", user_id="u", key_id="k", server_id="s", server_type="postgres", permissions=frozenset({"read"}),
                         allowed_tools=frozenset({"monitor_create"}), tenant=TenantSpec(mode="schema", schema_name="a"), binding_version=1, key_hash="0" * 64)


async def test_concurrent_creates_cannot_exceed_the_cap(redis14):
    # security review 2 #2: count -> first run -> save used to race
    import asyncio

    store = _FakeStore()
    m = _monitors(redis14, store)

    async def slow_runner(source, pipeline, op):
        await asyncio.sleep(0.2)  # the first inline run is a real query
        return {"count": 1}, {}

    m.run_compute = slow_runner
    results = await asyncio.gather(*(m.create(_ctx(), m.parse({**BASE, "interval_s": 60, "condition": {"path": "count", "op": "gt", "threshold": 5}})) for _ in range(10)),
                                   return_exceptions=True)
    assert len(store.defs) <= 2
    assert sum(isinstance(r, mon.MonitorRejected) for r in results) >= 8
    assert not await redis14.exists("monitor:create:postgres:c:u")  # lock released


def test_fair_order_interleaves_companies():
    defs = [{"company_id": "big", "monitor_id": f"b{i}"} for i in range(5)] + [{"company_id": "small", "monitor_id": "s0"}]
    order = [d["monitor_id"] for d in mon.fair_order(defs)]
    assert order.index("s0") <= 1  # not stuck behind five of the big tenant's runs
    assert sorted(order) == sorted(d["monitor_id"] for d in defs)


async def test_run_timeout_is_recorded_not_hanging(redis14):
    import asyncio

    store = _FakeStore()
    m = _monitors(redis14, store)
    m.s = CommonSettings(redis_db_auth=14, redis_db_logs=14, monitor_run_timeout_s=0.2)

    async def hang(source, pipeline, op):
        await asyncio.sleep(3600)

    m.run_compute = hang
    ev = await asyncio.wait_for(m._execute(_def(source={"sql": "x"}, op={}, condition={"path": "count", "op": "gt", "threshold": 1}), _ctx()), 2)
    assert ev["status"] == "error" and "timed out" in ev["error"]
