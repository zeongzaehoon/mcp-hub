"""Prometheus metrics for MCP servers and the gateway.

Labels are low-cardinality and carry no tenant identifiers: server type, tool name and outcome only. Who called
what stays in the access log (per company, access-controlled), never in metrics — a metrics scrape must not
reveal which customers are active. `/metrics` requires METRICS_TOKEN (`Authorization: Bearer <token>`, constant-time compare) outside
dev; startup fails without it. Only APP_ENV=dev may serve it open."""

import hmac
import os

from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Histogram, generate_latest

REGISTRY = CollectorRegistry(auto_describe=True)

TOOL_CALLS = Counter("mcp_tool_calls_total", "MCP tool calls by outcome", ["server_type", "tool", "status"], registry=REGISTRY)
TOOL_SECONDS = Histogram("mcp_tool_duration_seconds", "MCP tool call latency", ["server_type", "tool"], registry=REGISTRY,
                         buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30))
MONITOR_RUNS = Counter("mcp_monitor_runs_total", "Scheduled monitor runs by outcome", ["server_type", "status"], registry=REGISTRY)
GATEWAY_LIST_CACHE = Counter("mcp_gateway_list_cache_total", "Gateway upstream tool-list cache lookups", ["result"], registry=REGISTRY)

# tool names come from a fixed catalog; anything else (unknown/denied names typed by a client) collapses to one label
_KNOWN_TOOLS: set[str] = set()


def register_tools(names) -> None:
    _KNOWN_TOOLS.update(names)


def tool_label(name: str) -> str:
    return name if name in _KNOWN_TOOLS else "other"


def observe_call(server_type: str, tool: str, status: str, seconds: float) -> None:
    t = tool_label(tool)
    TOOL_CALLS.labels(server_type or "unknown", t, status).inc()
    TOOL_SECONDS.labels(server_type or "unknown", t).observe(max(0.0, seconds))


def install_metrics(mcp) -> None:
    from starlette.requests import Request
    from starlette.responses import JSONResponse, PlainTextResponse, Response

    token = os.environ.get("METRICS_TOKEN", "")
    if not token and os.environ.get("APP_ENV", "") != "dev":
        # outside dev the endpoint shares the public MCP port: never serve it unauthenticated
        raise RuntimeError("METRICS_TOKEN must be set outside dev (APP_ENV=dev allows an open /metrics)")

    @mcp.custom_route("/healthz", methods=["GET"], include_in_schema=False)
    async def healthz(request: Request) -> Response:
        # liveness for the backend's health checker (internal URL, see backend MCP_INTERNAL_*): no tenant data, no auth
        return JSONResponse({"ok": True})

    @mcp.custom_route("/metrics", methods=["GET"], include_in_schema=False)
    async def metrics(request: Request) -> Response:
        if token:
            auth = request.headers.get("authorization", "")
            if not hmac.compare_digest(auth.encode(), f"Bearer {token}".encode()):
                return PlainTextResponse("unauthorized", status_code=401)
        return Response(generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)
