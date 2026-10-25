from datetime import timedelta

from repolens.models import ApiToken, _now
from tests.conftest import register


def bearer(t):
    return {"Authorization": f"Bearer {t}"}


def make(client, h, **kw):
    r = client.post("/api/auth/api-tokens", headers=h, json={"name": "ci", **kw})
    assert r.status_code == 201, r.text
    return r.json()


def test_a_token_is_shown_once_stored_as_a_hash_and_acts_as_its_owner(client, auth, session):
    t = make(client, auth)
    assert t["token"].startswith("rlp_") and t["prefix"] == t["token"][:8] and t["scope"] == "read"
    row = session.query(ApiToken).one()
    assert t["token"] not in row.token_hash and row.token_hash != t["token"]
    me = client.get("/api/auth/me", headers=bearer(t["token"]))
    assert me.status_code == 200 and me.json()["email"] == "admin@acme.io"
    listed = client.get("/api/auth/api-tokens", headers=auth).json()
    assert len(listed) == 1 and "token" not in listed[0] and listed[0]["last_used_at"] is not None


def test_read_tokens_are_viewers_and_write_tokens_are_never_admins(client, auth):
    read, write = make(client, auth, scope="read")["token"], make(client, auth, scope="write", name="deploy")["token"]
    body = {"name": "x"}
    assert (
        client.post("/api/knowledge/collections", headers=bearer(read), json=body).status_code == 403
    )  # a viewer cannot write
    assert (
        client.post("/api/knowledge/collections", headers=bearer(write), json=body).status_code == 201
    )  # a member can
    assert (
        client.put("/api/settings/routing", headers=bearer(write), json={"savings_mode": False}).status_code == 403
    )  # not an admin
    assert client.get("/api/audit", headers=bearer(write)).status_code == 403
    assert client.get("/api/repositories", headers=bearer(read)).status_code == 200


def test_tokens_cannot_mint_tokens_or_change_account_security(client, auth):
    tok = make(client, auth, scope="write")["token"]
    h = bearer(tok)
    assert client.post("/api/auth/api-tokens", headers=h, json={"name": "more"}).status_code == 403
    assert client.get("/api/auth/api-tokens", headers=h).status_code == 403
    assert (
        client.post(
            "/api/auth/password",
            headers=h,
            json={"current_password": "correct-horse-1", "new_password": "Maple-Orbit-2026"},
        ).status_code
        == 403
    )
    assert client.post("/api/auth/totp/setup", headers=h).status_code == 403
    assert client.get("/api/auth/sessions", headers=h).status_code == 403
    assert client.patch("/api/auth/me", headers=h, json={"name": "x"}).status_code == 403


def test_revoked_expired_and_deactivated_tokens_stop_working(client, auth, session):
    a, b, c = make(client, auth, name="a"), make(client, auth, name="b"), make(client, auth, name="c")
    assert client.delete(f"/api/auth/api-tokens/{a['id']}", headers=auth).status_code == 204
    assert client.get("/api/auth/me", headers=bearer(a["token"])).status_code == 401
    row = session.get(ApiToken, b["id"])
    row.expires_at = _now() - timedelta(minutes=1)
    session.commit()
    assert client.get("/api/auth/me", headers=bearer(b["token"])).status_code == 401
    assert client.get("/api/auth/me", headers=bearer(c["token"])).status_code == 200
    assert client.get("/api/auth/me", headers=bearer("rlp_not-a-real-token")).status_code == 401


def test_people_only_see_and_revoke_their_own_tokens(client, auth):
    t = make(client, auth)
    other = register(client, "o@other.io", "O")
    assert client.get("/api/auth/api-tokens", headers=other).json() == []
    assert client.delete(f"/api/auth/api-tokens/{t['id']}", headers=other).status_code == 404
    assert client.post("/api/auth/api-tokens", headers=auth, json={"name": "bad", "scope": "root"}).status_code == 422


def test_viewers_can_only_make_read_tokens(client, auth):
    client.post(
        "/api/auth/users", headers=auth, json={"email": "v@acme.io", "password": "viewer-pass-1", "role": "viewer"}
    )
    v = bearer(
        client.post("/api/auth/login", json={"email": "v@acme.io", "password": "viewer-pass-1"}).json()["access_token"]
    )
    assert client.post("/api/auth/api-tokens", headers=v, json={"name": "x", "scope": "write"}).status_code == 403
    assert client.post("/api/auth/api-tokens", headers=v, json={"name": "x", "scope": "read"}).status_code == 201
