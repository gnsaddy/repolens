"""OpenTelemetry tracing + Prometheus metrics."""

import logging
import time

from fastapi import FastAPI, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

from .config import get_settings

REQUESTS = Counter("repolens_http_requests_total", "HTTP requests", ["method", "route", "status"])
LATENCY = Histogram("repolens_http_request_seconds", "HTTP latency", ["method", "route"])
INVESTIGATIONS = Counter("repolens_investigations_total", "Investigations", ["status"])
LLM_TOKENS = Counter("repolens_llm_tokens_total", "LLM tokens", ["kind"])


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format='{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}',
    )


def setup_telemetry(app: FastAPI) -> None:
    s = get_settings()
    if s.otlp_endpoint:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider(resource=Resource.create({"service.name": s.service_name}))
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=s.otlp_endpoint)))
        trace.set_tracer_provider(provider)
        FastAPIInstrumentor.instrument_app(app)

    @app.middleware("http")
    async def metrics_mw(request: Request, call_next):
        start = time.time()
        resp = await call_next(request)
        route = request.scope.get("route")
        path = getattr(route, "path", "unmatched")
        REQUESTS.labels(request.method, path, str(resp.status_code)).inc()
        LATENCY.labels(request.method, path).observe(time.time() - start)
        return resp

    @app.get("/metrics", include_in_schema=False)
    def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
