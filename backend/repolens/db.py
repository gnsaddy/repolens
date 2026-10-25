from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from .config import get_settings


class Base(DeclarativeBase):
    pass


_engine = None
_SessionLocal: sessionmaker | None = None


def init_engine(url: str | None = None):
    global _engine, _SessionLocal
    url = url or get_settings().database_url
    kwargs: dict = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs = {"connect_args": {"check_same_thread": False}}
        if ":memory:" in url or url == "sqlite://":
            kwargs["poolclass"] = StaticPool
    _engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):

        @event.listens_for(_engine, "connect")
        def _fk(dbapi_conn, _):  # pragma: no cover
            dbapi_conn.execute("PRAGMA foreign_keys=ON")

    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def get_engine():
    if _engine is None:
        init_engine()
    return _engine


def session_factory() -> sessionmaker:
    if _SessionLocal is None:
        init_engine()
    assert _SessionLocal is not None
    return _SessionLocal


def get_db() -> Iterator[Session]:
    db = session_factory()()
    try:
        yield db
    finally:
        db.close()


def create_all() -> None:
    from . import models  # noqa: F401

    engine = get_engine()
    if engine.dialect.name == "postgresql":
        with engine.begin() as conn:
            conn.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector")
    Base.metadata.create_all(engine)
    _add_missing_columns(engine)
    if engine.dialect.name == "postgresql":
        with engine.begin() as conn:
            conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_code_chunks_fts ON code_chunks "
                "USING gin (to_tsvector('simple', content))"
            )
            conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_code_chunks_vec ON code_chunks USING hnsw (embedding vector_cosine_ops)"
            )


def _add_missing_columns(engine) -> None:  # type: ignore[no-untyped-def]
    """Dev convenience: `create_all` never alters existing tables, so add nullable columns added by newer code."""
    from sqlalchemy import inspect

    insp = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in have or col.primary_key:
                    continue
                ddl = col.type.compile(dialect=engine.dialect)
                default = ""
                if not col.nullable:
                    d = getattr(col.default, "arg", None)
                    if callable(d):  # list / dict factories for JSON columns: an empty one is the right start value
                        try:
                            d = d(None) if d.__code__.co_argcount else d()
                        except Exception:  # noqa: BLE001
                            d = None
                    if isinstance(d, (list, dict)):
                        default = " DEFAULT '[]'" if isinstance(d, list) else " DEFAULT '{}'"
                    elif d is None:
                        continue  # cannot add a NOT NULL column without a default; use alembic
                    else:
                        default = f" DEFAULT {d!r}" if isinstance(d, (int, float, str)) else ""
                conn.exec_driver_sql(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {ddl}{default}')
