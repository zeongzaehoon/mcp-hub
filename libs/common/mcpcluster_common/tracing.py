"""Optional OpenTelemetry tracing. FastMCP already emits client/server spans and propagates W3C trace context through
MCP `_meta` (gateway -> upstream included); this only installs an SDK + OTLP exporter when OTEL_EXPORTER_OTLP_ENDPOINT
is set. Without it tracing stays a no-op (opentelemetry-api only).

Attribute policy: spans carry method / tool names and service identity only — never SQL text, tool arguments, rows,
or company/user/key ids (tests/test_tracing.py asserts it). Who did what is the access log's job."""

import logging
import os

log = logging.getLogger(__name__)
_done = False


def setup_tracing(service_name: str) -> bool:
    global _done
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "")
    if _done or not endpoint:
        return False
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    provider = TracerProvider(resource=Resource.create({"service.name": service_name, "deployment.environment": os.environ.get("APP_ENV", "")}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    _done = True
    log.info("tracing enabled -> %s", endpoint)
    return True
