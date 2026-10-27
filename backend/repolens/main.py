import asyncio
import hmac
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from . import VERSION_LABEL
from .api import (
    account,
    audit,
    auth,
    bitbucket,
    chat,
    feedback,
    fixes,
    github,
    golden,
    groups,
    investigations,
    jira,
    knowledge,
    mcp,
    measure,
    metrics,
    ownership,
    proposals,
    repos,
    risk,
    scim,
    settings,
    skills,
    sso,
    usage,
    webhooks,
    wiki,
)
from .config import get_settings
from .db import create_all, get_engine
from .middleware import EdgeGuard
from .telemetry import job_failed, job_ok, register_job, setup_logging, setup_telemetry


async def _checkout_janitor(hours: float = 6.0) -> None:
    """Periodically delete clones nobody has used for `checkout_ttl_days` (indexes are kept)."""
    from sqlalchemy import select

    from .db import session_factory
    from .models import Organization
    from .services import checkouts

    def sweep() -> None:
        ttl = get_settings().checkout_ttl_days
        with session_factory()() as db:
            for org_id in db.scalars(select(Organization.id)):
                checkouts.cleanup(db, org_id, ttl)

    await asyncio.sleep(600)
    while True:
        try:
            await asyncio.to_thread(sweep)
            job_ok("checkout-cleanup")
        except Exception as e:
            logging.getLogger("repolens.checkouts").exception("checkout cleanup failed")
            job_failed("checkout-cleanup", e)
        await asyncio.sleep(hours * 3600)


async def _source_scheduler(minutes: float = 5.0) -> None:
    """Re-sync connected sources (Confluence, Jira, Slack, ...) whose schedule says they are due."""
    from .db import session_factory
    from .models import Repository, WikiSource
    from .services import wiki_import as wi

    def tick() -> None:
        with session_factory()() as db:
            for sid in wi.due_sources(db):
                src = db.get(WikiSource, sid)
                repo = db.get(Repository, src.repo_id) if src else None
                if src is None or repo is None:
                    continue
                src.last_status, src.last_error = "running", None
                repo.status = "indexing"
                db.commit()
                wi.schedule_source_sync(sid)

    await asyncio.sleep(60)  # let the app finish starting before the first check
    while True:
        try:
            await asyncio.to_thread(tick)
            job_ok("source-sync")
        except Exception as e:
            logging.getLogger("repolens.wiki").exception("scheduled source sync failed")
            job_failed("source-sync", e)
        await asyncio.sleep(minutes * 60)


async def _system_sampler() -> None:
    """Sample CPU, memory, network and request rate every few seconds for the Insights page."""
    from .services import sysmetrics

    while True:
        try:
            await asyncio.to_thread(sysmetrics.sample)
            job_ok("system-sampler")
        except Exception as e:
            logging.getLogger("repolens.system").exception("system sample failed")
            job_failed("system-sampler", e)
        await asyncio.sleep(sysmetrics.INTERVAL)


async def _retention_sweeper(hours: float = 24.0) -> None:
    """Once a day remove records that have outlived their retention (see services/retention.py)."""
    from .db import session_factory
    from .services import retention

    def run() -> None:
        with session_factory()() as db:
            removed = {k: v for k, v in retention.sweep(db).items() if v}
            if removed:
                logging.getLogger("repolens.retention").info("retention sweep removed %s", removed)

    await asyncio.sleep(600)  # not during start-up
    while True:
        try:
            await asyncio.to_thread(run)
            job_ok("retention")
        except Exception as e:
            logging.getLogger("repolens.retention").exception("retention sweep failed")
            job_failed("retention", e)
        await asyncio.sleep(hours * 3600)


async def _auto_reindexer(hours: float) -> None:
    """Once every `hours`, index again each repository that changed since its last index or whose last index failed."""
    from .api import repos
    from .db import session_factory
    from .services import index_jobs

    def run() -> None:
        with session_factory()() as db:
            index_jobs.daily_pass(db, repos.schedule_index)

    await asyncio.sleep(900)  # not while the server is still starting and people are signing in
    while True:
        try:
            await asyncio.to_thread(run)
            job_ok("daily-reindex")
        except Exception as e:
            logging.getLogger("repolens.indexer").exception("daily re-index check failed")
            job_failed("daily-reindex", e)
        await asyncio.sleep(hours * 3600)


async def _investigation_reaper(seconds: float = 60.0) -> None:
    """Fail investigations left 'running' by a restart or a hung call, so they never spin forever."""
    from .db import session_factory
    from .services import investigations as inv_svc

    def sweep(restart: bool) -> None:
        with session_factory()() as db:
            n = inv_svc.reap_stuck(db, restart=restart)
            if n:
                logging.getLogger("repolens.investigations").warning("stopped %d stuck investigation(s)", n)

    first = True
    await asyncio.sleep(5)  # let the app finish starting
    while True:
        try:
            await asyncio.to_thread(sweep, first)
            job_ok("investigation-reaper")
        except Exception as e:
            logging.getLogger("repolens.investigations").exception("investigation sweep failed")
            job_failed("investigation-reaper", e)
        first = False
        await asyncio.sleep(seconds)


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    if s.env != "dev" and (s.jwt_secret.startswith("dev-insecure") or len(s.jwt_secret) < 32):
        raise RuntimeError("REPOLENS_JWT_SECRET must be set outside dev, and be at least 32 characters")
    if s.env == "dev":  # other environments run `alembic upgrade head` (see Dockerfile entrypoint)
        create_all()
    from .services import sysmetrics

    register_job("source-sync", 300, 60)
    register_job("investigation-reaper", 60, 5)
    register_job("system-sampler", sysmetrics.INTERVAL)
    register_job("retention", 24 * 3600, 600)
    tasks = [
        asyncio.create_task(_source_scheduler()),
        asyncio.create_task(_investigation_reaper()),
        asyncio.create_task(_system_sampler()),
        asyncio.create_task(_retention_sweeper()),  # was written but never started, so the daily clean-up never ran
    ]
    if s.auto_reindex_hours > 0 and not s.run_jobs_inline:
        register_job("daily-reindex", s.auto_reindex_hours * 3600, 900)
        tasks.append(asyncio.create_task(_auto_reindexer(s.auto_reindex_hours)))
    if s.checkout_ttl_days > 0:
        register_job("checkout-cleanup", 6 * 3600, 600)
        tasks.append(asyncio.create_task(_checkout_janitor()))
    yield
    for t in tasks:
        t.cancel()


def create_app() -> FastAPI:
    setup_logging()
    prod = get_settings().env != "dev"  # the API schema and interactive docs are for development only
    app = FastAPI(
        title="RepoLens AI",
        version=VERSION_LABEL,
        lifespan=lifespan,
        docs_url=None if prod else "/docs",
        redoc_url=None if prod else "/redoc",
        openapi_url=None if prod else "/openapi.json",
    )
    app.add_middleware(EdgeGuard)  # inside CORS, so its 413/429 answers still carry CORS headers
    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_settings().cors_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    for r in (
        account.router,
        audit.router,
        auth.router,
        repos.router,
        investigations.router,
        knowledge.router,
        ownership.router,
        proposals.router,
        sso.public,
        sso.admin,
        scim.router,
        scim.admin,
        github.router,
        bitbucket.router,
        risk.router,
        settings.router,
        usage.router,
        metrics.router,
        feedback.router,
        jira.router,
        mcp.router,
        fixes.router,
        chat.router,
        golden.router,
        groups.router,
        skills.router,
        measure.router,
        webhooks.router,
        wiki.router,
    ):
        app.include_router(r)

    @app.middleware("http")
    async def protect_metrics(request, call_next):  # optional bearer token for the Prometheus endpoint
        token = get_settings().metrics_token
        if token and request.url.path == "/metrics":
            sent = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
            if not hmac.compare_digest(sent, token):
                return JSONResponse({"detail": "Unauthorized"}, status_code=401)
        return await call_next(request)

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        return {"status": "ok", "version": VERSION_LABEL}

    @app.get("/readyz", include_in_schema=False)
    def readyz():
        with get_engine().connect() as c:
            c.execute(text("SELECT 1"))
        return {"status": "ready"}

    setup_telemetry(app)
    return app


app = create_app()
