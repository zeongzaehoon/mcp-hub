# Performance baseline

`scripts/loadtest.py` against the dev compose stack on one laptop (2026-09-24). These are **relative** numbers to
catch regressions and to compare paths, not production capacity. Re-run after changes to the request path.

```bash
cd mcp-gateway && PYTHONPATH=. uv run python ../scripts/loadtest.py --concurrency 20 --calls 30
```

| scenario | clients×calls | calls/s | p50 ms | p95 ms | p99 ms | errors |
|---|---|---|---|---|---|---|
| direct run_select | 20×30 | 353 | 42.6 | 105.8 | 171.3 | 0 |
| gateway run_select | 20×30 | 154 | 112.6 | 251.5 | 274.2 | 0 |
| direct compute(describe) | 20×30 | 380 | 46.9 | 94.7 | 107.2 | 0 |

Rate limit (key limited to 50/min): 50 allowed, 10 denied of 60 — as configured.

After T27 (upstream session pool, same run conditions):

| scenario | clients×calls | calls/s | p50 ms | p95 ms | p99 ms | errors |
|---|---|---|---|---|---|---|
| direct run_select | 20×30 | 400 | 43.0 | 110.0 | 114.5 | 0 |
| gateway run_select | 20×30 | 323 | 54.7 | 100.6 | 149.5 | 0 |
| direct compute(describe) | 20×30 | 353 | 49.5 | 88.2 | 140.1 | 0 |

Gateway overhead p50: +70 ms → **+12 ms**; gateway throughput 154 → 323 calls/s.

## Observations
- Every call does full authorization (one Redis pipeline), tenant-guarded SQL and an access-log publish; that is the
  direct-path cost.
- The gateway originally added ~70 ms p50 because `ProxyTool` opened a new upstream MCP session per call
  (initialize + call round trips). The upstream session pool (T27) keeps sessions open per (key hash, server,
  permission versions), closes them after 60 s idle, holds at most 1000, and replaces dead ones.
