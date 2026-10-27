"""OpenTelemetry tracing + Prometheus metrics."""

import logging
import threading
import time
from dataclasses import dataclass

from fastapi import FastAPI, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

from .config import get_settings

REQUESTS = Counter("repolens_http_requests_total", "HTTP requests", ["method", "route", "status"])
LATENCY = Histogram("repolens_http_request_seconds", "HTTP latency", ["method", "route"])
INVESTIGATIONS = Counter("repolens_investigations_total", "Investigations", ["status"])
LLM_TOKENS = Counter("repolens_llm_tokens_total", "LLM tokens", ["kind"])
# The work RepoLens does for itself, so a stuck index, a dead background loop or a quality drop shows up on a dashboard.
INDEX_RUNS = Counter("repolens_index_runs_total", "Finished index runs", ["status"])
INDEX_SECONDS = Histogram(
    "repolens_index_seconds", "How long an index run took", buckets=(5, 15, 30, 60, 120, 300, 600, 1200, 3600)
)
INDEX_RETRIES = Counter("repolens_index_retries_total", "Failed indexes scheduled to be tried again")
ANSWERS = Counter("repolens_answers_total", "Questions answered", ["mode", "cached"])
ANSWER_SECONDS = Histogram(
    "repolens_answer_seconds", "How long a question took to answer", ["mode"], buckets=(0.1, 0.5, 1, 2, 5, 10, 30, 60)
)
QUALITY_RUNS = Counter("repolens_quality_runs_total", "Saved-question runs", ["trigger", "status"])
QUALITY_PASS = Gauge("repolens_quality_pass_ratio", "Share of saved questions that passed the latest run", ["org"])
JOB_OK = Gauge("repolens_job_last_success_timestamp_seconds", "When a background loop last finished a pass", ["job"])
JOB_FAILURES = Counter("repolens_job_failures_total", "Background loop passes that raised", ["job"])
REPOS = Gauge("repolens_repositories", "Repositories by status", ["status"])
REPO_STATUSES = ("pending", "indexing", "ready", "error")


@dataclass
class Job:
    name: str
    every_s: float  # how often a pass should finish
    first_s: float  # wait before the first pass
    started: float
    last_ok: float | None = None
    last_error: str | None = None
    failed_since_ok: bool = False
    failures: int = 0


_jobs: dict[str, Job] = {}
_jobs_lock = threading.Lock()


def register_job(name: str, every_s: float, first_s: float = 0.0) -> None:
    """Declare a background loop, so a loop that never runs shows as late instead of not showing at all."""
    with _jobs_lock:
        _jobs[name] = Job(name, every_s, first_s, time.time())


def job_ok(name: str) -> None:
    with _jobs_lock:
        j = _jobs.get(name)
        if j is not None:
            j.last_ok, j.failed_since_ok = time.time(), False
    JOB_OK.labels(name).set(time.time())


def job_failed(name: str, error: BaseException) -> None:
    with _jobs_lock:
        j = _jobs.get(name)
        if j is not None:
            j.failures += 1
            j.failed_since_ok = True
            j.last_error = (str(error).splitlines() or [type(error).__name__])[0][:200]
    JOB_FAILURES.labels(name).inc()


def job_report(now: float | None = None) -> list[dict]:
    """Each loop's state: ok, waiting (still within its first wait), failing (the last pass raised), late (overdue)."""
    now = time.time() if now is None else now
    out = []
    with _jobs_lock:
        for j in _jobs.values():
            grace = 2 * j.every_s + 60
            if j.failed_since_ok:
                state = "failing"
            elif j.last_ok is not None:
                state = "ok" if now - j.last_ok <= grace else "late"
            else:
                state = "waiting" if now - j.started <= j.first_s + grace else "late"
            out.append(
                {
                    "name": j.name,
                    "state": state,
                    "every_s": j.every_s,
                    "last_ok": j.last_ok,
                    "failures": j.failures,
                    "last_error": j.last_error,
                }
            )
    return out


def refresh_repo_gauges() -> None:
    """Counts that live in the database are read when they are scraped, not kept up to date by every writer."""
    from sqlalchemy import func, select

    from .db import session_factory
    from .models import Repository

    try:
        with session_factory()() as db:
            rows = db.execute(select(Repository.status, func.count()).group_by(Repository.status)).all()
        counts = {status: n for status, n in rows}
        for s in REPO_STATUSES:
            REPOS.labels(s).set(counts.get(s, 0))
    except Exception:  # a scrape must still answer when the database is down; /readyz reports that
        logging.getLogger("repolens.metrics").warning("could not count repositories for /metrics", exc_info=True)


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
        refresh_repo_gauges()
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
