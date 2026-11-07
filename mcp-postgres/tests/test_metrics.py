"""Metrics: low-cardinality labels, no tenant identifiers, optional bearer token on /metrics."""

import httpx
import pytest
from fastmcp import FastMCP

from mcpcluster_common import metrics


def test_unknown_tool_names_collapse_to_one_label():
    metrics.register_tools({"run_select"})
    assert metrics.tool_label("run_select") == "run_select"
    assert metrics.tool_label("drop_all_tables_" + "x" * 50) == "other"  # client-typed names cannot explode label sets


async def _get(app_mcp, headers=None):
    app = app_mcp.http_app(path="/mcp")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        return await c.get("/metrics", headers=headers or {})


async def test_metrics_endpoint_and_contents(monkeypatch):
    monkeypatch.delenv("METRICS_TOKEN", raising=False)
    m = FastMCP("t")
    metrics.install_metrics(m)
    metrics.observe_call("postgres", "run_select", "ok", 0.02)
    r = await _get(m)
    assert r.status_code == 200 and 'mcp_tool_calls_total{server_type="postgres",status="ok",tool="run_select"}' in r.text
    for forbidden in ("company", "user_id", "key_id", "acme"):
        assert forbidden not in r.text


async def test_metrics_token(monkeypatch):
    monkeypatch.setenv("METRICS_TOKEN", "t0ken-dummy")
    m = FastMCP("t2")
    metrics.install_metrics(m)
    assert (await _get(m)).status_code == 401
    assert (await _get(m, {"Authorization": "Bearer wrong"})).status_code == 401
    assert (await _get(m, {"Authorization": "Bearer t0ken-dummy"})).status_code == 200


def test_open_metrics_refused_outside_dev(monkeypatch):
    # security review 2 #4
    monkeypatch.delenv("METRICS_TOKEN", raising=False)
    monkeypatch.setenv("APP_ENV", "prod")
    with pytest.raises(RuntimeError, match="METRICS_TOKEN"):
        metrics.install_metrics(FastMCP("t3"))
    monkeypatch.delenv("APP_ENV")  # unset is not dev either
    with pytest.raises(RuntimeError):
        metrics.install_metrics(FastMCP("t4"))


async def test_healthz_is_open_and_says_nothing(monkeypatch):
    # the backend health checker (MCP_INTERNAL_*) hits this; no token, no tenant data, no version/host details
    monkeypatch.setenv("METRICS_TOKEN", "t0ken-dummy")
    m = FastMCP("t5")
    metrics.install_metrics(m)
    app = m.http_app(path="/mcp")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/healthz")
    assert r.status_code == 200 and r.json() == {"ok": True}
