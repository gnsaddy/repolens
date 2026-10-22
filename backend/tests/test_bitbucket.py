import base64
import json

import httpx
import pytest

from repolens.api import bitbucket as bb_api
from repolens.models import Organization, Repository
from repolens.services import indexer
from repolens.services.bitbucket import BitbucketClient, BitbucketError, _strip_user, clone_header
from tests.conftest import register

CLOUD_REPOS = {
    "values": [
        {
            "full_name": "acme/orders",
            "slug": "orders",
            "workspace": {"slug": "acme"},
            "description": "Order service",
            "is_private": True,
            "language": "python",
            "updated_on": "2026-09-01T10:00:00Z",
            "mainbranch": {"name": "main"},
            "links": {
                "html": {"href": "https://bitbucket.org/acme/orders"},
                "clone": [
                    {"name": "https", "href": "https://ana@bitbucket.org/acme/orders.git"},
                    {"name": "ssh", "href": "git@bitbucket.org:acme/orders.git"},
                ],
            },
        },
        {
            "full_name": "acme/web",
            "slug": "web",
            "workspace": {"slug": "acme"},
            "is_private": False,
            "links": {
                "html": {"href": "https://bitbucket.org/acme/web"},
                "clone": [{"name": "https", "href": "https://bitbucket.org/acme/web.git"}],
            },
        },
    ]
}


def cloud(state):
    def handler(r: httpx.Request) -> httpx.Response:
        state["calls"].append((str(r.url), r.headers.get("authorization")))
        p = r.url.path
        if p == "/2.0/user":
            return httpx.Response(200, json={"display_name": "Ana"})
        if p == "/2.0/workspaces":
            return httpx.Response(200, json={"values": [{"slug": "acme"}]})
        if p == "/2.0/repositories/acme":
            if r.url.params.get("page") == "2":
                return httpx.Response(
                    200,
                    json={
                        "values": [
                            {
                                "full_name": "acme/extra",
                                "slug": "extra",
                                "workspace": {"slug": "acme"},
                                "links": {"clone": []},
                            }
                        ]
                    },
                )
            return httpx.Response(
                200, json={**CLOUD_REPOS, "next": "https://api.bitbucket.org/2.0/repositories/acme?page=2"}
            )
        if p == "/2.0/repositories/acme/orders/pullrequests":
            return httpx.Response(
                200,
                json={
                    "values": [
                        {
                            "id": 4,
                            "title": "Fix rounding",
                            "state": "MERGED",
                            "author": {"display_name": "Bo"},
                            "source": {"branch": {"name": "fix"}},
                            "destination": {"branch": {"name": "main"}},
                            "links": {"html": {"href": "https://bitbucket.org/acme/orders/pull-requests/4"}},
                            "updated_on": "2026-09-02T00:00:00Z",
                        },
                        {
                            "id": 5,
                            "title": "Retries",
                            "state": "OPEN",
                            "draft": True,
                            "author": {"display_name": "Cy"},
                            "source": {"branch": {"name": "retry"}},
                            "destination": {"branch": {"name": "main"}},
                            "links": {"html": {"href": "x"}},
                        },
                    ]
                },
            )
        if p == "/2.0/repositories/acme/orders/refs/branches":
            return httpx.Response(200, json={"values": [{"name": "main"}, {"name": "develop"}]})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


@pytest.fixture
def bb(monkeypatch):
    state = {"calls": []}
    monkeypatch.setattr(bb_api, "_transport", cloud(state))
    return state


def connect(client, auth, **over):
    r = client.put(
        "/api/bitbucket/connection",
        headers=auth,
        json={"deployment": "cloud", "username": "ana", "token": "app-password-123", **over},
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_cloud_connection_is_write_only_and_testable(client, auth, bb):
    assert client.get("/api/bitbucket/connection", headers=auth).json()["connected"] is False
    v = connect(client, auth)
    assert v == {
        "connected": True,
        "deployment": "cloud",
        "base_url": None,
        "username": "ana",
        "workspaces": None,
    } and "app-password" not in json.dumps(v)
    assert client.post("/api/bitbucket/test", headers=auth).json() == {"ok": True, "user": "Ana"}
    basic = "Basic " + base64.b64encode(b"ana:app-password-123").decode()
    assert all(c[1] == basic for c in bb["calls"])  # app password goes in the header only
    assert all("app-password" not in c[0] for c in bb["calls"])
    assert client.delete("/api/bitbucket/connection", headers=auth).status_code == 204
    assert client.get("/api/bitbucket/repos", headers=auth).status_code == 404


def test_browse_repos_pulls_branches_cloud(client, auth, bb, session):
    connect(client, auth)
    d = client.get("/api/bitbucket/repos", headers=auth).json()
    assert [r["full_name"] for r in d["items"]] == ["acme/orders", "acme/web", "acme/extra"]  # followed the second page
    o = d["items"][0]
    assert (
        o["private"] is True
        and o["clone_url"] == "https://bitbucket.org/acme/orders.git"
        and "ana@" not in o["clone_url"]
    )  # no user name kept
    assert o["default_branch"] == "main" and o["owner"] == "acme" and o["name"] == "orders"
    assert [
        r["full_name"]
        for r in client.get("/api/bitbucket/repos", headers=auth, params={"q": "service"}).json()["items"]
    ] == ["acme/orders"]
    org = session.query(Organization).first().id
    row = Repository(
        org_id=org, name="orders-svc", source="git", url="https://bitbucket.org/Acme/orders.git", status="ready"
    )
    session.add(row)
    session.commit()
    assert (
        client.get("/api/bitbucket/repos", headers=auth, params={"q": "orders"}).json()["items"][0]["added_repo_id"]
        == row.id
    )
    prs = client.get("/api/bitbucket/repos/acme/orders/pulls", headers=auth, params={"state": "all"}).json()
    assert [(p["number"], p["state"], p["draft"], p["author"], p["head"]) for p in prs] == [
        (4, "merged", False, "Bo", "fix"),
        (5, "open", True, "Cy", "retry"),
    ]
    assert [b["name"] for b in client.get("/api/bitbucket/repos/acme/orders/branches", headers=auth).json()] == [
        "main",
        "develop",
    ]
    assert client.get("/api/bitbucket/repos/acme/orders/pulls", headers=auth, params={"state": "x"}).status_code == 400
    assert client.get("/api/bitbucket/repos/acme/nope/pulls", headers=auth).status_code == 404
    assert client.get("/api/bitbucket/repos/a b/c/pulls", headers=auth).status_code in (400, 404)


def test_server_flavour_and_validation(client, auth, monkeypatch):
    def handler(r: httpx.Request) -> httpx.Response:
        assert r.headers["authorization"] == "Bearer pat-xyz-1"
        p = r.url.path
        if p == "/rest/api/1.0/repos":
            if r.url.params.get("start") == "0":
                return httpx.Response(
                    200,
                    json={
                        "values": [
                            {
                                "slug": "orders",
                                "name": "orders",
                                "project": {"key": "PAY"},
                                "public": False,
                                "links": {
                                    "clone": [{"name": "http", "href": "https://bb.corp.io/scm/pay/orders.git"}],
                                    "self": [{"href": "https://bb.corp.io/projects/PAY/repos/orders/browse"}],
                                },
                            }
                        ],
                        "isLastPage": False,
                        "nextPageStart": 1,
                    },
                )
            return httpx.Response(
                200,
                json={
                    "values": [
                        {
                            "slug": "web",
                            "project": {"key": "PAY"},
                            "public": True,
                            "links": {"clone": [], "self": [{"href": "u"}]},
                        }
                    ],
                    "isLastPage": True,
                },
            )
        if p == "/rest/api/1.0/projects/PAY/repos/orders/pull-requests":
            return httpx.Response(
                200,
                json={
                    "values": [
                        {
                            "id": 9,
                            "title": "Add cache",
                            "state": "OPEN",
                            "author": {"user": {"displayName": "Dee"}},
                            "fromRef": {"displayId": "cache"},
                            "toRef": {"displayId": "master"},
                            "links": {"self": [{"href": "https://bb.corp.io/pr/9"}]},
                            "updatedDate": 1790000000000,
                        }
                    ],
                    "isLastPage": True,
                },
            )
        if p == "/rest/api/1.0/projects/PAY/repos/orders/branches":
            return httpx.Response(
                200, json={"values": [{"displayId": "master", "isDefault": True}], "isLastPage": True}
            )
        return httpx.Response(404)

    monkeypatch.setattr(bb_api, "_transport", httpx.MockTransport(handler))
    connect(client, auth, deployment="server", base_url="https://bb.corp.io", username="", token="pat-xyz-1")
    items = client.get("/api/bitbucket/repos", headers=auth).json()["items"]
    assert [r["full_name"] for r in items] == ["PAY/orders", "PAY/web"] and items[0][
        "clone_url"
    ] == "https://bb.corp.io/scm/pay/orders.git"
    pr = client.get("/api/bitbucket/repos/PAY/orders/pulls", headers=auth).json()[0]
    assert (pr["number"], pr["author"], pr["head"], pr["base"], pr["state"]) == (
        9,
        "Dee",
        "cache",
        "master",
        "open",
    ) and pr["updated_at"].startswith("2026")
    assert client.get("/api/bitbucket/repos/PAY/orders/branches", headers=auth).json() == [
        {"name": "master", "protected": False, "default": True}
    ]
    # validation: server needs a safe URL; token required
    assert client.put(
        "/api/bitbucket/connection",
        headers=auth,
        json={"deployment": "server", "base_url": "http://127.0.0.1", "token": "abcd"},
    ).status_code in (200, 400)
    assert (
        client.put("/api/bitbucket/connection", headers=auth, json={"deployment": "cloud", "token": "x"}).status_code
        == 422
    )
    other = register(client, "x@other.io", "Other")
    assert client.get("/api/bitbucket/repos", headers=other).status_code == 404


def test_errors_are_explained_without_leaking(client, auth, monkeypatch):
    monkeypatch.setattr(
        bb_api,
        "_transport",
        httpx.MockTransport(lambda r: httpx.Response(401, json={"error": "secret app-password-123"})),
    )
    connect(client, auth)
    r = client.get("/api/bitbucket/repos", headers=auth)
    assert (
        r.status_code == 502
        and "did not accept the username and token" in r.json()["detail"]
        and "app-password" not in r.text
    )
    assert client.post("/api/bitbucket/test", headers=auth).json()["ok"] is False


def test_clone_credentials_only_go_to_the_matching_host_and_never_into_urls(client, auth, session):
    connect(client, auth)
    org = session.query(Organization).first().id
    basic = "Authorization: Basic " + base64.b64encode(b"ana:app-password-123").decode()
    assert indexer.clone_auth_for(session, org, "https://bitbucket.org/acme/orders.git") == basic
    assert indexer.clone_auth_for(session, org, "https://evil.example/acme/orders.git") is None
    assert indexer.clone_auth_for(session, org, "git@bitbucket.org:acme/orders.git") is None
    assert (
        clone_header("cloud", "tok") == "Authorization: Basic " + base64.b64encode(b"x-token-auth:tok").decode()
    )  # access tokens
    assert clone_header("server", "pat") == "Authorization: Bearer pat"
    assert _strip_user("https://ana@bitbucket.org:7999/a/b.git") == "https://bitbucket.org:7999/a/b.git"
    with pytest.raises(BitbucketError):
        BitbucketClient("server", "t", base_url="ftp://x")


def test_cloud_test_button_tolerates_tokens_without_the_profile_scope_and_explains_failures(client, auth, monkeypatch):
    def handler(r: httpx.Request) -> httpx.Response:
        if r.url.path == "/2.0/user":
            return httpx.Response(
                403, json={"error": {"message": "Your credentials lack one or more required privilege scopes."}}
            )
        if r.url.path == "/2.0/workspaces":
            return httpx.Response(200, json={"values": [{"slug": "acme"}]})
        return httpx.Response(404)

    monkeypatch.setattr(bb_api, "_transport", httpx.MockTransport(handler))
    connect(client, auth, username="me@example.com")
    out = client.post("/api/bitbucket/test", headers=auth).json()
    assert out["ok"] is True and "can list repositories but not read your profile" in out["user"]

    monkeypatch.setattr(bb_api, "_transport", httpx.MockTransport(lambda r: httpx.Response(403)))
    msg = client.post("/api/bitbucket/test", headers=auth).json()
    assert (
        msg["ok"] is False
        and "lacks permission to list workspaces" in msg["error"]
        and "read:workspace:bitbucket" in msg["error"]
    )

    monkeypatch.setattr(bb_api, "_transport", httpx.MockTransport(lambda r: httpx.Response(401)))
    msg = client.post("/api/bitbucket/test", headers=auth).json()
    assert msg["ok"] is False and "Atlassian email" in msg["error"] and "app-password" not in msg["error"]


def test_atlassian_api_tokens_need_the_email_and_clone_with_their_own_git_user(client, auth, bb, session):
    r = client.put(
        "/api/bitbucket/connection",
        headers=auth,
        json={"deployment": "cloud", "username": "", "token": "ATATT3xFfGF0abc123"},
    )
    assert r.status_code == 400 and "Atlassian email" in r.json()["detail"]
    ok = client.put(
        "/api/bitbucket/connection",
        headers=auth,
        json={"deployment": "cloud", "username": "me@example.com", "token": "ATATT3xFfGF0abc123"},
    )
    assert ok.status_code == 200
    client.post("/api/bitbucket/test", headers=auth)
    assert (
        bb["calls"][-1][1] == "Basic " + base64.b64encode(b"me@example.com:ATATT3xFfGF0abc123").decode()
    )  # REST: email + token
    org = session.query(Organization).first().id
    git = indexer.clone_auth_for(session, org, "https://bitbucket.org/acme/orders.git")
    assert git == "Authorization: Basic " + base64.b64encode(b"x-bitbucket-api-token-auth:ATATT3xFfGF0abc123").decode()
    assert (
        clone_header("cloud", "tok", "ana") == "Authorization: Basic " + base64.b64encode(b"ana:tok").decode()
    )  # app passwords keep their user


def test_account_wide_listing_is_never_used_and_workspaces_can_be_set(client, auth, monkeypatch):
    seen = []

    def handler(r: httpx.Request) -> httpx.Response:
        seen.append(r.url.path)
        if r.url.path == "/2.0/workspaces":
            return httpx.Response(
                404, json={"error": {"message": "Not found"}}
            )  # the credential cannot list workspaces
        if r.url.path == "/2.0/repositories/my-team":
            return httpx.Response(
                200,
                json={
                    "values": [
                        {
                            "full_name": "my-team/api",
                            "slug": "api",
                            "workspace": {"slug": "my-team"},
                            "links": {"clone": [{"name": "https", "href": "https://bitbucket.org/my-team/api.git"}]},
                        }
                    ]
                },
            )
        return httpx.Response(404, json={"error": {"message": "Not found"}})

    monkeypatch.setattr(bb_api, "_transport", httpx.MockTransport(handler))
    connect(client, auth)
    # without a workspace the failure tells you what to enter, and names the missing piece
    r = client.get("/api/bitbucket/repos", headers=auth)
    assert r.status_code == 404 and "Workspace field" in r.json()["detail"]
    assert "/2.0/repositories" not in seen  # the removed account-wide endpoint is not called
    v = client.put(
        "/api/bitbucket/connection",
        headers=auth,
        json={
            "deployment": "cloud",
            "username": "me@example.com",
            "token": "ATATT3xFfGF0abc123",
            "workspaces": "my-team, other",
        },
    ).json()
    assert v["workspaces"] == "my-team,other"
    seen.clear()
    items = client.get("/api/bitbucket/repos", headers=auth).json()["items"]
    assert [i["full_name"] for i in items] == ["my-team/api"]  # "other" could not be read, which did not hide my-team
    assert "/2.0/workspaces" not in seen  # configured workspaces are used directly
    # a 404 says which address was not found
    monkeypatch.setattr(bb_api, "_transport", httpx.MockTransport(lambda r: httpx.Response(404)))
    msg = client.post("/api/bitbucket/test", headers=auth).json()
    assert msg["ok"] is False and "/2.0/" in msg["error"]
    assert (
        client.put(
            "/api/bitbucket/connection",
            headers=auth,
            json={"deployment": "cloud", "token": "tok-123", "workspaces": "bad;slug"},
        ).status_code
        == 422
    )
