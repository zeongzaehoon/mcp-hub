"""Traces: context propagates from a caller span into the MCP server span, and no span attribute carries tool
arguments (SQL) or tenant data."""

import pytest
from fastmcp import Client, FastMCP
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from mcpcluster_common import tracing

SECRET_SQL = "select * from acme_secret_table where email = 'alice@acme.example'"


@pytest.fixture(scope="module")
def exporter():
    exp = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exp))
    trace.set_tracer_provider(provider)
    return exp


async def test_parent_context_reaches_the_server_span_and_args_are_not_recorded(exporter):
    srv = FastMCP("trace-test")

    @srv.tool
    async def run_select(sql: str) -> dict:
        return {"rows": [], "company": "acme-tenant"}

    tracer = trace.get_tracer("test")
    with tracer.start_as_current_span("caller") as parent:
        async with Client(srv) as cl:
            await cl.call_tool("run_select", {"sql": SECRET_SQL})
    spans = exporter.get_finished_spans()
    trace_ids = {s.context.trace_id for s in spans}
    assert parent.get_span_context().trace_id in trace_ids
    server = [s for s in spans if s.kind == trace.SpanKind.SERVER]
    assert server and all(s.context.trace_id == parent.get_span_context().trace_id for s in server)  # one trace end to end
    for s in spans:
        for v in (s.attributes or {}).values():
            text = str(v)
            assert "acme_secret_table" not in text and "alice@acme.example" not in text and "acme-tenant" not in text


def test_setup_is_a_noop_without_an_endpoint(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    assert tracing.setup_tracing("x") is False
