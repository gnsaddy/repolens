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


LOG_FORMAT = '{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}'


def setup_logging() -> None:
    """JSON log lines. With REPOLENS_LOG_FILE they go to a size-limited rotating file (at most (backups + 1) x max MB
    on disk) and only warnings stay on standard output; without it everything goes to standard output, where Docker or
    Kubernetes rotate it."""
    from logging.handlers import RotatingFileHandler

    s = get_settings()
    root = logging.getLogger()
    level = getattr(logging, s.log_level.upper(), logging.INFO)
    root.setLevel(level)
    for h in list(root.handlers):
        if getattr(h, "_repolens", False):
            root.removeHandler(h)
    fmt = logging.Formatter(LOG_FORMAT)
    out = logging.StreamHandler()
    out.setFormatter(fmt)
    out._repolens = True  # type: ignore[attr-defined]
    handlers: list[logging.Handler] = [out]
    if s.log_file:
        out.setLevel(logging.WARNING)
        fh = RotatingFileHandler(
            s.log_file, maxBytes=s.log_max_mb * 1024 * 1024, backupCount=s.log_backups, encoding="utf-8"
        )
        fh.setFormatter(fmt)
        fh._repolens = True  # type: ignore[attr-defined]
        handlers.append(fh)
    for h in handlers:
        root.addHandler(h)
    for noisy in ("httpx", "httpcore", "httpx2"):  # their INFO lines carry full request URLs, query strings included
        logging.getLogger(noisy).setLevel(logging.WARNING)
    if s.log_file:  # uvicorn keeps its own handlers: give its error log the same file
        for name in ("uvicorn", "uvicorn.error"):
            lg = logging.getLogger(name)
            lg.addHandler(handlers[-1])


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
