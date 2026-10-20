import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from .api import (
    auth,
    github,
    investigations,
    jira,
    knowledge,
    ownership,
    proposals,
    repos,
    risk,
    settings,
    usage,
    webhooks,
    wiki,
)
from .config import get_settings
from .db import create_all, get_engine
from .telemetry import setup_logging, setup_telemetry


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
        except Exception:
            logging.getLogger("repolens.checkouts").exception("checkout cleanup failed")
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
        except Exception:
            logging.getLogger("repolens.wiki").exception("scheduled source sync failed")
        await asyncio.sleep(minutes * 60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    if s.env != "dev" and s.jwt_secret.startswith("dev-insecure"):
        raise RuntimeError("REPOLENS_JWT_SECRET must be set outside dev")
    if s.env == "dev":  # other environments run `alembic upgrade head` (see Dockerfile entrypoint)
        create_all()
    tasks = [asyncio.create_task(_source_scheduler())]
    if s.checkout_ttl_days > 0:
        tasks.append(asyncio.create_task(_checkout_janitor()))
    yield
    for t in tasks:
        t.cancel()


def create_app() -> FastAPI:
    setup_logging()
    app = FastAPI(title="RepoLens AI", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_settings().cors_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    for r in (
        auth.router,
        repos.router,
        investigations.router,
        knowledge.router,
        ownership.router,
        proposals.router,
        github.router,
        risk.router,
        settings.router,
        usage.router,
        jira.router,
        webhooks.router,
        wiki.router,
    ):
        app.include_router(r)

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    def readyz():
        with get_engine().connect() as c:
            c.execute(text("SELECT 1"))
        return {"status": "ready"}

    setup_telemetry(app)
    return app


app = create_app()
