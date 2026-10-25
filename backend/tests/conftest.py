import os

import pytest
from fastapi.testclient import TestClient

from repolens import config, db
from repolens.demo import build_demo_repo


@pytest.fixture(autouse=True)
def settings(monkeypatch, tmp_path):
    monkeypatch.setenv("REPOLENS_ENV", "dev")
    monkeypatch.setenv("REPOLENS_RUN_JOBS_INLINE", "true")
    monkeypatch.setenv("REPOLENS_JWT_SECRET", "test-secret-test-secret-test-secret")
    monkeypatch.setenv("REPOLENS_LLM_PROVIDER", "heuristic")
    monkeypatch.setenv("REPOLENS_REPOS_DIR", str(tmp_path / "clones"))
    config.get_settings.cache_clear()
    from repolens.services import ratelimit

    ratelimit.reset()
    yield
    config.get_settings.cache_clear()


@pytest.fixture
def session():
    # Set REPOLENS_TEST_DATABASE_URL to run the suite against PostgreSQL + pgvector.
    url = os.environ.get("REPOLENS_TEST_DATABASE_URL", "sqlite://")
    db.init_engine(url)
    db.create_all()  # idempotent
    if url.startswith("postgresql"):  # fast reset: truncate instead of drop/create (HNSW index builds are slow)
        names = ", ".join(t.name for t in db.Base.metadata.sorted_tables)
        with db.get_engine().begin() as conn:
            conn.exec_driver_sql(f"TRUNCATE {names} RESTART IDENTITY CASCADE")
    s = db.session_factory()()
    yield s
    s.close()


@pytest.fixture
def client(session):
    from repolens.main import create_app

    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def demo_repo(tmp_path):
    path = tmp_path / "user-service"
    bad_sha = build_demo_repo(path)
    return path, bad_sha


def register(client, email="admin@acme.io", org="Acme"):
    r = client.post("/api/auth/register", json={"email": email, "password": "correct-horse-1", "organization": org})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
def auth(client):
    return register(client)
