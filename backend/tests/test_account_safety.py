import re
import time

from repolens.services import totp
from tests.conftest import register

PW = "Maple-Orbit-2026"


def login(client, email, password=PW, **kw):
    return client.post("/api/auth/login", json={"email": email, "password": password, **kw})


def bearer(r):
    return {"Authorization": "Bearer " + r.json()["access_token"]}


# ------------------------------------------------------------------------------------------------ TOTP primitives
def test_totp_matches_the_rfc_6238_reference_values():
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # the RFC's ASCII secret "12345678901234567890", base32
    assert totp._code(secret, 59 // 30) == "287082" and totp._code(secret, 1111111109 // 30) == "081804"
    assert totp.verify(secret, "287082", 0, now=59) == 1
    assert totp.verify(secret, "287082", 1, now=59) is None  # the same step cannot be used twice
    assert totp.verify(secret, "287082", 0, now=59 + 30) == 1  # one step of clock drift is tolerated
    assert totp.verify(secret, "287082", 0, now=59 + 90) is None and totp.verify(secret, "12345", 0, now=59) is None


def enable_mfa(client, h):
    s = client.post("/api/auth/totp/setup", headers=h).json()
    assert s["uri"].startswith("otpauth://totp/") and s["secret"] in s["uri"]
    r = client.post("/api/auth/totp/enable", headers=h, json={"code": totp._code(s["secret"], int(time.time() // 30))})
    assert r.status_code == 200, r.text
    return s["secret"], r.json()["recovery_codes"]


# ------------------------------------------------------------------------------------------------ sign-in with a second factor
def test_authenticator_app_is_required_after_it_is_enabled(client, auth):
    me = client.get("/api/auth/me", headers=auth).json()["email"]
    secret, recovery = enable_mfa(client, auth)
    assert len(recovery) == 8 and all(re.fullmatch(r"[0-9a-f]{6}-[0-9a-f]{6}", c) for c in recovery)
    assert client.get("/api/auth/me", headers=auth).json()["totp_enabled"] is True
    r = login(client, me, "correct-horse-1")
    assert r.status_code == 401 and r.json()["detail"] == "Authentication code required"
    assert login(client, me, "correct-horse-1", code="000000").status_code == 401
    step = int(time.time() // 30)
    good = totp._code(secret, step + 1)  # a different step from the one used to enable it
    assert login(client, me, "correct-horse-1", code=good).status_code == 200
    assert login(client, me, "correct-horse-1", code=good).status_code == 401  # a code works once
    assert login(client, me, "wrong-password-1", code=good).status_code == 401


def test_recovery_codes_work_once_and_failed_codes_are_audited(client, auth, session):
    from repolens.models import AuditEvent

    me = client.get("/api/auth/me", headers=auth).json()["email"]
    _, recovery = enable_mfa(client, auth)
    assert login(client, me, "correct-horse-1", code=recovery[0]).status_code == 200
    assert login(client, me, "correct-horse-1", code=recovery[0]).status_code == 401
    assert login(client, me, "correct-horse-1", code=recovery[1].upper()).status_code == 200
    assert session.query(AuditEvent).filter_by(action="auth.mfa_failed").count() == 1
    assert session.query(AuditEvent).filter_by(action="auth.mfa_enabled").count() == 1


def test_a_wrong_code_during_setup_changes_nothing_and_disabling_needs_the_password(client, auth):
    me = client.get("/api/auth/me", headers=auth).json()["email"]
    client.post("/api/auth/totp/setup", headers=auth)
    assert client.post("/api/auth/totp/enable", headers=auth, json={"code": "123456"}).status_code == 400
    assert login(client, me, "correct-horse-1").status_code == 200  # still no second factor
    s = client.post("/api/auth/totp/setup", headers=auth).json()
    client.post("/api/auth/totp/enable", headers=auth, json={"code": totp._code(s["secret"], int(time.time() // 30))})
    assert client.post("/api/auth/totp/setup", headers=auth).status_code == 409
    bad = client.post(
        "/api/auth/totp/disable",
        headers=auth,
        json={"password": "nope-nope-1", "code": totp._code(s["secret"], int(time.time() // 30) + 1)},
    )
    assert bad.status_code == 400
    ok = client.post(
        "/api/auth/totp/disable",
        headers=auth,
        json={"password": "correct-horse-1", "code": totp._code(s["secret"], int(time.time() // 30) + 1)},
    )
    assert ok.status_code == 204 and login(client, me, "correct-horse-1").status_code == 200


# ------------------------------------------------------------------------------------------------ invitations and resets
def test_an_invited_person_sets_their_own_password_once(client, auth):
    r = client.post(
        "/api/auth/invites", headers=auth, json={"email": "new@acme.io", "name": "New Person", "role": "viewer"}
    )
    assert r.status_code == 201
    link = r.json()["link"]
    assert link.startswith("/reset?token=") and r.json()["person"]["pending"] == "invite"
    token = link.split("=", 1)[1]
    assert login(client, "new@acme.io", "anything-at-all-1").status_code == 401  # no usable password yet
    assert (
        client.post("/api/auth/reset", json={"token": token, "password": "password"}).status_code == 400
    )  # policy applies
    assert client.post("/api/auth/reset", json={"token": token, "password": PW}).status_code == 204
    me = login(client, "new@acme.io")
    assert me.status_code == 200 and me.json()["user"]["role"] == "viewer"
    assert (
        client.post("/api/auth/reset", json={"token": token, "password": "Another-Pass-2027"}).status_code == 400
    )  # single use
    assert client.post("/api/auth/invites", headers=auth, json={"email": "new@acme.io"}).status_code == 409


def test_a_reset_link_replaces_the_password_and_ends_old_sessions(client, auth):
    client.post(
        "/api/auth/users", headers=auth, json={"email": "p@acme.io", "password": "member-pass-1", "role": "member"}
    )
    old = bearer(login(client, "p@acme.io", "member-pass-1"))
    uid = next(u["id"] for u in client.get("/api/auth/people", headers=auth).json() if u["email"] == "p@acme.io")
    link = client.post(f"/api/auth/users/{uid}/reset-link", headers=auth).json()["link"]
    assert client.get("/api/auth/me", headers=old).status_code == 200  # nothing changes until the link is used
    assert client.post("/api/auth/reset", json={"token": link.split("=", 1)[1], "password": PW}).status_code == 204
    assert client.get("/api/auth/me", headers=old).status_code == 401
    assert (
        login(client, "p@acme.io", "member-pass-1").status_code == 401 and login(client, "p@acme.io").status_code == 200
    )
    second = client.post(f"/api/auth/users/{uid}/reset-link", headers=auth).json()["link"]
    client.post(f"/api/auth/users/{uid}/reset-link", headers=auth)  # a newer link replaces the earlier one
    assert (
        client.post("/api/auth/reset", json={"token": second.split("=", 1)[1], "password": PW + "x"}).status_code == 400
    )  # superseded


def test_expired_and_unknown_links_are_refused_with_one_message(client, auth, session):
    from datetime import timedelta

    from repolens.models import AuthToken, _now

    token = client.post("/api/auth/invites", headers=auth, json={"email": "e@acme.io"}).json()["link"].split("=", 1)[1]
    row = session.query(AuthToken).first()
    row.expires_at = _now() - timedelta(minutes=1)
    session.commit()
    a = client.post("/api/auth/reset", json={"token": token, "password": PW})
    b = client.post("/api/auth/reset", json={"token": "x" * 43, "password": PW})
    assert a.status_code == b.status_code == 400 and a.json() == b.json()
    assert token not in str(session.query(AuthToken).first().token_hash)  # only a hash is stored


def test_admin_tools_are_admin_only_scoped_to_the_workspace_and_audited(client, auth, session):
    from repolens.models import AuditEvent

    client.post(
        "/api/auth/users", headers=auth, json={"email": "m@acme.io", "password": "member-pass-1", "role": "member"}
    )
    m = bearer(login(client, "m@acme.io", "member-pass-1"))
    uid = client.get("/api/auth/me", headers=m).json()["id"]
    for method, url, body in (
        ("get", "/api/auth/people", None),
        ("post", "/api/auth/invites", {"email": "q@acme.io"}),
        ("post", f"/api/auth/users/{uid}/reset-link", None),
        ("post", f"/api/auth/users/{uid}/reset-mfa", None),
    ):
        assert getattr(client, method)(url, headers=m, **({"json": body} if body else {})).status_code == 403, url
    other = register(client, "o@other.io", "Other")
    assert client.post(f"/api/auth/users/{uid}/reset-link", headers=other).status_code == 404
    assert client.get("/api/auth/people", headers=other).json()[0]["email"] == "o@other.io"
    client.post(f"/api/auth/users/{uid}/reset-link", headers=auth)
    assert session.query(AuditEvent).filter_by(action="user.create").count() >= 1


def test_an_admin_can_remove_a_lost_authenticator(client, auth):
    client.post(
        "/api/auth/users", headers=auth, json={"email": "l@acme.io", "password": "member-pass-1", "role": "member"}
    )
    h = bearer(login(client, "l@acme.io", "member-pass-1"))
    enable_mfa(client, h)
    assert login(client, "l@acme.io", "member-pass-1").status_code == 401
    uid = client.get("/api/auth/me", headers=h).json()["id"]
    assert client.post(f"/api/auth/users/{uid}/reset-mfa", headers=auth).status_code == 204
    assert (
        login(client, "l@acme.io", "member-pass-1").status_code == 200
    )  # back to password only; they can set it up again


def test_registration_can_be_closed_but_the_first_workspace_can_always_be_made(client, monkeypatch):
    from repolens import config

    monkeypatch.setenv("REPOLENS_ALLOW_REGISTRATION", "false")
    config.get_settings.cache_clear()
    assert (
        client.post(
            "/api/auth/register", json={"email": "first@acme.io", "password": PW, "organization": "First"}
        ).status_code
        == 201
    )
    r = client.post("/api/auth/register", json={"email": "second@acme.io", "password": PW, "organization": "Second"})
    assert r.status_code == 403 and "invitation" in r.json()["detail"]
