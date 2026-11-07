"""Load baseline against the running compose stack (dev machine numbers, not production capacity).

    cd mcp-gateway && PYTHONPATH=. uv run python ../scripts/loadtest.py [--concurrency 20] [--calls 30]

Creates its own company / user / access / keys (random ids), then measures, per scenario, throughput and latency
percentiles of real MCP tool calls: direct postgres endpoint vs. the gateway (run_select), and compute. Finally
checks that a key limited to N calls/minute is cut off after N. Prints a markdown table."""

import argparse
import asyncio
import statistics
import time
import uuid

import httpx
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from mcp.shared.exceptions import MCPError

from mcp_gateway.server import assign_aliases

BACKEND = "http://localhost:19000/api/v1"
DIRECT = "http://localhost:19000/mcp-postgres"
GATEWAY = "http://localhost:19000/mcp"


def setup():
    with httpx.Client(base_url=BACKEND, timeout=20) as c:
        tok = c.post("/auth/login", json={"email": "master@example.com", "password": "master-dev-password-123"}).json()["access_token"]
        c.headers["Authorization"] = f"Bearer {tok}"
        tag = uuid.uuid4().hex[:6]
        server = c.post("/servers", json={"server_type": "postgres", "name": f"Load PG {tag}"}).json()
        cid = f"load-{tag}"
        c.post("/companies", json={"company_id": cid, "name": "Load"})
        c.post(f"/companies/{cid}/bindings", json={"server_id": server["server_id"], "tenant": {"mode": "schema", "schema_name": "acme"}})
        u = c.post(f"/companies/{cid}/users", json={"email": f"l-{cid}@ex.com", "name": "L", "role": "member"}).json()
        c.post(f"/companies/{cid}/users/{u['user_id']}/access", json={"server_id": server["server_id"], "permissions": ["read"], "allowed_tools": ["run_select", "compute"]})
        key = c.post(f"/companies/{cid}/api-keys", json={"user_id": u["user_id"], "server_ids": [server["server_id"]], "name": "load", "rate_limit_per_minute": 1_000_000}).json()["raw_key"]
        limited = c.post(f"/companies/{cid}/api-keys", json={"user_id": u["user_id"], "server_ids": [server["server_id"]], "name": "limited", "rate_limit_per_minute": 50}).json()["raw_key"]
    alias = assign_aliases([(server["server_id"], "postgres", server["name"])])[server["server_id"]]
    return key, limited, alias


async def worker(url, key, tool, args, calls, lat, errors):
    async with Client(StreamableHttpTransport(url, headers={"Authorization": f"Bearer {key}"})) as cl:
        for _ in range(calls):
            t = time.perf_counter()
            try:
                await cl.call_tool(tool, args)
                lat.append(time.perf_counter() - t)
            except Exception:  # noqa: BLE001
                errors.append(1)


async def scenario(name, url, key, tool, args, conc, calls):
    lat: list[float] = []
    errors: list[int] = []
    t0 = time.perf_counter()
    await asyncio.gather(*(worker(url, key, tool, args, calls, lat, errors) for _ in range(conc)))
    wall = time.perf_counter() - t0
    q = statistics.quantiles(lat, n=100) if len(lat) > 1 else [0] * 99
    return f"| {name} | {conc}×{calls} | {len(lat) / wall:.0f} | {q[49] * 1000:.1f} | {q[94] * 1000:.1f} | {q[98] * 1000:.1f} | {len(errors)} |"


async def rate_limit_check(key, n=60):
    ok = denied = 0
    async with Client(StreamableHttpTransport(DIRECT, headers={"Authorization": f"Bearer {key}"})) as cl:
        for _ in range(n):
            try:
                await cl.call_tool("run_select", {"sql": "select 1 as x"})
                ok += 1
            except MCPError:
                denied += 1
    return ok, denied


async def main(conc, calls):
    key, limited, alias = setup()
    sel = {"sql": "select name from customers order by id"}
    comp = {"source": {"sql": "select id from customers"}, "op": {"op": "describe", "column": "id"}}
    for url in (DIRECT, GATEWAY):  # warm pools, caches and the Rust/JS engines
        await worker(url, key, "run_select" if url == DIRECT else f"{alias}__run_select", sel, 3, [], [])
    rows = [
        await scenario("direct run_select", DIRECT, key, "run_select", sel, conc, calls),
        await scenario("gateway run_select", GATEWAY, key, f"{alias}__run_select", sel, conc, calls),
        await scenario("direct compute(describe)", DIRECT, key, "compute", comp, conc, calls),
    ]
    ok, denied = await rate_limit_check(limited)
    print("| scenario | clients×calls | calls/s | p50 ms | p95 ms | p99 ms | errors |\n|---|---|---|---|---|---|---|")
    print("\n".join(rows))
    print(f"\nrate limit 50/min: {ok} allowed, {denied} denied of 60 (expected 50 / 10)")
    return ok, denied


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--concurrency", type=int, default=20)
    ap.add_argument("--calls", type=int, default=30)
    a = ap.parse_args()
    asyncio.run(main(a.concurrency, a.calls))
