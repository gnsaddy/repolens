"""Sign in with GitHub: allowed e-mail domains, verified addresses only, and the same one-time code as SSO."""

import json
from urllib.parse import parse_qs, unquote, urlparse

import httpx
import pytest

from repolens import config
from repolens.models import SsoFlow, User
from repolens.services import github_login as gl
from tests.conftest import register
from tests.test_chat import make_user


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("REPOLENS_GITHUB_LOGIN_CLIENT_ID", "gh-client")
    monkeypatch.setenv("REPOLENS_GITHUB_LOGIN_CLIENT_SECRET", "gh-secret")
    config.get_settings.cache_clear()
    yield
    config.get_settings.cache_clear()
    gl.transport = None


def github(monkeypatch, *, gh_id=4242, login="ann-gh", name="Ann", emails=None, token="tok"):
    """A fake GitHub: records what it was asked, answers the token, profile and e-mail calls."""
    seen = {"token_form": None, "auth": []}
    rows = (
        emails
        if emails is not None
        else [
            {"email": "ann@acme.io", "primary": True, "verified": True},
            {"email": "ann@gmail.com", "primary": False, "verified": True},
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "github.com" and request.url.path == "/login/oauth/access_token":
            seen["token_form"] = parse_qs(request.content.decode())
            return httpx.Response(200, json={"access_token": token} if token else {"error": "bad_verification_code"})
        seen["auth"].append(request.headers.get("authorization"))
        if request.url.path == "/user":
            return httpx.Response(200, json={"id": gh_id, "login": login, "name": name})
        if request.url.path == "/user/emails":
            return httpx.Response(200, json=rows)
        return httpx.Response(404)

    gl.transport = httpx.MockTransport(handler)
    return seen


def enable(client, auth, domains="acme.io", **extra):
    r = client.put("/api/settings/github-login", headers=auth, json={"allowed_domains": domains, **extra})
    assert r.status_code == 200, r.text
    return r.json()


def start(client):
    r = client.get("/api/auth/github/start", follow_redirects=False)
    assert r.status_code == 302, r.text
    q = parse_qs(urlparse(r.headers["location"]).query)
    return r.headers["location"], q["state"][0]


def callback(client, state, code="the-code"):
    return client.get("/api/auth/github/callback", params={"state": state, "code": code}, follow_redirects=False)


def sign_in(client, state):
    """The whole round trip: callback, then the one-time code the page trades for a token."""
    r = callback(client, state)
    loc = unquote(r.headers["location"])
    if not loc.startswith("/sso-complete?code="):
        return None, loc
    code = loc.split("code=")[1]
    t = client.post("/api/auth/sso/exchange", json={"code": code})
    return t, loc


def test_the_button_is_offered_only_when_the_server_has_an_app_and_a_workspace_allows_it(client, auth, monkeypatch):
    assert client.get("/api/auth/github/enabled").json() == {"enabled": False}
    enable(client, auth)  # a workspace allows it, but the server has no OAuth app
    assert client.get("/api/auth/github/enabled").json() == {"enabled": False}
    monkeypatch.setenv("REPOLENS_GITHUB_LOGIN_CLIENT_ID", "gh-client")
    monkeypatch.setenv("REPOLENS_GITHUB_LOGIN_CLIENT_SECRET", "gh-secret")
    config.get_settings.cache_clear()
    assert client.get("/api/auth/github/enabled").json() == {"enabled": True}
    enable(client, auth, enabled=False)
    assert client.get("/api/auth/github/enabled").json() == {"enabled": False}
    config.get_settings.cache_clear()


def test_start_sends_the_browser_to_github_with_state_pkce_and_only_the_e_mail_scope(client, auth, app):
    enable(client, auth)
    url, state = start(client)
    q = parse_qs(urlparse(url).query)
    assert url.startswith("https://github.com/login/oauth/authorize?")
    assert q["client_id"] == ["gh-client"] and q["scope"] == ["user:email"] and q["code_challenge_method"] == ["S256"]
    assert q["redirect_uri"][0].endswith("/api/auth/github/callback") and len(state) >= 32
    assert "gh-secret" not in url  # the secret never goes through the browser


def test_without_an_app_or_a_workspace_start_is_not_found(client, auth, app, monkeypatch):
    assert client.get("/api/auth/github/start", follow_redirects=False).status_code == 404  # no workspace allows it
    enable(client, auth)
    monkeypatch.setenv("REPOLENS_GITHUB_LOGIN_CLIENT_SECRET", "")
    config.get_settings.cache_clear()
    assert client.get("/api/auth/github/start", follow_redirects=False).status_code == 404  # no app secret


def test_a_new_person_in_an_allowed_domain_is_created_as_a_viewer_and_gets_a_session(
    client, auth, app, monkeypatch, session
):
    enable(client, auth)
    seen = github(monkeypatch)
    _, state = start(client)
    t, _ = sign_in(client, state)
    assert t.status_code == 200
    body = t.json()
    assert body["user"]["email"] == "ann@acme.io" and body["user"]["role"] == "viewer"
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"}).status_code == 200
    u = session.query(User).filter_by(email="ann@acme.io").one()
    assert (u.auth_source, u.external_id) == ("github", "github:4242")
    form = seen["token_form"]
    assert form["client_secret"] == ["gh-secret"] and form["code"] == ["the-code"] and form["code_verifier"][0]
    assert seen["auth"] == ["Bearer tok", "Bearer tok"]


def test_the_default_role_is_used_and_nobody_becomes_an_admin(client, auth, app, monkeypatch):
    enable(client, auth, default_role="member")
    github(monkeypatch)
    t, _ = sign_in(client, start(client)[1])
    assert t.json()["user"]["role"] == "member"
    assert (
        client.put(
            "/api/settings/github-login", headers=auth, json={"allowed_domains": "acme.io", "default_role": "admin"}
        ).status_code
        == 422
    )


def test_an_existing_account_with_the_same_verified_address_is_signed_in_and_linked(
    client, auth, app, monkeypatch, session
):
    make_user(client, auth, "ann@acme.io", "member")
    enable(client, auth)
    github(monkeypatch)
    t, _ = sign_in(client, start(client)[1])
    assert t.status_code == 200 and t.json()["user"]["role"] == "member"  # their own role, not the default
    u = session.query(User).filter_by(email="ann@acme.io").one()
    assert u.external_id == "github:4242" and u.auth_source == "password"  # the password still works
    assert (
        client.post("/api/auth/login", json={"email": "ann@acme.io", "password": "member-pass-12"}).status_code == 200
    )


def test_an_unverified_address_is_never_used(client, auth, app, monkeypatch):
    enable(client, auth)
    github(monkeypatch, emails=[{"email": "ceo@acme.io", "primary": True, "verified": False}])
    t, loc = sign_in(client, start(client)[1])
    assert t is None and "verified" in loc.lower()


def test_a_domain_that_is_not_allowed_or_no_address_at_all_is_refused_with_a_reason(client, auth, app, monkeypatch):
    enable(client, auth)
    github(monkeypatch, emails=[{"email": "me@gmail.com", "primary": True, "verified": True}])
    t, loc = sign_in(client, start(client)[1])
    assert t is None and "allowed" in loc
    github(monkeypatch, emails=[])
    t, loc = sign_in(client, start(client)[1])
    assert t is None and "verified" in loc


def test_a_second_verified_address_in_an_allowed_domain_is_enough(client, auth, app, monkeypatch):
    enable(client, auth)
    github(
        monkeypatch,
        emails=[
            {"email": "me@gmail.com", "primary": True, "verified": True},
            {"email": "ann@acme.io", "primary": False, "verified": True},
        ],
    )
    t, _ = sign_in(client, start(client)[1])
    assert t.json()["user"]["email"] == "ann@acme.io"


def test_new_accounts_can_be_switched_off_and_a_deactivated_one_cannot_sign_in(client, auth, app, monkeypatch, session):
    enable(client, auth, auto_provision=False)
    github(monkeypatch)
    t, loc = sign_in(client, start(client)[1])
    assert t is None and "not been created" in loc
    make_user(client, auth, "ann@acme.io", "member")
    session.query(User).filter_by(email="ann@acme.io").update({"active": False})
    session.commit()
    t, loc = sign_in(client, start(client)[1])
    assert t is None and "deactivated" in loc


def test_an_account_linked_to_another_github_identity_is_not_taken_over(client, auth, app, monkeypatch, session):
    enable(client, auth)
    github(monkeypatch, gh_id=1)
    sign_in(client, start(client)[1])
    github(monkeypatch, gh_id=2)  # a different GitHub account claims the same address
    t, loc = sign_in(client, start(client)[1])
    assert t is None and "different identity" in loc


def test_people_are_never_moved_between_workspaces(client, auth, app, monkeypatch):
    other = register(client, "owner@other.io", "Other")
    make_user(client, other, "ann@acme.io", "member")  # the address already belongs to another workspace
    enable(client, auth)
    github(monkeypatch)
    t, loc = sign_in(client, start(client)[1])
    assert t is None and "another organisation" in loc


def test_a_state_is_single_use_and_a_made_up_one_is_refused(client, auth, app, monkeypatch):
    enable(client, auth)
    github(monkeypatch)
    _, state = start(client)
    assert sign_in(client, state)[0].status_code == 200
    again = callback(client, state)
    assert again.headers["location"].startswith("/login?sso_error=") and "expired" in again.headers["location"]
    assert "expired" in callback(client, "made-up-state").headers["location"]
    assert callback(client, "", "").headers["location"].startswith("/login?sso_error=")


def test_a_code_github_refuses_sends_the_person_back_with_a_message(client, auth, app, monkeypatch):
    enable(client, auth)
    github(monkeypatch, token=None)
    t, loc = sign_in(client, start(client)[1])
    assert t is None and "refused" in loc and "gh-secret" not in loc


def test_a_flow_from_single_sign_on_cannot_complete_a_github_sign_in(client, auth, app, monkeypatch, session):
    enable(client, auth)
    github(monkeypatch)
    session.add(SsoFlow(id="oidc-state-" + "x" * 30, org_id="some-org", nonce="n", code_verifier="v"))
    session.commit()
    assert "expired" in callback(client, "oidc-state-" + "x" * 30).headers["location"]


def test_only_an_admin_sets_it_up_and_a_domain_belongs_to_one_workspace(client, auth, app):
    viewer = make_user(client, auth, "v@acme.io", "viewer")
    member = make_user(client, auth, "m@acme.io", "member")
    for h in (viewer, member):
        assert client.get("/api/settings/github-login", headers=h).status_code == 403
        assert (
            client.put("/api/settings/github-login", headers=h, json={"allowed_domains": "acme.io"}).status_code == 403
        )
    first = client.get("/api/settings/github-login", headers=auth).json()
    assert first["configured"] is False and first["app_configured"] is True
    assert first["redirect_uri"].endswith("/api/auth/github/callback")
    saved = enable(client, auth, "acme.io, @Corp.example")
    assert saved["allowed_domains"] == ["acme.io", "corp.example"] and saved["configured"] is True
    other = register(client, "owner@other.io", "Other")
    assert (
        client.put("/api/settings/github-login", headers=other, json={"allowed_domains": "corp.example"}).status_code
        == 409
    )
    assert (
        client.put("/api/settings/github-login", headers=auth, json={"allowed_domains": "not a domain"}).status_code
        == 400
    )
    assert client.delete("/api/settings/github-login", headers=auth).status_code == 204
    assert client.get("/api/settings/github-login", headers=auth).json()["configured"] is False


def test_nothing_in_the_settings_answer_holds_a_secret(client, auth, app):
    enable(client, auth)
    text = json.dumps(client.get("/api/settings/github-login", headers=auth).json())
    assert "gh-secret" not in text and "gh-client" not in text
