from datetime import timedelta

from repolens import config
from repolens.devtools import delete_workspace
from repolens.models import (
    AnswerMemory,
    AuditEvent,
    AuthToken,
    FeedbackEvent,
    Organization,
    Repository,
    User,
    UserSession,
    _now,
)
from tests.conftest import register
from tests.test_api import add_repo


def org_of(client, h):
    return client.get("/api/auth/me", headers=h).json()["org_id"]


def test_the_daily_sweep_removes_only_records_past_their_retention(client, auth, session, monkeypatch):
    org = org_of(client, auth)
    uid = client.get("/api/auth/me", headers=auth).json()["id"]
    old, fresh = _now() - timedelta(days=800), _now() - timedelta(days=1)
    session.add_all(
        [
            FeedbackEvent(org_id=org, user_id=uid, kind="answer", rating="up", created_at=old),
            FeedbackEvent(org_id=org, user_id=uid, kind="answer", rating="down", created_at=fresh),
            AuditEvent(org_id=org, actor_id=uid, action="old.event", created_at=old),
            AuditEvent(org_id=org, actor_id=uid, action="new.event", created_at=fresh),
            AnswerMemory(
                org_id=org,
                kind="ask",
                scope_key="k",
                query="old",
                answer={},
                source_state={},
                created_at=_now() - timedelta(hours=100),
            ),
            AnswerMemory(
                org_id=org, kind="ask", scope_key="k", query="new", answer={}, source_state={}, created_at=fresh
            ),
            UserSession(user_id=uid, org_id=org, revoked_at=_now() - timedelta(days=40)),
            AuthToken(
                org_id=org, user_id=uid, kind="reset", token_hash="a" * 64, expires_at=_now() - timedelta(days=40)
            ),
        ]
    )
    session.commit()
    removed = client.post("/api/settings/retention/run", headers=auth).json()["removed"]
    assert (
        removed["answer_memory"] == 1 and removed["audit"] == 1 and removed["sessions"] == 1 and removed["links"] == 1
    )
    assert removed["feedback"] == 1 and session.query(FeedbackEvent).count() == 1  # only the 800-day-old one
    assert session.query(AnswerMemory).count() == 1 and {
        e.action for e in session.query(AuditEvent).filter(AuditEvent.action.like("%.event"))
    } == {"new.event"}
    assert session.query(UserSession).filter_by(revoked_at=None).count() >= 1  # the live session is untouched


def test_a_retention_of_zero_keeps_records_forever_and_the_policy_is_visible(client, auth, session, monkeypatch):
    org = org_of(client, auth)
    uid = client.get("/api/auth/me", headers=auth).json()["id"]
    session.add(AuditEvent(org_id=org, actor_id=uid, action="ancient", created_at=_now() - timedelta(days=5000)))
    session.commit()
    monkeypatch.setenv("REPOLENS_RETENTION_AUDIT_DAYS", "0")
    config.get_settings.cache_clear()
    client.post("/api/settings/retention/run", headers=auth)
    assert session.query(AuditEvent).filter_by(action="ancient").count() == 1
    assert client.get("/api/settings/retention", headers=auth).json()["audit_days"] == 0


def test_the_sweep_is_admin_only_and_limited_to_the_workspace(client, auth, session):
    other = register(client, "r@other.io", "R")
    session.add(
        AuditEvent(org_id=org_of(client, other), actor_id="x", action="theirs", created_at=_now() - timedelta(days=900))
    )
    session.commit()
    client.post(
        "/api/auth/users", headers=auth, json={"email": "m@acme.io", "password": "member-pass-1", "role": "member"}
    )
    m = {
        "Authorization": "Bearer "
        + client.post("/api/auth/login", json={"email": "m@acme.io", "password": "member-pass-1"}).json()[
            "access_token"
        ]
    }
    assert client.post("/api/settings/retention/run", headers=m).status_code == 403
    client.post("/api/settings/retention/run", headers=auth)
    assert (
        session.query(AuditEvent).filter_by(action="theirs").count() == 1
    )  # another workspace's records are not touched


def test_deleting_a_workspace_removes_everything_of_it_and_nothing_else(client, auth, demo_repo, session, monkeypatch):
    add_repo(client, auth, demo_repo[0])
    client.post("/api/knowledge/collections", headers=auth, json={"name": "docs"})
    other = register(client, "keep@other.io", "Keep")
    add_repo(client, other, demo_repo[0])
    keep_repos = session.query(Repository).filter_by(org_id=org_of(client, other)).count()
    monkeypatch.setattr(delete_workspace.dbm, "init_engine", lambda *a, **k: None)
    counts = delete_workspace.run("admin@acme.io", yes=False)  # a dry run shows what is there and changes nothing
    assert counts["repositories"] == 2 and session.query(User).filter_by(email="admin@acme.io").count() == 1
    delete_workspace.run("admin@acme.io", yes=True)
    session.expire_all()
    assert session.query(User).filter_by(email="admin@acme.io").count() == 0
    assert session.query(Organization).filter_by(name="Acme").count() == 0
    assert (
        session.query(Repository).filter_by(org_id=org_of(client, other)).count() == keep_repos
    )  # the other workspace is intact
    assert client.get("/api/repositories", headers=other).status_code == 200


def test_a_dev_database_from_before_a_json_column_existed_is_upgraded_on_start(tmp_path):
    """create_all never alters tables; the dev helper adds missing columns, including NOT NULL JSON lists."""
    import sqlalchemy as sa

    from repolens import db as dbm

    eng = sa.create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with eng.begin() as c:
        c.exec_driver_sql(
            "CREATE TABLE users (id VARCHAR(32) PRIMARY KEY, org_id VARCHAR(32), email VARCHAR(320), name VARCHAR(200), password_hash VARCHAR(200), role VARCHAR(20), created_at DATETIME, active BOOLEAN, auth_source VARCHAR(10), external_id VARCHAR(200))"
        )
        c.exec_driver_sql(
            "INSERT INTO users (id, org_id, email, name, password_hash, role, active, auth_source, external_id) VALUES ('u1','o1','a@b.io','','x','admin',1,'password','')"
        )
    dbm._add_missing_columns(eng)
    cols = {c["name"] for c in sa.inspect(eng).get_columns("users")}
    assert {"totp_enabled", "totp_secret_encrypted", "totp_last_step", "recovery_codes"} <= cols
    with eng.begin() as c:
        assert c.exec_driver_sql("SELECT recovery_codes, totp_enabled FROM users").fetchone() == ("[]", 0)
