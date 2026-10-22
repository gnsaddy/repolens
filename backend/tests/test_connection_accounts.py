import json

import httpx

from repolens.api import bitbucket as bb_api
from repolens.api import github as gh_api
from repolens.api import jira as jira_api
from repolens.devtools.fake_confluence import FakeConfluence
from repolens.services import wiki_import as wi
from tests.conftest import register
from tests.test_wiki_confluence import make_transport

SECRET = "super-secret-value-123"


def only(path, payload):
    return httpx.MockTransport(
        lambda r: httpx.Response(200, json=payload) if r.url.path.endswith(path) else httpx.Response(404)
    )


def test_github_check_does_not_save_and_saving_records_the_login(client, auth, monkeypatch):
    monkeypatch.setattr(gh_api, "_transport", only("/user", {"login": "octo-admin"}))
    body = {"token": "github_pat_" + "a" * 30, "api_base": "https://api.github.com"}
    assert client.post("/api/github/check", headers=auth, json=body).json() == {"ok": True, "user": "octo-admin"}
    assert client.get("/api/github/connection", headers=auth).json()["connected"] is False  # nothing was stored
    saved = client.put("/api/github/connection", headers=auth, json=body).json()
    assert saved["connected"] is True and saved["login"] == "octo-admin" and "github_pat_" not in json.dumps(saved)
    monkeypatch.setattr(
        gh_api, "_transport", httpx.MockTransport(lambda r: httpx.Response(401, json={"message": SECRET}))
    )
    bad = client.post("/api/github/check", headers=auth, json=body).json()
    assert bad["ok"] is False and SECRET not in json.dumps(bad)
    other = register(client, "x@other.io", "Other")
    assert (
        client.post("/api/github/check", headers=other, json=body).status_code == 200
    )  # an admin of their own org may try tokens
    member = client.post(
        "/api/auth/users", headers=auth, json={"email": "m@acme.io", "password": "Password123!", "role": "member"}
    )
    assert member.status_code == 201
    mtok = client.post("/api/auth/login", json={"email": "m@acme.io", "password": "Password123!"}).json()[
        "access_token"
    ]
    assert client.post("/api/github/check", headers={"Authorization": f"Bearer {mtok}"}, json=body).status_code == 403


def test_jira_account_is_recorded_on_save_and_refreshed_by_test(client, auth, monkeypatch):
    monkeypatch.setattr(jira_api, "_transport", only("/myself", {"displayName": "Ana Lee"}))
    body = {"base_url": "https://acme.atlassian.net", "deployment": "cloud", "email": "ana@acme.io", "token": SECRET}
    assert client.post("/api/jira/check", headers=auth, json=body).json() == {"ok": True, "user": "Ana Lee"}
    assert client.get("/api/jira/connection", headers=auth).json() is None
    saved = client.put("/api/jira/connection", headers=auth, json=body).json()
    assert saved["account"] == "Ana Lee" and SECRET not in json.dumps(saved)
    monkeypatch.setattr(jira_api, "_transport", only("/myself", {"displayName": "Ana L. Lee"}))
    assert client.post("/api/jira/test", headers=auth).json()["user"] == "Ana L. Lee"
    assert (
        client.get("/api/jira/connection", headers=auth).json()["account"] == "Ana L. Lee"
    )  # the successful test updated it
    assert client.post("/api/jira/check", headers=auth, json={**body, "email": ""}).json()["ok"] is False
    assert client.post("/api/jira/check", headers=auth, json={**body, "base_url": "ftp://x"}).json()["ok"] is False
    monkeypatch.setattr(jira_api, "_transport", httpx.MockTransport(lambda r: httpx.Response(401)))
    assert (
        client.put("/api/jira/connection", headers=auth, json=body).json()["account"] == ""
    )  # saving never fails just because the name is unknown


def test_confluence_check_save_and_refresh(client, auth, monkeypatch):
    fake = FakeConfluence()
    monkeypatch.setattr(wi, "confluence_transport", make_transport(fake))
    body = {
        "base_url": "https://acme.atlassian.net/wiki",
        "deployment": "cloud",
        "email": "bot@acme.io",
        "token": "good-token",
    }
    assert client.post("/api/wiki/confluence/check", headers=auth, json=body).json() == {
        "ok": True,
        "user": "RepoLens Bot",
    }
    assert client.get("/api/wiki/confluence/connection", headers=auth).json() == {"connected": False}
    saved = client.put("/api/wiki/confluence/connection", headers=auth, json=body).json()
    assert saved["account"] == "RepoLens Bot" and "good-token" not in json.dumps(saved)
    assert client.get("/api/wiki/confluence/connection", headers=auth).json()["account"] == "RepoLens Bot"
    wrong = client.post("/api/wiki/confluence/check", headers=auth, json={**body, "token": "nope-nope"}).json()
    assert wrong["ok"] is False and "nope-nope" not in json.dumps(wrong)
    assert client.post("/api/wiki/confluence/check", headers=auth, json={**body, "email": ""}).json()["ok"] is False
    assert client.post("/api/wiki/confluence/test", headers=auth).json()["user"] == "RepoLens Bot"


def test_bitbucket_check_account_and_test(client, auth, monkeypatch):
    def handler(r: httpx.Request) -> httpx.Response:
        if r.url.path == "/2.0/user":
            return httpx.Response(200, json={"display_name": "Ana Lee", "nickname": "ana"})
        return httpx.Response(404)

    monkeypatch.setattr(bb_api, "_transport", httpx.MockTransport(handler))
    body = {"deployment": "cloud", "username": "ana@acme.io", "token": "ATATT" + "x" * 20, "workspaces": "acme"}
    assert client.post("/api/bitbucket/check", headers=auth, json=body).json() == {"ok": True, "user": "Ana Lee"}
    assert client.get("/api/bitbucket/connection", headers=auth).json()["connected"] is False
    saved = client.put("/api/bitbucket/connection", headers=auth, json=body).json()
    assert saved["account"] == "Ana Lee" and "ATATT" not in json.dumps(saved)
    monkeypatch.setattr(bb_api, "_transport", httpx.MockTransport(lambda r: httpx.Response(403)))
    # a token that cannot read the profile still saves; the account is just unknown
    assert client.put("/api/bitbucket/connection", headers=auth, json=body).json()["account"] is None
    bad = client.post("/api/bitbucket/check", headers=auth, json={**body, "token": "short"}).json()
    assert bad["ok"] is False
    assert (
        client.post(
            "/api/bitbucket/check",
            headers=auth,
            json={"deployment": "server", "base_url": "ftp://x", "token": "tok-123"},
        ).json()["ok"]
        is False
    )
