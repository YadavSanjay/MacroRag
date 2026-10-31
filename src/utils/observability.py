"""OpenTelemetry tracing hook (ARag review §2.3.6). Optional: if
``opentelemetry`` isn't installed or tracing is disabled, every call is a cheap
no-op, so the pipeline runs unchanged. When enabled it exports OTLP spans to a
collector (e.g. the one in ``infra/``), feeding Grafana/Tempo/LangFuse.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

from .logging import get_logger

log = get_logger(__name__)

_ENABLED = False


def init_observability(settings) -> bool:
    """Wire the global tracer provider + OTLP exporter. Returns True if active."""
    global _ENABLED
    if not settings.otel_enabled:
        return False
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        log.warning("opentelemetry not installed; tracing stays off")
        return False
    provider = TracerProvider(resource=Resource.create(
        {"service.name": settings.service_name}))
    provider.add_span_processor(BatchSpanProcessor(
        OTLPSpanExporter(endpoint=settings.otel_endpoint, insecure=True)))
    trace.set_tracer_provider(provider)
    _ENABLED = True
    log.info("OpenTelemetry tracing -> %s", settings.otel_endpoint)
    return True


@contextmanager
def span(name: str, **attributes: Any):
    """Context-managed span; no-op unless tracing was initialised."""
    if not _ENABLED:
        yield None
        return
    try:
        from opentelemetry import trace
        tracer = trace.get_tracer("macro-rag")
        with tracer.start_as_current_span(name) as sp:
            for k, v in attributes.items():
                if v is not None:
                    sp.set_attribute(k, v)
            yield sp
    except Exception:  # pragma: no cover - never let tracing break a request
        yield None
