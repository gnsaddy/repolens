from tests.conftest import register


def login(client, email, password):
    r = client.post(
        "/api/auth/login",
        json={"email": email, "password": password},
        headers={"user-agent": "Mozilla/5.0 (Windows NT 10.0) Chrome/120.0 Safari/537"},
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def make_user(client, auth, email="s@acme.io", password="Maple-Orbit-2026"):
    assert (
        client.post(
            "/api/auth/users", headers=auth, json={"email": email, "password": password, "role": "member"}
        ).status_code
        == 201
    )
    return email, password


def test_each_sign_in_is_a_session_that_can_be_listed_and_ended(client, auth):
    email, pw = make_user(client, auth)
    a, b = login(client, email, pw), login(client, email, pw)
    rows = client.get("/api/auth/sessions", headers=a).json()
    assert len(rows) == 2 and sum(r["current"] for r in rows) == 1 and rows[0]["device"] == "Chrome on Windows"
    other = next(r for r in rows if not r["current"])
    assert client.delete(f"/api/auth/sessions/{other['id']}", headers=a).status_code == 204
    assert client.get("/api/auth/me", headers=b).status_code == 401  # the other device was signed out
    assert client.get("/api/auth/me", headers=a).status_code == 200
    assert client.delete(f"/api/auth/sessions/{other['id']}", headers=a).status_code == 404


def test_logout_ends_only_this_session_and_logout_others_keeps_this_one(client, auth):
    email, pw = make_user(client, auth)
    a, b, c = (login(client, email, pw) for _ in range(3))
    assert client.post("/api/auth/logout", headers=a).status_code == 204
    assert (
        client.get("/api/auth/me", headers=a).status_code == 401
        and client.get("/api/auth/me", headers=b).status_code == 200
    )
    assert client.post("/api/auth/logout-others", headers=b).json()["ended"] == 1
    assert (
        client.get("/api/auth/me", headers=c).status_code == 401
        and client.get("/api/auth/me", headers=b).status_code == 200
    )


def test_sessions_are_private_to_their_owner(client, auth):
    email, pw = make_user(client, auth)
    mine = login(client, email, pw)
    sid = client.get("/api/auth/sessions", headers=mine).json()[0]["id"]
    assert (
        client.delete(f"/api/auth/sessions/{sid}", headers=auth).status_code == 404
    )  # not even an admin can end another user's here
    assert all(r["id"] != sid for r in client.get("/api/auth/sessions", headers=auth).json())


def test_profile_name_and_password_change(client, auth):
    email, pw = make_user(client, auth)
    a, b = login(client, email, pw), login(client, email, pw)
    assert client.patch("/api/auth/me", headers=a, json={"name": "  Sam Lee "}).json()["name"] == "Sam Lee"
    assert (
        client.post(
            "/api/auth/password", headers=a, json={"current_password": "nope", "new_password": "NewPassword456!"}
        ).status_code
        == 400
    )
    assert (
        client.post("/api/auth/password", headers=a, json={"current_password": pw, "new_password": "short"}).status_code
        == 422
    )
    assert (
        client.post(
            "/api/auth/password", headers=a, json={"current_password": pw, "new_password": "NewPassword456!"}
        ).status_code
        == 204
    )
    assert (
        client.get("/api/auth/me", headers=b).status_code == 401
        and client.get("/api/auth/me", headers=a).status_code == 200
    )
    assert client.post("/api/auth/login", json={"email": email, "password": pw}).status_code == 401
    assert client.post("/api/auth/login", json={"email": email, "password": "NewPassword456!"}).status_code == 200


def test_tokens_without_a_session_still_work_for_existing_installs(client, auth, session):
    from repolens.models import User
    from repolens.security import create_token

    u = session.query(User).first()
    old = {"Authorization": f"Bearer {create_token(u.id, u.org_id, u.role)}"}
    assert client.get("/api/auth/me", headers=old).status_code == 200


def test_metrics_overview_summarises_the_workspace(client, auth, demo_repo):
    from tests.test_api import add_repo

    add_repo(client, auth, demo_repo[0])
    m = client.get("/api/metrics/overview", headers=auth).json()
    assert m["content"]["repositories"] == 1 and m["content"]["chunks"] > 0 and m["content"]["graph_nodes"] > 0
    assert {"investigations", "avg_minutes", "drift_open"} <= set(m["work"])
    assert {"requests", "tokens", "cost", "tokens_avoided", "savings_rate", "by_model", "daily"} <= set(m["ai"])
    assert m["team"]["users"] >= 1 and m["team"]["active_sessions"] >= 1 and "entries" in m["memory"]
    assert (
        client.get("/api/metrics/overview", headers=register(client, "z@other.io", "Z")).json()["content"][
            "repositories"
        ]
        == 0
    )


def test_repeated_wrong_passwords_pause_sign_in_for_that_account_only(client, auth):
    email, pw = make_user(client, auth)
    for _ in range(5):
        assert client.post("/api/auth/login", json={"email": email, "password": "wrong-password-1"}).status_code == 401
    r = client.post("/api/auth/login", json={"email": email, "password": pw})
    assert (
        r.status_code == 429 and "Too many failed sign-ins" in r.json()["detail"] and int(r.headers["retry-after"]) > 0
    )
    other, other_pw = make_user(client, auth, "t@acme.io")
    assert client.post("/api/auth/login", json={"email": other, "password": other_pw}).status_code == 200


def test_a_successful_sign_in_forgives_earlier_mistakes(client, auth):
    email, pw = make_user(client, auth)
    for _ in range(4):
        client.post("/api/auth/login", json={"email": email, "password": "wrong-password-1"})
    assert client.post("/api/auth/login", json={"email": email, "password": pw}).status_code == 200
    for _ in range(4):
        client.post("/api/auth/login", json={"email": email, "password": "wrong-password-1"})
    assert client.post("/api/auth/login", json={"email": email, "password": pw}).status_code == 200


def test_idle_sessions_end_on_their_own(client, auth, session, monkeypatch):
    from datetime import timedelta

    from repolens.models import UserSession, _now

    email, pw = make_user(client, auth)
    a = login(client, email, pw)
    assert client.get("/api/auth/me", headers=a).status_code == 200
    row = session.query(UserSession).filter_by(revoked_at=None).order_by(UserSession.created_at.desc()).first()
    row.last_seen_at = _now() - timedelta(hours=9)
    session.commit()
    r = client.get("/api/auth/me", headers=a)
    assert r.status_code == 401 and "ended" in r.json()["detail"]
