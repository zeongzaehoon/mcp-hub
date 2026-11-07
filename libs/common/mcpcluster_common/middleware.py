"""FastMCP middleware: authenticate every request with the API key, filter list_tools to the key's
allowed_tools, enforce tool permission on call, bind TenantContext for the duration of the call,
and publish an access-log event for every call (ok / denied / error)."""

import logging
import time
from collections.abc import Mapping, Sequence
from typing import Any

import orjson
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_headers, get_http_request
from fastmcp.server.middleware import Middleware, MiddlewareContext
from mcp.shared.exceptions import MCPError

from mcpcluster_common.authz import Decision, Resolver
from mcpcluster_common.context import reset_tenant, set_tenant
from mcpcluster_common import metrics
from mcpcluster_common.logs import AccessLogPublisher
from mcpcluster_common.model import AccessEvent
from mcpcluster_common.ratelimit import RateLimiter
from mcpcluster_common.settings import CommonSettings

log = logging.getLogger(__name__)
UNAUTHORIZED = -32001  # JSON-RPC implementation-defined error range; surfaced verbatim to the client
RATE_LIMITED = -32002


def unauthorized(reason: str) -> MCPError:
    return MCPError(code=UNAUTHORIZED, message=f"unauthorized: {reason}")


def extract_api_key(headers: Mapping[str, str]) -> str | None:
    auth = headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth.split(" ", 1)[1].strip() or None
    return headers.get("x-api-key") or None


def summarize_args(args: Any, limit: int = 500) -> str:
    try:
        s = orjson.dumps(args, default=str).decode()
    except Exception:  # noqa: BLE001
        s = repr(args)
    return s if len(s) <= limit else s[:limit] + "…"


def requested_server() -> str | None:
    """Target server record from the MCP URL (`/mcp?server=<server_id>`). Every MCP client lets the
    user set the URL, unlike custom headers. Not a credential — authorization still decides."""
    try:
        v = get_http_request().query_params.get("server")
    except Exception:  # noqa: BLE001 — no HTTP request (stdio)
        return None
    return (v.strip() or None) if v else None


class TenantAuthMiddleware(Middleware):
    def __init__(self, settings: CommonSettings, tool_permissions: dict[str, str], resolver: Resolver | None = None, publisher: AccessLogPublisher | None = None, limiter: RateLimiter | None = None):
        self.s = settings
        self.tool_permissions = tool_permissions
        self.resolver = resolver or Resolver(settings)
        self.publisher = publisher or AccessLogPublisher(settings)
        self.limiter = limiter or RateLimiter(settings)
        metrics.register_tools(tool_permissions)
        if settings.tenant_secret_key_enc:  # fail at startup, not on the first request, if KMS cannot unwrap the key
            from mcpcluster_common.secrets import fernet

            fernet(settings)

    async def _decide(self) -> Decision:
        try:
            headers = get_http_headers(include={"authorization", "x-api-key"})
        except Exception:  # noqa: BLE001 — non-HTTP transport (stdio) has no headers => deny
            headers = {}
        return await self.resolver.resolve(extract_api_key(headers), requested_server())

    async def on_list_tools(self, context: MiddlewareContext, call_next) -> Sequence[Any]:
        d = await self._decide()
        if not d.allowed:
            raise unauthorized(d.reason)
        tools = await call_next(context)
        allowed = d.ctx.allowed_tools
        perms = d.ctx.permissions
        return [t for t in tools if t.name in allowed and self.tool_permissions.get(t.name) in perms]

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        name = context.message.name
        args = context.message.arguments or {}
        started = time.perf_counter()
        d = await self._decide()
        if not d.allowed:
            await self._log(d, name, args, "denied", d.reason, started)
            raise unauthorized(d.reason)
        ctx = d.ctx
        required = self.tool_permissions.get(name)
        if name not in ctx.allowed_tools or required is None or required not in ctx.permissions:
            reason = f"tool '{name}' not permitted for this key"
            await self._log(d, name, args, "denied", reason, started)
            raise ToolError(reason)
        rd = await self.limiter.check(ctx.key_id, d.key.rate_limit_per_minute if d.key else None)
        if not rd.allowed:
            reason = f"rate limited: {rd.limit} calls/minute exceeded; retry in {rd.retry_after_s}s"
            await self._log(d, name, args, "denied", reason, started)
            raise MCPError(code=RATE_LIMITED, message=reason, data={"limit": rd.limit, "retry_after_s": rd.retry_after_s})
        token = set_tenant(ctx)
        try:
            result = await call_next(context)
        except Exception as e:
            await self._log(d, name, args, "error", str(e)[:300], started)
            raise
        finally:
            reset_tenant(token)
        await self._log(d, name, args, "ok", None, started)
        return result

    async def _log(self, d: Decision, tool: str, args: Any, status: str, error: str | None, started: float) -> None:
        k = d.key
        ev = AccessEvent(
            key_id=k.key_id if k else "unknown", company_id=k.company_id if k else "unknown",
            user_id=k.user_id if k else "unknown", server_id=(d.ctx.server_id if d.ctx else d.server_id) or self.s.server_id or "unknown",
            server_type=d.ctx.server_type if d.ctx else self.s.server_type, tool=tool, args_summary=summarize_args(args),
            status=status, error=error, duration_ms=int((time.perf_counter() - started) * 1000),
        )
        metrics.observe_call(ev.server_type, tool, status, time.perf_counter() - started)
        await self.publisher.publish(ev)
